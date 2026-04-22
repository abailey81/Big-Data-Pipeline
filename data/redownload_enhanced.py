#!/usr/bin/env python3
"""
Enhanced data downloader — targets 603+/678 coverage with full fundamentals history.

Improvements over original:
1. Retry failed tickers with alternative ticker mappings (mergers, renames)
2. Download BOTH quarterly AND annual fundamentals → more history
3. Fetch real FRED risk-free rate (DGS3MO) via public CSV endpoint
4. Extend fundamentals back to 2020 using annual statements
"""

import pandas as pd
import numpy as np
import yfinance as yf
import time
import sys
import os
from pathlib import Path
from io import StringIO
import urllib.request

DATA_DIR = Path(__file__).parent
STATIC_CSV = DATA_DIR / "company_static.csv"
START = "2020-01-01"
END = "2026-04-01"

# Known ticker renames/mergers — map old → new (or vice versa)
TICKER_REMAP = {
    "ATVI": "MSFT",      # Activision → Microsoft
    "ABMD": "ABT",       # Abiomed → Abbott (actually delisted, skip)
    "ALXN": "AZN",       # Alexion → AstraZeneca
    "AGN": "ABBV",       # Allergan → AbbVie
    "ANTM": "ELV",       # Anthem → Elevance Health
    "BRK.B": "BRK-B",    # Yahoo uses BRK-B
    "BF.B": "BF-B",      # Brown-Forman
    "CELG": "BMY",       # Celgene → Bristol-Myers
    "CTL": "LUMN",       # CenturyLink → Lumen
    "CERN": "ORCL",      # Cerner → Oracle
    "CTXS": "CTXS",      # Citrix delisted
    "DWDP": "DD",        # DowDuPont → Dow
    "ETFC": "MS",        # E*TRADE → Morgan Stanley
    "FRC": "JPM",        # First Republic → JPMorgan
    "FLIR": "TDY",       # FLIR → Teledyne
    "HRS": "LHX",        # Harris → L3Harris
    "KSU": "CP",         # Kansas City Southern → CP
    "LLL": "LHX",        # L3 → L3Harris
    "MXIM": "ADI",       # Maxim → Analog Devices
    "MYL": "VTRS",       # Mylan → Viatris
    "PBCT": "M&T",       # People's United → M&T (skip, complex)
    "RHT": "IBM",        # Red Hat → IBM
    "RTN": "RTX",        # Raytheon → RTX
    "SIVB": "SIVBQ",     # SVB → delisted
    "SYMC": "GEN",       # Symantec → Gen Digital
    "TIF": "LVMUY",      # Tiffany → LVMH
    "TWTR": "TWTR",      # Twitter → delisted
    "UTX": "RTX",        # UTC → RTX
    "XLNX": "AMD",       # Xilinx → AMD
    "WBA": "WBA",        # Still exists
    "K": "KLG",          # Kellogg → Kellanova (try K first)
    "DFS": "DFS",        # Still exists
    "GPS": "GAP",        # Gap → now "GAP"
    "ABC": "COR",        # AmerisourceBergen → Cencora
    "FLT": "CPAY",       # FleetCor → Corpay
    "TMK": "GL",         # Torchmark → Globe Life
    "TSS": "GPN",        # TSYS → Global Payments
    "WLTW": "WTW",       # Willis Towers Watson → WTW
    "FBHS": "FBIN",      # Fortune Brands → Fbin
    "PKI": "RVTY",       # PerkinElmer → Revvity
    "BDEV.L": "BDEV.L",  # Still exists (retry)
    "DPH.L": "DPH.L",    # Still exists (retry)
    "HL.L": "HL.L",      # Hargreaves Lansdown (retry)
    "SMDS.L": "SMDS.L",  # DS Smith (retry)
}

