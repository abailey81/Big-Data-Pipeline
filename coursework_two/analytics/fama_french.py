"""Real Fama-French factor data loader + alpha regression (§5.8 PLAN).

Replaces the synthetic FF proxies in ``analytics/attribution_analysis.py``
with genuine Kenneth-French Data Library factors, fetched via the
``Fama_French_5_Factors_2x3`` and ``Momentum_Factor`` monthly files.

This is the empirical anchor of the report's Section 4.3: does the
strategy's return survive exposure to Mkt-RF, SMB, HML, RMW, CMA, and MOM?
If ``α`` is positive and t-stat > 2.0 (Newey-West HAC), the strategy has
**genuine alpha** — the strongest single claim in any fund pitch.

Data source: Kenneth R. French's Data Library (Dartmouth).
URL: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
Fair-use academic data, free.
"""

from __future__ import annotations

import io
import logging
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

logger = logging.getLogger(__name__)


FF5_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_CSV.zip"
)
MOM_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Momentum_Factor_CSV.zip"
)

_CACHE_DIR = Path(__file__).parent.parent / "output" / ".ff_cache"


def _fetch_zip_csv(url: str, csv_fallback_name: str | None = None) -> pd.DataFrame:
    """Download a Kenneth-French ZIP, extract its single CSV, return raw text."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _CACHE_DIR / (url.rsplit("/", 1)[-1] + ".csv")
    if cache.exists():
        logger.info("Using cached FF data: %s", cache.name)
        return _parse_ff_csv(cache.read_text(encoding="utf-8"))
    try:
        logger.info("Downloading FF data: %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "CW2-academic"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:
        logger.warning("FF download failed (%s) — returning empty DataFrame", exc)
        return pd.DataFrame()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            return pd.DataFrame()
        csv_name = csv_fallback_name or names[0]
        raw_text = zf.read(csv_name).decode("latin-1")
    # Cache raw text
    cache.write_text(raw_text, encoding="utf-8")
    return _parse_ff_csv(raw_text)


def _parse_ff_csv(raw_text: str) -> pd.DataFrame:
    """Parse a Kenneth-French CSV — skip the preamble and annual-section footer."""
    lines = raw_text.splitlines()
    # Find first row starting with YYYYMM digits (6-char date)
    start_idx = None
    for i, line in enumerate(lines):
        first = line.split(",")[0].strip()
        if len(first) == 6 and first.isdigit():
            start_idx = i
            break
    if start_idx is None:
        return pd.DataFrame()
    # Find end: first blank line or non-digit YYYYMM after start
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        first = lines[j].split(",")[0].strip()
        if not first or (len(first) != 6 or not first.isdigit()):
            end_idx = j
            break
    clean = "\n".join([lines[start_idx - 1]] + lines[start_idx:end_idx])  # include header row
    # Header row sits one line above first data row
    header_line = lines[start_idx - 1]
    df = pd.read_csv(
        io.StringIO(clean),
        skip_blank_lines=True,
    )
    # Normalize
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={df.columns[0]: "date"})
    # Parse YYYYMM → month-end Timestamp
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)
    # All factor values are percent × 100 (e.g. "1.23" = 1.23%)
    for c in df.columns:
        if c == "date":
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0
    return df.set_index("date").sort_index()


def load_ff5_mom_factors(start: date, end: date) -> pd.DataFrame:
    """Load monthly FF5 + Momentum factor returns between ``start`` and ``end``.

    Returns
    -------
    DataFrame with columns: Mkt-RF, SMB, HML, RMW, CMA, RF, MOM  (decimals)
    Index = month-end timestamps.
    """
    ff5 = _fetch_zip_csv(FF5_URL)
    mom = _fetch_zip_csv(MOM_URL)
    if ff5.empty or mom.empty:
        logger.warning("FF data unavailable — caller should fall back to synthetic")
        return pd.DataFrame()
    # Harmonise momentum column name
    mom_col = next((c for c in mom.columns if "mom" in c.lower() or c.strip().lower() in ("mom", "mom ", "wml")), None)
    if mom_col is None:
        logger.warning("Couldn't identify MOM column in Kenneth-French data")
        return ff5.loc[start:end]
    mom = mom[[mom_col]].rename(columns={mom_col: "MOM"})
    out = ff5.join(mom, how="inner").loc[pd.Timestamp(start):pd.Timestamp(end)]
    return out


# =============================================================================
# FF5 + MOM α regression with Newey-West HAC
# =============================================================================
def run_ff5_mom_regression(
    strategy_monthly_returns: pd.Series,
    start: date,
    end: date,
    nw_lags: int = 4,
) -> pd.DataFrame:
    """Regress strategy monthly returns on FF5+Mom with Newey-West SE (Andrews 1991).

    Parameters
    ----------
    strategy_monthly_returns : pd.Series
        Index = month-end dates, values = strategy monthly returns (decimal).
    start, end : date
        Date window.
    nw_lags : int
        Newey-West lags (default 4 per Andrews).

    Returns
    -------
    DataFrame
        factor · beta · se_nw · t_stat · p_value · annualised_alpha
    """
    ff = load_ff5_mom_factors(start, end)
    if ff.empty:
        logger.warning("FF factors empty — regression aborted")
        return pd.DataFrame(columns=["factor", "beta", "se_nw", "t_stat", "p_value"])
    # Align to month-end index
    sr = strategy_monthly_returns.copy()
    sr.index = pd.to_datetime(sr.index) + pd.offsets.MonthEnd(0)
    aligned = pd.concat([sr.rename("strategy"), ff], axis=1).dropna()
    if len(aligned) < 10:
        return pd.DataFrame(columns=["factor", "beta", "se_nw", "t_stat", "p_value"])

    # Excess strategy return
    y = aligned["strategy"] - aligned["RF"]
    X = sm.add_constant(aligned[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]], has_constant="add")
    model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": nw_lags})

    rows = []
    for name in X.columns:
        rows.append({
            "factor": "alpha (intercept)" if name == "const" else name,
            "beta": float(model.params[name]),
            "se_nw": float(model.bse[name]),
            "t_stat": float(model.tvalues[name]),
            "p_value": float(model.pvalues[name]),
        })
    out = pd.DataFrame(rows)
    # Annualise the alpha row
    alpha_row = out[out["factor"] == "alpha (intercept)"]
    if len(alpha_row):
        out.loc[out["factor"] == "alpha (intercept)", "annualised_alpha"] = float(alpha_row["beta"].iloc[0]) * 12.0
    out["r_squared"] = float(model.rsquared)
    out["adj_r_squared"] = float(model.rsquared_adj)
    out["n_months"] = int(model.nobs)
    return out


__all__ = ["load_ff5_mom_factors", "run_ff5_mom_regression"]
