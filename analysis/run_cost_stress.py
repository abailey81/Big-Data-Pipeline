"""
v6.5 Cost Stress Test (§5.1 Tier 2 of Integration Spec)
--------------------------------------------------------
Reruns v6.5 end-to-end at multiple trading cost assumptions to demonstrate
robustness to realistic cost variations.

Cost levels tested:
  10 bps  - large-cap liquid equities baseline (v6.5 default)
  20 bps  - mid-cap / less-liquid equities
  50 bps  - stress case (small-cap, wider spreads)
  100 bps - extreme stress (retail / illiquid markets)

Run from REPO ROOT:
    python analysis/run_cost_stress.py

Outputs:
    - Prints results table
    - Saves analysis/output/cost_stress_results.csv
"""
import os
import sys
import copy

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Tamer's analytics (for statistical functions)
sys.path.insert(0, os.path.join(REPO_ROOT, "coursework_two", "analytics"))
# Lucian's v6.5 engine (for the backtest machinery)
sys.path.insert(0, os.path.join(REPO_ROOT, "v6_long_biased"))

DATA_DIR = os.path.join(REPO_ROOT, "data")
V65_DIR = os.path.join(REPO_ROOT, "v6_long_biased")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

import pandas as pd
import numpy as np
import yaml

from data_loader import DataLoader
from backtest.engine import run_backtest
from backtest.performance import compute_performance_metrics
from portfolio.risk_overlay import apply_risk_overlay
from performance import (
    sharpe_ratio, max_drawdown, annualised_return,
    annualised_volatility, sortino_ratio,
    circular_block_bootstrap_sharpe,
)

# v6.5 is bi-monthly: 6 periods/year
ANN = 6

# Cost levels to test (basis points per side)
COST_LEVELS_BP = [10, 20, 50, 100]


def load_base_config():
    """Load base v6.5 config with long-only overrides (mirrors run_strategy.py)."""
    config_path = os.path.join(V65_DIR, "config", "strategy_params.yaml")
    config = yaml.safe_load(open(config_path))

    # Force long-only (mirrors run_strategy.py)
    config["portfolio"]["long_notional"] = 1.00
    config["portfolio"]["short_notional"] = 0.00
    config["portfolio"]["short_pct"] = 0.00
    config["portfolio"]["long_pct"] = 0.25

    # Override data_dir with absolute path so it works regardless of cwd
    config["data_dir"] = DATA_DIR

    return config


def run_at_cost(config_template, cost_bp, dl):
    """Run one full backtest at given cost level, return net return series."""
    config = copy.deepcopy(config_template)
    config["transaction_costs"]["trading_bps"] = cost_bp

    res = run_backtest(config, dl)
    mn_base = res["monthly_net"]
    rf = res["rf_series"]

    # Apply 3-signal risk overlay (same as run_strategy.py)
    benchmark = dl.get_benchmark()
    sp500 = benchmark[benchmark["symbol"] == "^GSPC"].set_index("date")["adj close"].sort_index()
    vix = dl.get_vix().set_index("date")["close"].sort_index()

    mn_overlay, _ = apply_risk_overlay(
        mn_base, sp500, vix, rf, res["rebalance_dates"]
    )
    return mn_overlay


def main():
    print("=" * 72)
    print("  v6.5 Cost Stress Test")
    print("  Rerunning the full bi-monthly backtest at 4 cost levels")
    print("=" * 72)

    config_template = load_base_config()
    dl = DataLoader(data_dir=config_template["data_dir"])

    rfr_df = pd.read_parquet(os.path.join(DATA_DIR, "risk_free_rate.parquet"))
    rfr_df["date"] = pd.to_datetime(rfr_df["date"])
    rfr_df = rfr_df.set_index("date").sort_index()

    results = []
    for bp in COST_LEVELS_BP:
        print(f"\n[cost_stress] Running at {bp} bps...")
        mn_overlay = run_at_cost(config_template, bp, dl)

        rfr_aligned = rfr_df["rate_pct"].reindex(mn_overlay.index, method="nearest") / 100 / ANN

        raw_sr = sharpe_ratio(mn_overlay, rf_series=0.0, ann=ANN)
        excess_sr = sharpe_ratio(mn_overlay, rf_series=rfr_aligned, ann=ANN)
        ann_ret = annualised_return(mn_overlay, ann=ANN)
        ann_vol = annualised_volatility(mn_overlay, ann=ANN)
        mdd = max_drawdown(mn_overlay)
        sortino = sortino_ratio(mn_overlay, rf_series=rfr_aligned, ann=ANN)

        boot = circular_block_bootstrap_sharpe(
            mn_overlay, block_size=3, n_bootstrap=1000, seed=42, ann=ANN,
        )

        results.append({
            "cost_bps":         bp,
            "ann_return":       ann_ret,
            "ann_vol":          ann_vol,
            "raw_sharpe":       raw_sr,
            "excess_sharpe":    excess_sr,
            "sortino":          sortino,
            "max_drawdown":     mdd,
            "boot_sharpe_low":  boot["low"],
            "boot_sharpe_high": boot["high"],
        })

    df_results = pd.DataFrame(results)

    print("\n" + "=" * 72)
    print("  COST STRESS RESULTS")
    print("=" * 72)
    header = (f"  {'Cost':>6}  {'Ann.Ret':>9}  {'Ann.Vol':>8}  "
              f"{'RawSR':>7}  {'ExcSR':>7}  {'Sortino':>8}  "
              f"{'MaxDD':>7}  {'95% CI':>22}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in results:
        print(f"  {r['cost_bps']:>4}bp  "
              f"{r['ann_return']:>+8.2%}  "
              f"{r['ann_vol']:>+7.2%}  "
              f"{r['raw_sharpe']:>+7.3f}  "
              f"{r['excess_sharpe']:>+7.3f}  "
              f"{r['sortino']:>+8.3f}  "
              f"{r['max_drawdown']:>+7.2%}  "
              f"[{r['boot_sharpe_low']:+.3f}, {r['boot_sharpe_high']:+.3f}]")

    print(f"\n  Sharpe degradation:")
    base_sr = results[0]["excess_sharpe"]
    for r in results:
        delta = r["excess_sharpe"] - base_sr
        print(f"    {r['cost_bps']:>3}bp → ExcSR {r['excess_sharpe']:+.3f}  "
              f"(Δ vs 10bp baseline: {delta:+.3f})")

    output_path = os.path.join(MY_OUTPUT, "cost_stress_results.csv")
    df_results.to_csv(output_path, index=False)
    print(f"\nSaved → {output_path}")


if __name__ == "__main__":
    main()
