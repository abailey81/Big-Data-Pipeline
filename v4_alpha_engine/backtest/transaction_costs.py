"""
backtest/transaction_costs.py -- Integrated transaction cost model.

TC = c_trade * turnover_long + c_trade * turnover_short
   + c_borrow * short_notional * (days / 252)

Separate long and short two-way turnover.
Borrowing cost scales with actual short notional (VaR-adjusted).
"""


def compute_transaction_cost(
    turnover_long: float,
    turnover_short: float,
    trading_bps: float = 20,
    borrowing_bps: float = 50,
    days_in_period: int = 21,
    short_notional: float = 1.0,
) -> float:
    """Compute total period transaction cost.

    Parameters
    ----------
    turnover_long : two-way turnover on long leg.
    turnover_short : two-way turnover on short leg.
    trading_bps : one-way proportional trading cost in basis points.
    borrowing_bps : annualised borrowing cost in basis points.
    days_in_period : number of trading days in this period.
    short_notional : absolute value of total short notional (VaR-scaled).

    Returns
    -------
    Total period cost as a decimal fraction.
    """
    c_trade = trading_bps / 10000.0
    c_borrow = borrowing_bps / 10000.0

    trade_cost_long = c_trade * turnover_long
    trade_cost_short = c_trade * turnover_short
    borrow_cost = c_borrow * short_notional * (days_in_period / 252.0)

    return trade_cost_long + trade_cost_short + borrow_cost
