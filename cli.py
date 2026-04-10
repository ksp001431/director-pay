"""Command-line runner for prototype testing.

Example:
  export ANTHROPIC_API_KEY=sk-...
  python cli.py --tickers NYSE:ACN,NASDAQ:ADP,NYSE:CRM \
    --template DIRECTOR_PAY__Template_.xlsx \
    --out test_output.xlsx
"""
import argparse
import os
import sys
from orchestrator import run_batch


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=True, help="Comma-separated, e.g. NYSE:ACN,NASDAQ:ADP")
    p.add_argument("--template", required=True, help="Path to blank template .xlsx")
    p.add_argument("--out", required=True, help="Output .xlsx path")
    p.add_argument("--provider", default="claude", choices=["claude", "openai"])
    p.add_argument("--model", default=None)
    p.add_argument("--api-key", default=None,
                   help="Defaults to ANTHROPIC_API_KEY or OPENAI_API_KEY env var")
    args = p.parse_args()

    api_key = args.api_key or os.environ.get(
        "ANTHROPIC_API_KEY" if args.provider == "claude" else "OPENAI_API_KEY")
    if not api_key:
        sys.exit("No API key provided.")

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    print(f"Running {len(tickers)} tickers via {args.provider}...")

    def cb(i, n, label):
        print(f"  [{i}/{n}] {label}")

    results = run_batch(tickers, args.template, args.out,
                        args.provider, api_key, args.model, progress_cb=cb)

    print(f"\nOutput: {args.out}")
    print("\nResults:")
    for r in results:
        print(f"  {r.ticker:15} {r.status:18} {r.detail}")


if __name__ == "__main__":
    main()
