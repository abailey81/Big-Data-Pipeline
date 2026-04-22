"""
v6.5 Fama-French 5-Factor + Momentum Attribution (§5.1 Tier 3)
---------------------------------------------------------------
Regresses v6.5's bi-monthly excess returns on Fama-French 5 factors + Momentum
using Newey-West HAC standard errors (Andrews 1991, lag=4).

Data source: Kenneth French's data library (Dartmouth), fetched directly.

Run from REPO ROOT:
    python analysis/run_attribution.py

Outputs:
    - Prints regression table
    - Saves analysis/output/ff5_mom_attribution.csv
"""
import os
import sys
import io
import urllib.request
import zipfile

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

DATA_DIR = os.path.join(REPO_ROOT, "data")
V65_OUTPUT = os.path.join(REPO_ROOT, "v6_long_biased", "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

import numpy as np
import pandas as pd
import statsmodels.api as sm

# =============================================================================
# Config
# =============================================================================
FF5_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/"
    "ftp/F-F_Research_Data_5_Factors_2x3_CSV.zip"
)
MOM_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/"
    "ftp/F-F_Momentum_Factor_CSV.zip"
)
NW_LAGS = 4   # Newey-West HAC lag (Andrews 1991)
ANN = 6       # bi-monthly annualisation factor


