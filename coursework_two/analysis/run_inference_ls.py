"""L/S Statistical Inference Driver."""
import os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CW2_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(CW2_ROOT, "analytics"))

LS_OUTPUT = os.path.join(CW2_ROOT, "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

import pandas as pd
import numpy as np
from performance import (
    annualised_return, annualised_volatility, sharpe_ratio,
    sortino_ratio, calmar_ratio, max_drawdown, drawdown_duration_months,
    skewness, excess_kurtosis, historical_var, expected_shortfall,
    monthly_hit_rate, best_month, worst_month,
    circular_block_bootstrap_sharpe, deflated_sharpe_ratio,
    probabilistic_sharpe_ratio, minimum_backtest_length,
)

ANN = 12
N_TRIALS = 15
BLOCK_SIZE = 3
N_BOOTSTRAP = 2000
SEED = 42
PSR_THRESHOLDS = [0.0, 0.5, 1.0]

LS_VARIANTS = [
    ("dynamic_net_20bp", "Dynamic L/S 20bp (HEADLINE)"),
    ("static_net_20bp",  "Static L/S 20bp"),
    ("bandit_net_20bp",  "Bandit L/S 20bp"),
    ("hrp_net_20bp",     "HRP L/S 20bp"),
]

print("Loading portfolio_returns.parquet...")
df = pd.read_parquet(os.path.join(LS_OUTPUT, "portfolio_returns.parquet"))
df["date"] = pd.to_datetime(df["date"])
df = df.set_index("date").sort_index()
rf_monthly = df["rf_rate"]
rfr_ann_mean = float(rf_monthly.mean() * ANN)
print(f"  Sample: {df.index.min().date()} to {df.index.max().date()}  ({len(df)} monthly obs)")
print(f"  Mean annualised RFR: {rfr_ann_mean:+.2%}")

print("\n" + "="*78)
print("  L/S STATISTICAL INFERENCE")
print("="*78)

all_results = []
for col, label in LS_VARIANTS:
    if col not in df.columns:
        print(f"\n[SKIP] {label}: column '{col}' not found")
        continue
    ret = df[col].dropna()
    rf = rf_monthly.reindex(ret.index).fillna(0.0)
    if len(ret) < 10:
        print(f"\n[SKIP] {label}: only {len(ret)} obs")
        continue

    print(f"\n{'='*78}\n  {label}  |  col='{col}'\n{'='*78}")
    print(f"  Observations: {len(ret)}  |  {ret.index.min().date()} to {ret.index.max().date()}")

    raw_sharpe = sharpe_ratio(ret, rf_series=0.0, ann=ANN)
    excess_sharpe = sharpe_ratio(ret, rf_series=rf, ann=ANN)
    metrics = {
        "variant": label, "column": col, "n_observations": len(ret),
        "sample_start": str(ret.index.min().date()), "sample_end": str(ret.index.max().date()),
        "ann_return": annualised_return(ret, ann=ANN),
        "ann_volatility": annualised_volatility(ret, ann=ANN),
        "raw_sharpe": raw_sharpe, "excess_sharpe": excess_sharpe,
        "sortino_ratio": sortino_ratio(ret, rf_series=rf, ann=ANN),
        "calmar_ratio": calmar_ratio(ret, ann=ANN),
        "max_drawdown": max_drawdown(ret),
        "drawdown_duration": drawdown_duration_months(ret),
        "skewness": skewness(ret), "excess_kurtosis": excess_kurtosis(ret),
        "historical_var_99": historical_var(ret, confidence=0.99),
        "expected_shortfall_99": expected_shortfall(ret, confidence=0.99),
        "hit_rate": monthly_hit_rate(ret),
        "best_month": best_month(ret), "worst_month": worst_month(ret),
    }

    print(f"\n  Headline Metrics")
    print(f"  {'-'*42}")
    print(f"  Ann. Return:           {metrics['ann_return']:+.2%}")
    print(f"  Ann. Volatility:       {metrics['ann_volatility']:+.2%}")
    print(f"  Raw Sharpe:            {raw_sharpe:+.4f}")
    print(f"  Excess Sharpe:         {excess_sharpe:+.4f}")
    print(f"  Sortino:               {metrics['sortino_ratio']:+.3f}")
    print(f"  Calmar:                {metrics['calmar_ratio']:+.3f}")
    print(f"  Max Drawdown:          {metrics['max_drawdown']:+.2%}")
    print(f"  DD duration (months):  {metrics['drawdown_duration']}")
    print(f"  Hit rate:              {metrics['hit_rate']:+.1%}")

    print(f"\n  Statistical Inference\n  {'-'*42}")
    # Bootstrap CI on the EXCESS-return series — matches the convention
    # used in the report's Table 11 ("Observed Excess SR" with bootstrap
    # 95% CI).  Passing raw returns would shift the CI roughly +0.4 units
    # higher and not match the headline figure.
    excess_ret = ret - rf
    bs = circular_block_bootstrap_sharpe(returns=excess_ret, block_size=BLOCK_SIZE,
                                          n_bootstrap=N_BOOTSTRAP, seed=SEED, ann=ANN)
    metrics["bootstrap_mean"] = bs["mean"]
    metrics["bootstrap_std"] = bs["std"]
    metrics["bootstrap_ci_lo"] = bs["low"]
    metrics["bootstrap_ci_hi"] = bs["high"]
    print(f"  Politis-Romano Bootstrap 95% CI (block={BLOCK_SIZE}, n={N_BOOTSTRAP})")
    print(f"    mean:   {bs['mean']:+.3f}")
    print(f"    std:    {bs['std']:+.3f}")
    print(f"    95%CI:  [{bs['low']:+.3f}, {bs['high']:+.3f}]")

    dsr = deflated_sharpe_ratio(observed_sharpe=raw_sharpe, n_trials=N_TRIALS, returns=ret, ann=ANN)
    print(f"\n  Deflated Sharpe Ratio (n_trials={N_TRIALS})")
    if isinstance(dsr, dict):
        for k, v in dsr.items():
            if isinstance(v, (int, float)):
                print(f"    {k}: {v:.4f}")
                metrics[f"dsr_{k}"] = v
            else:
                print(f"    {k}: {v}")
    else:
        print(f"    DSR: {dsr:.4f}")
        metrics["dsr_probability"] = float(dsr)

    print(f"\n  Probabilistic Sharpe Ratio — P(true SR > threshold)")
    for thr in PSR_THRESHOLDS:
        prob = probabilistic_sharpe_ratio(observed_sharpe=raw_sharpe,
                                           threshold_sharpe=thr, returns=ret, ann=ANN)
        metrics[f"psr_threshold_{thr}"] = prob
        print(f"    P(SR > {thr:.1f}):  {prob:.4f}")

    print(f"\n  Minimum Backtest Length\n    Current sample: {len(ret)} months")
    for target_sr in [0.5, 1.0, raw_sharpe]:
        lbl = f"observed ({raw_sharpe:+.3f})" if target_sr == raw_sharpe else f"{target_sr:.1f}"
        mbl = minimum_backtest_length(target_sharpe=target_sr, n_trials=N_TRIALS, alpha=0.05)
        mbl_str = f"{mbl:.0f}" if np.isfinite(mbl) else "inf"
        metrics[f"mbl_target_{target_sr:.3f}"] = mbl
        print(f"    Certify SR={lbl}: {mbl_str} months")

    all_results.append(metrics)

out_df = pd.DataFrame(all_results)
out_path = os.path.join(MY_OUTPUT, "ls_inference.csv")
out_df.to_csv(out_path, index=False)
print(f"\nSaved -> {out_path}")
