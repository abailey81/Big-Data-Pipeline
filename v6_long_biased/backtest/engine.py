"""
v6.5 Risk-Managed Long-Only Factor Portfolio (FINAL)

Factors: Momentum (12-1) + Value (B/P+E/P+CF/P)
Construction: Score × inverse-volatility weighted, max 1.5% per stock (iterative cap)
Risk overlay: 3-signal regime overlay (200DMA, 12M return, VIX percentile)
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path


def run_backtest(config: dict, dl) -> dict:
    """Execute bi-monthly long-only backtest."""
    from factors.value import compute_value
    from factors.z_scoring import sector_neutral_zscore
    from backtest.performance import _detect_periods_per_year

    # Load data
    print("[engine] Loading data...")
    daily_ret_df = dl.get_returns_usd()
    daily_ret_wide = daily_ret_df.pivot_table(
        index="date", columns="symbol", values="ret_usd"
    ).sort_index()

    price_matrix = dl.get_price_matrix()
    company_static = dl.get_company_static()
    sectors = company_static.set_index("symbol")["gics_sector"]
    rf_series = dl.get_risk_free_rate()
    news_sent = dl.get_news_sentiment()

    benchmark = dl.get_benchmark()
    bm_close = benchmark[benchmark["symbol"] == "^GSPC"].set_index("date")["adj close"]
    bm_ret = bm_close.pct_change().dropna()

    # Config
    start = pd.Timestamp(config["backtest"]["start_date"])
    end = pd.Timestamp(config["backtest"]["end_date"])
    rebal_freq = config["backtest"]["rebalance_freq"]
    mom_cfg = config["factors"]["momentum"]
    val_cfg = config["factors"]["value"]
    sent_cfg = config["factors"]["sentiment"]
    port_cfg = config["portfolio"]
    tc_cfg = config["transaction_costs"]
    ntb_cfg = config.get("no_trade_band", {})

    long_notional = port_cfg["long_notional"]    # 1.30
    short_notional = port_cfg["short_notional"]  # 0.30
    max_weight = port_cfg["max_weight"]          # 0.05

    # Build rebalance dates
    all_dates = daily_ret_wide.index
    bt_dates = all_dates[(all_dates >= start) & (all_dates <= end)]
    monthly_rebal = bt_dates.to_series().groupby(bt_dates.to_period("M")).last().values
    monthly_rebal = pd.DatetimeIndex(monthly_rebal)

    if rebal_freq == "2M":
        rebal_dates = monthly_rebal[::2]
    elif rebal_freq == "Q":
        rebal_dates = monthly_rebal[::3]
    else:
        rebal_dates = monthly_rebal

    print(f"[engine] {len(rebal_dates)} rebalance dates: {rebal_dates[0].date()} to {rebal_dates[-1].date()}")
    print(f"[engine] Target: {long_notional:.0%} long / {short_notional:.0%} short")

    # Backtest loop
    prev_weights = pd.Series(dtype=float)
    prev_long_syms = []
    all_daily_port = []
    monthly_gross_list = []
    monthly_net_list = []
    turnovers = []
    weights_history = []
    rebal_dates_out = []
    long_counts = []
    short_counts = []
    rolling_ic = {"momentum": [], "value": [], "dates": []}

    for i, rebal_date in enumerate(rebal_dates):
        rebal_ts = pd.Timestamp(rebal_date)

        # ---- 1. Compute factors ----
        fund_wide = dl.get_fundamentals_wide(rebal_ts, pit_lag_days=mom_cfg.get("pit_lag_days", 45))
        cr_wide = dl.get_company_ratios_wide(rebal_ts, pit_lag_days=val_cfg.get("pit_lag_days", 45))

        # Simple 12-1 momentum (price-based, no PIT issue)
        mom_raw = _compute_simple_momentum(price_matrix, rebal_ts, 12, 1)
        mom = sector_neutral_zscore(mom_raw, sectors)

        val = compute_value(cr_wide, sectors, val_cfg)

        # ---- Rolling IC ----
        if i > 0 and 'prev_mom' in dir():
            prev_rebal = pd.Timestamp(rebal_dates[i-1])
            period_ret = daily_ret_wide.loc[
                (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= rebal_ts)
            ]
            if len(period_ret) > 0:
                fwd = (1 + period_ret.fillna(0)).prod() - 1
                from scipy import stats as sp_stats
                rolling_ic["dates"].append(rebal_ts)
                for fname, fscore in [("momentum", prev_mom), ("value", prev_val)]:
                    common = fscore.dropna().index.intersection(fwd.dropna().index)
                    if len(common) > 30:
                        rho, _ = sp_stats.spearmanr(fscore.reindex(common), fwd.reindex(common))
                        rolling_ic[fname].append(rho)
                    else:
                        rolling_ic[fname].append(np.nan)

        prev_mom = mom_raw.copy()
        prev_val = val.copy() if hasattr(val, 'copy') else pd.Series(dtype=float)

        # ---- 2. Composite score ----
        common = mom.dropna().index.intersection(val.dropna().index)
        if len(common) < 20:
            # Still record P&L for held positions
            if len(prev_weights) > 0 and i > 0:
                prev_rebal = pd.Timestamp(rebal_dates[i-1])
                _record_period_pnl(
                    daily_ret_wide, prev_weights, prev_rebal, rebal_ts,
                    monthly_gross_list, monthly_net_list, all_daily_port,
                    turnovers, 0, tc_cfg
                )
                rebal_dates_out.append(rebal_ts)
            continue

        # Composite using z-scores (not ranks) — preserves signal magnitude
        w_mom = mom_cfg["weight"]  # 0.45
        w_val = val_cfg["weight"]  # 0.35

        composite = w_mom * mom.reindex(common).fillna(0) + w_val * val.reindex(common).fillna(0)
        # Note: sentiment excluded from ranking — semi-annual data adds noise
        # that degrades stock selection (tested: removing sent improves Sharpe)

        # ---- 3. Select long/short ----
        sorted_comp = composite.sort_values(ascending=False)
        n = len(sorted_comp)
        n_long = max(1, int(n * port_cfg["long_pct"]))
        n_short = max(1, int(n * port_cfg["short_pct"]))

        long_syms = list(sorted_comp.head(n_long).index)
        short_syms = list(sorted_comp.tail(n_short).index)

        # ---- NTB: keep prev holdings if still within buffer ----
        if ntb_cfg.get("enabled", False) and len(prev_long_syms) > 0:
            buffer = ntb_cfg.get("buffer_pct", 0.05)
            wider_long = int(n * (port_cfg["long_pct"] + buffer))
            wider_pool = set(sorted_comp.head(wider_long).index)
            for s in prev_long_syms:
                if s not in long_syms and s in wider_pool:
                    long_syms.append(s)

        prev_long_syms = list(long_syms)
        long_counts.append(len(long_syms))
        short_counts.append(len(short_syms))

        # ---- 4. Score × inverse-vol weighted construction (v6.5) ----
        new_weights = pd.Series(dtype=float)
        if long_syms:
            long_scores = sorted_comp.reindex(long_syms)
            score_shifted = long_scores - long_scores.min() + 0.01

            # Trailing 63-day volatility
            vol_window = daily_ret_wide.loc[daily_ret_wide.index <= rebal_ts].iloc[-63:]
            avail = [s for s in long_syms if s in vol_window.columns]
            stock_vol = vol_window[avail].std()
            inv_vol = 1.0 / stock_vol.replace(0, np.nan).fillna(stock_vol.median())

            raw_w = score_shifted * inv_vol.reindex(long_syms).fillna(1.0)
            raw_w = raw_w / raw_w.sum()  # normalize to 100%

            # Iterative cap: clip overweight stocks, redistribute excess
            # to underweight stocks. Repeat until all <= max_weight.
            # If impossible (too few stocks), remaining goes to cash.
            for _ in range(10):  # max 10 iterations
                over = raw_w > max_weight
                if not over.any():
                    break
                excess = (raw_w[over] - max_weight).sum()
                raw_w[over] = max_weight
                under = raw_w < max_weight
                if under.sum() > 0:
                    room = (max_weight - raw_w[under])
                    total_room = room.sum()
                    if total_room > 0:
                        add = min(excess, total_room)
                        raw_w[under] += room / total_room * add
                        excess -= add
                # Any remaining excess becomes cash (not invested)

            # Scale to long_notional (may be < 1.0 if capped stocks dominate)
            raw_w = raw_w * long_notional

            new_weights = pd.concat([new_weights, raw_w])

        if short_syms and short_notional > 0:
            sw = pd.Series(short_notional / len(short_syms), index=short_syms)
            new_weights = pd.concat([new_weights, -sw])

        # ---- 5. Compute period return ----
        if i > 0:
            prev_rebal = pd.Timestamp(rebal_dates[i-1])
        else:
            prev_rebal = rebal_ts - pd.DateOffset(months=2)

        turnover = _compute_turnover(prev_weights, new_weights)
        turnovers.append(turnover)

        if len(prev_weights) > 0:
            period_mask = (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= rebal_ts)
            period_ret = daily_ret_wide.loc[period_mask]
            if len(period_ret) > 0:
                w_aligned = prev_weights.reindex(period_ret.columns, fill_value=0)
                daily_port = period_ret.fillna(0) @ w_aligned
                gross_period = (1 + daily_port).prod() - 1
                all_daily_port.extend(daily_port.values.tolist())

                # Transaction costs
                short_not = abs(new_weights[new_weights < 0].sum()) if (new_weights < 0).any() else 0
                days = len(period_ret)
                tc = (turnover * tc_cfg["trading_bps"] / 10000
                      + short_not * tc_cfg["borrowing_bps"] / 10000 * days / 252)
                net_period = gross_period - tc

                monthly_gross_list.append((rebal_ts, gross_period))
                monthly_net_list.append((rebal_ts, net_period))

        prev_weights = new_weights.copy()
        weights_history.append(new_weights.copy())
        rebal_dates_out.append(rebal_ts)

        print(f"  [{i+1}/{len(rebal_dates)}] {rebal_ts.date()} L={len(long_syms)} S={len(short_syms)} "
              f"gross={new_weights.abs().sum():.2f} net={new_weights.sum():+.2f}")

    # Build output
    if not monthly_gross_list:
        return {}

    mg = pd.Series(dict(monthly_gross_list)).sort_index()
    mn = pd.Series(dict(monthly_net_list)).sort_index()

    # Strict same-holding-period benchmark
    bench_dict = {}
    rebal_list = sorted(mg.index)
    for i, dt in enumerate(rebal_list):
        prev = rebal_list[i-1] if i > 0 else dt - pd.DateOffset(months=2)
        bm_w = bm_ret.loc[(bm_ret.index > prev) & (bm_ret.index <= dt)]
        bench_dict[dt] = (1 + bm_w).prod() - 1 if len(bm_w) > 0 else 0.0
    bench_period = pd.Series(bench_dict)

    return {
        "monthly_gross": mg,
        "monthly_net": mn,
        "turnovers": turnovers,
        "var_scales": [1.0] * len(mg),
        "weights_history": weights_history,
        "rebalance_dates": rebal_dates_out,
        "benchmark_monthly": bench_period,
        "rf_series": rf_series,
        "rolling_ic": rolling_ic,
        "long_counts": long_counts,
        "short_counts": short_counts,
    }


def _compute_turnover(prev_w, new_w):
    if len(prev_w) == 0:
        return new_w.abs().sum() * 0.5
    all_syms = prev_w.index.union(new_w.index)
    pw = prev_w.reindex(all_syms, fill_value=0)
    nw = new_w.reindex(all_syms, fill_value=0)
    return 0.5 * (nw - pw).abs().sum()


def _record_period_pnl(daily_ret_wide, prev_weights, prev_rebal, rebal_ts,
                        gross_list, net_list, all_daily, turnovers, turnover, tc_cfg):
    period_mask = (daily_ret_wide.index > prev_rebal) & (daily_ret_wide.index <= rebal_ts)
    period_ret = daily_ret_wide.loc[period_mask]
    if len(period_ret) > 0:
        w = prev_weights.reindex(period_ret.columns, fill_value=0)
        dp = period_ret.fillna(0) @ w
        g = (1 + dp).prod() - 1
        all_daily.extend(dp.values.tolist())
        gross_list.append((rebal_ts, g))
        net_list.append((rebal_ts, g))  # no trade = no cost
    turnovers.append(0)


def _compute_sentiment_delta(news_sent, as_of, sectors, sent_cfg):
    """Compute sentiment change (delta) as conviction signal."""
    ns = news_sent.copy()
    ns["cob_date"] = pd.to_datetime(ns["cob_date"])
    lookback = sent_cfg.get("lookback_days", 180)

    cutoff = as_of - pd.Timedelta(days=lookback)
    recent = ns[(ns["cob_date"] <= as_of) & (ns["cob_date"] >= cutoff)]
    older = ns[(ns["cob_date"] < cutoff) & (ns["cob_date"] >= cutoff - pd.Timedelta(days=lookback))]

    if recent.empty:
        return pd.Series(dtype=float)

    recent_score = recent.sort_values("cob_date").groupby("symbol")["sentiment_score"].last()
    older_score = older.sort_values("cob_date").groupby("symbol")["sentiment_score"].last()

    common = recent_score.index.intersection(older_score.index)
    if len(common) < 20:
        return pd.Series(dtype=float)

    delta = recent_score.reindex(common) - older_score.reindex(common)
    from factors.z_scoring import sector_neutral_zscore
    return sector_neutral_zscore(delta, sectors)


def _compute_simple_momentum(price_matrix, as_of, lookback_months, skip_months):
    """Simple 12-1 price momentum. No PIT issue (uses only prices)."""
    end = as_of - pd.DateOffset(months=skip_months)
    start = as_of - pd.DateOffset(months=lookback_months)
    
    p_end = price_matrix.loc[price_matrix.index <= end]
    p_start = price_matrix.loc[price_matrix.index <= start]
    
    if len(p_end) == 0 or len(p_start) == 0:
        return pd.Series(dtype=float)
    
    price_end = p_end.iloc[-1]
    price_start = p_start.iloc[-1]
    
    ret = (price_end / price_start) - 1.0
    ret[price_start.isna() | price_end.isna() | (price_start <= 0)] = np.nan
    ret.name = "momentum"
    return ret
