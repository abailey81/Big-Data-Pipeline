"""
factors/quality_gate.py -- Asymmetric quality gate (20% weight as gate, not score).

ASYMMETRIC quality gate:
  Long eligible (not too strict -- just exclude junk):
    - ROE > sector 40th percentile
    - Gross margin not declining (current >= 1-year-ago, if available)

  Short eligible (must show distress signals -- ANY ONE of):
    - ROE < sector 30th percentile
    - OR D/E > sector 75th percentile
    - OR gross margin declining

Uses roe_hist, debt_to_equity_hist, gross_margin_hist from company_ratios.
"""

import pandas as pd
import numpy as np


def apply_quality_gate(
    company_ratios_wide: pd.DataFrame,
    company_ratios_wide_1y_ago: pd.DataFrame,
    sectors: pd.Series,
) -> tuple:
    """Apply asymmetric quality gate.

    Parameters
    ----------
    company_ratios_wide : current PIT-lagged company_ratios
    company_ratios_wide_1y_ago : company_ratios from ~1 year ago (for trend)
    sectors : symbol -> GICS sector

    Returns
    -------
    (long_eligible: pd.Series[bool], short_eligible: pd.Series[bool])
    """
    all_syms = sectors.index

    # Default: everyone eligible (if no data, don't block)
    long_eligible = pd.Series(True, index=all_syms)
    short_eligible = pd.Series(False, index=all_syms)

    if company_ratios_wide is None or company_ratios_wide.empty:
        # No quality data -> all long-eligible, none short-eligible
        return long_eligible, short_eligible

    cr = company_ratios_wide

    # ---- Extract metrics ----
    roe = cr["roe_hist"] if "roe_hist" in cr.columns else pd.Series(dtype=float)
    de = cr["debt_to_equity_hist"] if "debt_to_equity_hist" in cr.columns else pd.Series(dtype=float)
    gm = cr["gross_margin_hist"] if "gross_margin_hist" in cr.columns else pd.Series(dtype=float)

    # Previous gross margin for trend
    gm_prev = pd.Series(dtype=float)
    if (company_ratios_wide_1y_ago is not None
            and not company_ratios_wide_1y_ago.empty
            and "gross_margin_hist" in company_ratios_wide_1y_ago.columns):
        gm_prev = company_ratios_wide_1y_ago["gross_margin_hist"]

    # ---- FIX: Winsorize extreme values before sector percentiles ----
    # roe_hist has values from -175 to +216; de_hist from -1478 to +825
    # These extremes corrupt sector percentiles
    def winsorize(s, lo=0.025, hi=0.975):
        if len(s.dropna()) < 5:
            return s
        lower = s.quantile(lo)
        upper = s.quantile(hi)
        return s.clip(lower=lower, upper=upper)

    roe = winsorize(roe)
    de = winsorize(de)
    gm = winsorize(gm)

    # ---- Compute sector percentiles ----
    df = pd.DataFrame({
        "roe": roe,
        "de": de,
        "gm": gm,
        "sector": sectors,
    })

    # Only process symbols that have sector info
    df = df.loc[df["sector"].notna()]

    for sector_name, group in df.groupby("sector"):
        syms = group.index

        # ROE percentiles
        roe_vals = group["roe"].dropna()
        if len(roe_vals) >= 5:
            p40 = roe_vals.quantile(0.40)
            p30 = roe_vals.quantile(0.30)
            for sym in syms:
                rv = group.loc[sym, "roe"] if sym in group.index else np.nan
                if not np.isnan(rv):
                    # Long filter: ROE must be above sector p40
                    if rv < p40:
                        long_eligible[sym] = False
                    # Short filter: ROE below sector p30
                    if rv < p30:
                        short_eligible[sym] = True

        # Debt/Equity: high leverage -> short eligible
        de_vals = group["de"].dropna()
        if len(de_vals) >= 5:
            p75 = de_vals.quantile(0.75)
            for sym in syms:
                dv = group.loc[sym, "de"] if sym in group.index else np.nan
                if not np.isnan(dv) and dv > p75:
                    short_eligible[sym] = True

        # Gross margin trend
        for sym in syms:
            gm_curr = group.loc[sym, "gm"] if sym in group.index else np.nan
            gm_old = gm_prev.get(sym, np.nan) if len(gm_prev) > 0 else np.nan

            if not np.isnan(gm_curr) and not np.isnan(gm_old):
                if gm_curr < gm_old:
                    # Declining margin -> short eligible
                    short_eligible[sym] = True
                    # Long filter: margin not declining
                    # (already handled: we only block if ROE also fails,
                    #  but spec says "not_declining" for long)
                    long_eligible.loc[sym] = long_eligible.get(sym, True) and True
                    # Actually: margin declining by itself doesn't block long
                    # The long filter is: ROE > p40 AND margin not declining
                    # So if margin is declining, block long
                    if long_eligible.get(sym, True):
                        long_eligible[sym] = False

    return long_eligible, short_eligible
