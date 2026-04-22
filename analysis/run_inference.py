"""
v6.5 Statistical Inference Driver
----------------------------------
Applies Tamer's statistical inference module (coursework_two/analytics/
performance.py) to Lucian's v6.5 output (v6_long_biased/output/monthly_returns.csv).
Implements §5.1 Tier 1 of the CW2 Integration Spec.

Key detail — v6.5 rebalances BI-MONTHLY (every 2 months, 6 observations per
year), so ALL annualisation factors are 6, not 12.

Run from REPO ROOT:
    python analysis/run_inference.py

Or from the analysis/ folder directly:
    cd analysis && python run_inference.py

Outputs:
    - Prints results table to terminal
    - Saves analysis/output/statistical_inference.csv
"""
import os
import sys

# ---------------------------------------------------------------------------
# Path setup — make imports work regardless of where the script is called from
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Import Tamer's statistical inference module from coursework_two/analytics/
# (single source of truth — no duplicated performance.py in v6_long_biased/)
sys.path.insert(0, os.path.join(REPO_ROOT, "coursework_two", "analytics"))

# Path constants (all absolute, so script works from any cwd)
DATA_DIR = os.path.join(REPO_ROOT, "data")
V65_OUTPUT = os.path.join(REPO_ROOT, "v6_long_biased", "output")
MY_OUTPUT = os.path.join(SCRIPT_DIR, "output")
os.makedirs(MY_OUTPUT, exist_ok=True)

import pandas as pd
import numpy as np

from performance import (
    # Headline metrics
    annualised_return, annualised_volatility, sharpe_ratio,
    sortino_ratio, calmar_ratio,
    # Drawdown
    max_drawdown, drawdown_duration_months,
    # Distribution shape
    skewness, excess_kurtosis,
    historical_var, expected_shortfall,
    monthly_hit_rate, best_month, worst_month,
    # Statistical inference (§5.1 Tier 1)
    circular_block_bootstrap_sharpe,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    minimum_backtest_length,
)

# =============================================================================
# Configuration
# =============================================================================
# v6.5 rebalances bi-monthly -> 6 periods per year, NOT 12
ANN = 6

# Number of strategy variants tested (Bailey-LdP multiplicity correction).
# Conservative: 15 counts v2/v3/v4/v5/v6.0/v6.3/v6.5 + robustness sweeps.
# Narrow: 3 counts only the long-only adopted path (v6.0/v6.3/v6.5).
# Report both as a sensitivity exhibit.
N_TRIALS = 15

# Circular block bootstrap settings
BLOCK_SIZE = 3        # 3 bi-months = 6 months; preserves autocorrelation structure
N_BOOTSTRAP = 2000    # number of resamples
SEED = 42             # reproducibility

# Sharpe thresholds to test probabilistically
PSR_THRESHOLDS = [0.0, 0.5, 1.0]

# =============================================================================
# Load v6.5 returns
# =============================================================================
print("Loading v6.5 monthly_returns.csv from v6_long_biased/output/...")
returns_path = os.path.join(V65_OUTPUT, "monthly_returns.csv")
df = pd.read_csv(returns_path)
df["date"] = pd.to_datetime(df["date"])
df = df.set_index("date").sort_index()

net_ret = df["net_return"]       # net of transaction costs — the headline series

print(f"  {len(net_ret)} bi-monthly observations from "
      f"{net_ret.index.min().date()} to {net_ret.index.max().date()}")

# =============================================================================
# Load and align risk-free rate
# =============================================================================
print("Loading RFR and aligning to rebalance dates...")
rfr_df = pd.read_parquet(os.path.join(DATA_DIR, "risk_free_rate.parquet"))
rfr_df["date"] = pd.to_datetime(rfr_df["date"])
rfr_df = rfr_df.set_index("date").sort_index()

# `rate_pct` is ANNUAL rate in PERCENT (4.76 = 4.76% per year).
# Reindex to rebalance dates (nearest match), then convert:
#   percent -> decimal (÷100), annual -> bi-monthly (÷ANN).
rfr_annual_pct = rfr_df["rate_pct"].reindex(net_ret.index, method="nearest")
rfr_series = rfr_annual_pct / 100.0 / ANN
rfr_mean_annual = float(rfr_annual_pct.mean() / 100.0)

print(f"  Mean annualised RFR over sample: {rfr_mean_annual:+.2%}")

# =============================================================================
# Headline metrics
# =============================================================================
print("Computing headline metrics...")

raw_sharpe = sharpe_ratio(net_ret, rf_series=0.0, ann=ANN)
excess_sharpe = sharpe_ratio(net_ret, rf_series=rfr_series, ann=ANN)

metrics = {
    "n_observations":        len(net_ret),
    "sample_start":          str(net_ret.index.min().date()),
    "sample_end":            str(net_ret.index.max().date()),
    "ann_return":            annualised_return(net_ret, ann=ANN),
    "ann_volatility":        annualised_volatility(net_ret, ann=ANN),
    "raw_sharpe":            raw_sharpe,
    "excess_sharpe":         excess_sharpe,
    "sortino_ratio":         sortino_ratio(net_ret, rf_series=rfr_series, ann=ANN),
    "calmar_ratio":          calmar_ratio(net_ret, ann=ANN),
    "max_drawdown":          max_drawdown(net_ret),
    "drawdown_duration":     drawdown_duration_months(net_ret),
    "skewness":              skewness(net_ret),
    "excess_kurtosis":       excess_kurtosis(net_ret),
    "historical_var_99":     historical_var(net_ret, confidence=0.99),
    "expected_shortfall_99": expected_shortfall(net_ret, confidence=0.99),
    "hit_rate":              monthly_hit_rate(net_ret),
    "best_period":           best_month(net_ret),
    "worst_period":          worst_month(net_ret),
}

