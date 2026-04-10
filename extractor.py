"""LLM-based extraction of director compensation data from a DEF 14A.

Builds a structured-output prompt from the field dictionary, sends the
filing text to either Claude or OpenAI, parses the JSON response, and
returns (values_dict, audit_log).

Audit log entries: {key, value, section, quote, confidence}
"""
from typing import List, Dict, Tuple, Any
import json
import re
from bs4 import BeautifulSoup

from schema import Field

# Cap on filing text we send to the model. ~400k chars ≈ 100k tokens, well
# within Claude Sonnet's 200k context.
MAX_FILING_CHARS = 400_000

SYSTEM_PROMPT = """You are a senior compensation analyst extracting NON-EMPLOYEE DIRECTOR compensation data from a SEC DEF 14A proxy statement (or 10-K/A Part III).

CRITICAL RULES:
1. Extract ONLY non-employee director compensation. Ignore named executive officer (NEO) pay.
2. If a value is NOT disclosed in the filing, return null. NEVER invent zeros.
3. Dollar values: numeric only, no $ or commas (e.g., 110000 not "$110,000").
4. Flags ("X" or null): "X" if present, null if not.
5. For each non-null field, provide a short source quote (<=20 words) and confidence (high/medium/low).
6. If the company has both a Non-Executive Chair AND a Lead/Presiding Director, populate both sets of fields.
7. If a leadership role does not exist at the company, leave those fields null (do not write 0).
8. Return ONLY a JSON object — no preamble, no markdown fences."""


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def build_field_schema_block(fields: List[Field]) -> str:
    """Render the schema as a compact, grouped reference for the prompt."""
    by_block: Dict[str, List[Field]] = {}
    for f in fields:
        by_block.setdefault(f.block, []).append(f)
    out = []
    for block, flds in by_block.items():
        out.append(f"\n## {block}")
        for f in flds:
            line = f'- "{f.key}" ({f.fmt}): {f.definition}'
            if f.guidance and f.guidance not in f.definition:
                line += f" [{f.guidance}]"
            out.append(line)
    return "\n".join(out)


def build_user_prompt(fields: List[Field], filing_text: str, ticker: str, company: str) -> str:
    schema_block = build_field_schema_block(fields)
    if len(filing_text) > MAX_FILING_CHARS:
        filing_text = filing_text[:MAX_FILING_CHARS] + "\n[...truncated...]"

    return f"""Extract non-employee director compensation data for {company} ({ticker}) from the filing below.

Return a JSON object with this exact shape:
{{
  "extractions": {{
    "<variable_key>": {{
      "value": <number|string|null>,
      "section": "<short section name where you found it, e.g. 'Director Compensation Table'>",
      "quote": "<<=20 word verbatim snippet>",
      "confidence": "high|medium|low"
    }},
    ...
  }}
}}

Include EVERY variable_key from the schema below, even if value is null.

=== SCHEMA (variable_key, format, definition) ==={schema_block}

=== FILING TEXT ===
{filing_text}
"""


def call_claude(api_key: str, system: str, user: str, model: str = "claude-sonnet-4-5") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def call_openai(api_key: str, system: str, user: str, model: str = "gpt-4.1") -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def parse_response(raw: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    # Strip code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    data = json.loads(raw)
    extractions = data.get("extractions", data)
    values: Dict[str, Any] = {}
    audit: List[Dict[str, Any]] = []
    for key, info in extractions.items():
        if not isinstance(info, dict):
            values[key] = info
            continue
        v = info.get("value")
        if v is not None and v != "":
            values[key] = v
        audit.append({
            "key": key, "value": v,
            "section": info.get("section", ""),
            "quote": info.get("quote", ""),
            "confidence": info.get("confidence", ""),
        })
    return values, audit


def extract_from_filing(fields: List[Field], filing_html: str, ticker: str, company: str,
                        provider: str, api_key: str,
                        model: str = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    text = html_to_text(filing_html)
    user = build_user_prompt(fields, text, ticker, company)
    if provider == "claude":
        raw = call_claude(api_key, SYSTEM_PROMPT, user, model or "claude-sonnet-4-5")
    elif provider == "openai":
        raw = call_openai(api_key, SYSTEM_PROMPT, user, model or "gpt-4.1")
    else:
        raise ValueError(f"Unknown provider: {provider}")
    return parse_response(raw)
