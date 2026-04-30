"""Static vs dynamic head-to-head comparison (PLAN §10.3).

Four variants:
    (a) Static baseline (``cfg.factors.base_weights`` — 50/50 mom + val)
    (b) VIX-only tilting (γ=0, λ as tuned)
    (c) Dispersion-only scaling (γ as tuned, λ=0)
    (d) Combined (full dynamic model)

Output: ``comparison_results.parquet``.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from analytics.performance import (
    information_ratio,
    max_drawdown,
    sharpe_ratio,
)
from engine.backtest import BacktestEngine
from engine.config import Config, load_config
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine

logger = logging.getLogger(__name__)


def _build_engine(cfg: Config) -> BacktestEngine:
    return BacktestEngine(
        cfg=cfg,
        data_loader=DataLoader(cfg),
        factor_engine=FactorEngine(cfg),
        zscore_engine=ZScoreEngine(cfg),
        portfolio_engine=PortfolioEngine(cfg),
        cost_model=CostModel(cfg),
    )


def _run_variant(variant: str, start: date, end: date) -> dict:
    cfg = load_config()
    if variant == "static":
        # Baseline — set gamma=0 and lambda=0 so dynamic == static effectively
        cfg.dynamic_weights.gamma = 0.0
        for r in cfg.dynamic_weights.regime_tilts:
            for k in cfg.dynamic_weights.regime_tilts[r]:
                cfg.dynamic_weights.regime_tilts[r][k] = 0.0
        strategy = Strategy.STATIC
    elif variant == "vix_only":
        cfg.dynamic_weights.gamma = 0.0
        strategy = Strategy.DYNAMIC_GRID
    elif variant == "dispersion_only":
        for r in cfg.dynamic_weights.regime_tilts:
            for k in cfg.dynamic_weights.regime_tilts[r]:
                cfg.dynamic_weights.regime_tilts[r][k] = 0.0
        strategy = Strategy.DYNAMIC_GRID
    elif variant == "combined":
        strategy = Strategy.DYNAMIC_GRID
    else:
        raise ValueError(f"Unknown variant: {variant}")

    engine = _build_engine(cfg)
    result = engine.run(start=start, end=end, strategies_to_run=(strategy,))
    col = "static_net_20bp" if strategy == Strategy.STATIC else "dynamic_net_20bp"
    returns = result.returns[col].dropna() if col in result.returns.columns else pd.Series(dtype=float)
    bench = result.returns.get("benchmark_ew", pd.Series(dtype=float)).dropna()
    return {
        "variant": variant,
        "sharpe_net": sharpe_ratio(returns, 0.0),
        "max_dd": max_drawdown(returns),
        "info_ratio": information_ratio(returns, bench),
        "mean_monthly_return": float(returns.mean()) if len(returns) else 0.0,
        "volatility_annual": float(returns.std() * (12 ** 0.5)) if len(returns) else 0.0,
    }


def run_static_vs_dynamic(
    cfg: Config,
    start: date,
    end: date,
    out_dir: str | Path = "output",
) -> pd.DataFrame:
    rows = []
    for v in ["static", "vix_only", "dispersion_only", "combined"]:
        try:
            rows.append(_run_variant(v, start, end))
            logger.info("Variant %s done: Sharpe=%.3f", v, rows[-1]["sharpe_net"])
        except Exception as exc:
            logger.error("Variant %s failed: %s", v, exc)
    df = pd.DataFrame(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(Path(out_dir) / "comparison_results.parquet", index=False)
    return df


__all__ = ["run_static_vs_dynamic"]