def fetch_ff_zip_csv(url: str, skiprows: int = 3) -> pd.DataFrame:
    """Download a Kenneth French zip, extract CSV, parse monthly section."""
    print(f"  Fetching {url.rsplit('/', 1)[-1]}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        zip_bytes = resp.read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            raw = f.read().decode("latin-1")

    # FF CSVs have monthly then annual sections; extract monthly only.
    lines = raw.split("\n")
    data_lines = []
    data_started = False
    for line in lines[skiprows:]:
        stripped = line.strip()
        if not stripped:
            if data_started:
                break
            else:
                continue
        first = stripped.split(",")[0].strip()
        if first.isdigit() and len(first) == 6:
            data_started = True
            data_lines.append(line)
        elif data_started:
            break

    df = pd.read_csv(io.StringIO("\n".join(data_lines)), header=None)
    header_line = next(
        (l for l in lines[:skiprows + 3] if "Mkt-RF" in l or "Mom" in l),
        None,
    )
    if header_line is None:
        raise RuntimeError("Could not locate FF header row")
    cols = [c.strip() for c in header_line.split(",") if c.strip()]
    df.columns = ["date"] + cols[: len(df.columns) - 1]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m")
    df = df.set_index("date").sort_index()
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def compound_monthly_to_bimonthly(monthly: pd.DataFrame, bimonthly_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Compound monthly FF returns into bi-monthly periods matching v6.5."""
    monthly_dec = monthly / 100.0

    rows = []
    prev_dt = bimonthly_dates[0] - pd.DateOffset(months=2)
    for dt in bimonthly_dates:
        mask = (monthly_dec.index > prev_dt) & (monthly_dec.index <= dt)
        window = monthly_dec.loc[mask]
        if len(window) == 0:
            idx = monthly_dec.index.get_indexer([dt], method="nearest")[0]
            window = monthly_dec.iloc[[idx]]
        compounded = (1.0 + window).prod() - 1.0
        compounded.name = dt
        rows.append(compounded)
        prev_dt = dt

    return pd.DataFrame(rows)


def run_ff_regression(y: pd.Series, X: pd.DataFrame, nw_lags: int = NW_LAGS) -> pd.DataFrame:
    """OLS with Newey-West HAC standard errors."""
    aligned = pd.concat([y.rename("y"), X], axis=1).dropna()
    X_mat = aligned.drop(columns=["y"])
    X_mat = sm.add_constant(X_mat, has_constant="add")
    model = sm.OLS(aligned["y"], X_mat).fit(
        cov_type="HAC", cov_kwds={"maxlags": nw_lags}
    )

    rows = []
    for name in X_mat.columns:
        rows.append({
            "factor":  "alpha" if name == "const" else name,
            "beta":    float(model.params[name]),
            "se_nw":   float(model.bse[name]),
            "t_stat":  float(model.tvalues[name]),
            "p_value": float(model.pvalues[name]),
        })
    result = pd.DataFrame(rows)
    result.attrs["r_squared"] = float(model.rsquared)
    result.attrs["r_squared_adj"] = float(model.rsquared_adj)
    result.attrs["n_obs"] = int(model.nobs)
    return result


def main():
    print("=" * 72)
    print("  v6.5 FF5 + Momentum Attribution Regression")
    print("  Newey-West HAC standard errors (lag = {})".format(NW_LAGS))
    print("=" * 72)

    # 1. Load v6.5 returns
    returns_path = os.path.join(V65_OUTPUT, "monthly_returns.csv")
    df = pd.read_csv(returns_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    net_ret = df["net_return"]
    print(f"\nv6.5: {len(net_ret)} bi-monthly observations, "
          f"{net_ret.index.min().date()} to {net_ret.index.max().date()}")

    # 2. Load RFR, compute excess returns
    rfr_df = pd.read_parquet(os.path.join(DATA_DIR, "risk_free_rate.parquet"))
    rfr_df["date"] = pd.to_datetime(rfr_df["date"])
    rfr_df = rfr_df.set_index("date").sort_index()
    rfr_bimo = rfr_df["rate_pct"].reindex(net_ret.index, method="nearest") / 100 / ANN
    excess = net_ret - rfr_bimo

    # 3. Fetch FF factors
    print("\nFetching Fama-French data from Kenneth French's library...")
    ff5 = fetch_ff_zip_csv(FF5_URL)
    mom = fetch_ff_zip_csv(MOM_URL)

    mom.columns = ["MOM" if "om" in c.lower() else c for c in mom.columns]
    ff = ff5.join(mom[["MOM"]], how="inner")
    sample_start = net_ret.index.min() - pd.DateOffset(months=3)
    ff = ff.loc[ff.index >= sample_start].copy()
    print(f"  FF data: {len(ff)} monthly obs, {ff.index.min().date()} to {ff.index.max().date()}")

    # 4. Compound to bi-monthly
    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]
    ff_bimo = compound_monthly_to_bimonthly(ff[factor_cols], net_ret.index)
    print(f"  Compounded to {len(ff_bimo)} bi-monthly periods")

    # 5. Run regression
    print("\nRunning OLS with Newey-West HAC standard errors...")
    result = run_ff_regression(excess, ff_bimo, nw_lags=NW_LAGS)

    # 6. Display
    print("\n" + "=" * 72)
    print("  FF5 + MOM ATTRIBUTION — v6.5 Excess Bi-Monthly Returns")
    print("=" * 72)
    print(f"\nR² = {result.attrs['r_squared']:.4f}   "
          f"Adj R² = {result.attrs['r_squared_adj']:.4f}   "
          f"N = {result.attrs['n_obs']}")
    print(f"\n{'Factor':<8} {'Beta':>9} {'SE (NW)':>9} {'t-stat':>8} {'p-value':>9} {'Sig':>5}")
    print("  " + "-" * 56)
    for _, row in result.iterrows():
        p = row["p_value"]
        sig = "***" if p < 0.01 else ("**" if p < 0.05 else ("*" if p < 0.10 else ""))
        print(f"  {row['factor']:<6} "
              f"{row['beta']:>+9.4f} "
              f"{row['se_nw']:>+9.4f} "
              f"{row['t_stat']:>+8.3f} "
              f"{row['p_value']:>9.4f} {sig:>5}")
    print("\n  Significance: *** p<0.01  ** p<0.05  * p<0.10")

    alpha_row = result[result["factor"] == "alpha"].iloc[0]
    ann_alpha = alpha_row["beta"] * ANN
    print(f"\n  Key finding — ALPHA (after controlling for factor exposure):")
    print(f"    Bi-monthly alpha:    {alpha_row['beta']:+.4f}  "
          f"(t = {alpha_row['t_stat']:+.3f},  p = {alpha_row['p_value']:.4f})")
    print(f"    Annualised alpha:    {ann_alpha:+.2%}  "
          f"({'significant' if alpha_row['p_value'] < 0.05 else 'not significant'} at 95%)")

    # Save
    output_path = os.path.join(MY_OUTPUT, "ff5_mom_attribution.csv")
    result.to_csv(output_path, index=False)
    print(f"\nSaved → {output_path}")


if __name__ == "__main__":
    main()
