"""
backtest/engine.py -- v4 Alpha Engine: 4-factor sector-neutral strategy.

Monthly rebalance loop with:
  - Multi-horizon residual momentum (45%)
  - PIT-safe value from _hist ratios (20%)
  - Asymmetric quality gate (20%)
  - Sentiment delta conviction modifier (15%)
  - Sector-neutral construction (per-sector selection)
  - Max weight 5% enforced THREE TIMES
  - Skip-rebalance still records P&L for held positions
  - VaR scaling, beta check, gross leverage cap
"""

import pandas as pd
import numpy as np
from scipy import stats as scipy_stats

from data_loader import DataLoader
from factors.momentum import compute_multi_horizon_momentum
from factors.value import compute_value
from factors.quality_gate import apply_quality_gate
from factors.sentiment_modifier import compute_sentiment_conviction
from portfolio.sector_neutral_constructor import construct_sector_neutral_portfolio
from portfolio.risk_manager import (
    var_position_scale,
    enforce_max_weight,
    check_beta_and_adjust,
    check_gross_leverage,
    final_neutrality_projection,
)
from backtest.transaction_costs import compute_transaction_cost


def run_backtest(config: dict, data_loader: DataLoader) -> dict:
    """Run the full v4 backtest and return results dict."""

    # ---- Load data (cached in DataLoader) ----
    print("[engine] Loading data...")
    daily_ret_wide = data_loader.get_daily_returns_wide()
    price_matrix = data_loader.get_price_matrix()
    company_static = data_loader.get_company_static()
    sectors = company_static.set_index("symbol")["gics_sector"]
    benchmark = data_loader.get_benchmark()
    rf_series = data_loader.get_risk_free_rate()

    # S&P 500 benchmark returns
    bm_df = benchmark[benchmark["symbol"] == "^GSPC"].set_index("date")["adj close"]
    bm_ret = bm_df.pct_change().dropna()

    # News sentiment
    news_sent = data_loader.get_news_sentiment()

    # ---- Config ----
    start = pd.Timestamp(config["backtest"]["start_date"])
    end = pd.Timestamp(config["backtest"]["end_date"])
    mom_cfg = config["factors"]["momentum"]
    val_cfg = config["factors"]["value"]
    sent_cfg = config["factors"]["sentiment"]
    port_cfg = config["portfolio"]
    risk_cfg = config["risk"]
    tc_cfg = config["transaction_costs"]
    ntb_cfg = config.get("no_trade_band", {})

    mom_weight = config["factors"]["momentum"]["weight"]
    val_weight = config["factors"]["value"]["weight"]
    pit_lag = val_cfg.get("pit_lag_days", 45)
    max_w = port_cfg["max_weight"]

    # ---- Build rebalance dates (frequency-aware) ----
    all_dates = daily_ret_wide.index
    bt_dates = all_dates[(all_dates >= start) & (all_dates <= end)]
    if len(bt_dates) == 0:
        print("[engine] No data in backtest window!")
        return {}

    rebal_freq = config.get("backtest", {}).get("rebalance_freq", "M")
    monthly_rebal = pd.DatetimeIndex(
        bt_dates.to_series().groupby(bt_dates.to_period("M")).last().values
    )
    if rebal_freq == "2M":
        rebal_dates = monthly_rebal[::2]
    elif rebal_freq == "Q":
        rebal_dates = monthly_rebal[::3]
    else:
        rebal_dates = monthly_rebal
    print(f"[engine] {len(rebal_dates)} rebalance dates: "
          f"{rebal_dates[0].date()} to {rebal_dates[-1].date()}")

    # ---- Backtest loop ----
    prev_weights = pd.Series(dtype=float)
    all_daily_port_returns = []

    monthly_gross_list = []
    monthly_net_list = []
    turnovers = []
    var_scales_list = []
    weights_history = []
    rebal_dates_out = []
    long_counts = []
    short_counts = []
    rolling_ic = {"momentum": [], "value": [], "dates": []}
    prev_mom_raw = None
    prev_val_raw = None

    for i, rebal_date in enumerate(rebal_dates):
        rebal_ts = pd.Timestamp(rebal_date)

        # ---- FIX #4: ALWAYS compute P&L for held positions ----
        if len(prev_weights) > 0 and i > 0:
            prev_rebal_ts = pd.Timestamp(rebal_dates[i - 1])
            period_mask = (
                (daily_ret_wide.index > prev_rebal_ts)
                & (daily_ret_wide.index <= rebal_ts)
            )
            period_ret = daily_ret_wide.loc[period_mask]
            if len(period_ret) > 0:
                w_aligned = prev_weights.reindex(period_ret.columns, fill_value=0)
                daily_port = period_ret.fillna(0) @ w_aligned
                gross_m = (1 + daily_port).prod() - 1
                all_daily_port_returns.extend(daily_port.values.tolist())
            else:
                gross_m = 0.0
                daily_port = pd.Series(dtype=float)
        else:
            gross_m = None
            daily_port = pd.Series(dtype=float)

        print(f"  [{i+1}/{len(rebal_dates)}] {rebal_ts.date()}", end="")

        # ---- 1. Compute momentum ----
        momentum = compute_multi_horizon_momentum(
            price_matrix, daily_ret_wide, bm_ret, sectors, rebal_ts, mom_cfg
        )

        # ---- 2. Compute value (PIT-lagged by data_loader) ----
        cr_wide = data_loader.get_company_ratios_wide(rebal_ts, pit_lag)
        value = compute_value(cr_wide, sectors, val_cfg)

        # ---- Rolling IC ----
        if i > 0 and prev_mom_raw is not None:
            _compute_rolling_ic(
                rolling_ic, rebal_dates, i, daily_ret_wide,
                prev_mom_raw, prev_val_raw, rebal_ts
            )
        prev_mom_raw = momentum.copy()
        prev_val_raw = value.copy()

        # ---- 3. Composite alpha (momentum + value only; quality/sentiment are modifiers) ----
        common = momentum.dropna().index.intersection(value.dropna().index)
        if len(common) < 20:
            print(f"  WARN: Only {len(common)} stocks, skipping rebalance")
            # FIX #4: Record P&L even on skip
            if gross_m is not None:
                monthly_gross_list.append((rebal_ts, gross_m))
                monthly_net_list.append((rebal_ts, gross_m))  # no TC
                rebal_dates_out.append(rebal_ts)
            continue

        # Normalise weights so they sum to 1.0
        total_factor_w = mom_weight + val_weight
        alpha = (
            (mom_weight / total_factor_w) * momentum.reindex(common).fillna(0)
            + (val_weight / total_factor_w) * value.reindex(common).fillna(0)
        )

        # ---- 4. Quality gate ----
        fund_wide = data_loader.get_fundamentals_wide(rebal_ts, pit_lag)
        # Get company_ratios from 1 year ago for trend comparison
        cr_wide_1y = data_loader.get_company_ratios_wide(
            rebal_ts - pd.DateOffset(years=1), pit_lag
        )
        quality_long, quality_short = apply_quality_gate(
            cr_wide, cr_wide_1y, sectors
        )

        # ---- 5. Sentiment conviction modifier ----
        sentiment_conv = compute_sentiment_conviction(news_sent, rebal_ts, sent_cfg)

        # ---- 6. Sector-neutral portfolio construction ----
        # Inject NTB config into port_cfg for the constructor
        port_cfg_with_ntb = dict(port_cfg)
        port_cfg_with_ntb["_ntb_config"] = ntb_cfg

        new_weights = construct_sector_neutral_portfolio(
            alpha, quality_long, quality_short, sentiment_conv,
            sectors, daily_ret_wide, rebal_ts, port_cfg_with_ntb,
            prev_weights,
        )

        if len(new_weights) == 0:
            print(f"  WARN: Empty portfolio")
            if gross_m is not None:
                monthly_gross_list.append((rebal_ts, gross_m))
                monthly_net_list.append((rebal_ts, gross_m))
                rebal_dates_out.append(rebal_ts)
            continue

        # ---- 7. FINAL risk constraints (ABSOLUTE, no exceptions) ----
        # Pass 1: enforce max weight after construction
        new_weights = enforce_max_weight(new_weights, max_w)

        # Gross leverage check
        new_weights = check_gross_leverage(
            new_weights, risk_cfg.get("max_gross_leverage", 2.5)
        )

        # VaR scaling
        cum_ret = pd.Series(all_daily_port_returns)
        scale = var_position_scale(cum_ret, risk_cfg)
        var_scales_list.append(scale)
        new_weights = new_weights * scale

        # Pass 2: re-enforce after VaR scaling
        new_weights = enforce_max_weight(new_weights, max_w)

        # Beta check
        if risk_cfg.get("beta_check", True):
            new_weights = check_beta_and_adjust(
                new_weights, daily_ret_wide, bm_ret, rebal_ts,
                max_beta=risk_cfg.get("beta_max_abs", 0.15),
            )

        # Pass 3: re-enforce after beta adjustment
        new_weights = enforce_max_weight(new_weights, max_w)

        # Final gross leverage check
        new_weights = check_gross_leverage(
            new_weights, risk_cfg.get("max_gross_leverage", 2.5)
        )

        # ---- FINAL NEUTRALITY PROJECTION (LAST STEP) ----
        # Guarantees: net=0, sector_net=0, max_weight=5%, gross<=2.5x
        # This is the ONLY place where market-neutral is a hard constraint.
        benchmark = data_loader.get_benchmark()
        bm_close = benchmark[benchmark["symbol"] == "^GSPC"].set_index("date")["adj close"]
        bm_ret_for_beta = bm_close.pct_change().dropna()
        new_weights = final_neutrality_projection(
            new_weights, sectors, daily_ret_wide, bm_ret_for_beta, rebal_ts,
            max_weight=port_cfg["max_weight"],
            max_net=0.02,
            max_sector_net=port_cfg.get("max_sector_imbalance", 0.02),
            max_beta=risk_cfg.get("beta_max_abs", 0.10),
            max_gross=risk_cfg.get("max_gross_leverage", 2.5),
        )

        # ---- Stats ----
        n_long = (new_weights > 0).sum()
        n_short = (new_weights < 0).sum()
        long_counts.append(n_long)
        short_counts.append(n_short)

        # ---- Turnover ----
        turnover = _compute_drift_turnover(
            prev_weights, new_weights, daily_ret_wide, rebal_dates, i
        )
        turnovers.append(turnover)

        # ---- Transaction costs ----
        if i > 0:
            prev_rebal = pd.Timestamp(rebal_dates[i - 1])
            days = len(daily_ret_wide.loc[
                (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= rebal_ts)
            ])
        else:
            days = 21

        # Separate long/short turnover
        all_syms = prev_weights.index.union(new_weights.index)
        w_prev = prev_weights.reindex(all_syms, fill_value=0)
        w_new = new_weights.reindex(all_syms, fill_value=0)
        delta = w_new - w_prev

        turnover_long = delta[delta > 0].sum()
        turnover_short = abs(delta[delta < 0].sum())
        short_notional = abs(new_weights[new_weights < 0].sum())

        tc = compute_transaction_cost(
            turnover_long=turnover_long,
            turnover_short=turnover_short,
            trading_bps=tc_cfg["trading_bps"],
            borrowing_bps=tc_cfg["borrowing_bps"],
            days_in_period=max(days, 1),
            short_notional=short_notional,
        )

        # ---- Record period return ----
        if gross_m is not None:
            net_m = gross_m - tc
            monthly_gross_list.append((rebal_ts, gross_m))
            monthly_net_list.append((rebal_ts, net_m))
        else:
            monthly_gross_list.append((rebal_ts, 0.0))
            monthly_net_list.append((rebal_ts, -tc))

        max_abs_w = new_weights.abs().max()
        gross_lev = new_weights.abs().sum()
        print(f"  L={n_long} S={n_short} maxW={max_abs_w:.3f} "
              f"gross={gross_lev:.2f} VaR_s={scale:.3f} TO={turnover:.3f}")

        prev_weights = new_weights.copy()
        weights_history.append(new_weights.copy())
        rebal_dates_out.append(rebal_ts)

    # ---- Build output ----
    if not monthly_gross_list:
        print("[engine] No returns generated!")
        return {}

    mg = pd.Series(dict(monthly_gross_list)).sort_index()
    mn = pd.Series(dict(monthly_net_list)).sort_index()

    # Benchmark monthly
    bench_monthly = bm_ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    bench_monthly = bench_monthly.reindex(mg.index, method="nearest")

    return {
        "monthly_gross": mg,
        "monthly_net": mn,
        "turnovers": turnovers,
        "var_scales": var_scales_list,
        "weights_history": weights_history,
        "rebalance_dates": rebal_dates_out,
        "benchmark_monthly": bench_monthly,
        "rf_series": rf_series,
        "rolling_ic": rolling_ic,
        "long_counts": long_counts,
        "short_counts": short_counts,
    }


