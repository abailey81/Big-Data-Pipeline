"""
portfolio/risk_overlay.py — Market Regime Risk Overlay

Three classical signals (all using only data available BEFORE the decision date):
  1. S&P 500 price > 200-day moving average (trend filter)
  2. S&P 500 trailing 12-month return > 0 (time-series momentum)
  3. VIX < trailing 3-year 80th percentile (volatility regime)

Exposure mapping:
  3 signals OK → 100% equity
  2 signals OK → 80% equity + 20% cash
  1 signal OK  → 60% equity + 40% cash
  0 signals OK → 40% equity + 60% cash

Uninvested portion earns risk-free rate (cash return).

References:
  - Faber (2007): "A Quantitative Approach to Tactical Asset Allocation"
  - 200DMA as trend filter: Brock, Lakonishok & LeBaron (1992)
  - VIX regime: Ang et al. (2006)

This is NOT parameter-optimized on our sample. All thresholds are standard values
from published literature.
"""

import pandas as pd
import numpy as np


def compute_regime_exposure(
    sp500_close: pd.Series,
    vix_close: pd.Series,
    as_of: pd.Timestamp,
) -> float:
    """Compute equity exposure based on 3-signal regime classification.

    All signals use ONLY data on or before as_of (no look-ahead).

    Parameters
    ----------
    sp500_close : S&P 500 daily close prices, indexed by date.
    vix_close : VIX daily close prices, indexed by date.
    as_of : decision date.

    Returns
    -------
    Exposure fraction in [0.40, 1.00].
    """
    signals_ok = 0

    # Signal 1: S&P 500 > 200DMA
    sp_available = sp500_close.loc[sp500_close.index <= as_of]
    if len(sp_available) >= 200:
        current_price = sp_available.iloc[-1]
        dma_200 = sp_available.iloc[-200:].mean()
        if current_price > dma_200:
            signals_ok += 1

    # Signal 2: S&P 500 trailing 12-month return > 0
    if len(sp_available) >= 252:
        ret_12m = sp_available.iloc[-1] / sp_available.iloc[-252] - 1
        if ret_12m > 0:
            signals_ok += 1

    # Signal 3: VIX < trailing 3-year 80th percentile
    vix_available = vix_close.loc[vix_close.index <= as_of]
    if len(vix_available) >= 252:  # at least 1 year
        window = min(len(vix_available), 756)  # up to 3 years
        vix_window = vix_available.iloc[-window:]
        p80 = vix_window.quantile(0.80)
        current_vix = vix_available.iloc[-1]
        if current_vix < p80:
            signals_ok += 1

    # Exposure mapping
    exposure_map = {3: 1.00, 2: 0.80, 1: 0.60, 0: 0.40}
    return exposure_map[signals_ok]


def apply_risk_overlay(
    period_returns: pd.Series,
    sp500_close: pd.Series,
    vix_close: pd.Series,
    rf_series: pd.Series,
    rebalance_dates: list,
) -> tuple:
    """Apply risk overlay to a return series.

    For each period, scales equity return by regime exposure and
    adds cash return (RF) for the uninvested portion.

    Parameters
    ----------
    period_returns : gross or net strategy returns, indexed by rebalance date.
    sp500_close : S&P 500 daily close.
    vix_close : VIX daily close.
    rf_series : annualized risk-free rate (%), indexed by date.
    rebalance_dates : list of rebalance Timestamps.

    Returns
    -------
    (adjusted_returns, exposures) : tuple of pd.Series.
    """
    adjusted = []
    exposures = []

    for dt in period_returns.index:
        # Compute exposure using data BEFORE this period's return is realized
        # The signal is computed at the START of the period (previous rebalance date)
        # For safety, use the date itself (end of period) as decision date
        # This is conservative: signal at month-end, applied to NEXT period
        prev_dates = [d for d in rebalance_dates if d < dt]
        signal_date = prev_dates[-1] if prev_dates else dt

        exposure = compute_regime_exposure(sp500_close, vix_close, signal_date)
        exposures.append(exposure)

        # Equity return scaled by exposure
        equity_return = period_returns[dt] * exposure

        # Cash return for uninvested portion
        rf_at = rf_series.loc[rf_series.index <= signal_date]
        if len(rf_at) > 0:
            rf_annual = rf_at.iloc[-1] / 100.0  # convert from % to decimal
        else:
            rf_annual = 0.0

        # Estimate periods per year from the return series
        if len(period_returns) >= 2:
            avg_gap = (period_returns.index[-1] - period_returns.index[0]).days / (len(period_returns) - 1)
            ppy = 365.25 / max(avg_gap, 1)
        else:
            ppy = 12.0

        rf_period = (1 + rf_annual) ** (1 / ppy) - 1
        cash_return = (1 - exposure) * rf_period

        total_return = equity_return + cash_return
        adjusted.append(total_return)

    return (
        pd.Series(adjusted, index=period_returns.index),
        pd.Series(exposures, index=period_returns.index),
    )
