"""Company ratio computation and download stage functions."""

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

from modules.db_ops.kafka_ops import TOPICS
from modules.orchestration.state import check_shutdown, inactive_tickers, make_log_entry
from modules.utils import pipeline_logger


def compute_historical_ratios(
    db_client,
    ticker_map,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
):
    """Compute historical financial ratios from fundamentals + daily_prices.

    Derives 6-year time-series ratios by joining the fundamentals EAV table
    with daily price data. This produces historical P/E, D/E, ROE, margins,
    and other ratios that cannot be obtained from yfinance snapshots.

    Inserts into the company_ratios table (same EAV schema as yfinance
    snapshot ratios) with snapshot_date set to the fundamentals report_date.
    """
    import bisect

    # No skip-if-done check: this phase is DB-only (no API calls, ~35s total).
    # Always recompute to ensure new ratio formulas are applied to all tickers.
    # upsert_company_ratios uses ON CONFLICT DO UPDATE — safe for re-runs.
    need_compute = [t for t in ticker_map if t[0] not in inactive_tickers()]
    pipeline_logger.info(
        f"Computing historical ratios from fundamentals + prices "
        f"({len(need_compute)}/{len(ticker_map)} tickers)..."
    )

    total_loaded = 0
    _total_lock = threading.Lock()
    hist_workers = 8

    def _process_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if check_shutdown("historical_ratios"):
            return
        if db_symbol in inactive_tickers():
            if metrics:
                metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                make_log_entry(run_id, "historical_ratios", db_symbol, "SKIPPED", 0, "inactive", frequency)
            )
            return

        try:
            # Fetch all fundamentals for this ticker
            fund_rows = db_client.read_query(
                "SELECT field_name, field_value, report_date, period_type "
                "FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym "
                "ORDER BY report_date",
                {"sym": db_symbol},
            )
            if not fund_rows:
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
                return

            # Build lookup: (field_name, report_date, period_type) -> value
            fund_lookup = {}
            report_dates = set()
            for fname, fval, rdate, ptype in fund_rows:
                fund_lookup[(fname, rdate, ptype)] = float(fval) if fval is not None else None
                report_dates.add((rdate, ptype))

            # Fetch closest price for each report date
            price_rows = db_client.read_query(
                "SELECT cob_date, close_price "
                "FROM systematic_equity.daily_prices "
                "WHERE TRIM(symbol) = :sym AND close_price IS NOT NULL "
                "ORDER BY cob_date",
                {"sym": db_symbol},
            )
            price_lookup = {}
            if price_rows:
                for cob, close in price_rows:
                    price_lookup[cob] = float(close)

            # Fetch shares_outstanding from ratios table as fallback
            # (more reliable than deriving from equity / book_value)
            shares_fallback = None
            try:
                shares_rows = db_client.read_query(
                    "SELECT field_value FROM systematic_equity.company_ratios "
                    "WHERE TRIM(symbol) = :sym AND field_name = 'shares_outstanding' "
                    "ORDER BY snapshot_date DESC LIMIT 1",
                    {"sym": db_symbol},
                )
                if shares_rows and shares_rows[0][0]:
                    shares_fallback = float(shares_rows[0][0])
            except Exception:
                pass

            # For each report period, compute ratios
            records = []
            seen = set()
            sorted_prices = sorted(price_lookup.keys()) if price_lookup else []

            for report_date, period_type in sorted(report_dates):
                # Find closest price on or after report_date
                close_price = None
                if sorted_prices:
                    idx = bisect.bisect_left(sorted_prices, report_date)
                    if idx < len(sorted_prices):
                        close_price = price_lookup[sorted_prices[idx]]
                    elif idx > 0:
                        close_price = price_lookup[sorted_prices[idx - 1]]

                def _get(field):
                    return fund_lookup.get((field, report_date, period_type))

                def _add(field_name, value):
                    if value is not None and not (
                        isinstance(value, float) and (abs(value) > 1e15 or value != value)
                    ):
                        key = (field_name, report_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": report_date,
                                "field_name": field_name,
                                "field_value": round(value, 6),
                            })

                net_income = _get("net_income")
                equity = _get("stockholders_equity")
                total_debt = _get("total_debt")
                total_rev = _get("total_revenue")
                operating_inc = _get("operating_income")
                ebitda_val = _get("ebitda")
                total_assets = _get("total_assets")
                total_liab = _get("total_liabilities")
                ocf = _get("operating_cash_flow")
                capex = _get("capital_expenditure")
                fcf = _get("free_cash_flow")
                diluted_eps = _get("diluted_eps")
                basic_eps = _get("basic_eps")
                gross_profit = _get("gross_profit")
                book_val = _get("book_value")

                # ── Derive missing fields from available data ──
                # equity fallback: total_assets - total_liabilities
                if equity is None and total_assets and total_liab:
                    equity = total_assets - total_liab

                # EPS fallback: use basic_eps when diluted_eps missing
                eps = diluted_eps if diluted_eps is not None else basic_eps

                # FCF fallback: operating_cash_flow - |capital_expenditure|
                if fcf is None and ocf is not None and capex is not None:
                    fcf = ocf - abs(capex)

                # Derive book_value from equity when missing
                if book_val is None and equity is not None:
                    book_val = equity

                # Derive shares: prefer equity/book_value, fallback to shares_outstanding
                shares = None
                if book_val and book_val != 0 and equity:
                    shares = equity / book_val
                if (shares is None or shares <= 0) and shares_fallback:
                    shares = shares_fallback

                # Market cap (reused across multiple ratios)
                mcap = None
                if close_price and shares and shares > 0:
                    mcap = close_price * shares

                # ── P/E ratio ──
                # Primary: price / diluted_eps
                # Fallback: price / basic_eps
                if close_price and eps and eps != 0:
                    _add("pe_ratio_hist", close_price / eps)

                # ── D/E ratio ──
                # Primary: total_debt / stockholders_equity
                # Fallback: total_debt / (total_assets - total_liabilities)
                if total_debt is not None and equity and equity != 0:
                    _add("debt_to_equity_hist", total_debt / equity)

                # ── ROE ──
                # Primary: net_income / stockholders_equity
                # Fallback: net_income / (total_assets - total_liabilities)
                if net_income is not None and equity and equity != 0:
                    _add("roe_hist", net_income / equity)

                # ── Profit margin ──
                if net_income is not None and total_rev and total_rev != 0:
                    _add("profit_margin_hist", net_income / total_rev)

                # ── Gross margin ──
                # Primary: gross_profit / total_revenue
                # Fallback: (total_revenue - (total_revenue - gross_profit)) — circular
                # No valid fallback without cost_of_revenue
                if gross_profit is not None and total_rev and total_rev != 0:
                    _add("gross_margin_hist", gross_profit / total_rev)

                # ── Operating margin ──
                # Primary: operating_income / total_revenue
                # Fallback 1: ebitda / total_revenue (EBITDA margin)
                # Fallback 2: (net_income + interest + tax) / total_revenue — no interest/tax fields
                if operating_inc is not None and total_rev and total_rev != 0:
                    _add("operating_margin_hist", operating_inc / total_rev)
                elif ebitda_val is not None and total_rev and total_rev != 0:
                    _add("operating_margin_hist", ebitda_val / total_rev)

                # ── EV/EBITDA ──
                # EV = market_cap + total_debt
                if ebitda_val and ebitda_val != 0 and mcap and total_debt is not None:
                    ev = mcap + total_debt
                    _add("ev_to_ebitda_hist", ev / ebitda_val)

                # ── ROA ──
                if net_income is not None and total_assets and total_assets != 0:
                    _add("roa_hist", net_income / total_assets)

                # ── Assets to liabilities ──
                if total_assets and total_liab and total_liab != 0:
                    _add("assets_to_liab_hist", total_assets / total_liab)

                # ── Debt to assets ──
                if total_debt is not None and total_assets and total_assets != 0:
                    _add("debt_to_assets_hist", total_debt / total_assets)

                # ── Equity ratio (equity / total_assets) ──
                if equity and total_assets and total_assets != 0:
                    _add("equity_ratio_hist", equity / total_assets)

                # ── FCF yield ──
                # Primary: free_cash_flow / market_cap
                # Fallback: (operating_cash_flow - |capex|) / market_cap
                if fcf is not None and mcap and mcap > 0:
                    _add("fcf_yield_hist", fcf / mcap)

                # ── FCF margin ──
                if fcf is not None and total_rev and total_rev != 0:
                    _add("fcf_margin_hist", fcf / total_rev)

                # ── OCF to debt (cash flow coverage) ──
                if ocf is not None and total_debt and total_debt != 0:
                    _add("ocf_to_debt_hist", ocf / total_debt)

                # ── Earnings yield (E/P) ──
                # Primary: diluted_eps / price
                # Fallback: basic_eps / price
                if close_price and close_price != 0 and eps is not None:
                    _add("earnings_to_price_hist", eps / close_price)

                # ── Cashflow to price (OCF/P) ──
                if ocf is not None and mcap and mcap > 0:
                    _add("cashflow_to_price_hist", ocf / mcap)

                # ── Revenue to market cap (sales yield) ──
                if total_rev and mcap and mcap > 0:
                    _add("revenue_to_mcap_hist", total_rev / mcap)

                # ── EBITDA margin ──
                if ebitda_val is not None and total_rev and total_rev != 0:
                    _add("ebitda_margin_hist", ebitda_val / total_rev)

                # ── Interest coverage proxy (EBITDA / interest) ──
                # interest ≈ operating_income - net_income (very rough, includes tax)
                # Better: EBITDA / (total_debt * assumed_rate) — too speculative
                # Skip: no clean derivation without explicit interest expense

                # ── Book to price ──
                # Primary: book_value_per_share / price
                # Fallback: (equity / shares) / price
                bvps = book_val
                if bvps is None and equity and shares and shares > 0:
                    bvps = equity / shares
                if close_price and close_price != 0 and bvps and bvps > 0:
                    _add("book_to_price_hist", bvps / close_price)

                # ── Price to book (inverse of book_to_price) ──
                if close_price and bvps and bvps > 0:
                    _add("price_to_book_hist", close_price / bvps)

                # ── Revenue growth (sequential QoQ/YoY) ──
                # Handled in the sequential loop below

            # ── Sequential growth metrics: QoQ comparison ──
            quarterly_dates = sorted(
                [rd for rd, pt in report_dates if pt == "quarterly"]
            )
            for i in range(1, len(quarterly_dates)):
                prev_date = quarterly_dates[i - 1]
                curr_date = quarterly_dates[i]

                # Earnings growth (EPS QoQ)
                prev_eps = fund_lookup.get(("diluted_eps", prev_date, "quarterly"))
                curr_eps = fund_lookup.get(("diluted_eps", curr_date, "quarterly"))
                if prev_eps is None:
                    prev_eps = fund_lookup.get(("basic_eps", prev_date, "quarterly"))
                if curr_eps is None:
                    curr_eps = fund_lookup.get(("basic_eps", curr_date, "quarterly"))
                if prev_eps is not None and curr_eps is not None and abs(prev_eps) > 0.001:
                    growth = (curr_eps - prev_eps) / abs(prev_eps)
                    if abs(growth) < 100:
                        key = ("earnings_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "earnings_growth_hist",
                                "field_value": round(growth, 6),
                            })

                # Revenue growth (QoQ)
                prev_rev = fund_lookup.get(("total_revenue", prev_date, "quarterly"))
                curr_rev = fund_lookup.get(("total_revenue", curr_date, "quarterly"))
                if prev_rev is not None and curr_rev is not None and abs(prev_rev) > 0.001:
                    rev_growth = (curr_rev - prev_rev) / abs(prev_rev)
                    if abs(rev_growth) < 100:
                        key = ("revenue_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "revenue_growth_hist",
                                "field_value": round(rev_growth, 6),
                            })

                # Net income growth (QoQ)
                prev_ni = fund_lookup.get(("net_income", prev_date, "quarterly"))
                curr_ni = fund_lookup.get(("net_income", curr_date, "quarterly"))
                if prev_ni is not None and curr_ni is not None and abs(prev_ni) > 0.001:
                    ni_growth = (curr_ni - prev_ni) / abs(prev_ni)
                    if abs(ni_growth) < 100:
                        key = ("net_income_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "net_income_growth_hist",
                                "field_value": round(ni_growth, 6),
                            })

                # OCF growth (QoQ)
                prev_ocf = fund_lookup.get(("operating_cash_flow", prev_date, "quarterly"))
                curr_ocf = fund_lookup.get(("operating_cash_flow", curr_date, "quarterly"))
                if prev_ocf is not None and curr_ocf is not None and abs(prev_ocf) > 0.001:
                    ocf_growth = (curr_ocf - prev_ocf) / abs(prev_ocf)
                    if abs(ocf_growth) < 100:
                        key = ("ocf_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "ocf_growth_hist",
                                "field_value": round(ocf_growth, 6),
                            })

            if records:
                n = db_client.upsert_company_ratios(records)
                with _total_lock:
                    total_loaded += n
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SUCCESS", n)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
                db_client.insert_log(
                    make_log_entry(
                        run_id, "historical_ratios", db_symbol, "SUCCESS", n,
                        frequency=frequency,
                    )
                )
            else:
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
        except Exception as e:
            if metrics:
                metrics.record_outcome("historical_ratios", db_symbol, "FAILED")
            if progress_update:
                progress_update(db_symbol, "FAILED")
            db_client.insert_log(
                make_log_entry(
                    run_id, "historical_ratios", db_symbol, "FAILED", 0,
                    str(e), frequency,
                )
            )
            pipeline_logger.debug(f"Historical ratios failed for {db_symbol}: {e}")

    pool = ThreadPoolExecutor(max_workers=hist_workers)
    try:
        futures = [pool.submit(_process_ticker, item) for item in need_compute]
        done, pending = futures_wait(futures, timeout=300)
        for future in done:
            try:
                future.result()
            except Exception as e:
                pipeline_logger.error(f"Historical ratios thread error: {e}")
    finally:
        pool.shutdown(wait=False)

    pipeline_logger.info(f"Historical ratios: computed {total_loaded} records total")
    db_client.update_pipeline_metadata("historical_ratios")