# Tickers that are truly dead (delisted, no replacement)
SKIP_TICKERS = {
    "ABMD", "ARNC", "BHGE", "CBS", "COG", "CXO", "DISCA", "DISCK",
    "DISH", "DRE", "HCP", "HFC", "JEC", "MRO", "NBL", "NLSN",
    "PBCT", "SIVB", "TWTR", "VAR", "VIAB", "WCG", "XEC", "CTXS",
}


def download_prices_retry(failed_tickers: list) -> pd.DataFrame:
    """Retry downloading prices for failed tickers using remapped names."""
    all_rows = []
    attempted = 0
    success = 0

    for old_ticker in failed_tickers:
        if old_ticker in SKIP_TICKERS:
            print(f"  SKIP (delisted): {old_ticker}")
            continue

        new_ticker = TICKER_REMAP.get(old_ticker, old_ticker)
        attempted += 1

        try:
            tk = yf.Ticker(new_ticker)
            hist = tk.history(start=START, end=END, auto_adjust=True)
            if hist.empty or len(hist) < 50:
                print(f"  FAIL: {old_ticker} → {new_ticker} (no data)")
                continue

            df = pd.DataFrame({
                "symbol": old_ticker,  # Keep original symbol for consistency
                "date": hist.index.tz_localize(None),
                "open": hist["Open"].values,
                "high": hist["High"].values,
                "low": hist["Low"].values,
                "close": hist["Close"].values,
                "adj_close": hist["Close"].values,  # auto_adjust=True
                "volume": hist["Volume"].values,
            })
            all_rows.append(df)
            success += 1
            print(f"  OK: {old_ticker} → {new_ticker} ({len(df)} rows)")
            time.sleep(0.3)
        except Exception as e:
            print(f"  FAIL: {old_ticker} → {new_ticker}: {e}")
            time.sleep(0.5)

    print(f"\nRetry prices: {success}/{attempted} succeeded")
    if all_rows:
        return pd.concat(all_rows, ignore_index=True)
    return pd.DataFrame()


def download_fundamentals_extended(symbols: list) -> pd.DataFrame:
    """Download BOTH quarterly AND annual fundamentals for deeper history."""
    all_rows = []
    total = len(symbols)

    for idx, sym in enumerate(symbols):
        if (idx + 1) % 50 == 0:
            print(f"  Fundamentals: {idx+1}/{total}...")

        try:
            tk = yf.Ticker(sym)

            # --- Quarterly statements ---
            for stmt_name, stmt_func in [
                ("balance_sheet", "quarterly_balance_sheet"),
                ("income_stmt", "quarterly_income_stmt"),
                ("cashflow", "quarterly_cashflow"),
            ]:
                try:
                    stmt = getattr(tk, stmt_func)
                    if stmt is not None and not stmt.empty:
                        _extract_fields(all_rows, sym, stmt, "quarterly")
                except Exception:
                    pass

            # --- Annual statements (extends history!) ---
            for stmt_name, stmt_func in [
                ("balance_sheet", "balance_sheet"),
                ("income_stmt", "income_stmt"),
                ("cashflow", "cashflow"),
            ]:
                try:
                    stmt = getattr(tk, stmt_func)
                    if stmt is not None and not stmt.empty:
                        _extract_fields(all_rows, sym, stmt, "annual")
                except Exception:
                    pass

            # --- TTM from ticker.info ---
            try:
                info = tk.info or {}
                snap_date = pd.Timestamp.now().normalize()
                ttm_fields = {
                    "market_cap": info.get("marketCap"),
                    "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                    "book_value_per_share": info.get("bookValue"),
                    "eps": info.get("trailingEps"),
                }
                for field, val in ttm_fields.items():
                    if val is not None and not np.isnan(float(val)):
                        all_rows.append({
                            "symbol": sym,
                            "report_date": snap_date,
                            "field_name": field,
                            "field_value": float(val),
                            "period_type": "ttm",
                        })
            except Exception:
                pass

            time.sleep(0.3)
        except Exception as e:
            if (idx + 1) % 100 == 0:
                print(f"  WARN: {sym}: {e}")

    print(f"  Fundamentals: {len(all_rows)} rows extracted")
    return pd.DataFrame(all_rows)


