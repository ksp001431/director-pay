"""SEC EDGAR client.

Resolves a ticker to its filings, applies the precedence rules:
  1. Latest DEF 14A within last 12 months
  2. Else, latest 10-K/A (which may carry director comp via Part III incorporation)
  3. Else, most recent DEF 14A regardless of age
  4. If only 20-F filings exist (foreign filer), return SkippedForeignFiler

Requires a User-Agent header per SEC guidelines.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import requests
import json

USER_AGENT = "DirectorPayResearch contact@example.com"  # override in deployment
SEC_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


@dataclass
class FilingRef:
    ticker: str
    cik: str
    company_name: str
    form: str          # "DEF 14A", "10-K/A", etc.
    filing_date: str   # YYYY-MM-DD
    accession: str
    primary_doc: str
    url: str           # full URL to primary document
    fiscal_year_end: Optional[str] = None


class SkippedForeignFiler(Exception):
    pass


class TickerNotFound(Exception):
    pass


class NoEligibleFiling(Exception):
    pass


_ticker_cache: Optional[Dict[str, dict]] = None


def _load_ticker_map() -> Dict[str, dict]:
    global _ticker_cache
    if _ticker_cache is None:
        r = requests.get(TICKER_MAP_URL, headers=SEC_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        # data is { "0": {"cik_str":..., "ticker":"AAPL", "title":"..."}, ... }
        _ticker_cache = {row["ticker"].upper(): row for row in data.values()}
    return _ticker_cache


def resolve_ticker(ticker_input: str) -> dict:
    """Accept 'NYSE:ACN' or 'ACN' and return ticker map row."""
    sym = ticker_input.split(":")[-1].strip().upper()
    tmap = _load_ticker_map()
    if sym not in tmap:
        raise TickerNotFound(f"{ticker_input} not in SEC ticker list")
    return tmap[sym]


def find_director_pay_filing(ticker_input: str) -> FilingRef:
    row = resolve_ticker(ticker_input)
    cik_padded = str(row["cik_str"]).zfill(10)
    company = row["title"]
    sub_url = SUBMISSIONS_URL.format(cik=cik_padded)
    r = requests.get(sub_url, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    sub = r.json()
    recent = sub["filings"]["recent"]
    fye_month_day = sub.get("fiscalYearEnd")  # e.g. "1231"

    # Build a list of {form, date, accession, primary_doc}
    filings: List[dict] = []
    for i in range(len(recent["form"])):
        filings.append({
            "form": recent["form"][i],
            "date": recent["filingDate"][i],
            "accession": recent["accessionNumber"][i],
            "primary_doc": recent["primaryDocument"][i],
        })

    def16 = [f for f in filings if f["form"] == "DEF 14A"]
    tenk_a = [f for f in filings if f["form"] == "10-K/A"]
    twenty_f = [f for f in filings if f["form"] in ("20-F", "20-F/A")]

    # 20-F-only filer → skip
    if not def16 and not tenk_a and twenty_f:
        raise SkippedForeignFiler(f"{ticker_input} files Form 20-F (foreign filer)")

    cutoff = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
    chosen = None
    if def16 and def16[0]["date"] >= cutoff:
        chosen = def16[0]
    elif tenk_a:
        chosen = tenk_a[0]
    elif def16:
        chosen = def16[0]
    else:
        raise NoEligibleFiling(f"{ticker_input}: no DEF 14A or 10-K/A found")

    accession_clean = chosen["accession"].replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik_padded)}/"
           f"{accession_clean}/{chosen['primary_doc']}")
    return FilingRef(
        ticker=ticker_input, cik=cik_padded, company_name=company,
        form=chosen["form"], filing_date=chosen["date"],
        accession=chosen["accession"], primary_doc=chosen["primary_doc"],
        url=url, fiscal_year_end=fye_month_day,
    )


def fetch_filing_text(filing: FilingRef) -> str:
    """Download the filing HTML and return text/HTML content."""
    r = requests.get(filing.url, headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    return r.text