# Financial ratios we extract from yfinance Ticker.info
RATIO_FIELDS = {
    "marketCap": "market_cap",
    "trailingPE": "pe_ratio_trailing",
    "forwardPE": "pe_ratio_forward",
    "priceToBook": "price_to_book",
    "enterpriseToEbitda": "ev_to_ebitda",
    "enterpriseValue": "enterprise_value",
    "dividendYield": "dividend_yield",
    "beta": "beta",
    "returnOnEquity": "return_on_equity",
    "debtToEquity": "debt_to_equity",
    "currentRatio": "current_ratio",
    "operatingMargins": "operating_margin",
    "profitMargins": "profit_margin",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "trailingEps": "trailing_eps",
    "forwardEps": "forward_eps",
    "pegRatio": "peg_ratio",
    "shortRatio": "short_ratio",
    "fiftyTwoWeekHigh": "fifty_two_week_high",
    "fiftyTwoWeekLow": "fifty_two_week_low",
    "sharesOutstanding": "shares_outstanding",
    "floatShares": "float_shares",
    "bookValue": "book_value_per_share",
    "freeCashflow": "free_cash_flow",
    "operatingCashflow": "operating_cash_flow",
    "totalRevenue": "total_revenue_ttm",
    "grossMargins": "gross_margin",
}

