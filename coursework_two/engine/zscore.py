"""Sector-neutral cross-sectional z-scoring (CW1 report Eq. 8).

``z_{i,f,t} = (x_{i,f,t} − μ_{s,f,t}) / σ_{s,f,t}``

where s = GICS sector of stock i.  Sectors with fewer than ``min_sector_size``
stocks at date t are assigned neutral z=0 for every constituent.

Also supports winsorisation at configurable percentiles, composite weighting,
and per-factor IC computation (Spearman) for downstream diagnostics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from engine.config import Config
from engine.factors import _winsorise_within_groups, _zscore_within_groups, orthogonalise

logger = logging.getLogger(__name__)


@dataclass
class ZScoreEngine:
    cfg: Config

    # ------------------------------------------------------------------
    def zscore_cross_section(
        self,
        raw: pd.DataFrame,
        gics_map: dict[str, str],
        winsorise: bool = True,
    ) -> pd.DataFrame:
        """Apply per-factor sector-neutral z-score with optional winsorisation.

        Parameters
        ----------
        raw : DataFrame
            Symbol × factor cross-section of raw scores.
        gics_map : dict
            symbol → sector label.
        winsorise : bool
            If True, winsorise at (winsor_lower_pct, winsor_upper_pct) within
            sector before z-scoring.

        Returns
        -------
        DataFrame
            Same shape as ``raw`` with z-scored values.
        """
        fcfg = self.cfg.factors
        sectors = pd.Series({s: gics_map.get(s, "Unknown") for s in raw.index})
        out = pd.DataFrame(index=raw.index)
        for f in raw.columns:
            s = raw[f].copy()
            if winsorise:
                s = _winsorise_within_groups(s, sectors, fcfg.winsor_lower_pct, fcfg.winsor_upper_pct)
            z = _zscore_within_groups(s, sectors, fcfg.min_sector_size)
            out[f] = z
        return out

    # ------------------------------------------------------------------
    def apply_orthogonalisation(
        self, z: pd.DataFrame, gics_map: dict[str, str]
    ) -> pd.DataFrame:
        """Sequential Gram-Schmidt residualisation (§5.14) then re-z."""
        if not self.cfg.factors.orthogonalise:
            return z
        # Residualise within-sector, then z-score again for unit scale
        res = orthogonalise(
            z,
            gics_map,
            order=self.cfg.factors.orthogonalisation_order,
            min_group_size=self.cfg.factors.min_sector_size,
        )
        sectors = pd.Series({s: gics_map.get(s, "Unknown") for s in res.index})
        re_z = pd.DataFrame(index=res.index)
        for f in res.columns:
            re_z[f] = _zscore_within_groups(
                res[f], sectors, self.cfg.factors.min_sector_size
            )
        return re_z

    # ------------------------------------------------------------------
    def composite(
        self,
        z: pd.DataFrame,
        weights: Optional[dict[str, float]] = None,
    ) -> pd.Series:
        """Weighted sum composite z-score.

        Defaults to ``cfg.factors.base_weights`` if ``weights`` is None
        (the implemented composite is 50/50 momentum + value; quality and
        sentiment carry zero weight per the IC-based factor reduction).

        **Sentinel-coverage safeguard**: if any factor has zero non-zero
        coverage across the universe (e.g. sentiment missing pre-snapshot),
        its weight is proportionally redistributed to the other factors.
        This is a standard industry-practice fallback and avoids "wasting"
        composite weight on a constant-zero signal.
        """
        if weights is None:
            bw = self.cfg.factors.base_weights
            weights = {
                "momentum": bw.momentum,
                "value": bw.value,
                "quality": bw.quality,
                "sentiment": bw.sentiment,
            }
        cols = [c for c in z.columns if c in weights]
        # Detect zero-coverage factors
        effective = weights.copy()
        missing_sum = 0.0
        for c in cols:
            if z[c].notna().sum() == 0 or z[c].abs().sum() < 1e-10:
                missing_sum += effective.get(c, 0.0)
                effective[c] = 0.0
        if missing_sum > 0 and missing_sum < 0.999:
            scale = 1.0 / (1.0 - missing_sum)
            effective = {c: v * scale for c, v in effective.items()}
        w = np.array([effective[c] for c in cols], dtype=float)
        return pd.Series(
            z[cols].fillna(0.0).values @ w, index=z.index, name="composite"
        )

    # ------------------------------------------------------------------
    def factor_ic(
        self,
        z: pd.DataFrame,
        forward_returns: pd.Series,
    ) -> pd.DataFrame:
        """Per-factor IC — Spearman rank correlation with forward returns.

        Returns a DataFrame with one row per factor.
        """
        rows = []
        for f in z.columns:
            merged = pd.concat([z[f], forward_returns.rename("ret")], axis=1).dropna()
            if len(merged) < 5:
                rows.append({"factor": f, "ic_spearman": np.nan, "ic_pearson": np.nan, "n": len(merged)})
                continue
            rho, _ = spearmanr(merged[f], merged["ret"])
            pear = merged[f].corr(merged["ret"])
            rows.append(
                {"factor": f, "ic_spearman": float(rho), "ic_pearson": float(pear), "n": len(merged)}
            )
        return pd.DataFrame(rows)


__all__ = ["ZScoreEngine"]
