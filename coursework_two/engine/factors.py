"""Four-factor raw-score computation + Gram-Schmidt orthogonalisation.

Replicates CW1 report §3.2 equations (4)–(7) verbatim, then layers the
sector-neutral Gram-Schmidt orthogonalisation of §5.14 of the CW2 PLAN.

Factors
-------
Momentum
    12-1 cumulative return:  mom_i = P_{t-1} / P_{t-12} − 1
    Absolute-momentum filter applied to long leg only (Antonacci 2016).

Value
    Equal-weighted z-composite of (B/P, E/P, CF/P); all sub-metrics
    winsorised at 2.5/97.5 percentile within GICS (Asness-Frazzini-Israel-
    Moskowitz 2015; Cornell-Damodaran 2021).

Quality
    Equal-weighted z-composite of (ROE, earnings stability, inverse D/E)
    where earnings-stability = 1 / stdev(quarterly EPS growth, TTM 12Q)
    following QMJ (Asness-Frazzini-Pedersen 2019) and Piotroski (2000).

Sentiment
    Pre-computed composite from CW1 ``news_sentiment.sentiment_score``
    (Hutto-Gilbert 2014 VADER + financial-domain lexicon; Tetlock 2007).

Orthogonalisation (§5.14)
-------------------------
Sequential Gram-Schmidt residualisation within each GICS sector, in the
order specified by ``cfg.factors.orthogonalisation_order`` (default:
momentum → value → quality → sentiment).  Residuals are the z-scored
"pure" factor scores.

References
----------
Grinold & Kahn (2000) ch. 4; Novy-Marx (2013).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from engine.config import Config
from engine.data_loader import PITContext
from engine.types import FactorPayload

logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================
def _winsorise_within_groups(s: pd.Series, groups: pd.Series, lo: float, hi: float) -> pd.Series:
    """Clip to (lo, hi) quantiles within each group label."""
    def _clip(x: pd.Series) -> pd.Series:
        q_lo = x.quantile(lo)
        q_hi = x.quantile(hi)
        return x.clip(q_lo, q_hi)

    return s.groupby(groups, group_keys=False).apply(_clip)


def _zscore_within_groups(s: pd.Series, groups: pd.Series, min_group_size: int = 5) -> pd.Series:
    """Sector-neutral z-score (§3.3 of CW1 report, Eq. 8)."""
    def _z(x: pd.Series) -> pd.Series:
        if len(x) < min_group_size:
            return pd.Series(0.0, index=x.index)   # neutral z for small sectors
        mu = x.mean()
        sigma = x.std(ddof=0)
        if sigma == 0 or np.isnan(sigma):
            return pd.Series(0.0, index=x.index)
        return (x - mu) / sigma

    return s.groupby(groups, group_keys=False).apply(_z)


# =============================================================================
# FactorEngine — swappable in the DI backtest (PLAN §7.1)
# =============================================================================
@dataclass
class FactorEngine:
    """Compute the four raw factor scores from a PITContext."""

    cfg: Config

    # ------------------------------------------------------------------
    def compute_momentum(self, ctx: PITContext) -> pd.Series:
        """12-1 momentum: return from 12 months ago to 1 month ago.

        Uses ~252 trading days for 12m and ~21 for 1m skip.
        """
        px = ctx.prices
        lookback = 252
        skip = 21
        if len(px) < lookback:
            logger.warning("Insufficient price history for momentum: %d rows", len(px))
            return pd.Series(np.nan, index=px.columns, name="momentum")
        p_t_minus_1 = px.iloc[-skip]
        p_t_minus_12 = px.iloc[-lookback]
        mom = (p_t_minus_1 / p_t_minus_12) - 1.0
        return mom.rename("momentum")

    # ------------------------------------------------------------------
    def compute_value(self, ctx: PITContext) -> pd.Series:
        """B/P + E/P + CF/P equal-weighted composite.

        Uses CW1 ``company_ratios`` pre-computed per-share ratios (``_hist``
        variants where available for strict PIT) with fallback to raw
        fundamentals / price.  CW1 Eq. (5).
        """
        ratios = ctx.ratios_pit if hasattr(ctx, "ratios_pit") else None
        # Prefer _hist (PIT-safe) then snapshot then computed
        bp = self._pick_ratio(ctx, ["book_to_price_hist", "book_to_price"])
        ep = self._pick_ratio(ctx, ["earnings_to_price_hist", "earnings_to_price"])
        cfp = self._pick_ratio(ctx, ["cashflow_to_price_hist", "cashflow_to_price"])

        # Fallback to fundamental-derived if ratios missing
        fund = ctx.fundamentals
        px = ctx.prices.iloc[-1]
        if bp.isna().all() and "book_value_per_share" in fund.columns:
            bp = fund["book_value_per_share"] / px.reindex(fund.index)
        if ep.isna().all() and "diluted_eps" in fund.columns:
            ep = fund["diluted_eps"] / px.reindex(fund.index)
        if cfp.isna().all() and "free_cash_flow" in fund.columns:
            # CF per market-cap proxy (normalised by price × shares ≈ market cap)
            cfp = fund["free_cash_flow"] / px.reindex(fund.index)

        # Equal-weighted composite (§3.2 Eq 5) — each sub-factor enters with
        # mean-normalised scale (z-score happens later so raw scale immaterial)
        out = pd.concat([bp, ep, cfp], axis=1).mean(axis=1, skipna=True)
        return out.rename("value")

    # ------------------------------------------------------------------
    def compute_quality(self, ctx: PITContext) -> pd.Series:
        """QMJ-style quality composite — revised 2026-04-22 per IC diagnostic.

        Previous implementation fell through to two broken fallbacks (1/rank(|EPS|)
        for stability; ``eq / (|debt| + 0.01|eq|)`` for inverse D/E) because the
        corresponding CW1 ratios (`earnings_stability`, `debt_to_equity_inv`)
        were single-snapshot (2026-03-20) and dropped out of PIT for every
        pre-2026-03-20 rebalance.  Quality IC was mean = -0.0005, t = -0.04,
        p = 0.96 — economically zero.

        This version prefers the 400+-snapshot ``_hist`` variants that the
        CW1 schema actually carries:

        * **ROE** — ``roe_hist`` (433 snapshots over 2020-2026).  The previous
          first-priority ``roe_computed`` only has a single 2026-03-20 snapshot
          and never contributed PIT values.
        * **Inverse D/E** — ``debt_to_equity_hist`` (433 snapshots) inverted as
          ``1 / (|D/E| + 0.1)`` so zero-debt firms score high without blowing
          up numerically.  The 0.1 offset is the approximate 1st-quartile D/E
          in US large-caps.
        * **Earnings stability** — ``profit_margin_hist`` (431 snapshots) as a
          PIT-safe proxy for QMJ-style earnings stability.  Proper TTM-12Q
          EPS-growth volatility would require multi-snapshot retrieval; margin
          is the cleanest single-column stability correlate and is the
          published QMJ "profitability" sub-factor (Asness-Frazzini-Pedersen
          2019 §III.A).  Documented limitation in Report §7.

        References: Asness, Frazzini & Pedersen (2019) "Quality Minus Junk";
        Novy-Marx (2013) "The Other Side of Value".
        """
        # ROE — prefer the 433-snapshot time series
        roe = self._pick_ratio(ctx, ["roe_hist", "roe_computed", "return_on_equity"])

        # Inverse D/E from the 433-snapshot time series
        de_hist = self._pick_ratio(ctx, ["debt_to_equity_hist"])
        if not de_hist.isna().all():
            inv_de = 1.0 / (de_hist.abs() + 0.1)
        else:
            inv_de = self._pick_ratio(ctx, ["debt_to_equity_inv"])

        # Earnings-stability proxy — profit_margin_hist preferred over the
        # 1-snapshot `earnings_stability` column.
        stab = self._pick_ratio(
            ctx, ["profit_margin_hist", "operating_margin_hist", "earnings_stability"]
        )

        # Fallback — derive ROE from fundamentals if every ratio column missing
        if roe.isna().all():
            fund = ctx.fundamentals
            ni = fund.get("net_income", pd.Series(np.nan, index=fund.index))
            eq = fund.get("stockholders_equity", pd.Series(np.nan, index=fund.index))
            roe = ni / eq.replace(0, np.nan)
        if inv_de.isna().all():
            fund = ctx.fundamentals
            eq = fund.get("stockholders_equity", pd.Series(np.nan, index=fund.index))
            debt = fund.get("total_debt", pd.Series(np.nan, index=fund.index))
            inv_de = 1.0 / ((debt.abs() / eq.abs().replace(0, np.nan)).clip(lower=0) + 0.1)
        if stab.isna().all():
            fund = ctx.fundamentals
            rev = fund.get("total_revenue", pd.Series(np.nan, index=fund.index))
            ni = fund.get("net_income", pd.Series(np.nan, index=fund.index))
            stab = ni / rev.replace(0, np.nan)

        out = pd.concat([roe, stab, inv_de], axis=1).mean(axis=1, skipna=True)
        return out.rename("quality")

    # ------------------------------------------------------------------
    @staticmethod
    def _pick_ratio(ctx: PITContext, candidates: list[str]) -> pd.Series:
        """Pick first available column from CW1 ratios in priority order."""
        ratios = getattr(ctx, "ratios", None)
        if ratios is None or ratios.empty:
            return pd.Series(np.nan, index=[])
        for c in candidates:
            if c in ratios.columns:
                return ratios[c].copy()
        return pd.Series(np.nan, index=ratios.index)

    # ------------------------------------------------------------------
    def compute_sentiment(self, ctx: PITContext) -> pd.Series:
        """Retrieve pre-computed VADER-composite sentiment scores."""
        return ctx.sentiment.rename("sentiment")

    # ==================================================================
    def compute_all(self, ctx: PITContext) -> FactorPayload:
        """Compute all four raw factor series at ``ctx.rebalance_date``.

        Returns a FactorPayload with per-symbol dicts (drops NaN symbols).
        """
        mom = self.compute_momentum(ctx).dropna()
        val = self.compute_value(ctx).dropna()
        qual = self.compute_quality(ctx).dropna()
        sent = self.compute_sentiment(ctx).dropna()
        return FactorPayload(
            date=ctx.rebalance_date,
            momentum=mom.to_dict(),
            value=val.to_dict(),
            quality=qual.to_dict(),
            sentiment=sent.to_dict(),
        )

    # ==================================================================
    def to_long_df(self, payload: FactorPayload, universe_symbols: list[str]) -> pd.DataFrame:
        """Cross-section dataframe indexed by symbol, columns = 4 raw factors."""
        df = pd.DataFrame(index=universe_symbols)
        df["momentum"] = pd.Series(payload.momentum)
        df["value"] = pd.Series(payload.value)
        df["quality"] = pd.Series(payload.quality)
        df["sentiment"] = pd.Series(payload.sentiment)
        return df


# =============================================================================
# Gram-Schmidt sector-neutral orthogonalisation (§5.14)
# =============================================================================
def orthogonalise(
    raw: pd.DataFrame,
    gics_map: dict[str, str],
    order: Optional[list[str]] = None,
    min_group_size: int = 5,
) -> pd.DataFrame:
    """Sector-neutral sequential Gram-Schmidt residualisation.

    For each factor f (in order), regress f on already-orthogonalised prior
    factors *within GICS sector* and retain residuals.  The first factor in
    order is used raw (already z-scored later).

    Parameters
    ----------
    raw : DataFrame
        Cross-section indexed by symbol, columns = factor names.
    gics_map : dict
        symbol → GICS sector label.
    order : list[str] | None
        Residualisation order; defaults to columns of ``raw``.
    min_group_size : int
        Sectors with < this many names skip residualisation (retain raw).

    Returns
    -------
    DataFrame
        Same shape as ``raw`` with residualised columns.
    """
    if order is None:
        order = list(raw.columns)
    # Align sector labels
    sec = pd.Series({s: gics_map.get(s, "Unknown") for s in raw.index}, name="sector")
    out = pd.DataFrame(index=raw.index)
    out[order[0]] = raw[order[0]]
    for i, f in enumerate(order[1:], start=1):
        f_current = raw[f].copy()
        priors = order[:i]
        residual = f_current.copy()
        # Per-sector OLS residualisation
        for sector_name, group_idx in sec.groupby(sec).groups.items():
            idx = list(group_idx)
            if len(idx) < min_group_size:
                continue
            y = f_current.loc[idx]
            X = out.loc[idx, priors].copy()
            # Drop rows with NaN in y or X
            mask = y.notna() & X.notna().all(axis=1)
            if mask.sum() < min_group_size:
                continue
            y_fit = y[mask].values.astype(float)
            X_fit = X[mask].values.astype(float)
            # OLS: beta = (X'X)^-1 X'y
            XtX = X_fit.T @ X_fit
            try:
                beta = np.linalg.solve(XtX, X_fit.T @ y_fit)
            except np.linalg.LinAlgError:
                continue
            pred = X_fit @ beta
            residual.loc[y[mask].index] = y_fit - pred
        out[f] = residual
    return out


__all__ = ["FactorEngine", "orthogonalise"]
