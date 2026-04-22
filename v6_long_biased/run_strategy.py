#!/usr/bin/env python3
"""
v6.5: Risk-Managed Long-Only Factor Portfolio (FINAL)

Strategy:
  - 2 alpha factors: Momentum (12-1) + Value (B/P+E/P+CF/P)
  - Sentiment tested but excluded (semi-annual too coarse, adds noise)
  - Long-only 100/0 (no shorting)
  - Score × inverse-vol weighted, max 1.5% per stock (iterative cap)
  - 3-signal market regime risk overlay (200DMA, 12M return, VIX percentile)
  - Bi-monthly rebalancing
  - PIT lag 45 days, 10bps trading cost

Run: cd v6_long_biased && python run_strategy.py
"""
import sys, os, time, yaml, copy
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import DataLoader
from backtest.engine import run_backtest
from backtest.performance import compute_performance_metrics
from portfolio.risk_overlay import apply_risk_overlay


def main():
    t0 = time.time()
    config = yaml.safe_load(open("config/strategy_params.yaml"))
    dl = DataLoader(data_dir=config["data_dir"])

    print("=" * 70)
    print("  v6.5: Risk-Managed Long-Only Factor Portfolio (FINAL)")
    print("  Factors: Mom+Val (z-score) | Score×inv-vol weighted, max 1.5%")
    print("  Risk Overlay: 3-signal (200DMA, 12M ret, VIX percentile)")
    print("=" * 70)

    # Force long-only config
    config["portfolio"]["long_notional"] = 1.00
    config["portfolio"]["short_notional"] = 0.00
    config["portfolio"]["short_pct"] = 0.00
    config["portfolio"]["long_pct"] = 0.25

    # Run base backtest
    res = run_backtest(config, dl)
    if not res:
        print("ERROR: no results"); return

    mg_base = res["monthly_gross"]
    mn_base = res["monthly_net"]
    rf = res["rf_series"]

    # Load market data for overlay
    benchmark = dl.get_benchmark()
    sp500 = benchmark[benchmark["symbol"] == "^GSPC"].set_index("date")["adj close"].sort_index()
    vix_df = dl.get_vix()
    vix = vix_df.set_index("date")["close"].sort_index()

    # Apply risk overlay
    print("\n[overlay] Applying 3-signal risk overlay...")
    mg_overlay, exposures_g = apply_risk_overlay(
        mg_base, sp500, vix, rf, res["rebalance_dates"]
    )
    mn_overlay, exposures_n = apply_risk_overlay(
        mn_base, sp500, vix, rf, res["rebalance_dates"]
    )

    # Compute benchmark returns over EXACT same holding periods as strategy
    bm_ret = sp500.pct_change().dropna()
    rebal_ts_list = sorted(mg_overlay.index)
    bm_period_dict = {}
    for i, dt in enumerate(rebal_ts_list):
        if i == 0:
            prev_dt = dt - pd.DateOffset(months=2)
        else:
            prev_dt = rebal_ts_list[i-1]
        bm_window = bm_ret.loc[(bm_ret.index > prev_dt) & (bm_ret.index <= dt)]
        bm_period_dict[dt] = (1 + bm_window).prod() - 1 if len(bm_window) > 0 else 0.0
    bm_period = pd.Series(bm_period_dict)

    gm = compute_performance_metrics(mg_overlay, rf, bm_period)
    nm = compute_performance_metrics(mn_overlay, rf, bm_period)

    print(f"\n{'='*60}")
    print(f"  FINAL RESULTS: v6.5 Risk-Managed Long-Only")
    print(f"{'='*60}")

    for label, m in [("GROSS", gm), ("NET", nm)]:
        print(f"\n  {label}:")
        print(f"    Ann Return:  {m['annualised_return']:+.2%}")
        print(f"    Sharpe:      {m['sharpe_ratio']:+.3f}")
        print(f"    Total:       {m['total_return']:+.2%}")
        print(f"    Max DD:      {m['max_drawdown']:+.2%}")
        print(f"    Win Rate:    {m['win_rate']:.0%}")
        print(f"    Vol:         {m['annualised_volatility']:.2%}")
        if "beta" in m:
            print(f"    Beta:        {m['beta']:+.3f}")

    print(f"\n  Avg exposure:  {exposures_g.mean():.0%}")
    print(f"  Min exposure:  {exposures_g.min():.0%}")
    print(f"  Risk-off months: {(exposures_g < 1.0).sum()}/{len(exposures_g)}")

    # IS/OOS
    print(f"\n  IS / OOS:")
    for period, start, end in [
        ("IS", "2022-03-01", "2024-03-31"),
        ("OOS", "2024-05-01", "2026-03-31"),
    ]:
        mask = (mn_overlay.index >= start) & (mn_overlay.index <= end)
        if mask.sum() < 3:
            continue
        n = compute_performance_metrics(mn_overlay[mask], rf, bm_period.reindex(mn_overlay[mask].index, method="nearest"))
        g = compute_performance_metrics(mg_overlay[mask], rf, bm_period.reindex(mg_overlay[mask].index, method="nearest"))
        print(f"    {period}: G.Sharpe={g['sharpe_ratio']:+.3f}  N.Sharpe={n['sharpe_ratio']:+.3f}  "
              f"N.Ann={n['annualised_return']:+.2%}  DD={n['max_drawdown']:+.2%}")

    # Save outputs
    output_dir = config.get("output_dir", "./output")
    os.makedirs(output_dir, exist_ok=True)

    pd.DataFrame({
        "date": mn_overlay.index,
        "gross_return": mg_overlay.values,
        "net_return": mn_overlay.values,
        "exposure": exposures_n.values,
    }).to_csv(os.path.join(output_dir, "monthly_returns.csv"), index=False)

    print(f"\n  Output saved to {output_dir}/monthly_returns.csv")
    print(f"  Elapsed: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