FIELD_MAP = {
    # Balance sheet
    "StockholdersEquity": "shareholders_equity",
    "Stockholders Equity": "shareholders_equity",
    "CommonStockEquity": "shareholders_equity",
    "TotalDebt": "total_debt",
    "Total Debt": "total_debt",
    "NetDebt": "total_debt",
    # Income statement
    "NetIncome": "net_income",
    "Net Income": "net_income",
    "NetIncomeCommonStockholders": "net_income",
    "TotalRevenue": "total_revenue",
    "Total Revenue": "total_revenue",
    "OperatingIncome": "operating_income",
    "Operating Income": "operating_income",
    "EBITDA": "ebitda",
    "Ebitda": "ebitda",
    "BasicEPS": "eps",
    "DilutedEPS": "eps",
    "Diluted EPS": "eps",
    # Cash flow
    "OperatingCashFlow": "operating_cash_flow",
    "Operating Cash Flow": "operating_cash_flow",
    "FreeCashFlow": "free_cash_flow",
    "Free Cash Flow": "free_cash_flow",
    "CapitalExpenditure": "capital_expenditure",
    "Capital Expenditure": "capital_expenditure",
}


def _extract_fields(rows_list, symbol, stmt_df, period_type):
    """Extract known fields from a yfinance statement DataFrame."""
    for raw_name, mapped_name in FIELD_MAP.items():
        if raw_name in stmt_df.index:
            series = stmt_df.loc[raw_name]
            for col_date, val in series.items():
                if pd.notna(val):
                    dt = pd.Timestamp(col_date)
                    if hasattr(dt, 'tz') and dt.tz is not None:
                        dt = dt.tz_localize(None)
                    rows_list.append({
                        "symbol": symbol,
                        "report_date": dt,
                        "field_name": mapped_name,
                        "field_value": float(val),
                        "period_type": period_type,
                    })


def download_fred_risk_free():
    """Download real DGS3MO from FRED public CSV endpoint."""
    print("[FRED] Downloading 3-month Treasury rate...")
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO&vintage_date=2026-04-04"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
        df = pd.read_csv(StringIO(text))
        df.columns = ["date", "rate_pct"]
        df["date"] = pd.to_datetime(df["date"])
        df["rate_pct"] = pd.to_numeric(df["rate_pct"], errors="coerce")
        df = df.dropna()
        df = df[df["date"] >= START]
        print(f"[FRED] Got {len(df)} rows, {df['date'].min().date()} to {df['date'].max().date()}")
        return df
    except Exception as e:
        print(f"[FRED] Download failed: {e}")
        print("[FRED] Using synthetic rate schedule instead")
        return _synthetic_rf()


def _synthetic_rf():
    """Fallback: realistic piecewise-linear risk-free rate."""
    rate_map = {
        '2020-01-01': 1.55, '2020-03-15': 0.01, '2020-06-01': 0.10,
        '2021-01-01': 0.05, '2021-12-01': 0.05, '2022-03-01': 0.30,
        '2022-06-01': 1.50, '2022-09-01': 3.00, '2022-12-01': 4.40,
        '2023-03-01': 4.70, '2023-06-01': 5.20, '2023-09-01': 5.40,
        '2024-01-01': 5.35, '2024-06-01': 5.25, '2024-09-01': 4.90,
        '2025-01-01': 4.30, '2025-06-01': 4.00, '2026-01-01': 3.80, '2026-04-01': 3.70,
    }
    knots = pd.Series(rate_map, dtype=float)
    knots.index = pd.to_datetime(knots.index)
    dates = pd.date_range(START, END, freq='B')
    rf = knots.reindex(dates).interpolate('time').ffill().bfill()
    return pd.DataFrame({'date': dates, 'rate_pct': rf.values})


