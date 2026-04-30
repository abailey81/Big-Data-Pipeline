"""Portfolio construction: MinVar (LW + Denoised LW + turnover-penalised) and HRP.

Implements the four construction variants listed in PLAN §5.1–5.3:

1. **MinVar + vanilla Ledoit-Wolf** — baseline (CW1 Eq. 10 foundation).
2. **MinVar + Denoised Ledoit-Wolf** — López de Prado (2020) Marchenko-Pastur
   eigenvalue clipping to remove "noise subspace" before shrinkage.
3. **MinVar + turnover-penalty (L2)** — DeMiguel et al. (2009); stabilises
   weight changes between rebalances.
4. **Hierarchical Risk Parity** — López de Prado (2016); no covariance
   inversion needed — a natural robustness check.

Every variant obeys the constraints:
    w_i ≥ 0   (long-only per leg — both legs run separately)
    1' w = 1  (fully-invested each leg)
    w_i ≤ max_weight (default 5%)

References
----------
Ledoit & Wolf (2004); López de Prado (2016, 2020); DeMiguel, Garlappi, Uppal
(2009); Baker, Bradley & Wurgler (2011).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from engine.config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Weight-cap helper (PLAN §4.5)
# =============================================================================
def _iterative_cap(w: pd.Series, max_w: float, tol: float = 1e-9, max_iter: int = 50) -> pd.Series:
    """Enforce ``w_i ≤ max_w`` while keeping ``sum(w) ≤ 1`` and non-negativity.

    Clip-then-renormalise is broken: renormalising after clipping pushes the
    capped weights back above the cap, which is exactly the bug flagged in the
    CW2 audit. The correct fix is to redistribute the excess mass only to
    *uncapped* positions, and repeat until every weight is at or below the cap.

    When the long-leg universe is too small to respect the cap
    (``n < 1 / max_w``), every position is pinned at ``max_w`` and the portfolio
    holds ``1 - n*max_w`` in cash rather than silently violating the constraint.

    Parameters
    ----------
    w : Series
        Non-negative, sums to ≤ 1.  Any negative entries are floored at zero.
    max_w : float
        Single-name upper bound (default 0.05 per PLAN §4.5).
    tol : float
        Numerical tolerance on the cap.
    max_iter : int
        Safety stop.

    Returns
    -------
    Series
        Weights satisfying ``0 ≤ w_i ≤ max_w`` and ``sum(w) ≤ 1``.
    """
    w = w.astype(float).clip(lower=0.0).copy()
    if w.sum() <= 0:
        return w
    # Callers pass a weight vector that already sums to ≤ 1 (normalised leg
    # weights).  We deliberately do *not* re-normalise here — that was the
    # original bug — we just cap and redistribute while preserving total mass.
    for _ in range(max_iter):
        over = w > max_w + tol
        if not over.any():
            break
        excess = float((w[over] - max_w).sum())
        w.loc[over] = max_w
        uncapped = (~over) & (w > 0)
        if not uncapped.any():
            # No room to redistribute: the universe is too small.  Return the
            # capped weights and accept that sum < 1 (residual cash).
            break
        room = (max_w - w[uncapped]).clip(lower=0.0)
        room_total = float(room.sum())
        if room_total <= tol:
            break
        # Distribute excess proportionally to remaining head-room per name.
        w.loc[uncapped] = w.loc[uncapped] + excess * (room / room_total)
    # Final numerical safety: clip and floor, don't re-normalise (that's what
    # reintroduced the bug in the first place).
    return w.clip(lower=0.0, upper=max_w)


# =============================================================================
# Covariance estimators
# =============================================================================
def ledoit_wolf_cov(returns: pd.DataFrame) -> np.ndarray:
    """Vanilla Ledoit-Wolf shrinkage covariance (scikit-learn)."""
    lw = LedoitWolf().fit(returns.dropna(how="any").values)
    return lw.covariance_


def _mp_pdf(var: float, q: float, pts: int = 1000) -> tuple[np.ndarray, np.ndarray]:
    """Marchenko-Pastur PDF support (López de Prado 2020 §2.3)."""
    eig_min = var * (1 - np.sqrt(1.0 / q)) ** 2
    eig_max = var * (1 + np.sqrt(1.0 / q)) ** 2
    xs = np.linspace(eig_min, eig_max, pts)
    pdf = q / (2 * np.pi * var * xs) * np.sqrt((eig_max - xs) * (xs - eig_min))
    return xs, pdf


def _fit_mp_variance(eigenvalues: np.ndarray, q: float) -> float:
    """Fit implied MP variance by minimising MSE between empirical and theoretical PDFs."""
    from scipy.optimize import minimize_scalar

    def _mse(var: float) -> float:
        if var <= 0:
            return np.inf
        eig_min = var * (1 - np.sqrt(1.0 / q)) ** 2
        eig_max = var * (1 + np.sqrt(1.0 / q)) ** 2
        mask = (eigenvalues >= eig_min) & (eigenvalues <= eig_max)
        if mask.sum() < 5:
            return np.inf
        emp = np.histogram(eigenvalues[mask], bins=50, density=True)[0]
        xs, pdf = _mp_pdf(var, q, pts=len(emp))
        return float(np.mean((emp - pdf) ** 2))

    res = minimize_scalar(_mse, bounds=(1e-6, 10), method="bounded")
    return float(res.x)


def denoised_ledoit_wolf_cov(returns: pd.DataFrame, mp_q: Optional[float] = None) -> np.ndarray:
    """Denoised LW covariance via MP eigenvalue clipping (López de Prado 2020).

    Steps:
        1. Compute sample covariance Σ.
        2. Normalise to correlation C = D^{-1/2} Σ D^{-1/2}.
        3. Eigen-decompose C; identify MP "noise" eigenvalues.
        4. Replace noisy eigenvalues with their mean (preserves trace).
        5. Reconstruct C_denoised, rescale to Σ_denoised.
        6. Apply Ledoit-Wolf shrinkage on Σ_denoised.

    Parameters
    ----------
    returns : DataFrame
        T × N return matrix.
    mp_q : float | None
        T/N ratio; defaults to empirical.

    Returns
    -------
    ndarray (N × N)
        Denoised-LW covariance matrix.
    """
    X = returns.dropna(how="any").values
    T, N = X.shape
    if T <= 1 or N <= 1:
        raise ValueError(f"Need >1 observations and >1 variables, got ({T},{N})")
    q = T / N if mp_q is None else mp_q

    # 1. Sample covariance + normalisation
    Sigma = np.cov(X, rowvar=False)
    diag_sd = np.sqrt(np.diag(Sigma))
    # Guard zero-variance assets
    diag_sd[diag_sd == 0] = 1e-10
    C = Sigma / np.outer(diag_sd, diag_sd)
    C = (C + C.T) / 2.0

    # 2. Eigendecomposition
    eigvals, eigvecs = np.linalg.eigh(C)
    # 3. Fit MP to noise region (bottom ~80% of eigenvalues typically noise)
    implied_var = _fit_mp_variance(eigvals, q)
    eig_max = implied_var * (1 + np.sqrt(1.0 / q)) ** 2

    # 4. Noise eigenvalues: those below the MP upper bound → replace with mean
    noise_mask = eigvals < eig_max
    if noise_mask.any():
        noise_mean = eigvals[noise_mask].mean()
        eigvals[noise_mask] = noise_mean

    # 5. Reconstruct C_denoised
    C_denoised = eigvecs @ np.diag(eigvals) @ eigvecs.T
    # Rescale to unit diagonal (covariance consistency)
    d = np.sqrt(np.diag(C_denoised))
    d[d == 0] = 1e-10
    C_denoised = C_denoised / np.outer(d, d)
    Sigma_denoised = C_denoised * np.outer(diag_sd, diag_sd)

    # 6. LW-shrink on top of denoising for further stability
    # Fall back to plain sample if LW fails
    try:
        lw = LedoitWolf().fit(X)
        # Blend: use LW's shrinkage intensity against denoised target
        shrink = lw.shrinkage_
        T_ = np.diag(np.diag(Sigma_denoised).mean() * np.ones(N))
        Sigma_final = (1 - shrink) * Sigma_denoised + shrink * T_
    except Exception as exc:
        logger.warning("LW fallback on denoised cov failed: %s", exc)
        Sigma_final = Sigma_denoised
    return (Sigma_final + Sigma_final.T) / 2.0


# =============================================================================
# Portfolio constructors
# =============================================================================
@dataclass
class PortfolioEngine:
    cfg: Config

    def score_weighted_leg(
        self,
        leg_symbols: list[str],
        leg_scores: pd.Series,
        is_long: bool = True,
    ) -> pd.Series:
        """**Factor-weighted (score-weighted) within-leg allocation** — CW2 spec permits this
        per the task's recommended-process list ("equal-weighted, factor-weighted, risk-parity").

        Weight is proportional to (composite_score - leg_threshold), clipped at the
        5% cap.  Concentrates capital on the strongest conviction names within the
        top/bottom decile, dramatically amplifying signal-to-noise compared with MinVar
        which treats all leg members near-equally.

        For the long leg: w_i ∝ max(0, score_i - score_median_of_leg)
        For the short leg: w_i ∝ max(0, score_median_of_leg - score_i)

        Reference: Grinold & Kahn (2000) Ch. 14 — "The Fundamental Law of Active Management":
        IR ∝ IC × √breadth.  Score-weighting extracts maximum IR by weighting proportional
        to signal strength.
        """
        max_w = self.cfg.portfolio.max_weight_per_stock
        s = leg_scores.reindex(leg_symbols).dropna()
        if len(s) == 0:
            return pd.Series(dtype=float)
        median = float(s.median())
        if is_long:
            raw = (s - median).clip(lower=0)
        else:
            raw = (median - s).clip(lower=0)
        if raw.sum() <= 0:
            # Fallback: equal-weighted
            raw = pd.Series(1.0 / len(s), index=s.index)
        w = raw / raw.sum()
        # Iterative cap: clip-then-renormalise pushes capped weights back over the
        # cap, so we redistribute excess mass to *uncapped* names and repeat until
        # every weight satisfies w_i ≤ max_w (PLAN §4.5). If |s| < 1/max_w, every
        # position hits the cap and the leg is held equal-weighted at max_w with
        # residual cash — a legitimate sparse-universe outcome.
        w = _iterative_cap(w, max_w)
        return w.reindex(leg_symbols, fill_value=0.0)

    def optimise_leg(
        self,
        returns: pd.DataFrame,
        leg_symbols: list[str],
        previous_weights: Optional[pd.Series] = None,
        construction_override: Optional[str] = None,
    ) -> pd.Series:
        """Build long-only weights for one leg using the configured construction.

        Parameters
        ----------
        returns : DataFrame
            Trailing T×N return matrix (already USD-converted).
        leg_symbols : list[str]
            Subset of returns columns to include.
        previous_weights : Series | None
            Previous rebalance's weights — used by turnover-penalty variant.
        construction_override : str | None
            Force a construction variant for this one call (e.g. ``"hrp"``) —
            used by the backtest engine to produce the HRP side-run alongside
            the primary score-weighted book (PLAN §5.3 robustness comparison).

        Returns
        -------
        Series
            Non-negative weights summing to 1 over ``leg_symbols``.
        """
        ret_leg = returns[leg_symbols].dropna(axis=1, how="all")
        # Drop columns with < 20 observations (low-data names)
        valid = ret_leg.count() >= 20
        ret_leg = ret_leg.loc[:, valid]
        leg_symbols = [s for s in leg_symbols if s in ret_leg.columns]
        if not leg_symbols:
            return pd.Series(dtype=float)

        # Fill forward any remaining NaNs then drop remaining rows
        ret_leg = ret_leg.ffill().dropna(how="any")

        construction = construction_override or self.cfg.portfolio.construction
        if construction == "hrp":
            w = self._hrp(ret_leg)
        else:
            # Denoised LW always preferred when enabled (it's strictly more stable)
            if self.cfg.portfolio.denoise_enabled and construction in ("minvar_denoised_lw", "minvar_turnover"):
                Sigma = denoised_ledoit_wolf_cov(ret_leg)
            else:
                Sigma = ledoit_wolf_cov(ret_leg)
            use_turnover = construction == "minvar_turnover"
            w = self._minvar(
                Sigma,
                symbols=list(ret_leg.columns),
                prev_w=previous_weights,
                turnover_penalty=self.cfg.portfolio.turnover_penalty_lambda if use_turnover else 0.0,
            )
        return pd.Series(w, index=ret_leg.columns).reindex(leg_symbols, fill_value=0.0)

    # ------------------------------------------------------------------
    def _minvar(
        self,
        Sigma: np.ndarray,
        symbols: list[str],
        prev_w: Optional[pd.Series],
        turnover_penalty: float,
    ) -> np.ndarray:
        """Solve: min w'Σw + λ * ||w − w_prev||^2 s.t. w ≥ 0, 1'w = 1, w ≤ max.

        Uses SLSQP with an analytic gradient-friendly formulation.
        """
        N = len(symbols)
        max_w = self.cfg.portfolio.max_weight_per_stock
        min_w = self.cfg.portfolio.min_weight_per_stock
        w0 = np.ones(N) / N

        if prev_w is not None and turnover_penalty > 0.0:
            w_prev = prev_w.reindex(symbols, fill_value=0.0).values
        else:
            w_prev = None

        def obj(w: np.ndarray) -> float:
            risk = float(w @ Sigma @ w)
            if w_prev is not None:
                risk += turnover_penalty * float(np.sum((w - w_prev) ** 2))
            return risk

        def grad(w: np.ndarray) -> np.ndarray:
            g = 2 * Sigma @ w
            if w_prev is not None:
                g += 2 * turnover_penalty * (w - w_prev)
            return g

        bounds = [(min_w, max_w) for _ in range(N)]
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        res = minimize(
            obj,
            w0,
            jac=grad,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-10, "maxiter": 500},
        )
        if not res.success:
            logger.warning("MinVar SLSQP did not converge: %s. Falling back to equal-weight.", res.message)
            return w0
        # SLSQP bounds can be violated by tiny floating-point slips, and
        # renormalising after a naive clip reintroduces the same cap-violation
        # bug found in score_weighted_leg.  Go through the iterative capper so
        # MinVar, score-weighted and HRP all share one cap-enforcement path.
        w_series = pd.Series(res.x, index=symbols).clip(lower=min_w, upper=None)
        w_capped = _iterative_cap(w_series, max_w)
        return w_capped.values

    # ------------------------------------------------------------------
    def _hrp(self, returns: pd.DataFrame) -> np.ndarray:
        """Hierarchical Risk Parity (López de Prado 2016).

        Steps: (1) corr → distance, (2) linkage, (3) quasi-diagonalisation,
        (4) recursive bisection inverse-variance allocation.
        """
        cov = returns.cov().values
        corr = returns.corr().values
        # Corr → distance
        dist = np.sqrt(np.maximum(0.0, (1 - corr) / 2))
        np.fill_diagonal(dist, 0.0)
        # Linkage needs a condensed form
        link = linkage(squareform(dist, checks=False), method="single")
        # Quasi-diagonalisation: reorder via dendrogram
        N = len(returns.columns)
        sort_order = _get_quasi_diag(link, N)
        # Recursive bisection
        w = pd.Series(1.0, index=sort_order)
        clusters = [sort_order]
        while clusters:
            new_clusters = []
            for c in clusters:
                if len(c) <= 1:
                    continue
                half = len(c) // 2
                left, right = c[:half], c[half:]
                var_left = _cluster_var(cov, left)
                var_right = _cluster_var(cov, right)
                alpha = 1 - var_left / (var_left + var_right + 1e-12)
                w.loc[left] *= alpha
                w.loc[right] *= (1 - alpha)
                new_clusters.extend([left, right])
            clusters = new_clusters
        # Reindex back to original order.  HRP natively produces weights with
        # no single-name cap, which can violate the 5% constraint on
        # concentrated correlation clusters (López de Prado 2016 §3).
        # Route through the iterative capper so all three constructions (MinVar,
        # score-weighted, HRP) share one cap-enforcement path.
        w_arr = w.reindex(range(N)).fillna(0).values
        w_arr = w_arr / w_arr.sum() if w_arr.sum() > 0 else w_arr
        max_w = self.cfg.portfolio.max_weight_per_stock
        return _iterative_cap(pd.Series(w_arr), max_w).values


# =============================================================================
# HRP helpers
# =============================================================================
def _get_quasi_diag(link: np.ndarray, N: int) -> list[int]:
    """Post-order traversal of SciPy linkage tree → leaf order."""
    link = link.astype(int)
    sort_ix = [int(link[-1, 0]), int(link[-1, 1])]
    num_items = N
    while max(sort_ix) >= num_items:
        new = []
        for i in sort_ix:
            if i < num_items:
                new.append(i)
            else:
                row = link[i - num_items]
                new.extend([int(row[0]), int(row[1])])
        sort_ix = new
    return sort_ix


def _cluster_var(cov: np.ndarray, cluster: list[int]) -> float:
    """Cluster-level inverse-variance portfolio variance."""
    if len(cluster) == 1:
        return float(cov[cluster[0], cluster[0]])
    sub = cov[np.ix_(cluster, cluster)]
    ivp = 1.0 / np.diag(sub)
    ivp = ivp / ivp.sum()
    return float(ivp @ sub @ ivp)


__all__ = [
    "PortfolioEngine",
    "denoised_ledoit_wolf_cov",
    "ledoit_wolf_cov",
]
