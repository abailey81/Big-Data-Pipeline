#!/usr/bin/env python3
"""
run_strategy.py -- v4 Alpha Engine Runner

Run from inside v4_alpha_engine/:
    python run_strategy.py

v4 = 4-factor sector-neutral strategy:
  - Momentum: PRIMARY alpha (45%), multi-horizon residual momentum
  - Value: Slow anchor (20%), PIT-safe, _hist ratios only
  - Quality: Asymmetric gate (20%), different rules long vs short
  - Sentiment: Conviction modifier (15%), delta-based, confidence-weighted

All Codex audit bugs fixed:
  #1 Frequency-aware annualization (detect monthly vs bi-monthly)
  #2 PIT lag 45 days on fundamentals AND company_ratios
  #3 Value uses _hist ratios from company_ratios (no book_value/price hack)
  #4 Skip rebalance still records P&L for held positions
  #5 Max weight 5% enforced AFTER all scaling (3 times)
  #6 Sentiment cob_date = window END
  #7 No dead code files
"""

import sys
import os
import time
import yaml
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import DataLoader
from backtest.engine import run_backtest
from backtest.performance import compute_performance_metrics


def load_config(path="config/strategy_params.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    t0 = time.time()
    print("=" * 72)
    print("  v4 Alpha Engine -- 4-Factor Sector-Neutral Strategy")
    print("  Mom(45%) + Val(20%) + Quality Gate(20%) + Sentiment(15%)")
    print("=" * 72)

    config = load_config()
    data_dir = config.get("data_dir", "../data")
    output_dir = config.get("output_dir", "./output")
    os.makedirs(output_dir, exist_ok=True)

    dl = DataLoader(data_dir=data_dir)

    # ---- Full sample backtest ----
    print("\n--- Full Sample Backtest ---")
    results = run_backtest(config, dl)

    if not results or len(results.get("monthly_gross", [])) == 0:
        print("ERROR: No results generated")
        sys.exit(1)

    mg = results["monthly_gross"]
    mn = results["monthly_net"]
    rf = results["rf_series"]
    bm = results.get("benchmark_monthly")

    gm = compute_performance_metrics(mg, rf, bm, results.get("turnovers"))
    nm = compute_performance_metrics(mn, rf, bm, results.get("turnovers"))

    print("\n" + "=" * 72)
    print("  FULL SAMPLE Performance Summary")
    print("=" * 72)
    _print_metrics("GROSS", gm)
    _print_metrics("NET", nm)

    # Print diagnostics
    print(f"\n  Frequency detected: {gm.get('periods_per_year', '?')} periods/year "
          f"(annualization: sqrt({gm.get('periods_per_year', '?')}))")
    print(f"  Avg turnover:  {np.mean(results['turnovers']):.4f}")
    if results.get("var_scales"):
        print(f"  VaR scale:     [{min(results['var_scales']):.3f}, "
              f"{max(results['var_scales']):.3f}]")
    if results.get("long_counts"):
        print(f"  Avg long:      {np.mean(results['long_counts']):.0f} stocks")
        print(f"  Avg short:     {np.mean(results['short_counts']):.0f} stocks")

    # Max weight and leverage checks
    print("\n  --- Constraint Verification ---")
    if results.get("weights_history"):
        max_abs_w_all = max(
            w.abs().max() for w in results["weights_history"] if len(w) > 0
        )
        max_gross_all = max(
            w.abs().sum() for w in results["weights_history"] if len(w) > 0
        )
        print(f"  Max |weight| across all periods: {max_abs_w_all:.4f} "
              f"(limit: {config['portfolio']['max_weight']})")
        print(f"  Max gross leverage:              {max_gross_all:.2f} "
              f"(limit: {config['risk']['max_gross_leverage']})")

    # Rolling IC
    ric = results.get("rolling_ic", {})
    if ric.get("dates"):
        print(f"\n  Rolling IC:")
        for f in ["momentum", "value"]:
            vals = [v for v in ric.get(f, []) if not np.isnan(v)]
            if vals:
                print(f"    {f:<12}: {np.mean(vals):>+.4f} "
                      f"(hit rate: {sum(1 for v in vals if v > 0) / len(vals):.0%})")

    # ---- IS / OOS ----
    print("\n" + "=" * 72)
    print("  In-Sample / Out-of-Sample Validation")
    print("=" * 72)
    for period, start, end in [
        ("IS  (2022-03 to 2024-03)", "2022-03-01", "2024-03-31"),
        ("OOS (2024-05 to 2026-03)", "2024-05-01", "2026-03-31"),
    ]:
        cfg_copy = load_config()  # fresh copy
        cfg_copy["backtest"]["start_date"] = start
        cfg_copy["backtest"]["end_date"] = end
        res = run_backtest(cfg_copy, dl)
        if not res or len(res.get("monthly_gross", [])) == 0:
            print(f"  {period}: No results")
            continue
        mg2, mn2 = res["monthly_gross"], res["monthly_net"]
        gm2 = compute_performance_metrics(mg2, rf, bm, res.get("turnovers"))
        nm2 = compute_performance_metrics(mn2, rf, bm, res.get("turnovers"))
        print(f"\n  {period}:")
        print(f"    Gross: Sharpe={gm2.get('sharpe_ratio', 0):+.3f}  "
              f"Ann={gm2.get('annualised_return', 0):+.2%}  "
              f"DD={gm2.get('max_drawdown', 0):+.2%}")
        print(f"    Net:   Sharpe={nm2.get('sharpe_ratio', 0):+.3f}  "
              f"Ann={nm2.get('annualised_return', 0):+.2%}  "
              f"DD={nm2.get('max_drawdown', 0):+.2%}")

    # ---- Cost Stress Test ----
    print("\n" + "=" * 72)
    print("  Transaction Cost Stress Test")
    print("=" * 72)
    for bps in [0, 20, 50, 100]:
        cfg_copy = load_config()
        cfg_copy["transaction_costs"]["trading_bps"] = bps
        res = run_backtest(cfg_copy, dl)
        if not res or len(res.get("monthly_net", [])) == 0:
            print(f"  {bps:>3} bps: No results")
            continue
        nm_stress = compute_performance_metrics(
            res["monthly_net"], rf, bm, res.get("turnovers")
        )
        print(f"  {bps:>3} bps: Sharpe={nm_stress.get('sharpe_ratio', 0):+.3f}  "
              f"Ann={nm_stress.get('annualised_return', 0):+.2%}  "
              f"DD={nm_stress.get('max_drawdown', 0):+.2%}")

    # ---- Save CSVs ----
    print("\n--- Saving outputs ---")
    config = load_config()  # reset
    results = run_backtest(config, dl)
    mg = results["monthly_gross"]
    mn = results["monthly_net"]

    pd.DataFrame({
        "date": mg.index,
        "gross_return": mg.values,
        "net_return": mn.values,
    }).to_csv(os.path.join(output_dir, "monthly_returns.csv"), index=False)

    if results.get("weights_history"):
        wh = []
        for dt, w in zip(results["rebalance_dates"], results["weights_history"]):
            for sym, wt in w.items():
                if abs(wt) > 1e-8:
                    wh.append({"date": dt, "symbol": sym, "weight": wt})
        pd.DataFrame(wh).to_csv(
            os.path.join(output_dir, "weights_history.csv"), index=False
        )

    # Save full metrics
    gm_final = compute_performance_metrics(mg, rf, bm, results.get("turnovers"))
    nm_final = compute_performance_metrics(mn, rf, bm, results.get("turnovers"))
    metrics_df = pd.DataFrame({"gross": gm_final, "net": nm_final})
    metrics_df.to_csv(os.path.join(output_dir, "performance_metrics.csv"))

    print(f"  CSVs saved to {output_dir}/")
    print(f"\n  Total elapsed: {time.time() - t0:.0f}s")
    print("=" * 72)


def _print_metrics(label, m):
    print(f"\n  {label}:")
    print(f"    Annualised Return : {m.get('annualised_return', 0):>+8.2%}")
    print(f"    Sharpe Ratio      : {m.get('sharpe_ratio', 0):>+8.3f}")
    print(f"    Sortino Ratio     : {m.get('sortino_ratio', 0):>+8.3f}")
    print(f"    Total Return      : {m.get('total_return', 0):>+8.2%}")
    print(f"    Max Drawdown      : {m.get('max_drawdown', 0):>+8.2%}")
    print(f"    Calmar Ratio      : {m.get('calmar_ratio', 0):>+8.3f}")
    print(f"    Win Rate          : {m.get('win_rate', 0):>8.0%}")
    print(f"    Volatility        : {m.get('annualised_volatility', 0):>8.2%}")
    print(f"    Beta              : {m.get('beta', 0):>+8.3f}")
    print(f"    Alpha             : {m.get('alpha', 0):>+8.2%}")
    print(f"    Info Ratio        : {m.get('information_ratio', 0):>+8.3f}")
    print(f"    HVaR 99%          : {m.get('hvar_99', 0):>8.2%}")


if __name__ == "__main__":
    main()