# Finnhub /stock/metric fields → canonical ratio names (US tickers only on free tier)
FINNHUB_METRIC_FIELDS = {
    "marketCapitalization": "market_cap",
    "peNormalizedAnnual": "pe_ratio_trailing",
    "priceToBookAnnual": "price_to_book",
    "dividendYieldIndicatedAnnual": "dividend_yield",
    "beta": "beta",
    "roeTTM": "return_on_equity",
    "debtEquityAnnual": "debt_to_equity",
    "currentRatioAnnual": "current_ratio",
    "operatingMarginAnnual": "operating_margin",
    "netProfitMarginTTM": "profit_margin",
    "revenueGrowth3Y": "revenue_growth",
    "epsNormalizedAnnual": "trailing_eps",
    "52WeekHigh": "fifty_two_week_high",
    "52WeekLow": "fifty_two_week_low",
    "grossMarginAnnual": "gross_margin",
    "totalSharesOutstanding": "shares_outstanding",
    "bookValueShareAnnual": "book_value_per_share",
    "freeCashFlowTTM": "free_cash_flow",
    "operatingCashFlowTTM": "operating_cash_flow",
    "enterpriseValueAnnual": "enterprise_value",
    "evEbitdaAnnual": "ev_to_ebitda",
}


def _extract_ratios_from_info(info: dict, db_symbol: str) -> list[dict]:
    """Extract financial ratios from yfinance Ticker.info dict.

    :param info: Ticker.info dictionary from yfinance
    :param db_symbol: Database symbol
    :return: List of ratio record dicts for company_ratios table
    """
    from datetime import date

    import numpy as np

    if not info or not isinstance(info, dict):
        return []

    today = date.today()
    records = []

    for yf_key, canonical_name in RATIO_FIELDS.items():
        val = info.get(yf_key)
        if val is None:
            continue
        try:
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval):
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical_name,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError):
            continue

    # ── Default dividend_yield to 0.0 if not present ──
    # Non-dividend payers have no dividendYield in yfinance info.
    # A yield of 0.0 is factually correct (not NULL).
    if not any(r["field_name"] == "dividend_yield" for r in records):
        records.append({
            "symbol": db_symbol,
            "snapshot_date": today,
            "field_name": "dividend_yield",
            "field_value": 0.0,
        })

    # ── Derived ratios computed from raw fields ──
    _derived = _compute_derived_ratios(info, db_symbol, today)
    records.extend(_derived)

    return records


