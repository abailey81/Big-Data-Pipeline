"""L/S FF5 + Momentum attribution regression.

Reproduces the report's Table 10 numbers: regress raw monthly strategy
returns on the Fama-French 5-factor + Momentum benchmark with
Newey-West HAC standard errors at lag 4 (Andrews 1991).  ``run_ff5_mom_regression``
internally constructs the excess-return dependent variable as
``strategy − FF_RF``; the strategy series passed in must therefore be raw
returns (not pre-subtracted by CW1's risk-free rate).  Annualised alpha is
reported on the geometric / compound convention ``(1+α_m)^12 − 1`` to
match the headline tables in the report; the arithmetic ``α_m × 12``
figure is also reported for transparency.

Outputs
-------
``analysis/output/ls_ff5_mom_attribution.csv`` — per-factor coefficient
table for the Dynamic and Static L/S specifications.
"""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CW2_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(CW2_ROOT, "analytics"))

LS_OUTPUT = os.path.join(CW2_ROOT, "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

from fama_french import run_ff5_mom_regression  # noqa: E402  (path setup above)


NW_LAGS = 4
ANN_MONTHS = 12

LS_VARIANTS = [
    ("dynamic_net_20bp", "Dynamic L/S (HEADLINE)"),
    ("static_net_20bp",  "Static L/S (robustness)"),
]


def _compound_ann(monthly: float) -> float:
    """Geometric annualisation of a monthly return: (1 + r_m)^12 − 1."""
    return float((1.0 + monthly) ** ANN_MONTHS - 1.0)


def main() -> None:
    print("=" * 72)
    print(f"  L/S FF5 + Momentum Attribution  (NW lag = {NW_LAGS})")
    print("=" * 72)

    df = pd.read_parquet(os.path.join(LS_OUTPUT, "portfolio_returns.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # Use month-end of last strategy month so the FF5 join keeps that observation.
    start = df.index.min().date()
    end_me = (df.index.max() + pd.offsets.MonthEnd(0)).date()
    print(f"\nSample: {start} to {end_me}  ({len(df)} strategy observations)")

    all_rows: list[pd.DataFrame] = []
    for col, label in LS_VARIANTS:
        if col not in df.columns:
            print(f"\n[SKIP] {label}: column '{col}' not found")
            continue
        ret_raw = df[col].dropna()

        print(f"\n{'='*72}\n  {label}  |  col='{col}'\n{'='*72}")

        # Pass RAW strategy returns; the regression's `y = strategy − FF_RF`
        # constructs the correct excess-return dependent variable internally.
        result = run_ff5_mom_regression(
            strategy_monthly_returns=ret_raw,
            start=start,
            end=end_me,
            nw_lags=NW_LAGS,
        )
        if result.empty:
            print("  [WARN] Regression returned empty")
            continue

        print(f"\n  {'Factor':<10} {'Beta':>10} {'SE(NW)':>10} {'t-stat':>10} {'p-value':>10}")
        print("  " + "-" * 54)
        for _, row in result.iterrows():
            p = row["p_value"]
            sig = "***" if p < 0.01 else ("**" if p < 0.05 else ("*" if p < 0.10 else ""))
            print(f"  {row['factor']:<10} {row['beta']:>+10.4f} {row['se_nw']:>+10.4f} "
                  f"{row['t_stat']:>+10.3f} {row['p_value']:>10.4f}  {sig}")
        print("\n  Significance: *** p<0.01  ** p<0.05  * p<0.10")

        alpha = result[result["factor"].str.startswith("alpha")].iloc[0]
        ann_arith = float(alpha["beta"]) * ANN_MONTHS
        ann_comp = _compound_ann(float(alpha["beta"]))
        # Update the annualised_alpha column to the compound figure so the
        # CSV reports the report-aligned number.
        result.loc[result["factor"] == alpha["factor"], "annualised_alpha"] = ann_comp
        print(f"\n  ALPHA:")
        print(f"    Monthly:                {alpha['beta']:+.4f}  (t = {alpha['t_stat']:+.3f},  p = {alpha['p_value']:.4f})")
        print(f"    Annualised (compound):  {ann_comp:+.2%}")
        print(f"    Annualised (arith ×12): {ann_arith:+.2%}")
        print(f"    {'SIGNIFICANT at 95%' if alpha['p_value'] < 0.05 else 'NOT significant at 95%'}")

        result["variant"] = label
        result["column"] = col
        all_rows.append(result)

    if all_rows:
        out = pd.concat(all_rows, ignore_index=True)
        out_path = os.path.join(MY_OUTPUT, "ls_ff5_mom_attribution.csv")
        out.to_csv(out_path, index=False)
        print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
