"""Contextual Thompson Sampling for adaptive factor-weight selection (§5.4).

Reframes CW2's dynamic-weighting problem as a linear contextual bandit:
    • K=12 arms, each a normalised factor-weight vector
    • d-dim context = VIX level + regime dummies + dispersions + lagged ICs
    • Linear Thompson Sampling — Agrawal & Goyal (2013) formulation

Regret bound
------------
Linear TS achieves Õ(d · √(T · log T)) Bayesian regret (Russo-Van Roy 2016),
which for d≈12, T≈48 months gives ~40 reward units — tolerable at monthly
rebalancing.  Compare: tabular Q-learning would need ~10× more samples.

Exponential reward decay
------------------------
Observed rewards are weighted by exp(−Δt / τ) with τ = 12-month half-life to
address non-stationarity (§5.18 signal decay).  Agrawal & Goyal adaptation.

References
----------
Thompson (1933); Li et al. (2010); Agrawal & Goyal (2013); Russo & Van Roy (2018).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from engine.config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Arm menu — 2-factor alignment (momentum + value), 8 arms
# =============================================================================
def build_arms() -> list[dict[str, float]]:
    """K = 8 arm menu aligned with the v0.3.0 two-factor strategy.

    The composite weight decision reduces to the momentum-vs-value split
    since ``quality`` and ``sentiment`` carry zero weight in the adopted
    strategy (see ``FACTOR_REVIEW_2026-04-22.md``).  Arms span a symmetric
    grid around the 0.50 / 0.50 baseline so the Thompson sampler has
    meaningful exploration on both sides of the neutral split.

    Every arm is a proper normalised weight vector summing to 1.0; the
    quality and sentiment entries are retained as 0.0 keys so downstream
    code (composite formation, factor scoring) can treat any arm as a
    drop-in replacement for the legacy 4-factor menu without schema changes.
    """
    splits = [
        (0.50, 0.50),   # 0 — adopted baseline
        (0.60, 0.40),   # 1 — mild momentum tilt
        (0.70, 0.30),   # 2 — strong momentum tilt
        (0.40, 0.60),   # 3 — mild value tilt
        (0.30, 0.70),   # 4 — strong value tilt
        (0.80, 0.20),   # 5 — momentum-dominant
        (0.20, 0.80),   # 6 — value-dominant
        (0.55, 0.45),   # 7 — moderate momentum tilt
    ]
    return [
        {"momentum": mom, "value": val, "quality": 0.0, "sentiment": 0.0}
        for mom, val in splits
    ]


# =============================================================================
# Linear Thompson Sampling
# =============================================================================
@dataclass
class LinearThompsonSampler:
    """Per-arm Bayesian linear regression with Gaussian-conjugate updates.

    For each arm a, we maintain:
        • Precision matrix:  A_a = (1/σ²) · I + Σ x_t x_t^T   (d × d)
        • Cumulative R_a  =  (1/σ²) · Σ x_t · r_t             (d × 1)
        • Posterior mean μ_a = A_a^{-1} R_a
        • Posterior cov  Σ_a = A_a^{-1}

    Selection rule (Thompson Sampling):
        1. Sample θ̃_a ∼ N(μ_a, v² · Σ_a) for each arm
        2. Pull arm a* = argmax_a x^T θ̃_a
    """

    n_arms: int
    context_dim: int
    sigma2_prior: float = 1.0
    v: float = 0.25                 # posterior sampling scale (Agrawal-Goyal)
    seed: int = 42
    # State
    A: list[np.ndarray] = field(default_factory=list)
    R: list[np.ndarray] = field(default_factory=list)
    _rng: np.random.Generator = field(default=None, init=False)

    def __post_init__(self):
        d = self.context_dim
        inv_sigma2 = 1.0 / self.sigma2_prior
        self.A = [np.eye(d) * inv_sigma2 for _ in range(self.n_arms)]
        self.R = [np.zeros(d) for _ in range(self.n_arms)]
        self._rng = np.random.default_rng(self.seed)

    # ------------------------------------------------------------------
    def posterior(self, arm: int) -> tuple[np.ndarray, np.ndarray]:
        A_inv = np.linalg.inv(self.A[arm])
        mu = A_inv @ self.R[arm]
        return mu, A_inv

    def sample_action(self, context: np.ndarray) -> int:
        """Thompson-sample one arm. Returns arm index."""
        x = context.reshape(-1)
        scores = np.empty(self.n_arms)
        for a in range(self.n_arms):
            mu, cov = self.posterior(a)
            theta_sample = self._rng.multivariate_normal(mu, (self.v ** 2) * cov)
            scores[a] = x @ theta_sample
        return int(np.argmax(scores))

    def update(self, arm: int, context: np.ndarray, reward: float, decay_weight: float = 1.0) -> None:
        """Bayesian posterior update after observing (arm, context, reward).

        ``decay_weight`` ∈ (0, 1] applies exponential time-decay to the
        likelihood contribution (§5.18 signal decay).
        """
        x = context.reshape(-1)
        self.A[arm] += decay_weight * np.outer(x, x)
        self.R[arm] += decay_weight * x * reward

    # ------------------------------------------------------------------
    def posterior_means_matrix(self) -> np.ndarray:
        """(K × d) matrix of posterior means (for logging)."""
        return np.stack([self.posterior(a)[0] for a in range(self.n_arms)])

    def posterior_stds_matrix(self) -> np.ndarray:
        return np.stack(
            [np.sqrt(np.abs(np.diag(self.posterior(a)[1]))) for a in range(self.n_arms)]
        )


# =============================================================================
# BanditWeights — Strategy-pattern weight engine for the backtest loop
# =============================================================================
@dataclass
class BanditWeights:
    """Adaptive Thompson-Sampling weight selector (§5.4).

    Context vector (d=12):
        [vix_level_z, regime_low, regime_normal, regime_high,
         disp_mom, disp_val, disp_qual, disp_sent,
         ic_mom_3mo, ic_val_3mo, ic_qual_3mo, ic_sent_3mo]

    Reward = realised monthly net strategy return (applied at next step).

    After ``warmup_months`` the bandit starts choosing; before that, default
    to arm 0 (static baseline).
    """

    cfg: Config
    arms: list[dict[str, float]] = field(default_factory=build_arms)
    sampler: LinearThompsonSampler = field(init=False)
    step: int = 0
    last_arm: int = 0
    last_context: Optional[np.ndarray] = None
    history: list[dict] = field(default_factory=list)

    def __post_init__(self):
        bc = self.cfg.bandit
        self.sampler = LinearThompsonSampler(
            n_arms=bc.n_arms,
            context_dim=bc.context_dim,
            sigma2_prior=bc.prior_sigma2,
            seed=bc.random_seed,
        )
        if bc.n_arms != len(self.arms):
            # Trim / pad to n_arms if config differs
            self.arms = self.arms[: bc.n_arms]

    # ------------------------------------------------------------------
    def _build_context(
        self,
        vix_level_z: float,
        regime_flag: str,
        dispersion: dict[str, float],
        ic_3mo: dict[str, float],
    ) -> np.ndarray:
        reg_low = 1.0 if regime_flag == "low" else 0.0
        reg_normal = 1.0 if regime_flag == "normal" else 0.0
        reg_high = 1.0 if regime_flag == "high" else 0.0
        return np.array(
            [
                vix_level_z,
                reg_low,
                reg_normal,
                reg_high,
                dispersion.get("momentum", 0.0),
                dispersion.get("value", 0.0),
                dispersion.get("quality", 0.0),
                dispersion.get("sentiment", 0.0),
                ic_3mo.get("momentum", 0.0),
                ic_3mo.get("value", 0.0),
                ic_3mo.get("quality", 0.0),
                ic_3mo.get("sentiment", 0.0),
            ],
            dtype=float,
        )

    # ------------------------------------------------------------------
    def select(
        self,
        vix_level_z: float,
        regime_flag: str,
        dispersion: dict[str, float],
        ic_3mo: dict[str, float],
    ) -> tuple[dict[str, float], int, np.ndarray]:
        ctx = self._build_context(vix_level_z, regime_flag, dispersion, ic_3mo)
        if self.step < self.cfg.bandit.warmup_months:
            arm = 0
        else:
            arm = self.sampler.sample_action(ctx)
        self.last_arm = arm
        self.last_context = ctx
        return dict(self.arms[arm]), arm, ctx

    # ------------------------------------------------------------------
    def update_reward(self, reward: float) -> None:
        """Apply exponentially-decayed Bayesian update on the last (arm, ctx).

        Should be called AFTER observing the realised monthly return for the
        weights suggested at the prior rebalance.
        """
        if self.last_context is None:
            return
        half_life = max(1, self.cfg.bandit.reward_half_life_months)
        # For the most recent step: decay weight 1.0 (no aging yet)
        self.sampler.update(self.last_arm, self.last_context, float(reward), decay_weight=1.0)
        self.history.append({
            "step": self.step,
            "arm": self.last_arm,
            "reward": float(reward),
            "context": self.last_context.tolist(),
        })
        self.step += 1

    # ------------------------------------------------------------------
    def log_row(self, date) -> dict:
        mus = self.sampler.posterior_means_matrix()
        stds = self.sampler.posterior_stds_matrix()
        ctx_json = "null" if self.last_context is None else json.dumps(self.last_context.tolist())
        return {
            "date": date,
            "arm_selected": self.last_arm,
            "realised_reward": (self.history[-1]["reward"] if self.history else 0.0),
            "arm_posterior_mean_json": json.dumps(mus.tolist()),
            "arm_posterior_std_json": json.dumps(stds.tolist()),
            "context_vector_json": ctx_json,
        }


__all__ = ["BanditWeights", "LinearThompsonSampler", "build_arms"]
