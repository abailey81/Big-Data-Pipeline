"""Composite risk scaler — HVaR → Volatility-target → Drawdown-control chain.

Implements the three-stage risk-scaling pipeline from PLAN §5.16–5.17 layered
on top of CW1 §3.5 HVaR-based position sizing.

Stage 1 — 99% Historical VaR scaler (CW1 Eq. 10)
------------------------------------------------
    position_scale = target_budget / |VaR_99|
where VaR_99 is the 1st-percentile daily loss over a 756-day rolling window.

Stage 2 — Conditional Volatility Targeting (§5.16, Moreira-Muir 2017)
---------------------------------------------------------------------
    σ̂_60 = √(252/60 · Σ_{s=t-60}^{t-1} (R_s − R̄)²)
    vol_scalar = target_annual / σ̂_60    (clipped to [0.3, 1.5])

Stage 3 — Drawdown-Control Overlay (§5.17, Korn-Korn-Kroisandt 2017)
--------------------------------------------------------------------
    DD_t = (NAV_t − peak_{12m,t}) / peak_{12m,t}
    dd_scalar = 1.0 if DD > −3%, 0.75 if DD ≥ −6%, 0.50 if DD < −6%
    (with hysteresis: scalar returns to 1.0 only once DD > −1%)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from engine.config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Stage 1 — Historical VaR (CW1 §3.5)
# =============================================================================
def historical_var_99(daily_returns: pd.Series, confidence: float = 0.99) -> float:
    """Empirical 1-day 99% VaR magnitude (positive number)."""
    r = daily_returns.dropna()
    if len(r) < 30:
        return 0.02  # fallback if too little history
    alpha = 1 - confidence
    return float(-r.quantile(alpha))


def historical_es_99(daily_returns: pd.Series, confidence: float = 0.99) -> float:
    """Expected Shortfall — mean loss in the worst (1-confidence) tail."""
    r = daily_returns.dropna()
    if len(r) < 30:
        return 0.025
    alpha = 1 - confidence
    cutoff = r.quantile(alpha)
    return float(-r[r <= cutoff].mean())


# =============================================================================
# Composite scaler with state tracking (needed for drawdown-control)
# =============================================================================
@dataclass
class CompositeRiskScaler:
    """Chain: HVaR-scale → vol-target → DD-control.

    State is mutated between rebalances — track NAV history for drawdown-based
    logic.  Pass this object to the backtest engine as the ``risk_scaler``
    dependency.
    """

    cfg: Config
    nav_history: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    # ------------------------------------------------------------------
    def var_scalar(self, portfolio_returns_daily: pd.Series) -> tuple[float, float, float]:
        """Stage 1: target_budget / |VaR_99|.  Returns (scalar, var99, es99)."""
        rs_cfg = self.cfg.risk_scaler
        var = historical_var_99(portfolio_returns_daily, rs_cfg.hvar_confidence)
        es = historical_es_99(portfolio_returns_daily, rs_cfg.hvar_confidence)
        if var <= 0:
            return 1.0, var, es
        scalar = rs_cfg.hvar_target_budget / var
        return float(scalar), float(var), float(es)

    # ------------------------------------------------------------------
    def vol_scalar(self, portfolio_returns_daily: pd.Series) -> float:
        """Stage 2: Moreira-Muir (2017) conditional vol targeting."""
        rs = self.cfg.risk_scaler
        if not rs.vol_target_enabled:
            return 1.0
        r = portfolio_returns_daily.dropna().iloc[-self.cfg.estimation_windows.vol_target_days:]
        if len(r) < 10:
            return 1.0
        realised_vol = float(r.std() * np.sqrt(252))
        if realised_vol <= 0:
            return 1.0
        target = rs.vol_target_annual
        raw = target / realised_vol
        return float(np.clip(raw, rs.vol_target_clip_lower, rs.vol_target_clip_upper))

    # ------------------------------------------------------------------
    def dd_scalar(self) -> tuple[float, float]:
        """Stage 3: drawdown-control overlay with hysteresis.

        Returns (scalar, drawdown_12m).
        """
        rs = self.cfg.risk_scaler
        if not rs.dd_control_enabled or len(self.nav_history) == 0:
            return 1.0, 0.0

        df = pd.DataFrame(self.nav_history, columns=["date", "nav"]).sort_values("date")
        # 12-month rolling peak (approx 12 rebalance months)
        lookback = min(len(df), self.cfg.estimation_windows.drawdown_lookback_months + 1)
        recent = df.iloc[-lookback:]
        peak = recent["nav"].cummax().iloc[-1]
        curr = float(recent["nav"].iloc[-1])
        if peak <= 0:
            return 1.0, 0.0
        dd = (curr - peak) / peak

        # Hysteresis: only re-expose when we've recovered above dd_recover_threshold
        if dd > rs.dd_recover_threshold:
            scalar = 1.0
        elif dd > rs.dd_threshold_soft:
            scalar = 1.0
        elif dd > rs.dd_threshold_hard:
            scalar = rs.dd_scalar_soft
        else:
            scalar = rs.dd_scalar_hard
        return float(scalar), float(dd)

    # ------------------------------------------------------------------
    def apply(
        self,
        target_weights: pd.Series,
        portfolio_returns_daily: pd.Series,
    ) -> tuple[pd.Series, dict]:
        """Run the three-stage scaler, return (scaled_weights, diagnostics)."""
        var_s, var_99, es_99 = self.var_scalar(portfolio_returns_daily)
        vol_s = self.vol_scalar(portfolio_returns_daily)
        dd_s, dd = self.dd_scalar()

        composite = var_s * vol_s * dd_s
        # Composite safety ceiling — PLAN-compliant 2.0 ceiling
        composite = float(np.clip(composite, 0.0, 2.0))
        scaled = target_weights * composite
        diag = {
            "position_scale": composite,
            "var_99": var_99,
            "es_99": es_99,
            "vol_target_scalar": vol_s,
            "dd_control_scalar": dd_s,
            "drawdown_12m": dd,
        }
        return scaled, diag

    # ------------------------------------------------------------------
    def record_nav(self, date: pd.Timestamp, nav: float) -> None:
        self.nav_history.append((pd.Timestamp(date), float(nav)))


__all__ = ["CompositeRiskScaler", "historical_var_99", "historical_es_99"]
