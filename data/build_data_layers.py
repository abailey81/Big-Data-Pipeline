#!/usr/bin/env python3
"""
build_data_layers.py — Build all "must fix" data layers for cw2_strategy.

Produces:
  1. ticker_mapping.parquet
  2. universe_monthly_snapshot.parquet
  3. fundamentals_eav_pit.parquet  (clean EAV, ratios separated)
  4. company_ratios.parquet        (market_cap / pe_ratio / bvps PIT)
  5. daily_prices_clean.parquet    (currency filled)
  6. symbol_to_currency.parquet    (validation table)
  7. prices_month_end.parquet      (month-end price matrix)
  8. price_asof_report_date.parquet
  9. coverage_by_rebalance_date.csv

Run:  python build_data_layers.py
"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent
OUT_DIR = DATA_DIR  # write alongside existing parquets

# ── Known ticker events (from redownload_enhanced.py) ──────────────────────

TICKER_REMAP = {
    "ATVI": ("MSFT", "merger"),
    "ABMD": ("ABT", "delisted"),
    "ALXN": ("AZN", "merger"),
    "AGN": ("ABBV", "merger"),
    "ANTM": ("ELV", "rename"),
    "BRK.B": ("BRK-B", "rename"),
    "BF.B": ("BF-B", "rename"),
    "CELG": ("BMY", "merger"),
    "CTL": ("LUMN", "rename"),
    "CERN": ("ORCL", "merger"),
    "CTXS": (None, "delisted"),
    "DWDP": ("DD", "spinoff"),
    "ETFC": ("MS", "merger"),
    "FRC": ("JPM", "merger"),
    "FLIR": ("TDY", "merger"),
    "HRS": ("LHX", "merger"),
    "KSU": ("CP", "merger"),
    "LLL": ("LHX", "merger"),
    "MXIM": ("ADI", "merger"),
    "MYL": ("VTRS", "rename"),
    "PBCT": (None, "delisted"),
    "RHT": ("IBM", "merger"),
    "RTN": ("RTX", "merger"),
    "SIVB": (None, "delisted"),
    "SYMC": ("GEN", "rename"),
    "TIF": ("LVMUY", "merger"),
    "TWTR": (None, "delisted"),
    "UTX": ("RTX", "merger"),
    "XLNX": ("AMD", "merger"),
    "K": ("KLG", "rename"),
    "GPS": ("GAP", "rename"),
    "ABC": ("COR", "rename"),
    "FLT": ("CPAY", "rename"),
    "TMK": ("GL", "rename"),
    "TSS": ("GPN", "merger"),
    "WLTW": ("WTW", "rename"),
    "FBHS": ("FBIN", "rename"),
    "PKI": ("RVTY", "rename"),
}

SKIP_TICKERS = {
    "ARNC", "BHGE", "CBS", "COG", "CXO", "DISCA", "DISCK",
    "DISH", "DRE", "HCP", "HFC", "JEC", "MRO", "NBL", "NLSN",
    "VAR", "VIAB", "WCG", "XEC",
}

# Suffix → currency
_SUFFIX_TO_CCY = {
    ".L": "GBP", ".PA": "EUR", ".AS": "EUR", ".DE": "EUR",
    ".TO": "CAD", ".SW": "CHF", ".S": "CHF",
}

_CCY_TO_PAIR = {
    "GBP": "GBPUSD=X", "EUR": "EURUSD=X",
    "CAD": "CADUSD=X", "CHF": "CHFUSD=X",
}

# Fields that belong in company_ratios, not core fundamentals
RATIO_FIELDS = {"market_cap", "pe_ratio", "book_value_per_share", "eps"}
# Core fundamental fields (income/balance/cashflow)
FUNDAMENTAL_FIELDS = {
    "net_income", "shareholders_equity", "total_debt",
    "total_revenue", "operating_income", "ebitda",
    "operating_cash_flow", "free_cash_flow", "capital_expenditure",
}


def infer_currency(symbol: str) -> str:
    for suffix, ccy in _SUFFIX_TO_CCY.items():
        if symbol.endswith(suffix):
            return ccy
    return "USD"


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: ticker_mapping
# ═══════════════════════════════════════════════════════════════════════════
def build_ticker_mapping() -> pd.DataFrame:
    """Build ticker_mapping table: old_ticker → current_ticker / status / event_type."""
    print("\n" + "=" * 60)
    print("STEP 1: Building ticker_mapping")

    cs = pd.read_parquet(DATA_DIR / "company_static.parquet")
    prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    fund = pd.read_parquet(DATA_DIR / "fundamentals.parquet")

    all_static = set(cs["symbol"])
    has_price = set(prices["symbol"].unique())
    has_fund = set(fund["symbol"].unique())

    rows = []

    # 1) Known remaps
    for old, (new, event) in TICKER_REMAP.items():
        rows.append({
            "old_ticker": old,
            "current_ticker": new,
            "event_type": event,
            "status": "dead" if new is None else "remapped",
            "in_static": old in all_static,
            "has_price": old in has_price,
            "has_fundamentals": old in has_fund,
        })

    # 2) Known dead tickers not already in REMAP
    for tk in SKIP_TICKERS:
        if tk not in TICKER_REMAP:
            rows.append({
                "old_ticker": tk,
                "current_ticker": None,
                "event_type": "delisted",
                "status": "dead",
                "in_static": tk in all_static,
                "has_price": tk in has_price,
                "has_fundamentals": tk in has_fund,
            })

    # 3) Static tickers with no price data and not already mapped
    mapped_old = {r["old_ticker"] for r in rows}
    no_price = all_static - has_price - mapped_old
    for tk in sorted(no_price):
        rows.append({
            "old_ticker": tk,
            "current_ticker": None,
            "event_type": "unknown",
            "status": "no_price_data",
            "in_static": True,
            "has_price": False,
            "has_fundamentals": tk in has_fund,
        })

    # 4) Tickers that have price but no fundamentals (and not mapped)
    price_no_fund = has_price - has_fund - mapped_old
    for tk in sorted(price_no_fund):
        rows.append({
            "old_ticker": tk,
            "current_ticker": tk,
            "event_type": "active",
            "status": "missing_fundamentals",
            "in_static": tk in all_static,
            "has_price": True,
            "has_fundamentals": False,
        })

    # 5) Active tickers (have both price + fundamentals)
    active = has_price & has_fund - mapped_old
    for tk in sorted(active):
        rows.append({
            "old_ticker": tk,
            "current_ticker": tk,
            "event_type": "active",
            "status": "active",
            "in_static": tk in all_static,
            "has_price": True,
            "has_fundamentals": True,
        })

    df = pd.DataFrame(rows)
    df.to_parquet(OUT_DIR / "ticker_mapping.parquet", index=False)

    print(f"  Total entries: {len(df)}")
    print(f"  Status breakdown:\n{df['status'].value_counts().to_string()}")
    print(f"  Saved → ticker_mapping.parquet")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: universe_monthly_snapshot
# ═══════════════════════════════════════════════════════════════════════════
def build_universe_monthly_snapshot(ticker_map: pd.DataFrame) -> pd.DataFrame:
    """For each month-end rebalance date, freeze which symbols are tradeable + scoreable."""
    print("\n" + "=" * 60)
    print("STEP 2: Building universe_monthly_snapshot")

    prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    prices["date"] = pd.to_datetime(prices["date"])
    fund = pd.read_parquet(DATA_DIR / "fundamentals.parquet")
    fund["report_date"] = pd.to_datetime(fund["report_date"])
    cs = pd.read_parquet(DATA_DIR / "company_static.parquet")

    has_sector = set(cs.loc[cs["gics_sector"].notna(), "symbol"])

    # Generate month-end rebalance dates from price data
    all_dates = prices["date"].sort_values().unique()
    month_ends = pd.to_datetime(
        pd.Series(all_dates).dt.to_period("M").drop_duplicates().dt.to_timestamp("M")
    )
    # Snap to last actual trading day of each month
    price_dates = pd.DatetimeIndex(all_dates)
    rebalance_dates = []
    for me in month_ends:
        candidates = price_dates[price_dates <= me]
        if len(candidates) > 0:
            rebalance_dates.append(candidates[-1])
    rebalance_dates = pd.DatetimeIndex(sorted(set(rebalance_dates)))

    # Precompute: for each symbol, date range of price data
    sym_price_range = prices.groupby("symbol")["date"].agg(["min", "max"])

    # Precompute: for each symbol, set of report_dates for fundamentals
    fund_by_sym = fund.groupby("symbol")["report_date"].max()

    # Core fundamental fields needed for value/quality scoring
    value_fields = {"book_value_per_share", "eps", "net_income",
                    "shareholders_equity", "operating_cash_flow", "market_cap"}
    quality_fields = {"net_income", "shareholders_equity", "total_debt"}

    rows = []
    for rd in rebalance_dates:
        # Which symbols have price on or near this date?
        # "near" = had a price within last 5 trading days
        lookback = rd - pd.Timedelta(days=7)
        symbols_with_price = set(
            prices.loc[(prices["date"] >= lookback) & (prices["date"] <= rd), "symbol"].unique()
        )

        # Which symbols have fundamentals available as_of this date?
        fund_available = fund[fund["report_date"] <= rd]
        symbols_with_fund = set(fund_available["symbol"].unique())

        # For each symbol, check field coverage
        fund_snapshot = fund_available.groupby("symbol")["field_name"].apply(set)

        for sym in sorted(symbols_with_price):
            has_p = True
            has_f = sym in symbols_with_fund
            has_s = sym in has_sector

            # Check specific factor readiness
            sym_fields = fund_snapshot.get(sym, set()) if has_f else set()
            has_value = bool(sym_fields & {"book_value_per_share", "eps"})
            has_quality = bool(sym_fields & quality_fields)
            scoreable = has_p and has_value and has_quality and has_s

            rows.append({
                "rebalance_date": rd,
                "symbol": sym,
                "has_price": has_p,
                "has_fundamentals": has_f,
                "has_sector": has_s,
                "has_value_fields": has_value,
                "has_quality_fields": has_quality,
                "is_scoreable": scoreable,
            })

    df = pd.DataFrame(rows)
    df.to_parquet(OUT_DIR / "universe_monthly_snapshot.parquet", index=False)

    # Summary
    summary = df.groupby("rebalance_date").agg(
        total_with_price=("has_price", "sum"),
        with_fundamentals=("has_fundamentals", "sum"),
        with_value=("has_value_fields", "sum"),
        with_quality=("has_quality_fields", "sum"),
        scoreable=("is_scoreable", "sum"),
    )
    print(f"  Rebalance dates: {len(rebalance_dates)}")
    print(f"  Total rows: {len(df)}")
    print(f"\n  Coverage summary (first 6 months):")
    print(summary.head(6).to_string())
    print(f"\n  Coverage summary (last 6 months):")
    print(summary.tail(6).to_string())
    print(f"  Saved → universe_monthly_snapshot.parquet")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: fundamentals_eav_pit + company_ratios
# ═══════════════════════════════════════════════════════════════════════════
def build_fundamentals_pit_and_ratios():
    """
    Split current fundamentals.parquet into:
      - fundamentals_eav_pit.parquet  (core accounting fields only)
      - company_ratios.parquet        (market_cap, pe_ratio, bvps, eps)
    Add inferred period_end where possible.
    """
    print("\n" + "=" * 60)
    print("STEP 3: Building fundamentals_eav_pit + company_ratios")

    fund = pd.read_parquet(DATA_DIR / "fundamentals.parquet")
    fund["report_date"] = pd.to_datetime(fund["report_date"])

    print(f"  Original rows: {len(fund)}")
    print(f"  Unique field_names: {sorted(fund['field_name'].unique())}")
    print(f"  Report date range: {fund['report_date'].min()} → {fund['report_date'].max()}")

    # ── Separate ratio fields from core fundamentals ──
    is_ratio = fund["field_name"].isin(RATIO_FIELDS)

    ratios_df = fund[is_ratio].copy()
    core_df = fund[~is_ratio].copy()

    # ── Infer period_end for core fundamentals ──
    # Quarterly: period_end ≈ report_date (most quarterly reports cover the quarter ending on report_date)
    # Annual: period_end ≈ fiscal year end (often ~report_date for annual)
    # This is an approximation; real PIT would need the actual fiscal period info
    core_df["period_end"] = core_df["report_date"]  # best we can do without fiscal calendar

    # ── Flag future-dated records ──
    today = pd.Timestamp("2026-04-01")  # last price date
    core_df["is_future"] = core_df["report_date"] > today

    future_count = core_df["is_future"].sum()
    if future_count > 0:
        print(f"  WARNING: {future_count} records with report_date > {today}")

    # ── Save fundamentals_eav_pit ──
    pit_cols = ["symbol", "report_date", "period_end", "field_name",
                "field_value", "period_type", "is_future"]
    core_df[pit_cols].to_parquet(OUT_DIR / "fundamentals_eav_pit.parquet", index=False)
    print(f"  fundamentals_eav_pit: {len(core_df)} rows, "
          f"{core_df['symbol'].nunique()} symbols, "
          f"{sorted(core_df['field_name'].unique())}")

    # ── Save company_ratios ──
    # Reshape to cw1 schema: symbol / snapshot_date / field_name / field_value
    ratios_out = ratios_df.rename(columns={"report_date": "snapshot_date"})
    ratios_out = ratios_out[["symbol", "snapshot_date", "field_name",
                              "field_value", "period_type"]]
    ratios_out.to_parquet(OUT_DIR / "company_ratios.parquet", index=False)
    print(f"  company_ratios: {len(ratios_out)} rows, "
          f"{ratios_out['symbol'].nunique()} symbols, "
          f"fields={sorted(ratios_out['field_name'].unique())}")

    print(f"  Saved → fundamentals_eav_pit.parquet, company_ratios.parquet")
    return core_df, ratios_out


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: Fix currency in daily_prices
# ═══════════════════════════════════════════════════════════════════════════
def fix_currency_and_fx():
    """Fill NaN currency in daily_prices, build symbol_to_currency validation."""
    print("\n" + "=" * 60)
    print("STEP 4: Fixing currency / FX consistency")

    prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    fx = pd.read_parquet(DATA_DIR / "fx_rates.parquet")
    fx["date"] = pd.to_datetime(fx["date"])

    # Count NaN currency
    null_ccy = prices["currency"].isna().sum()
    print(f"  NaN currency rows before: {null_ccy}")

    # Fill currency by inference from symbol suffix
    prices["currency_filled"] = prices.apply(
        lambda r: r["currency"] if pd.notna(r["currency"]) else infer_currency(r["symbol"]),
        axis=1,
    )

    # Build symbol_to_currency validation table
    sym_ccy = (
        prices.groupby("symbol")["currency_filled"]
        .agg(lambda x: x.mode()[0] if len(x.mode()) > 0 else "USD")
        .reset_index()
        .rename(columns={"currency_filled": "currency"})
    )
    sym_ccy["fx_pair"] = sym_ccy["currency"].map(_CCY_TO_PAIR)
    sym_ccy["needs_fx"] = sym_ccy["currency"] != "USD"

    # Check FX coverage for non-USD symbols
    fx_pairs_available = set(fx["pair"].unique())
    sym_ccy["fx_pair_available"] = sym_ccy["fx_pair"].apply(
        lambda p: p in fx_pairs_available if pd.notna(p) else True
    )
    non_usd = sym_ccy[sym_ccy["needs_fx"]]
    missing_fx = non_usd[~non_usd["fx_pair_available"]]
    if len(missing_fx) > 0:
        print(f"  WARNING: {len(missing_fx)} non-USD symbols missing FX pair:")
        print(f"    {missing_fx[['symbol', 'currency', 'fx_pair']].to_string()}")
    else:
        print(f"  All {len(non_usd)} non-USD symbols have FX coverage ✓")

    # Check FX date coverage vs price date coverage
    for pair in sorted(fx_pairs_available):
        fx_dates = fx.loc[fx["pair"] == pair, "date"]
        print(f"  FX {pair}: {fx_dates.min().date()} → {fx_dates.max().date()}, "
              f"{len(fx_dates)} days")

    # Save
    sym_ccy.to_parquet(OUT_DIR / "symbol_to_currency.parquet", index=False)

    # Update daily_prices with filled currency
    prices["currency"] = prices["currency_filled"]
    prices.drop(columns=["currency_filled"], inplace=True)
    prices.to_parquet(OUT_DIR / "daily_prices_clean.parquet", index=False)

    null_after = prices["currency"].isna().sum()
    print(f"  NaN currency rows after: {null_after}")
    print(f"  Symbol-currency pairs: {len(sym_ccy)} "
          f"(USD={len(sym_ccy[sym_ccy['currency']=='USD'])}, "
          f"non-USD={len(non_usd)})")
    print(f"  Saved → symbol_to_currency.parquet, daily_prices_clean.parquet")
    return sym_ccy


# ═══════════════════════════════════════════════════════════════════════════
# STEP 5: Price alignment layers
# ═══════════════════════════════════════════════════════════════════════════
def build_price_alignment():
    """Build prices_month_end and price_asof_report_date."""
    print("\n" + "=" * 60)
    print("STEP 5: Building price alignment layers")

    prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
    prices["date"] = pd.to_datetime(prices["date"])
    fund = pd.read_parquet(DATA_DIR / "fundamentals.parquet")
    fund["report_date"] = pd.to_datetime(fund["report_date"])

    # ── prices_month_end ──
    # For each symbol, get the last trading day price in each month
    prices["year_month"] = prices["date"].dt.to_period("M")
    idx = prices.groupby(["symbol", "year_month"])["date"].idxmax()
    month_end = prices.loc[idx, ["date", "symbol", "adj_close", "close", "volume"]].copy()
    month_end.rename(columns={"date": "month_end_date"}, inplace=True)
    month_end.drop(columns=["volume"], errors="ignore", inplace=True)
    month_end.sort_values(["symbol", "month_end_date"], inplace=True)
    month_end.reset_index(drop=True, inplace=True)
    month_end.to_parquet(OUT_DIR / "prices_month_end.parquet", index=False)
    print(f"  prices_month_end: {len(month_end)} rows, "
          f"{month_end['symbol'].nunique()} symbols")

    # ── price_asof_report_date ──
    # For each (symbol, report_date) in fundamentals, find the closest available price
    # on or before that report_date
    report_dates = fund[["symbol", "report_date"]].drop_duplicates()
    prices_sorted = prices.sort_values(["symbol", "date"])

    results = []
    for sym, grp in report_dates.groupby("symbol"):
        sym_prices = prices_sorted[prices_sorted["symbol"] == sym][["date", "adj_close", "close"]]
        if sym_prices.empty:
            continue
        sym_prices = sym_prices.set_index("date").sort_index()

        for _, row in grp.iterrows():
            rd = row["report_date"]
            # Find last price on or before report_date
            valid = sym_prices.loc[:rd]
            if valid.empty:
                continue
            last = valid.iloc[-1]
            results.append({
                "symbol": sym,
                "report_date": rd,
                "price_date": valid.index[-1],
                "adj_close": last["adj_close"],
                "close": last["close"],
                "days_lag": (rd - valid.index[-1]).days,
            })

    asof_df = pd.DataFrame(results)
    asof_df.to_parquet(OUT_DIR / "price_asof_report_date.parquet", index=False)
    print(f"  price_asof_report_date: {len(asof_df)} rows")
    if len(asof_df) > 0:
        print(f"  Days lag stats: mean={asof_df['days_lag'].mean():.1f}, "
              f"median={asof_df['days_lag'].median():.0f}, "
              f"max={asof_df['days_lag'].max()}")
    print(f"  Saved → prices_month_end.parquet, price_asof_report_date.parquet")
    return month_end, asof_df


# ═══════════════════════════════════════════════════════════════════════════
# STEP 6: Coverage report
# ═══════════════════════════════════════════════════════════════════════════
def build_coverage_report():
    """Per rebalance date: coverage counts for each factor layer."""
    print("\n" + "=" * 60)
    print("STEP 6: Building coverage_by_rebalance_date")

    universe = pd.read_parquet(OUT_DIR / "universe_monthly_snapshot.parquet")

    summary = universe.groupby("rebalance_date").agg(
        total_with_price=("has_price", "sum"),
        with_fundamentals=("has_fundamentals", "sum"),
        with_sector=("has_sector", "sum"),
        with_value=("has_value_fields", "sum"),
        with_quality=("has_quality_fields", "sum"),
        scoreable=("is_scoreable", "sum"),
    ).reset_index()

    # Add coverage ratios
    summary["value_coverage_pct"] = (
        summary["with_value"] / summary["total_with_price"] * 100
    ).round(1)
    summary["quality_coverage_pct"] = (
        summary["with_quality"] / summary["total_with_price"] * 100
    ).round(1)
    summary["scoreable_pct"] = (
        summary["scoreable"] / summary["total_with_price"] * 100
    ).round(1)

    summary.to_csv(OUT_DIR / "coverage_by_rebalance_date.csv", index=False)
    print(f"\n  Full coverage report:")
    print(summary.to_string(index=False))
    print(f"\n  Saved → coverage_by_rebalance_date.csv")

    # Identify when coverage becomes usable (>80%)
    usable = summary[summary["scoreable_pct"] >= 80]
    if len(usable) > 0:
        first_usable = usable.iloc[0]["rebalance_date"]
        print(f"\n  RECOMMENDATION: First date with ≥80% scoreable coverage: {first_usable}")
        print(f"  Consider setting backtest start to this date for plan_pdf strategy.")
    else:
        print(f"\n  WARNING: No rebalance date reaches 80% scoreable coverage!")

    return summary


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Building all data layers for cw2_strategy")
    print("=" * 60)

    # Step 1
    ticker_map = build_ticker_mapping()

    # Step 2
    universe = build_universe_monthly_snapshot(ticker_map)

    # Step 3 + Step 5 (fundamentals split produces both)
    fund_pit, ratios = build_fundamentals_pit_and_ratios()

    # Step 4
    sym_ccy = fix_currency_and_fx()

    # Step 6
    month_end, asof = build_price_alignment()

    # Step 7
    coverage = build_coverage_report()

    print("\n" + "=" * 60)
    print("ALL DONE — new files in data/:")
    print("=" * 60)
    new_files = [
        "ticker_mapping.parquet",
        "universe_monthly_snapshot.parquet",
        "fundamentals_eav_pit.parquet",
        "company_ratios.parquet",
        "daily_prices_clean.parquet",
        "symbol_to_currency.parquet",
        "prices_month_end.parquet",
        "price_asof_report_date.parquet",
        "coverage_by_rebalance_date.csv",
    ]
    for f in new_files:
        p = OUT_DIR / f
        if p.exists():
            size_kb = p.stat().st_size / 1024
            print(f"  ✓ {f:45s} {size_kb:8.1f} KB")
        else:
            print(f"  ✗ {f:45s} MISSING")
