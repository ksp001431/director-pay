# Director Pay Research Tool — Prototype

Populates the director compensation peer-data template from SEC DEF 14A
filings, using an LLM (Claude or OpenAI) for extraction.

## Status: Prototype, Stage 1 of 3

| Layer | Status |
|---|---|
| Column mapping (dictionary → 107 input fields) | **Validated** — round-trip diff = 0 cells |
| Template writer (preserves formulas/formatting) | **Validated** — round-trip diff = 0 cells |
| EDGAR client (ticker → filing precedence) | Built, untested in this sandbox (no network) |
| LLM extraction (Claude/OpenAI) | Built, **needs your API key to validate** |
| Audit log + low-confidence highlighting | Built |
| Streamlit UI | Built |
| Hosting / deploy | Not yet — validate extraction first |

## What you should do before we move forward

The whole point of stopping at Stage 1 is to test extraction quality on
ACN/ADP/CRM (which you already have ground truth for) before I build out the
deployment. Here's the workflow:

### 1. Install
```bash
cd director_pay
pip install -r requirements.txt
```

### 2. Run on the 3 known tickers
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python cli.py \
  --tickers NYSE:ACN,NASDAQ:ADP,NYSE:CRM \
  --template /path/to/DIRECTOR_PAY__Template_.xlsx \
  --out test_output.xlsx
```
Cost: ~$1 total at Claude Sonnet 4.5 prices.

### 3. Compare against your example file
```bash
python validate.py --generated test_output.xlsx
```
This prints, for each ticker: match count, mismatch count, missed fields,
hallucinated fields, and overall recall on disclosed fields. Anything below
**~90% recall** means I need to improve the prompt/schema before we build
the full app. **Send me the validate.py output and I'll iterate.**

### 4. Manually inspect the output file
- `Peer Data` tab: rows 15–17 should be populated for ACN/ADP/CRM
- `_Run Summary` tab: status of each ticker
- `_Extraction Log` (hidden — unhide via right-click on tab): every field
  with source quote and confidence
- Yellow-highlighted cells in Peer Data = low confidence, worth double-checking

## What I built and how it fits together

```
schema.py        Loads the data dictionary, builds 107 input field map
edgar.py         Ticker → CIK → filing precedence (DEF 14A → 10-K/A → skip 20-F)
extractor.py     Builds prompt from schema, calls Claude/OpenAI, parses JSON
writer.py        Writes values into the template, preserves formulas
orchestrator.py  Runs the full pipeline + audit log + low-conf highlighting
cli.py           Command-line entry point for testing
app.py           Streamlit web UI
validate.py      Compare generated output vs. example file
roundtrip_test.py  Validation that the writer is byte-perfect (already passes)
```

## Design choices worth flagging

- **Filing precedence**: DEF 14A within 12 months → 10-K/A → most recent
  DEF 14A regardless of age → skip if 20-F only.
- **Diversity & comp consultant fields**: per your instruction, omitted from
  extraction. They live beyond column FA, which the data dictionary doesn't
  cover anyway, so this was free.
- **Both chair and lead director**: both sets of fields populate when both
  roles exist. Unused-position fields stay blank (no zeros).
- **Helper / formula cells**: writer skips them entirely. Round-trip test
  confirmed no formula was overwritten.
- **Low-confidence flagging**: cells the LLM marked confidence "low" get
  yellow fill so you can spot-check them quickly.
- **Audit trail**: hidden `_Extraction Log` sheet with source section + ≤20
  word quote + confidence for every field. Unhide it from Excel's tab menu.

## Open items / things I want to confirm after you've tested

- **Prompt iteration**: I'll likely need to refine wording on a few field
  groups after seeing the first results — incremental chair retainers and
  the cash/equity split on leadership retainers are the most likely sources
  of error.
- **Filing text size**: I cap at 400k chars (~100k tokens) — well under
  Claude Sonnet's 200k window. If a filing exceeds the cap I truncate. May
  need smarter chunking (extract just the director comp section first) for
  unusually long filings.
- **Concurrency**: CLI runs sequentially right now. For 20-ticker batches
  I'll add async/parallel calls in Stage 2.
- **Hosting**: Streamlit Community Cloud is free and would work. Deploy
  step happens after we're happy with extraction quality.

## Round-trip validation results (already done)

```
Extracted NYSE:ACN: 51 non-blank input fields
Extracted NASDAQ:ADP: 51 non-blank input fields
Extracted NYSE:CRM: 59 non-blank input fields
=== ROUND-TRIP DIFF: 0 differences ===
```

This proves the writer can populate the template byte-identically to your
example file when given the correct values. The only remaining unknown is
whether the LLM produces those correct values from the raw filing.
