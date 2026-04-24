"""L/S Cost Stress v2 — correct column mapping.

Engine hardcodes columns as dynamic_net_20bp and dynamic_net_30bp, but the
VALUES in them reflect the cost rates in cost_per_side_bp_headline and
cost_per_side_bp_sensitivity respectively. So to test 4 cost levels we:
  Run A:  headline=10, sensitivity=20  → read _20bp as 10bp, _30bp as 20bp
  Run B:  headline=50, sensitivity=100 → read _20bp as 50bp, _30bp as 100bp
"""
import os, sys, subprocess, shutil, re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "coursework_two", "analytics"))

CW2 = os.path.join(REPO_ROOT, "coursework_two")
LS_OUTPUT = os.path.join(CW2, "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")

import pandas as pd
import numpy as np
from performance import (
    annualised_return, annualised_volatility, sharpe_ratio,
    max_drawdown, sortino_ratio, circular_block_bootstrap_sharpe,
)

ANN = 12
BLOCK_SIZE = 3
N_BOOTSTRAP = 2000
SEED = 42
END_DATE = "2026-03-31"

# (headline_cost, sensitivity_cost): the two cost rates to test in one run
# The engine writes headline_cost values into _net_20bp and
# sensitivity_cost values into _net_30bp (column names are hardcoded).
COST_RUNS = [
    (10, 20),
    (50, 100),
]

VARIANTS = [("dynamic", "Dynamic L/S"), ("static", "Static L/S")]

CONFIG_PATH = os.path.join(CW2, "config", "backtest_config.yaml")
CONFIG_BACKUP = os.path.join(CW2, "config", "backtest_config.yaml.cost_stress_bak")


def update_cost_config(headline_bp, sensitivity_bp):
    with open(CONFIG_PATH) as f:
        c = f.read()
    c = re.sub(r"cost_per_side_bp_headline:\s*\d+",
               f"cost_per_side_bp_headline: {headline_bp}", c)
    c = re.sub(r"cost_per_side_bp_sensitivity:\s*\d+",
               f"cost_per_side_bp_sensitivity: {sensitivity_bp}", c)
    with open(CONFIG_PATH, "w") as f:
        f.write(c)


def run_main():
    print(f"  [run] python Main.py --end {END_DATE}")
    r = subprocess.run(
        ["poetry", "run", "python", "Main.py", "--end", END_DATE],
        cwd=CW2, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr[-500:])
        raise RuntimeError(f"Main.py failed: {r.returncode}")
    print("\n".join(r.stdout.splitlines()[-4:]))


def extract(col):
    df = pd.read_parquet(os.path.join(LS_OUTPUT, "portfolio_returns.parquet"))
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    ret = df[col].dropna()
    rf = df["rf_rate"].reindex(ret.index).fillna(0.0)
    raw_sr = sharpe_ratio(ret, rf_series=0.0, ann=ANN)
    excess_sr = sharpe_ratio(ret, rf_series=rf, ann=ANN)
    bs = circular_block_bootstrap_sharpe(
        returns=ret, block_size=BLOCK_SIZE, n_bootstrap=N_BOOTSTRAP, seed=SEED, ann=ANN,
    )
    return {
        "ann_return": annualised_return(ret, ann=ANN),
        "ann_vol": annualised_volatility(ret, ann=ANN),
        "raw_sharpe": raw_sr,
        "excess_sharpe": excess_sr,
        "sortino": sortino_ratio(ret, rf_series=rf, ann=ANN),
        "max_dd": max_drawdown(ret),
        "bs_ci_lo": bs["low"],
        "bs_ci_hi": bs["high"],
    }


shutil.copy(CONFIG_PATH, CONFIG_BACKUP)
print(f"Config backed up -> {CONFIG_BACKUP}")

results = []
try:
    for headline_bp, sensitivity_bp in COST_RUNS:
        print(f"\n{'='*72}")
        print(f"  Cost config: headline={headline_bp}bp, sensitivity={sensitivity_bp}bp")
        print(f"  (Will read _net_20bp as {headline_bp}bp, _net_30bp as {sensitivity_bp}bp)")
        print(f"{'='*72}")
        update_cost_config(headline_bp, sensitivity_bp)
        run_main()

        # Read BOTH columns, remapping their logical cost
        column_cost_map = [
            ("_net_20bp", headline_bp),
            ("_net_30bp", sensitivity_bp),
        ]
        for prefix, vlabel in VARIANTS:
            for suffix, logical_cost in column_cost_map:
                col = f"{prefix}{suffix}"
                df_check = pd.read_parquet(os.path.join(LS_OUTPUT, "portfolio_returns.parquet"))
                if col not in df_check.columns:
                    print(f"  [WARN] {col} not found")
                    continue
                m = extract(col)
                results.append({
                    "variant": vlabel, "prefix": prefix,
                    "cost_bp": logical_cost, **m,
                })
                print(f"  {vlabel:<15} @ {logical_cost:3d}bp:  "
                      f"raw={m['raw_sharpe']:+.3f}  "
                      f"excess={m['excess_sharpe']:+.3f}  "
                      f"CI=[{m['bs_ci_lo']:+.3f},{m['bs_ci_hi']:+.3f}]")
finally:
    shutil.copy(CONFIG_BACKUP, CONFIG_PATH)
    os.remove(CONFIG_BACKUP)
    print("\nConfig restored.")

# Summary
print("\n" + "="*72)
print("  COST STRESS SUMMARY (4 cost levels)")
print("="*72)
df = pd.DataFrame(results).sort_values(["prefix", "cost_bp"]).reset_index(drop=True)
for vlabel in df["variant"].unique():
    sub = df[df["variant"] == vlabel]
    print(f"\n  {vlabel}")
    print(f"  {'Cost':>6} {'Raw SR':>10} {'Excess SR':>11} {'Ann.Ret':>10} "
          f"{'Max DD':>9} {'CI Lo':>9} {'CI Hi':>9}")
    print("  " + "-"*72)
    for _, row in sub.iterrows():
        print(f"  {row['cost_bp']:>4}bp  {row['raw_sharpe']:>+10.3f} {row['excess_sharpe']:>+11.3f} "
              f"{row['ann_return']:>+10.2%} {row['max_dd']:>+9.2%} "
              f"{row['bs_ci_lo']:>+9.3f} {row['bs_ci_hi']:>+9.3f}")

df.to_csv(os.path.join(MY_OUTPUT, "ls_cost_stress.csv"), index=False)
print(f"\nSaved -> {os.path.join(MY_OUTPUT, 'ls_cost_stress.csv')}")

print("\n[restore] Re-running at 20/30 canonical...")
run_main()
print("Done.")