def _compute_derived_ratios(info: dict, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute value-signal and quality-signal ratios from Ticker.info.

    Value signals (Section 4.2 of the Investment Strategy Spec):
      B/P  = Book Value per Share / Price
      E/P  = EPS (TTM) / Price
      CF/P = Operating Cash Flow (TTM) / Market Cap

    Quality signals (Section 4.3):
      ROE (computed)      = Net Income / Shareholders' Equity
      D/E (inverted)      = 1 / Debt-to-Equity  (lower D/E = higher quality)

    :param info: yfinance Ticker.info dict
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List of computed ratio record dicts
    """
    import numpy as np

    records = []
    price = info.get("regularMarketPrice") or info.get("currentPrice")

    def _safe_append(field_name, value):
        try:
            fval = float(value)
            if not np.isnan(fval) and not np.isinf(fval):
                records.append(
                    {
                        "symbol": db_symbol,
                        "snapshot_date": snapshot_date,
                        "field_name": field_name,
                        "field_value": fval,
                    }
                )
        except (ValueError, TypeError):
            pass

    if price and float(price) > 0:
        p = float(price)

        # B/P = Book Value per Share / Price
        bvps = info.get("bookValue")
        if bvps is not None:
            _safe_append("book_to_price", float(bvps) / p)

        # E/P = EPS (TTM) / Price
        eps = info.get("trailingEps")
        if eps is not None:
            _safe_append("earnings_to_price", float(eps) / p)

        # CF/P = Operating Cash Flow / Market Cap
        ocf = info.get("operatingCashflow")
        mcap = info.get("marketCap")
        if ocf is not None and mcap and float(mcap) > 0:
            _safe_append("cashflow_to_price", float(ocf) / float(mcap))

    # ROE (computed) = netIncomeToCommon / shareholders' equity
    # Primary: use totalStockholderEquity if available
    # Fallback: estimate equity as bookValue * sharesOutstanding
    net_income = info.get("netIncomeToCommon")
    equity = info.get("totalStockholderEquity")
    if equity is None or float(equity or 0) == 0:
        bv = info.get("bookValue")
        shares = info.get("sharesOutstanding")
        if bv is not None and shares is not None and float(shares) > 0:
            equity = float(bv) * float(shares)
    if net_income is not None and equity and float(equity) != 0:
        _safe_append("roe_computed", float(net_income) / float(equity))

    # D/E inverted (for quality scoring — lower D/E = higher quality)
    de = info.get("debtToEquity")
    if de is not None and float(de) != 0:
        _safe_append("debt_to_equity_inv", 1.0 / float(de))

    return records


def _compute_earnings_stability(db_client, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute earnings stability from historical quarterly EPS.

    Earnings Stability = 1 / std_dev(quarter-over-quarter EPS growth)
    over trailing 3 years (12 quarters).  Higher value = more stable.

    Requires at least 4 quarterly EPS observations to compute growth
    standard deviation.

    :param db_client: Database client for querying fundamentals
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List with one record dict, or empty list if not computable
    """
    import numpy as np

    try:
        from sqlalchemy import text

        query = text(
            "SELECT field_value FROM systematic_equity.fundamentals "
            "WHERE TRIM(symbol) = :sym "
            "AND field_name IN ('diluted_eps', 'basic_eps') "
            "AND period_type = 'quarterly' "
            "ORDER BY report_date DESC LIMIT 12"
        )
        with db_client.connection.connect() as conn:
            rows = conn.execute(query, {"sym": db_symbol}).fetchall()

        if len(rows) < 3:
            return []

        eps_values = [float(r[0]) for r in rows if r[0] is not None]
        if len(eps_values) < 3:
            return []

        # Quarter-over-quarter growth rates
        growths = []
        for i in range(len(eps_values) - 1):
            prev = eps_values[i + 1]  # older quarter (rows are DESC)
            curr = eps_values[i]
            if prev != 0:
                growths.append((curr - prev) / abs(prev))

        if len(growths) < 2:
            return []

        std = float(np.std(growths, ddof=1))
        if std <= 0 or np.isnan(std) or np.isinf(std):
            return []

        stability = 1.0 / std
        # Cap at a reasonable maximum to avoid extreme outliers
        stability = min(stability, 100.0)

        return [
            {
                "symbol": db_symbol,
                "snapshot_date": snapshot_date,
                "field_name": "earnings_stability",
                "field_value": round(stability, 6),
            }
        ]
    except Exception:
        return []


def _compute_debt_equity_from_fundamentals(db_client, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute D/E ratio from fundamentals when yfinance debtToEquity is missing.

    Uses the most recent quarterly total_debt and stockholders_equity from
    the fundamentals table.  Returns both debt_to_equity and debt_to_equity_inv.

    :param db_client: Database client for querying fundamentals
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List of record dicts (0-2 items), empty if not computable
    """
    try:
        from sqlalchemy import text

        query = text(
            "SELECT f1.field_value AS total_debt, f2.field_value AS equity "
            "FROM systematic_equity.fundamentals f1 "
            "JOIN systematic_equity.fundamentals f2 "
            "  ON TRIM(f1.symbol) = TRIM(f2.symbol) "
            "  AND f1.report_date = f2.report_date "
            "  AND f1.period_type = f2.period_type "
            "WHERE TRIM(f1.symbol) = :sym "
            "  AND f1.field_name = 'total_debt' "
            "  AND f2.field_name = 'stockholders_equity' "
            "  AND f1.period_type = 'quarterly' "
            "  AND f2.field_value IS NOT NULL AND f2.field_value != 0 "
            "ORDER BY f1.report_date DESC LIMIT 1"
        )
        with db_client.connection.connect() as conn:
            rows = conn.execute(query, {"sym": db_symbol}).fetchall()

        if not rows:
            return []

        total_debt = float(rows[0][0])
        equity = float(rows[0][1])
        if equity == 0:
            return []

        de_ratio = total_debt / equity
        records = [
            {
                "symbol": db_symbol,
                "snapshot_date": snapshot_date,
                "field_name": "debt_to_equity",
                "field_value": round(de_ratio, 6),
            },
        ]
        if de_ratio != 0:
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": snapshot_date,
                    "field_name": "debt_to_equity_inv",
                    "field_value": round(1.0 / de_ratio, 6),
                }
            )
        return records
    except Exception:
        return []


def _fetch_finnhub_metric_ratios(yf_ticker: str, api_key: str, db_symbol: str) -> list[dict]:
    """Fetch financial metrics from Finnhub for US tickers (free tier).

    Uses Finnhub's /stock/metric endpoint.  Only works for US tickers
    on the free API plan; non-US tickers return 403 Forbidden.

    :param yf_ticker: Yahoo Finance ticker (US only — no dot suffix)
    :param api_key: Finnhub API key
    :param db_symbol: Database symbol
    :return: List of ratio record dicts (empty on failure / non-US)
    """
    import json
    import urllib.error
    import urllib.request
    from datetime import date as _date

    import numpy as np

    if not api_key or "." in yf_ticker:
        return []

    url = f"https://finnhub.io/api/v1/stock/metric" f"?symbol={yf_ticker}&metric=all&token={api_key}"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return []
        raise
    except Exception:
        return []

    metric = data.get("metric", {})
    if not metric:
        return []

    today = _date.today()
    records = []
    for fh_key, canonical in FINNHUB_METRIC_FIELDS.items():
        val = metric.get(fh_key)
        if val is None:
            continue
        try:
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval):
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError):
            continue

    return records


def _extract_ratios_from_fast_info(ticker_obj, db_symbol: str) -> list[dict]:
    """Extract basic ratio fields from yfinance fast_info (all tickers).

    fast_info is a lightweight endpoint that works for ALL tickers
    including non-US, even when Ticker.info fails or returns sparse data.
    Provides: market_cap, shares_outstanding, 52-week high/low.

    :param ticker_obj: yfinance Ticker object (already instantiated)
    :param db_symbol: Database symbol
    :return: List of ratio record dicts (may be empty)
    """
    from datetime import date as _date

    import numpy as np

    fast_info_map = {
        "market_cap": "market_cap",
        "shares": "shares_outstanding",
        "year_high": "fifty_two_week_high",
        "year_low": "fifty_two_week_low",
    }

    try:
        fi = ticker_obj.fast_info
    except Exception:
        return []

    today = _date.today()
    records = []
    for fi_key, canonical in fast_info_map.items():
        try:
            val = getattr(fi, fi_key, None)
            if val is None:
                continue
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval) or fval == 0.0:
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError, AttributeError):
            continue

    return records


def run_ratios(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download financial ratios and market cap from yfinance Ticker.info.

    Extracts P/E, P/B, EV/EBITDA, market cap, beta, margins, etc.
    These are point-in-time snapshots (current values, not historical).

    Downloads are parallelised via ``ThreadPoolExecutor`` — each worker
    handles a *different* symbol, so there is no same-symbol concurrent
    access (safe for yfinance ``Ticker.info`` in Python 3).
    """
    import os
    import time as _time

    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError, YFTickerMissingError, YFTzMissingError

    api_delay = pipeline_params.get("api_delay_seconds", 0.5)
    max_retries = pipeline_params.get("max_retries", 3)
    backoff_base = pipeline_params.get("backoff_base", 2.0)
    workers = pipeline_params.get("ratios_workers", 8)

    pipeline_logger.info(
        f"Starting company ratios download ({workers} parallel workers, "
        f"up to {max_retries} retries per ticker)..."
    )

    total_loaded = 0
    _count_lock = threading.Lock()
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY", "")

    def _process_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, _currency = item

        if check_shutdown("ratios"):
            return

        if db_symbol in inactive_tickers():
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            return

        # ── Primary source: yfinance Ticker.info ──
        ticker_obj = None
        records = []
        last_exc = None

        for attempt in range(max_retries):
            try:
                ticker_obj = yf.Ticker(yf_ticker)
                info = ticker_obj.info
                records = _extract_ratios_from_info(info, db_symbol)

                # Store raw in MongoDB (semi-structured archive)
                if mongo_store and info:
                    mongo_store.store_document(
                        "raw_ratios",
                        {
                            "symbol": db_symbol,
                            "source": "yfinance",
                            "fields_extracted": len(records),
                            "field_names": [r["field_name"] for r in records],
                            "key_metrics": {
                                "market_cap": info.get("marketCap"),
                                "pe_ratio": info.get("trailingPE"),
                                "price_to_book": info.get("priceToBook"),
                                "dividend_yield": info.get("dividendYield"),
                                "beta": info.get("beta"),
                                "sector": info.get("sector", ""),
                            },
                            "run_id": run_id,
                        },
                    )

                break  # info fetched (even if 0 records extracted)

            except (YFTzMissingError, YFTickerMissingError) as e:
                # Delisted / missing tickers — no point retrying.
                # Classify as SKIPPED so they don't inflate the FAILED count.

                if metrics:
                    metrics.record_outcome("ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
                pipeline_logger.debug(f"Ratios SKIPPED {db_symbol} (delisted/missing): {e}")
                return
            except Exception as e:
                # HTTP 404 "Quote not found" — ticker not available via
                # quoteSummary endpoint; classify as SKIPPED, not FAILED.
                e_str = str(e)
                if "404" in e_str or "Not Found" in e_str or "not found for symbol" in e_str.lower():

                    if metrics:
                        metrics.record_outcome("ratios", db_symbol, "SKIPPED")
                    if progress_update:
                        progress_update(db_symbol, "SKIPPED")
                    pipeline_logger.debug(f"Ratios SKIPPED {db_symbol} (404 not found): {e}")
                    return
                last_exc = e
                ticker_obj = None
                if attempt < max_retries - 1:
                    # Rate-limit errors need a longer cool-down before retry.
                    wait = backoff_base**attempt
                    if isinstance(e, YFRateLimitError):
                        wait = max(wait, 30.0)
                    pipeline_logger.debug(
                        f"Ratios retry {attempt + 1}/{max_retries} "
                        f"for {db_symbol}: {e}. Waiting {wait:.1f}s"
                    )
                    _time.sleep(wait)
                else:
                    if metrics:
                        metrics.record_outcome("ratios", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    pipeline_logger.debug(
                        f"Ratios failed for {db_symbol} after {max_retries} " f"attempts (info): {last_exc}"
                    )
                    return

        # ── Gap-fill 1: Finnhub /stock/metric (US tickers only, free tier) ──
        if not records and finnhub_api_key and "." not in yf_ticker:
            try:
                fh_records = _fetch_finnhub_metric_ratios(yf_ticker, finnhub_api_key, db_symbol)
                if fh_records:
                    records = fh_records
            except Exception as e:
                pipeline_logger.debug(f"Finnhub metric gap-fill failed for {db_symbol}: {e}")

        # ── Gap-fill 2: yfinance fast_info (all tickers) ──
        if not records:
            try:
                if ticker_obj is None:
                    ticker_obj = yf.Ticker(yf_ticker)
                fi_records = _extract_ratios_from_fast_info(ticker_obj, db_symbol)
                if fi_records:
                    records = fi_records
            except Exception as e:
                pipeline_logger.debug(f"fast_info gap-fill failed for {db_symbol}: {e}")

        # ── Earnings Stability (cross-table: needs historical quarterly EPS) ──
        from datetime import date as _d

        es_records = _compute_earnings_stability(db_client, db_symbol, _d.today())
        records.extend(es_records)

        # ── Fundamentals-based D/E fallback ──
        has_de = any(r.get("field_name") == "debt_to_equity_inv" for r in records)
        if not has_de:
            de_records = _compute_debt_equity_from_fundamentals(db_client, db_symbol, _d.today())
            records.extend(de_records)

        # ── Fundamentals-based fallbacks for missing snapshot ratios ──
        # All computations below use REAL data from the fundamentals and
        # daily_prices tables. They fill gaps where yfinance Ticker.info
        # doesn't return certain fields (common for non-US tickers).
        existing_fields = {r.get("field_name") for r in records}
        today = _d.today()

        try:
            from sqlalchemy import text as _text

            # Fetch the most recent quarterly fundamentals for this ticker
            fq = _text(
                "SELECT field_name, field_value FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym AND period_type = 'quarterly' "
                "AND field_value IS NOT NULL "
                "AND report_date = (SELECT MAX(report_date) FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym AND period_type = 'quarterly')"
            )
            with db_client.connection.connect() as conn:
                fund_rows = conn.execute(fq, {"sym": db_symbol}).fetchall()

            latest = {r[0]: float(r[1]) for r in fund_rows} if fund_rows else {}

            # Fetch latest close price
            pq = _text(
                "SELECT close_price FROM systematic_equity.daily_prices "
                "WHERE TRIM(symbol) = :sym AND close_price IS NOT NULL "
                "ORDER BY cob_date DESC LIMIT 1"
            )
            with db_client.connection.connect() as conn:
                price_row = conn.execute(pq, {"sym": db_symbol}).fetchone()
            price = float(price_row[0]) if price_row else None

            def _add_if_missing(field_name, value):
                if field_name not in existing_fields and value is not None:
                    import numpy as np
                    fv = float(value)
                    if not np.isnan(fv) and not np.isinf(fv) and abs(fv) < 1e15:
                        records.append({
                            "symbol": db_symbol, "snapshot_date": today,
                            "field_name": field_name, "field_value": round(fv, 6),
                        })
                        existing_fields.add(field_name)

            equity = latest.get("stockholders_equity")
            net_inc = latest.get("net_income")
            total_rev = latest.get("total_revenue")
            total_debt = latest.get("total_debt")
            ocf = latest.get("operating_cash_flow")
            total_assets = latest.get("total_assets")
            total_liab = latest.get("total_liabilities")
            gross_prof = latest.get("gross_profit")
            op_inc = latest.get("operating_income")
            capex = latest.get("capital_expenditure")
            ebitda = latest.get("ebitda")

            # return_on_equity = net_income / equity
            if net_inc and equity and abs(equity) > 0.001:
                _add_if_missing("return_on_equity", net_inc / equity)

            # profit_margin = net_income / revenue
            if net_inc and total_rev and abs(total_rev) > 0.001:
                _add_if_missing("profit_margin", net_inc / total_rev)

            # operating_margin = operating_income / revenue
            if op_inc and total_rev and abs(total_rev) > 0.001:
                _add_if_missing("operating_margin", op_inc / total_rev)

            # gross_margin = gross_profit / revenue
            if gross_prof and total_rev and abs(total_rev) > 0.001:
                _add_if_missing("gross_margin", gross_prof / total_rev)

            # current_ratio = total_assets / total_liabilities (rough proxy)
            if total_assets and total_liab and abs(total_liab) > 0.001:
                _add_if_missing("current_ratio", total_assets / total_liab)

            # free_cash_flow = ocf - |capex|
            if ocf is not None and capex is not None:
                _add_if_missing("free_cash_flow", ocf - abs(capex))

            # operating_cash_flow
            if ocf is not None:
                _add_if_missing("operating_cash_flow", ocf)

            # total_revenue_ttm (latest quarterly × 4 as rough proxy)
            if total_rev:
                _add_if_missing("total_revenue_ttm", total_rev * 4)

            # pe_ratio_trailing = price / trailing_eps
            eps = latest.get("diluted_eps") or latest.get("basic_eps")
            if price and eps and abs(eps) > 0.001:
                _add_if_missing("pe_ratio_trailing", price / eps)
                _add_if_missing("trailing_eps", eps)

            # price_to_book = price / (equity / shares)
            shares = latest.get("shares_outstanding")
            if not shares:
                # Try from company_ratios
                sq = _text(
                    "SELECT field_value FROM systematic_equity.company_ratios "
                    "WHERE TRIM(symbol) = :sym AND field_name = 'shares_outstanding' "
                    "ORDER BY snapshot_date DESC LIMIT 1"
                )
                with db_client.connection.connect() as conn:
                    sr = conn.execute(sq, {"sym": db_symbol}).fetchone()
                if sr and sr[0]:
                    shares = float(sr[0])

            if price and equity and shares and shares > 0:
                bvps = equity / shares
                if bvps > 0:
                    _add_if_missing("price_to_book", price / bvps)
                    _add_if_missing("book_value_per_share", bvps)
                    _add_if_missing("book_to_price", bvps / price)
                # market_cap
                _add_if_missing("market_cap", price * shares)
                _add_if_missing("shares_outstanding", shares)

            # ev_to_ebitda
            mcap = price * shares if price and shares and shares > 0 else None
            if mcap and total_debt is not None and ebitda and abs(ebitda) > 0.001:
                ev = mcap + total_debt
                _add_if_missing("ev_to_ebitda", ev / ebitda)
                _add_if_missing("enterprise_value", ev)

            # earnings_growth from two most recent EPS
            eq2 = _text(
                "SELECT field_value FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym AND field_name IN ('diluted_eps', 'basic_eps') "
                "AND period_type = 'quarterly' AND field_value IS NOT NULL "
                "ORDER BY report_date DESC LIMIT 2"
            )
            with db_client.connection.connect() as conn:
                eps_rows = conn.execute(eq2, {"sym": db_symbol}).fetchall()
            if len(eps_rows) == 2:
                curr_e, prev_e = float(eps_rows[0][0]), float(eps_rows[1][0])
                if abs(prev_e) > 0.001:
                    eg_val = (curr_e - prev_e) / abs(prev_e)
                    if abs(eg_val) < 100:
                        _add_if_missing("earnings_growth", eg_val)

            # revenue_growth from two most recent quarterly revenues
            rq = _text(
                "SELECT field_value FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym AND field_name = 'total_revenue' "
                "AND period_type = 'quarterly' AND field_value IS NOT NULL "
                "ORDER BY report_date DESC LIMIT 2"
            )
            with db_client.connection.connect() as conn:
                rev_rows = conn.execute(rq, {"sym": db_symbol}).fetchall()
            if len(rev_rows) == 2:
                curr_r, prev_r = float(rev_rows[0][0]), float(rev_rows[1][0])
                if abs(prev_r) > 0.001:
                    rg_val = (curr_r - prev_r) / abs(prev_r)
                    if abs(rg_val) < 100:
                        _add_if_missing("revenue_growth", rg_val)

        except Exception:
            pass

        if records:
            n = db_client.upsert_company_ratios(records)
            with _count_lock:
                total_loaded += n
            # Publish to Kafka — fire-and-forget daemon thread (Fix 15 pattern).
            # kafka_producer.flush(timeout=10) blocks up to 10s per ticker;
            # daemon thread prevents exhausting the PostgreSQL connection pool.
            if kafka_producer:
                threading.Thread(
                    target=kafka_producer.publish_batch,
                    args=(
                        TOPICS.get("fundamentals", "market.fundamentals"),
                        records,
                    ),
                    kwargs={"key_field": "symbol"},
                    daemon=True,
                ).start()
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SUCCESS", n)
            if progress_update:
                progress_update(db_symbol, "SUCCESS")
            db_client.insert_log(
                make_log_entry(run_id, "ratios", db_symbol, "SUCCESS", n, frequency=frequency)
            )
        else:
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")

        _time.sleep(api_delay)

    # ── Crumb warm-up: refresh yfinance session before parallel workers start ──
    # The prices phase may have poisoned the shared crumb via delisted tickers
    # (HTTP 401 Invalid Crumb errors). A single warm-up call with a known
    # stable ticker forces yfinance to obtain a fresh valid crumb.
    try:
        _warmup = yf.Ticker("AAPL")
        _ = _warmup.info.get("regularMarketPrice")
        pipeline_logger.info("Ratios: yfinance crumb warm-up OK")
    except Exception as _e:
        pipeline_logger.warning(f"Ratios: crumb warm-up failed ({_e}) — workers will retry")

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ratios-worker")
    ratio_futures = [pool.submit(_process_ticker, t) for t in ticker_map]
    done, pending = futures_wait(ratio_futures, timeout=180)
    if pending:
        pipeline_logger.warning(
            f"Ratios: {len(pending)} workers still running after 180s timeout "
            f"— continuing (Fix 15 pattern)"
        )
    pool.shutdown(wait=False)

    pipeline_logger.info(f"Ratios: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("ratios")