# =============================================================================
# §5.1 — Statistical inference (Tier 1)
# =============================================================================
print("Running statistical inference...")

bootstrap = circular_block_bootstrap_sharpe(
    returns=net_ret, block_size=BLOCK_SIZE, n_bootstrap=N_BOOTSTRAP,
    seed=SEED, ann=ANN,
)

dsr = deflated_sharpe_ratio(
    observed_sharpe=excess_sharpe, n_trials=N_TRIALS,
    returns=net_ret, ann=ANN,
)

psr = {
    thresh: probabilistic_sharpe_ratio(excess_sharpe, thresh, net_ret, ann=ANN)
    for thresh in PSR_THRESHOLDS
}

mbl = {
    "mbl_target_0.5":     minimum_backtest_length(target_sharpe=0.5, n_trials=N_TRIALS),
    "mbl_target_1.0":     minimum_backtest_length(target_sharpe=1.0, n_trials=N_TRIALS),
    "mbl_at_observed_sr": minimum_backtest_length(target_sharpe=excess_sharpe, n_trials=N_TRIALS),
}
current_sample_months = len(net_ret) * 2

# =============================================================================
# Pretty print
# =============================================================================
def hr(title):
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}")

hr("v6.5 STATISTICAL INFERENCE — APPLIED TO monthly_returns.csv")
print(f"""
Sample:               {metrics['n_observations']} bi-monthly observations ({current_sample_months} months)
Date range:           {metrics['sample_start']} to {metrics['sample_end']}
Mean RFR (ann):       {rfr_mean_annual:+.2%}
n_trials assumed:     {N_TRIALS}  (multiplicity correction for Deflated SR + MBL)
""")

hr("Headline Metrics (Net of Transaction Costs)")
print(f"""
Ann. Return:          {metrics['ann_return']:>+10.2%}
Ann. Volatility:      {metrics['ann_volatility']:>+10.2%}
Raw Sharpe:           {metrics['raw_sharpe']:>+10.3f}
Excess Sharpe:        {metrics['excess_sharpe']:>+10.3f}   (net of RFR)
Sortino:              {metrics['sortino_ratio']:>+10.3f}
Calmar:               {metrics['calmar_ratio']:>+10.3f}
Max Drawdown:         {metrics['max_drawdown']:>+10.2%}
Drawdown duration:    {metrics['drawdown_duration']:>10d} months
Skewness:             {metrics['skewness']:>+10.3f}
Excess Kurtosis:      {metrics['excess_kurtosis']:>+10.3f}
99% Historical VaR:   {metrics['historical_var_99']:>+10.2%}
99% Expected Short:   {metrics['expected_shortfall_99']:>+10.2%}
Hit rate:             {metrics['hit_rate']:>+10.1%}
Best 2-month return:  {metrics['best_period']:>+10.2%}
Worst 2-month return: {metrics['worst_period']:>+10.2%}
""")

hr("§5.1 Statistical Inference — Tier 1")
print(f"""
Circular-Block Bootstrap 95% CI for Excess Sharpe
  (Politis-Romano 1994, block_size = {BLOCK_SIZE} bi-months, n = {N_BOOTSTRAP})
  Bootstrap mean:     {bootstrap['mean']:+.3f}
  Bootstrap std:      {bootstrap['std']:+.3f}
  95% CI:             [{bootstrap['low']:+.3f}, {bootstrap['high']:+.3f}]

Deflated Sharpe Ratio (Bailey-Lopez de Prado 2014)
  n_trials = {N_TRIALS}
  Noise threshold SR: {dsr['threshold_sr']:+.3f}   (expected max SR under the null)
  Prob(true SR > 0 | trials): {dsr['deflated_sharpe']:.3f}
  Interpretation: {'significant at 95%' if (dsr['deflated_sharpe'] or 0) > 0.95 else 'NOT significant at 95% — honest disclosure'}

Probabilistic Sharpe Ratio — P(true SR > threshold)
  threshold = 0.0:    {psr[0.0]:.3f}
  threshold = 0.5:    {psr[0.5]:.3f}
  threshold = 1.0:    {psr[1.0]:.3f}

Minimum Backtest Length (Bailey-Borwein-LdP-Zhu 2017)
  To prove SR = 0.5 at 95%:              {mbl['mbl_target_0.5']:>6.0f} months
  To prove SR = 1.0 at 95%:              {mbl['mbl_target_1.0']:>6.0f} months
  To prove observed SR ({excess_sharpe:+.3f}) at 95%: {mbl['mbl_at_observed_sr']:>6.0f} months
  Current sample length:                 {current_sample_months:>6d} months
""")

# =============================================================================
# Save results for the report
# =============================================================================
output_row = {
    **metrics,
    "bootstrap_mean_sharpe": bootstrap["mean"],
    "bootstrap_ci_low":      bootstrap["low"],
    "bootstrap_ci_high":     bootstrap["high"],
    "deflated_sharpe_prob":  dsr["deflated_sharpe"],
    "deflated_threshold_sr": dsr["threshold_sr"],
    **{f"psr_vs_{t}": psr[t] for t in PSR_THRESHOLDS},
    **mbl,
    "sample_length_months":  current_sample_months,
    "n_trials_assumed":      N_TRIALS,
}

# Save with filename that reflects n_trials (so n=15 and n=3 runs don't overwrite)
output_path = os.path.join(MY_OUTPUT, f"statistical_inference_n{N_TRIALS}.csv")
pd.Series(output_row).to_csv(output_path, header=["value"])
print(f"\nSaved → {output_path}")
