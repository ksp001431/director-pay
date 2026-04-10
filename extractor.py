"""LLM-based extraction of director compensation data from a DEF 14A.

Builds a structured-output prompt from the field dictionary, sends the
filing text to either Claude or OpenAI, parses the JSON response, and
returns (values_dict, audit_log).

Audit log entries: {key, value, section, quote, confidence}
"""
from typing import List, Dict, Tuple, Any
import json
import re
import time
from bs4 import BeautifulSoup

from schema import Field

# Hard cap on filing text we send to the model. ~80k chars ≈ 20k tokens,
# safely under Anthropic Tier 1's 30k tokens-per-minute limit when combined
# with the schema prompt. Tier 3+ users could raise this for fewer truncations.
MAX_FILING_CHARS = 80_000

# Heuristic patterns for finding the director compensation section. Ordered
# from most specific to most general — we use the first match.
SECTION_HEADINGS = [
    r"(?im)^\s*director\s+compensation(?:\s+for\s+fiscal\s+year)?\s*$",
    r"(?im)^\s*non-?employee\s+director\s+compensation\s*$",
    r"(?im)^\s*compensation\s+of\s+(?:our\s+)?(?:non-?employee\s+)?directors\s*$",
    r"(?i)\bdirector\s+compensation\s+(?:table|program)",
    r"(?i)\bnon-?employee\s+director\s+compensation\b",
    r"(?i)\bcompensation\s+of\s+(?:our\s+)?directors\b",
]

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


def extract_director_comp_section(text: str, max_chars: int = MAX_FILING_CHARS) -> str:
    """Find the director compensation section and return a window around it.

    Strategy: locate the first heading match, take a window of max_chars starting
    from ~5% before the heading (to capture context) extending forward.
    Falls back to first max_chars of the document if no heading is found.
    """
    for pat in SECTION_HEADINGS:
        m = re.search(pat, text)
        if m:
            start = max(0, m.start() - max_chars // 20)
            end = min(len(text), start + max_chars)
            return text[start:end]
    # Fallback: return the first chunk
    return text[:max_chars]


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
    return f"""Extract non-employee director compensation data for {company} ({ticker}) from the filing excerpt below.

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

=== FILING EXCERPT (director compensation section) ===
{filing_text}
"""


def call_claude(api_key: str, system: str, user: str, model: str = "claude-sonnet-4-5") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    # The SDK retries 429s automatically with backoff; we add an outer retry
    # for the case where a single request exceeds per-minute budget.
    last_err = None
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=model, max_tokens=16000, system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text
        except anthropic.RateLimitError as e:
            last_err = e
            wait = 60 * (attempt + 1)
            time.sleep(wait)
    raise last_err


def call_openai(api_key: str, system: str, user: str, model: str = "gpt-4.1") -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, max_retries=4)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def parse_response(raw: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
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
    full_text = html_to_text(filing_html)
    section_text = extract_director_comp_section(full_text)
    user = build_user_prompt(fields, section_text, ticker, company)
    if provider == "claude":
        raw = call_claude(api_key, SYSTEM_PROMPT, user, model or "claude-sonnet-4-5")
    elif provider == "openai":
        raw = call_openai(api_key, SYSTEM_PROMPT, user, model or "gpt-4.1")
    else:
        raise ValueError(f"Unknown provider: {provider}")
    return parse_response(raw)