# ========================================================================
# Helper functions
# ========================================================================

def _compute_drift_turnover(prev_weights, new_weights, daily_ret_wide, rebal_dates, idx):
    """Compute drift-adjusted two-way turnover."""
    if len(prev_weights) == 0 or idx == 0:
        return 0.5 * new_weights.abs().sum()

    prev_rebal = pd.Timestamp(rebal_dates[idx - 1])
    curr_rebal = pd.Timestamp(rebal_dates[idx])
    period = daily_ret_wide.loc[
        (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= curr_rebal)
    ]

    all_syms = prev_weights.index.union(new_weights.index)
    w_prev = prev_weights.reindex(all_syms, fill_value=0)
    w_new = new_weights.reindex(all_syms, fill_value=0)

    if len(period) == 0:
        return 0.5 * (w_new - w_prev).abs().sum()

    stock_ret = (1 + period.fillna(0)).prod() - 1
    r = stock_ret.reindex(all_syms, fill_value=0)
    w_drift = w_prev * (1 + r)
    drift_sum = w_drift.abs().sum()
    if drift_sum > 0:
        w_drift = w_drift / drift_sum * w_prev.abs().sum()
    else:
        w_drift = w_prev

    return 0.5 * (w_new - w_drift).abs().sum()


def _compute_rolling_ic(rolling_ic, rebal_dates, i, daily_ret_wide,
                         prev_mom, prev_val, current_date):
    """Compute Spearman rank IC for momentum and value factors."""
    prev_rebal = pd.Timestamp(rebal_dates[i - 1])
    period = daily_ret_wide.loc[
        (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= current_date)
    ]
    if len(period) == 0:
        return
    fwd_ret = (1 + period.fillna(0)).prod() - 1
    rolling_ic["dates"].append(current_date)
    for name, scores in [("momentum", prev_mom), ("value", prev_val)]:
        common = scores.dropna().index.intersection(fwd_ret.dropna().index)
        if len(common) < 10:
            rolling_ic[name].append(np.nan)
        else:
            rho, _ = scipy_stats.spearmanr(
                scores.reindex(common).values, fwd_ret.reindex(common).values
            )
            rolling_ic[name].append(rho)
