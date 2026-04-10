"""Streamlit web app for Director Pay extraction.

Run locally:  streamlit run app.py
Deploy:       Streamlit Community Cloud (free) — point at this repo.

User flow:
1. Paste ticker list (one per line, or comma-separated)
2. Pick provider (Claude / OpenAI), paste API key
3. Click Run → progress bar → download populated xlsx
"""
import streamlit as st
import tempfile
import os
from pathlib import Path

from orchestrator import run_batch

st.set_page_config(page_title="Director Pay Research", layout="wide")
st.title("Director Pay Research Tool")
st.caption("Populate the director compensation template from SEC DEF 14A filings.")

with st.sidebar:
    st.header("Settings")
    provider = st.selectbox("LLM Provider", ["claude", "openai"], index=0)
    default_model = "claude-sonnet-4-5" if provider == "claude" else "gpt-4.1"
    model = st.text_input("Model", value=default_model)
    api_key = st.text_input(f"{provider.title()} API Key", type="password")
    st.markdown("---")
    st.markdown("**Filing precedence:**\n"
                "1. DEF 14A within 12 months\n"
                "2. Else 10-K/A\n"
                "3. Else most recent DEF 14A\n"
                "4. 20-F filers skipped")

ticker_text = st.text_area(
    "Tickers (one per line, or comma-separated; up to 40)",
    height=200,
    placeholder="NYSE:ACN\nNASDAQ:ADP\nNYSE:CRM",
)

st.markdown("**Template:** using bundled `template_default.xlsx`. "
            "Upload below to override.")
template_file = st.file_uploader("Override template (.xlsx, optional)", type=["xlsx"])

run_btn = st.button("Run extraction", type="primary",
                    disabled=not (ticker_text.strip() and api_key))

if run_btn:
    raw = ticker_text.replace(",", "\n")
    tickers = [t.strip() for t in raw.splitlines() if t.strip()]
    if len(tickers) > 40:
        st.error(f"Maximum 40 tickers per run; received {len(tickers)}.")
        st.stop()

    with tempfile.TemporaryDirectory() as td:
        tpl_path = Path(td) / "template.xlsx"
        if template_file is not None:
            tpl_path.write_bytes(template_file.getvalue())
        else:
            bundled = Path(__file__).parent / "template_default.xlsx"
            tpl_path.write_bytes(bundled.read_bytes())
        out_path = Path(td) / "director_pay_output.xlsx"

        progress = st.progress(0.0)
        status = st.empty()

        def cb(i, n, label):
            progress.progress(min(i / n, 1.0))
            status.write(f"Processing {i}/{n}: {label}")

        try:
            results = run_batch(tickers, str(tpl_path), str(out_path),
                                provider, api_key, model, progress_cb=cb)
        except Exception as e:
            st.error(f"Run failed: {e}")
            st.stop()

        progress.progress(1.0)
        status.write("Done.")

        ok = [r for r in results if r.status == "ok"]
        skipped = [r for r in results if r.status != "ok"]

        col1, col2 = st.columns(2)
        col1.metric("Successful", len(ok))
        col2.metric("Skipped / Failed", len(skipped))

        if skipped:
            st.subheader("Skipped / Failed")
            st.table([{"Ticker": r.ticker, "Status": r.status, "Detail": r.detail}
                      for r in skipped])

        with open(out_path, "rb") as f:
            st.download_button("Download populated template", f.read(),
                               file_name="director_pay_output.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