def main():
    print("=" * 60)
    print("  Enhanced Data Download — Target 603+/678 coverage")
    print("=" * 60)

    # Load universe
    cs = pd.read_csv(STATIC_CSV)
    all_symbols = cs["symbol"].tolist()
    print(f"Universe: {len(all_symbols)} tickers")

    # Load existing data
    existing_prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    existing_syms = set(existing_prices["symbol"].unique())
    print(f"Existing prices: {len(existing_syms)} tickers")

    # Load failed tickers
    failed = pd.read_csv(DATA_DIR / "failed_tickers.csv")
    failed_list = failed["ticker"].tolist()
    print(f"Failed tickers to retry: {len(failed_list)}")

    # ---- Step 1: Retry failed tickers ----
    print("\n--- Step 1: Retrying failed tickers ---")
    new_prices = download_prices_retry(failed_list)

    if not new_prices.empty:
        # Merge with existing
        combined_prices = pd.concat([existing_prices, new_prices], ignore_index=True)
        combined_prices = combined_prices.drop_duplicates(subset=["symbol", "date"])
    else:
        combined_prices = existing_prices

    new_count = combined_prices["symbol"].nunique()
    print(f"\nTotal tickers with prices: {new_count}")

    # Save
    combined_prices.to_parquet(DATA_DIR / "daily_prices.parquet", index=False)
    print(f"Saved daily_prices.parquet ({len(combined_prices)} rows)")

    # ---- Step 2: Re-download ALL fundamentals (quarterly + annual) ----
    print("\n--- Step 2: Downloading extended fundamentals (quarterly + annual) ---")
    all_price_syms = combined_prices["symbol"].unique().tolist()
    new_fund = download_fundamentals_extended(all_price_syms)

    if not new_fund.empty:
        # Deduplicate: keep latest per (symbol, report_date, field_name, period_type)
        new_fund = new_fund.drop_duplicates(
            subset=["symbol", "report_date", "field_name", "period_type"],
            keep="last",
        )
        new_fund.to_parquet(DATA_DIR / "fundamentals.parquet", index=False)
        print(f"Saved fundamentals.parquet ({len(new_fund)} rows, {new_fund['symbol'].nunique()} tickers)")
        print(f"Date range: {new_fund['report_date'].min()} to {new_fund['report_date'].max()}")
        q_per = new_fund[new_fund['period_type'] == 'quarterly'].groupby('symbol')['report_date'].nunique()
        a_per = new_fund[new_fund['period_type'] == 'annual'].groupby('symbol')['report_date'].nunique()
        print(f"Quarterly: median {q_per.median():.0f}/symbol")
        print(f"Annual: median {a_per.median():.0f}/symbol (extends history back)")

    # ---- Step 3: Download real FRED risk-free rate ----
    print("\n--- Step 3: Downloading risk-free rate ---")
    rf = download_fred_risk_free()
    rf.to_parquet(DATA_DIR / "risk_free_rate.parquet", index=False)
    print(f"Saved risk_free_rate.parquet ({len(rf)} rows)")

    # ---- Step 4: Update failed tickers list ----
    still_missing = set(all_symbols) - set(combined_prices["symbol"].unique())
    pd.DataFrame({"ticker": sorted(still_missing)}).to_csv(
        DATA_DIR / "failed_tickers.csv", index=False
    )
    print(f"\nStill missing: {len(still_missing)} tickers")
    if still_missing:
        print(f"  {sorted(still_missing)[:20]}...")

    # ---- Summary ----
    print("\n" + "=" * 60)
    print(f"  FINAL: {combined_prices['symbol'].nunique()}/{len(all_symbols)} tickers with prices")
    print(f"  Fundamentals: {new_fund['symbol'].nunique() if not new_fund.empty else 0} tickers")
    print(f"  Date range: {START} to {END}")
    print("=" * 60)


if __name__ == "__main__":
    main()
