#!/usr/bin/env python3
"""
download_edgar.py — Standalone EDGAR XBRL fundamentals downloader.

Stripped from cw1's edgar_downloader.py, no PG/Mongo/Kafka dependencies.
Downloads 5+ years of quarterly (10-Q) and annual (10-K) fundamentals
for all US-listed tickers in the universe.

SEC EDGAR: free, no API key, only requires User-Agent header.
Rate limit: 10 requests/second (we use 0.15s delay to be safe).

Usage:
    python3 download_edgar.py                  # all US tickers
    python3 download_edgar.py --tickers AAPL MSFT
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

DATA_DIR = Path(__file__).parent

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT = "KolmogorovTeam research@kolmogorov.dev"

# XBRL US-GAAP concept → canonical field name (from cw1)
XBRL_FIELD_MAP = {
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "LiabilitiesCurrent": "total_liabilities",
    "StockholdersEquity": "shareholders_equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "shareholders_equity",
    "LongTermDebt": "total_debt",
    "LongTermDebtNoncurrent": "total_debt",
    "DebtCurrent": "total_debt",
    "ShortTermBorrowings": "total_debt",
    "LongTermDebtAndCapitalLeaseObligations": "total_debt",
    "NetIncomeLoss": "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "net_income",
    "Revenues": "total_revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "total_revenue",
    "SalesRevenueNet": "total_revenue",
    "EarningsPerShareBasic": "eps",
    "EarningsPerShareDiluted": "diluted_eps",
    "OperatingIncomeLoss": "operating_income",
    "GrossProfit": "gross_profit",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": "operating_cash_flow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
}

XBRL_DEPRECIATION_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "Depreciation",
]


def is_us_ticker(symbol: str) -> bool:
    return "." not in symbol.strip()


def load_sec_ticker_map() -> dict:
    """Load SEC ticker → CIK mapping."""
    print("[EDGAR] Loading SEC ticker→CIK map...")
    req = urllib.request.Request(SEC_COMPANY_TICKERS_URL, headers={"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    ticker_to_cik = {}
    for entry in data.values():
        ticker = entry.get("ticker", "").upper()
        cik = entry.get("cik_str")
        if ticker and cik:
            ticker_to_cik[ticker] = int(cik)
    print(f"[EDGAR] Loaded {len(ticker_to_cik)} SEC ticker→CIK mappings")
    return ticker_to_cik


def download_company_facts(ticker: str, cik: int, max_retries: int = 3) -> dict:
    """Download company facts JSON from EDGAR."""
    url = SEC_COMPANY_FACTS_URL.format(cik=str(cik).zfill(10))
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # Not in EDGAR
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
    return None


def extract_fundamentals(company_facts: dict, symbol: str, start_date: str = "2019-01-01") -> list:
    """Extract fundamental records from EDGAR company facts JSON."""
    if not company_facts:
        return []

    facts = company_facts.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    if not us_gaap:
        return []

    seen = set()
    records = []
    cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()

    form_map = {"10-Q": "quarterly", "10-K": "annual"}

    for xbrl_concept, canonical_name in XBRL_FIELD_MAP.items():
        concept_data = us_gaap.get(xbrl_concept)
        if not concept_data:
            continue

        units = concept_data.get("units", {})
        unit_key = None
        for k in ["USD", "USD/shares"]:
            if k in units:
                unit_key = k
                break
        if unit_key is None:
            continue

        for entry in units[unit_key]:
            form = entry.get("form", "")
            end_date = entry.get("end")
            period_type = form_map.get(form)
            if period_type is None or not end_date:
                continue

            # Filter by fiscal period
            fp = entry.get("fp", "")
            if form == "10-Q" and fp not in ("Q1", "Q2", "Q3", "Q4"):
                continue
            if form == "10-K" and fp not in ("FY",):
                continue

            report_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            if report_date < cutoff:
                continue

            key = (canonical_name, report_date, period_type)
            if key in seen:
                continue
            seen.add(key)

            val = entry.get("val")
            if val is not None:
                records.append({
                    "symbol": symbol,
                    "report_date": pd.Timestamp(report_date),
                    "field_name": canonical_name,
                    "field_value": float(val),
                    "period_type": period_type,
                })

    # Compute EBITDA = operating_income + depreciation
    record_lookup = {(r["field_name"], r["report_date"], r["period_type"]): r["field_value"] for r in records}

    depreciation_vals = {}
    for dep_concept in XBRL_DEPRECIATION_CONCEPTS:
        dep_data = us_gaap.get(dep_concept)
        if not dep_data:
            continue
        for entry in dep_data.get("units", {}).get("USD", []):
            form = entry.get("form", "")
            pt = form_map.get(form)
            if pt is None:
                continue
            end = entry.get("end")
            if not end:
                continue
            rd = datetime.strptime(end, "%Y-%m-%d").date()
            if rd < cutoff:
                continue
            dep_key = (rd, pt)
            if dep_key not in depreciation_vals:
                depreciation_vals[dep_key] = float(entry.get("val", 0))

    for (rd, pt), dep_val in depreciation_vals.items():
        oi = record_lookup.get(("operating_income", rd, pt))
        ebitda_key = ("ebitda", rd, pt)
        if oi is not None and ebitda_key not in seen:
            seen.add(ebitda_key)
            records.append({
                "symbol": symbol,
                "report_date": pd.Timestamp(rd),
                "field_name": "ebitda",
                "field_value": oi + abs(dep_val),
                "period_type": pt,
            })

    # Compute free_cash_flow = operating_cash_flow - |capital_expenditure|
    for r in list(records):
        if r["field_name"] == "operating_cash_flow":
            rd, pt = r["report_date"], r["period_type"]
            capex = record_lookup.get(("capital_expenditure", rd, pt))
            fcf_key = ("free_cash_flow", rd, pt)
            if capex is not None and fcf_key not in seen:
                seen.add(fcf_key)
                records.append({
                    "symbol": symbol,
                    "report_date": rd,
                    "field_name": "free_cash_flow",
                    "field_value": r["field_value"] - abs(capex),
                    "period_type": pt,
                })

    return records


def main():
    parser = argparse.ArgumentParser(description="Download EDGAR XBRL fundamentals")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between requests (default 0.15s)")
    args = parser.parse_args()

    # Load universe
    prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    all_symbols = sorted(prices["symbol"].unique())
    us_symbols = [s for s in all_symbols if is_us_ticker(s)]

    if args.tickers:
        us_symbols = [s for s in args.tickers if is_us_ticker(s)]

    print(f"[EDGAR] {len(us_symbols)} US tickers to process")

    # Load SEC CIK map
    ticker_to_cik = load_sec_ticker_map()

    # Check existing EDGAR data to skip already-done tickers
    out_path = DATA_DIR / "edgar_fundamentals.parquet"
    existing_symbols = set()
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing_symbols = set(existing["symbol"].unique())
        print(f"[EDGAR] {len(existing_symbols)} tickers already downloaded, skipping")

    remaining = [s for s in us_symbols if s not in existing_symbols]
    print(f"[EDGAR] {len(remaining)} tickers remaining")

    all_records = []
    success = 0
    fail = 0
    not_found = 0

    for i, sym in enumerate(remaining):
        cik = ticker_to_cik.get(sym.upper())
        if cik is None:
            not_found += 1
            continue

        facts = download_company_facts(sym, cik)
        if facts:
            records = extract_fundamentals(facts, sym, args.start_date)
            if records:
                all_records.extend(records)
                success += 1
            else:
                fail += 1
        else:
            fail += 1

        time.sleep(args.delay)

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(remaining)}] success={success}, fail={fail}, not_found={not_found}, "
                  f"records={len(all_records)}", flush=True)

        # Incremental save every 100 tickers
        if (i + 1) % 100 == 0 and all_records:
            _save_incremental(all_records, out_path)
            all_records = []

    # Final save
    if all_records:
        _save_incremental(all_records, out_path)

    # Print summary
    if out_path.exists():
        final = pd.read_parquet(out_path)
        q = final[final["period_type"] == "quarterly"]
        a = final[final["period_type"] == "annual"]
        print(f"\n{'='*60}")
        print(f"[EDGAR] DONE")
        print(f"  Total records: {len(final)}")
        print(f"  Tickers: {final['symbol'].nunique()}")
        print(f"  Quarterly: {len(q)} rows, {q['symbol'].nunique()} tickers")
        print(f"  Annual: {len(a)} rows, {a['symbol'].nunique()} tickers")
        print(f"  Fields: {sorted(final['field_name'].unique())}")
        print(f"  Date range: {final['report_date'].min()} to {final['report_date'].max()}")
        q_depth = q.groupby("symbol")["report_date"].nunique()
        if len(q_depth) > 0:
            print(f"  Quarterly depth: min={q_depth.min()}, median={q_depth.median():.0f}, max={q_depth.max()}")
    else:
        print("[EDGAR] No data saved")

    print(f"\n  Success: {success}, Failed: {fail}, Not in SEC: {not_found}")


def _save_incremental(records, out_path):
    df = pd.DataFrame(records)
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined.drop_duplicates(
            subset=["symbol", "report_date", "field_name", "period_type"],
            keep="last",
            inplace=True,
        )
        combined.to_parquet(out_path, index=False)
    else:
        df.to_parquet(out_path, index=False)


if __name__ == "__main__":
    main()
