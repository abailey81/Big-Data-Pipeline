"""Regime-conditional performance decomposition (PLAN §7.6).

Produces ``output/regime_performance.parquet`` — one row per (regime × strategy)
containing the full metric suite (Sharpe, Sortino, IR, Max-DD, hit-rate,
annualised return, annualised vol, turnover) computed on the subset of months
the VIX regime classifier assigned to each label.

This is what validates — or invalidates — the Vayanos-Woolley regime-
conditional flow-cycle hypothesis (PLAN §2.1): dynamic weighting is expected
to earn its keep primarily in the high-VIX regime.  If regime-conditional
Sharpe does not exceed the static baseline in the high-VIX regime, the
dynamic-weighting narrative is weaker and must be reported honestly (§15 P7).

Schema
------
    regime: "low" | "normal" | "high"
    strategy: "dynamic" | "static" | "bandit"
    n_months: int
    ann_return: float
    ann_vol: float
    sharpe: float
    sortino: float
    max_dd: float
    hit_rate: float
    mean_turnover: float  (if exposure_log available)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from analytics.performance import (
    annualised_return,
    annualised_volatility,
    max_drawdown,
    monthly_hit_rate,
    sharpe_ratio,
    sortino_ratio,
)

logger = logging.getLogger(__name__)


STRATEGY_COLUMNS = {
    "dynamic": "dynamic_net_20bp",
    "static": "static_net_20bp",
    "bandit": "bandit_net_20bp",
    "hrp": "hrp_net_20bp",
}


def run_regime_performance(out_dir: str | Path = "output") -> pd.DataFrame:
    """Compute per-regime metrics from the existing engine outputs."""
    out_dir = Path(out_dir)
    returns_df = pd.read_parquet(out_dir / "portfolio_returns.parquet")
    regime_df = pd.read_parquet(out_dir / "regime_log.parquet")
    exposure_df = None
    exp_path = out_dir / "exposure_log.parquet"
    if exp_path.exists():
        exposure_df = pd.read_parquet(exp_path)
        exposure_df["date"] = pd.to_datetime(exposure_df["date"])
    returns_df["date"] = pd.to_datetime(returns_df["date"])
    regime_df["date"] = pd.to_datetime(regime_df["date"])

    # Join regime onto returns — regime is labelled at the start-of-period
    # rebalance date, so an ``asof`` match on ``date`` lines each return with
    # the regime that was in effect at the rebalance it came from.  Prefer the
    # HMM label when populated (PLAN §5.6) and fall back to the percentile
    # label otherwise — "regime_hmm" exists but is all-None when the HMM
    # classifier was not enabled.
    if (
        "regime_hmm" in regime_df.columns
        and regime_df["regime_hmm"].notna().any()
    ):
        regime_col = "regime_hmm"
    else:
        regime_col = "regime_pct"
    joined = pd.merge_asof(
        returns_df.sort_values("date"),
        regime_df[["date", regime_col]].sort_values("date"),
        on="date",
        direction="backward",
    )
    joined = joined.rename(columns={regime_col: "regime"})
    joined["regime"] = joined["regime"].astype(str)

    rows: list[dict] = []
    for regime_value, grp in joined.groupby("regime"):
        for strategy_label, col in STRATEGY_COLUMNS.items():
            if col not in grp.columns:
                continue
            series = pd.to_numeric(grp[col], errors="coerce").dropna()
            if len(series) == 0:
                continue
            mean_to = float("nan")
            if exposure_df is not None and strategy_label == "dynamic":
                # exposure_log is the dynamic-grid canonical book
                exp_mask = exposure_df["date"].isin(grp["date"])
                if "turnover_1way" in exposure_df.columns:
                    mean_to = float(exposure_df.loc[exp_mask, "turnover_1way"].mean())
            rows.append({
                "regime": regime_value,
                "strategy": strategy_label,
                "n_months": int(len(series)),
                "ann_return": float(annualised_return(series)),
                "ann_vol": float(annualised_volatility(series)),
                "sharpe": float(sharpe_ratio(series, 0.0)),
                "sortino": float(sortino_ratio(series, 0.0)),
                "max_dd": float(max_drawdown(series)),
                "hit_rate": float(monthly_hit_rate(series)),
                "mean_turnover": mean_to,
            })
    df = pd.DataFrame(rows)
    out_path = out_dir / "regime_performance.parquet"
    df.to_parquet(out_path, index=False)
    logger.info("Wrote %s (%d rows)", out_path, len(df))
    return df


__all__ = ["run_regime_performance"]
