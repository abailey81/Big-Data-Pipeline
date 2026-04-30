"""Production-grade benchmark suite (PLAN §7.13 + Viz Ref §1.6).

The Viz & Metrics Reference mandates exactly one benchmark column in the
headline 4-column performance table: **"Benchmark EW (Universe)"** — an
equal-weight monthly-rebalanced portfolio over the *same investable universe*
used by the strategy.  This is the only apples-to-apples comparator.

On top of that, we compute three *supplementary* benchmarks for the report's
empirical section:

1. **EW Universe** (headline)  — equal-weight rebalanced monthly over the
   liquidity-filtered universe.  USD-converted.

2. **S&P 500 (^GSPC)**  — pulled from CW1 ``benchmark_index`` table.  Market
   beta reference; appears in Fama-French regressions as Mkt-RF.

3. **Risk-free rate** (DGS3MO)  — CW1 ``risk_free_rate`` table.  Excess-return
   calculations and Sharpe/Sortino denominators.

4. **Cash + Market parity** — 50/50 blend of rf and S&P 500.  Serves as a
   conservative "passive allocator" reference in Section 6 fund pitch.

Tracking error & IR
-------------------
Tracking error τ = σ(strategy − EW_benchmark) (annualised).
Information Ratio = (R_strategy − R_benchmark) / τ.  Already implemented in
``analytics/performance.py`` but here we compute the full series.

References
----------
Viz Ref §1.4 (IR target > 0.5); Grinold & Kahn (2000) ch. 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from engine.config import Config
from engine.data_loader import DataLoader, PITContext, currency_to_fx_pair

logger = logging.getLogger(__name__)


# =============================================================================
# Equal-weight universe benchmark — THE canonical comparator
# =============================================================================
@dataclass
class EqualWeightBenchmark:
    """Monthly-rebalanced EW benchmark over the strategy's own universe.

    On each rebalance date we equal-weight across the liquidity-filtered
    universe (same as strategy) and hold until the next rebalance.  Returns
    are USD-converted via the same FX conversion formula as the strategy.

    The use of the *filtered* universe (not the full 678) is intentional —
    it ensures the benchmark represents the opportunity set actually
    available to the strategy, following Asness, Moskowitz & Pedersen (2013)
    best-practice for L/S evaluation.
    """

    cfg: Config
    data_loader: DataLoader

    # State
    _held_weights: pd.Series = None
    _held_symbols: list[str] = None

    def rebalance(self, universe_symbols: list[str]) -> pd.Series:
        """Equal-weight across ``universe_symbols``.  Long-only (1.0 gross)."""
        n = len(universe_symbols)
        if n == 0:
            return pd.Series(dtype=float)
        w = 1.0 / n
        self._held_symbols = list(universe_symbols)
        self._held_weights = pd.Series(w, index=universe_symbols)
        return self._held_weights

    def period_return(
        self, rb_date: date, next_rb_date: date, weights: Optional[pd.Series] = None
    ) -> float:
        """Realised USD-converted return between rb_date and next_rb_date."""
        w = weights if weights is not None else self._held_weights
        if w is None or len(w) == 0:
            return 0.0

        # Query prices in window [rb_date, next_rb_date] directly
        from sqlalchemy import text
        q = text(
            f"""
            SELECT cob_date, symbol, adj_close_price
            FROM {self.data_loader._schema}.daily_prices
            WHERE cob_date >= :start AND cob_date <= :end
              AND symbol = ANY(:syms) AND adj_close_price IS NOT NULL
            """
        )
        df = pd.read_sql(
            q, self.data_loader._engine,
            params={"start": rb_date, "end": next_rb_date, "syms": list(w.index)},
        )
        if df.empty:
            return 0.0
        wide = df.pivot_table(
            index="cob_date", columns="symbol", values="adj_close_price"
        ).sort_index()
        if len(wide) < 2:
            return 0.0

        # USD conversion: load FX prices for the same window
        fx = self.data_loader.load_fx(next_rb_date + timedelta(days=1), 90)
        # Per-stock local-return
        local_ret = (wide.iloc[-1] / wide.iloc[0] - 1).reindex(w.index, fill_value=0.0)

        # FX-adjust: R_usd = (1 + R_local) × (FX_end / FX_start) − 1
        usd_ret = local_ret.copy()
        univ = self.data_loader.load_universe(rb_date)
        for sym in w.index:
            ccy = univ.currency_map.get(sym, "USD")
            pair = currency_to_fx_pair(ccy)
            if pair is None or pair not in fx.columns:
                continue
            if pd.Timestamp(rb_date) in fx.index and pd.Timestamp(next_rb_date) in fx.index:
                fx_ratio = fx[pair].loc[:pd.Timestamp(next_rb_date)].iloc[-1] / \
                           fx[pair].loc[:pd.Timestamp(rb_date)].iloc[-1]
            else:
                # Nearest-available forward-fill
                fx_slice = fx[pair].ffill()
                try:
                    fx_ratio = fx_slice.asof(pd.Timestamp(next_rb_date)) / fx_slice.asof(pd.Timestamp(rb_date))
                except Exception:
                    fx_ratio = 1.0
            if pd.isna(fx_ratio) or fx_ratio <= 0:
                fx_ratio = 1.0
            usd_ret[sym] = (1 + local_ret[sym]) * fx_ratio - 1

        return float((w * usd_ret).sum())


# =============================================================================
# S&P 500 reference benchmark (CW1 benchmark_index.^GSPC)
# =============================================================================
@dataclass
class SPXBenchmark:
    """Market-beta reference ($SPX = ^GSPC) straight from CW1 benchmark_index."""

    cfg: Config
    data_loader: DataLoader

    def period_return(self, rb_date: date, next_rb_date: date) -> float:
        bench = self.data_loader.load_benchmark(next_rb_date + timedelta(days=1), 60, "^GSPC")
        if len(bench) < 2:
            return 0.0
        # Slice exactly [rb_date, next_rb_date]
        bench = bench.loc[pd.Timestamp(rb_date):pd.Timestamp(next_rb_date)]
        if len(bench) < 2:
            return 0.0
        return float(bench.iloc[-1] / bench.iloc[0] - 1)


# =============================================================================
# Cash + Market 50/50 blend
# =============================================================================
@dataclass
class CashMarketBlend:
    cfg: Config
    data_loader: DataLoader
    cash_weight: float = 0.5

    def period_return(self, rb_date: date, next_rb_date: date) -> float:
        rf_annual = self.data_loader.load_rf_rate(next_rb_date + timedelta(days=1))
        months = max(1, (pd.Timestamp(next_rb_date).to_period("M") - pd.Timestamp(rb_date).to_period("M")).n)
        rf_period = (1 + rf_annual) ** (months / 12) - 1
        mkt = SPXBenchmark(self.cfg, self.data_loader).period_return(rb_date, next_rb_date)
        return float(self.cash_weight * rf_period + (1 - self.cash_weight) * mkt)


# =============================================================================
# Tracking-error / active-return analytics
# =============================================================================
def tracking_error(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    annualisation: int = 12,
) -> float:
    df = pd.concat([strategy_returns, benchmark_returns], axis=1).dropna()
    if len(df) < 2:
        return 0.0
    active = df.iloc[:, 0] - df.iloc[:, 1]
    return float(active.std(ddof=1) * np.sqrt(annualisation))


def active_return(
    strategy_returns: pd.Series, benchmark_returns: pd.Series
) -> pd.Series:
    df = pd.concat([strategy_returns, benchmark_returns], axis=1).dropna()
    return df.iloc[:, 0] - df.iloc[:, 1]


__all__ = [
    "CashMarketBlend",
    "EqualWeightBenchmark",
    "SPXBenchmark",
    "active_return",
    "tracking_error",
]
