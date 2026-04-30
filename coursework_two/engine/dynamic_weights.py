"""Dynamic factor-weight selection: VIX regime × factor dispersion tilting.

Implements CW1 report §3.5 equations (1)–(3):
    D_{f,t} = z̄_{f,t}^{TopQ} − z̄_{f,t}^{BottomQ}
    w*_{f,t} = w_base_f × (1 + λ^(r_t)_f) × (1 + γ·D_{f,t})
    w_{f,t}  = w*_{f,t} / Σ_k w*_{k,t}

Regime classification
---------------------
VIX level at ``rebalance_date - 1`` compared to trailing 252-day percentile:
    • low  if pct < P30
    • high if pct > P80
    • normal otherwise

References
----------
CW1 report §3.5; Bender et al. (2018); Daniel & Moskowitz (2016); Hamilton (1989).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from engine.config import Config
from engine.types import Regime

logger = logging.getLogger(__name__)


# =============================================================================
# VIX regime classification — percentile method (baseline) + HMM (T3 stretch)
# =============================================================================
def classify_regime_percentile(
    vix_series: pd.Series, low_pct: float, high_pct: float
) -> tuple[Regime, float]:
    """Classify VIX regime at latest observation via trailing percentile."""
    if len(vix_series) < 20:
        return Regime.NORMAL, 0.5
    current = float(vix_series.iloc[-1])
    pct = float((vix_series.rank(pct=True).iloc[-1]))
    if pct < low_pct:
        return Regime.LOW, pct
    if pct > high_pct:
        return Regime.HIGH, pct
    return Regime.NORMAL, pct


def classify_regime_hmm(vix_series: pd.Series) -> tuple[Regime, float]:
    """§5.6 Gaussian HMM regime classification (optional T3 stretch).

    Requires ``hmmlearn`` — returns gracefully (falls back to percentile)
    if the package is missing.
    """
    try:
        from hmmlearn.hmm import GaussianHMM
    except Exception:  # pragma: no cover
        logger.info("hmmlearn unavailable; HMM regime skipped")
        return Regime.NORMAL, 0.5

    data = vix_series.dropna().apply(np.log).diff().dropna().values.reshape(-1, 1)
    if len(data) < 50:
        return Regime.NORMAL, 0.5
    try:
        hmm = GaussianHMM(n_components=2, n_iter=50, random_state=42)
        hmm.fit(data)
        probs = hmm.predict_proba(data)
        # Identify high-vol state as the one with larger mean |return|
        vol_means = [abs(hmm.means_[i][0]) for i in range(2)]
        high_state = int(np.argmax(vol_means))
        prob_high = float(probs[-1, high_state])
        if prob_high > 0.7:
            return Regime.HIGH, prob_high
        if prob_high < 0.3:
            return Regime.LOW, prob_high
        return Regime.NORMAL, prob_high
    except Exception as exc:  # pragma: no cover
        logger.warning("HMM regime fit failed: %s", exc)
        return Regime.NORMAL, 0.5


# =============================================================================
# Factor dispersion D_{f,t} — cross-sectional top-vs-bottom quartile z-spread
# =============================================================================
def factor_dispersion(
    z_scores: pd.DataFrame, long_q: float = 0.25, short_q: float = 0.25
) -> pd.Series:
    """Top-quartile mean z − bottom-quartile mean z, per factor."""
    out = {}
    for f in z_scores.columns:
        s = z_scores[f].dropna()
        if len(s) < 10:
            out[f] = 0.0
            continue
        top = s.quantile(1 - long_q)
        bot = s.quantile(short_q)
        out[f] = float(s[s >= top].mean() - s[s <= bot].mean())
    return pd.Series(out)


# =============================================================================
# Weight engines — Strategy pattern (PLAN §7.1)
# =============================================================================
@dataclass
class StaticWeights:
    """Static composite weights from ``cfg.factors.base_weights``.

    Implemented composite is 50/50 momentum + value (CW1's original
    30/30/25/15 four-factor proposal was reduced to two factors based
    on out-of-sample IC evidence — see report §§1.2, 2.2.1, 4.2).
    """

    cfg: Config

    def compute(
        self,
        z_scores: pd.DataFrame,
        vix_series: pd.Series,
        use_hmm: bool = False,
    ) -> tuple[dict[str, float], Regime, float, pd.Series]:
        bw = self.cfg.factors.base_weights
        w = {
            "momentum": bw.momentum,
            "value": bw.value,
            "quality": bw.quality,
            "sentiment": bw.sentiment,
        }
        regime, vix_pct = classify_regime_percentile(
            vix_series, self.cfg.dynamic_weights.vix_low_pct, self.cfg.dynamic_weights.vix_high_pct
        )
        disp = factor_dispersion(
            z_scores,
            long_q=self.cfg.portfolio.long_quartile,
            short_q=self.cfg.portfolio.short_quartile,
        )
        return w, regime, vix_pct, disp


@dataclass
class DynamicGridWeights:
    """§3.5 VIX × dispersion dynamic tilt (Eqs 1–3 of CW1 report).

    ``gamma`` and ``lambda_tilts`` can be overridden (e.g., during CPCV grid
    search) without reloading the full config.
    """

    cfg: Config
    gamma_override: Optional[float] = None
    lambda_scale_override: Optional[float] = None   # multiplier on regime_tilts

    def compute(
        self,
        z_scores: pd.DataFrame,
        vix_series: pd.Series,
        use_hmm: bool = False,
    ) -> tuple[dict[str, float], Regime, float, pd.Series]:
        regime, vix_pct = (
            classify_regime_hmm(vix_series) if use_hmm
            else classify_regime_percentile(
                vix_series,
                self.cfg.dynamic_weights.vix_low_pct,
                self.cfg.dynamic_weights.vix_high_pct,
            )
        )
        disp = factor_dispersion(
            z_scores,
            long_q=self.cfg.portfolio.long_quartile,
            short_q=self.cfg.portfolio.short_quartile,
        )
        bw = self.cfg.factors.base_weights
        base = {"momentum": bw.momentum, "value": bw.value, "quality": bw.quality, "sentiment": bw.sentiment}

        tilts = self.cfg.dynamic_weights.regime_tilts[regime.value]
        scale = 1.0 if self.lambda_scale_override is None else self.lambda_scale_override
        gamma = self.cfg.dynamic_weights.gamma if self.gamma_override is None else self.gamma_override

        w_star = {}
        for f in base:
            tilt = tilts.get(f, 0.0) * scale
            d_f = float(disp.get(f, 0.0))
            w_star[f] = base[f] * (1 + tilt) * (1 + gamma * d_f)
            w_star[f] = max(w_star[f], 0.0)  # Enforce non-negative

        total = sum(w_star.values())
        if total <= 0:
            return base, regime, vix_pct, disp
        w_norm = {k: v / total for k, v in w_star.items()}
        return w_norm, regime, vix_pct, disp


__all__ = [
    "DynamicGridWeights",
    "StaticWeights",
    "classify_regime_hmm",
    "classify_regime_percentile",
    "factor_dispersion",
]
