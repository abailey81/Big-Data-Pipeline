"""L/S FF5 + Mom Attribution using Tamer's helper."""
import os, sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "coursework_two", "analytics"))

LS_OUTPUT = os.path.join(REPO_ROOT, "coursework_two", "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

import pandas as pd
import numpy as np
from fama_french import run_ff5_mom_regression

ANN = 12
NW_LAGS = 4

LS_VARIANTS = [
    ("dynamic_net_20bp", "Dynamic L/S (HEADLINE)"),
    ("static_net_20bp",  "Static L/S (robustness)"),
]

print("=" * 72)
print(f"  L/S FF5 + Momentum Attribution  (NW lag = {NW_LAGS})")
print("=" * 72)

df = pd.read_parquet(os.path.join(LS_OUTPUT, "portfolio_returns.parquet"))
df["date"] = pd.to_datetime(df["date"])
df = df.set_index("date").sort_index()
start = df.index.min().date()
end = df.index.max().date()
print(f"\nSample: {start} to {end}  ({len(df)} obs)")

all_rows = []
for col, label in LS_VARIANTS:
    if col not in df.columns:
        print(f"\n[SKIP] {label}")
        continue
    ret = df[col].dropna()
    rf = df["rf_rate"].reindex(ret.index).fillna(0.0)
    excess = ret - rf

    print(f"\n{'='*72}\n  {label}  |  col='{col}'\n{'='*72}")

    result = run_ff5_mom_regression(
        strategy_monthly_returns=excess, start=start, end=end, nw_lags=NW_LAGS,
    )
    if result.empty:
        print("  [WARN] Regression returned empty")
        continue

    print(f"\n{'Factor':<10} {'Beta':>10} {'SE(NW)':>10} {'t-stat':>10} {'p-value':>10}")
    print("  " + "-"*54)
    for _, row in result.iterrows():
        p = row["p_value"]
        sig = "***" if p < 0.01 else ("**" if p < 0.05 else ("*" if p < 0.10 else ""))
        print(f"  {row['factor']:<8} {row['beta']:>+10.4f} {row['se_nw']:>+10.4f} "
              f"{row['t_stat']:>+10.3f} {row['p_value']:>10.4f}  {sig}")
    print("\n  Significance: *** p<0.01  ** p<0.05  * p<0.10")

    alpha = result[result["factor"].str.startswith("alpha")].iloc[0]
    aa = alpha.get("annualised_alpha", alpha["beta"] * ANN)
    print(f"\n  ALPHA:")
    print(f"    Monthly:      {alpha['beta']:+.4f}  (t = {alpha['t_stat']:+.3f},  p = {alpha['p_value']:.4f})")
    print(f"    Annualised:   {aa:+.2%}")
    print(f"    {'SIGNIFICANT at 95%' if alpha['p_value'] < 0.05 else 'NOT significant at 95%'}")

    result["variant"] = label
    result["column"] = col
    all_rows.append(result)

if all_rows:
    out = pd.concat(all_rows, ignore_index=True)
    out_path = os.path.join(MY_OUTPUT, "ls_ff5_mom_attribution.csv")
    out.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path}")
