"""Monte Carlo path simulation via circular block bootstrap (PLAN §7.5).

Reads ``output/portfolio_returns.parquet`` and produces
``output/monte_carlo_paths.parquet`` — 10,000 bootstrap NAV paths for the
Dynamic Net 20bp return series, with block length 6 months per Politis &
Romano (1994).

The output schema (``MonteCarloRow`` in ``engine/types.py``):
    path_id: int   — bootstrap iteration 0..N-1
    date: date     — rebalance date
    nav: float     — cumulative NAV starting from 1.0

Downstream: ``analytics/charts.py`` renders the 5th/95th percentile envelope
around the realised path ("Strategy Performance Envelope") and exposes the
terminal-Sharpe / max-DD / Calmar distributions required by the fund-pitch
section (Report §6).

References
----------
Politis, D. N. & Romano, J. P. (1994). 'The stationary bootstrap'.
López de Prado (2018) §7.5.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def circular_block_bootstrap_paths(
    returns: pd.Series,
    n_paths: int = 10_000,
    block_size: int = 6,
    seed: int = 42,
    start_nav: float = 1.0,
) -> pd.DataFrame:
    """Generate bootstrap NAV paths using circular block bootstrap.

    Parameters
    ----------
    returns : Series
        Monthly return series (indexed by date).  ``Series.index`` is carried
        through to the output for alignment with the realised path.
    n_paths : int
        Number of bootstrap iterations (10,000 per PLAN §7.5).
    block_size : int
        Block length in periods (6 months per §7.5).
    seed : int
        RNG seed for reproducibility — keeps CI bit-level reproducible.
    start_nav : float
        Initial NAV (default 1.0 so terminal NAV is a growth multiple).

    Returns
    -------
    DataFrame with columns ``path_id, date, nav``.
    """
    r = pd.to_numeric(returns, errors="coerce").dropna()
    T = len(r)
    if T < block_size * 2:
        logger.warning(
            "MC bootstrap: series too short (T=%d, block=%d). Returning realised path.",
            T, block_size,
        )
        nav = (1 + r).cumprod() * start_nav
        return pd.DataFrame({"path_id": 0, "date": nav.index, "nav": nav.values})

    rng = np.random.default_rng(seed)
    values = r.values
    dates = r.index
    n_blocks = int(np.ceil(T / block_size))
    rows = []
    for pid in range(n_paths):
        starts = rng.integers(0, T, size=n_blocks)
        # Circular wrap: for each starting index, take block_size consecutive
        # observations with modulo T.
        idx = (starts[:, None] + np.arange(block_size)[None, :]) % T
        sample = values[idx.flatten()][:T]
        nav = np.cumprod(1 + sample) * start_nav
        for d, v in zip(dates, nav):
            rows.append({"path_id": pid, "date": d, "nav": float(v)})
    return pd.DataFrame(rows)


def run_monte_carlo(
    out_dir: str | Path = "output",
    returns_col: str = "dynamic_net_20bp",
    n_paths: int = 10_000,
    block_size: int = 6,
    seed: int = 42,
) -> pd.DataFrame:
    """Load portfolio_returns, produce bootstrap NAV paths, write parquet."""
    out_dir = Path(out_dir)
    returns_df = pd.read_parquet(out_dir / "portfolio_returns.parquet")
    returns_df["date"] = pd.to_datetime(returns_df["date"])
    returns_df = returns_df.sort_values("date").set_index("date")
    if returns_col not in returns_df.columns:
        raise KeyError(
            f"Column {returns_col!r} not in portfolio_returns.parquet — "
            f"available: {list(returns_df.columns)}"
        )
    series = returns_df[returns_col].dropna()
    logger.info(
        "Monte Carlo: %d paths × %d months, block=%d (source=%s)",
        n_paths, len(series), block_size, returns_col,
    )
    paths = circular_block_bootstrap_paths(
        series, n_paths=n_paths, block_size=block_size, seed=seed,
    )
    out_path = out_dir / "monte_carlo_paths.parquet"
    paths.to_parquet(out_path, index=False)
    logger.info("Wrote %s (%d rows)", out_path, len(paths))
    return paths


__all__ = ["circular_block_bootstrap_paths", "run_monte_carlo"]
