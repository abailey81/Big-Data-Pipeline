"""CW1↔CW2 schema-contract tests — auto-skipped if CW1 DB unreachable.

Fails CI loudly if CW1 ever drops a column CW2 reads from, renames a field,
or changes schema. This is the automated equivalent of
``scripts/validate_cw1_integration.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


def _db_available() -> bool:
    try:
        from engine.config import load_config
        from engine.data_loader import DataLoader
        return DataLoader(load_config()).health_check()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="CW1 DB unavailable")


# -------- The contract ----------
REQUIRED_COLUMNS: dict[str, set[str]] = {
    "company_static":   {"symbol", "gics_sector", "country"},
    "daily_prices":     {"symbol", "cob_date", "adj_close_price", "currency", "volume"},
    "fundamentals":     {"symbol", "report_date", "field_name", "field_value", "period_type"},
    "company_ratios":   {"symbol", "snapshot_date", "field_name", "field_value"},
    "fx_rates":         {"currency_pair", "cob_date", "close_rate"},
    "vix_data":         {"cob_date", "close_price"},
    "risk_free_rate":   {"cob_date", "rate_pct"},
    "benchmark_index":  {"symbol", "cob_date", "adj_close_price"},
    "news_sentiment":   {"symbol", "cob_date", "sentiment_score"},
}


@pytest.fixture(scope="module")
def data_loader():
    from engine.config import load_config
    from engine.data_loader import DataLoader
    return DataLoader(load_config())


@pytest.mark.integration
@pytest.mark.parametrize("table,required_cols", list(REQUIRED_COLUMNS.items()))
def test_cw1_table_contract(data_loader, table, required_cols):
    """CW2 requires these columns in CW1's schema — drift is a breaking change."""
    with data_loader._engine.connect() as conn:
        q = text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = :sch AND table_name = :tbl
            """
        )
        actual = {r[0] for r in conn.execute(q, {"sch": data_loader._schema, "tbl": table}).all()}
    missing = required_cols - actual
    assert not missing, f"CW1 table '{table}' missing columns required by CW2: {missing}"


@pytest.mark.integration
def test_cw1_currency_inference_parity(data_loader):
    """CW1's `ticker_utils.infer_currency` → must match CW2's engine.data_loader.infer_currency."""
    from engine.data_loader import infer_currency
    cases = {
        "BARC.L": "GBP", "BNP.PA": "EUR", "SAP.DE": "EUR", "SAN.MC": "EUR",
        "ABX.TO": "CAD", "NOVN.S": "CHF", "NESN.SW": "CHF", "AAPL": "USD",
    }
    for sym, expected in cases.items():
        assert infer_currency(sym) == expected, f"currency drift: {sym}"


@pytest.mark.integration
def test_cw1_universe_size_sane(data_loader):
    """CW1 populated ~678 companies; CW2 expects >= 500 for sector-neutral z-scores to work."""
    with data_loader._engine.connect() as conn:
        n = conn.execute(
            text(f"SELECT COUNT(*) FROM {data_loader._schema}.company_static")
        ).scalar()
    assert n >= 500, f"CW1 company_static has only {n} rows; CW2 needs ≥ 500"


@pytest.mark.integration
def test_cw1_daily_prices_freshness(data_loader):
    """CW1 prices must extend at least to the start of CW2's OOS window."""
    from datetime import date
    from engine.config import load_config
    cfg = load_config()
    with data_loader._engine.connect() as conn:
        max_date = conn.execute(
            text(f"SELECT MAX(cob_date) FROM {data_loader._schema}.daily_prices")
        ).scalar()
    assert max_date >= cfg.dates.oos_start, (
        f"CW1 prices only extend to {max_date}; OOS window starts {cfg.dates.oos_start}"
    )


@pytest.mark.integration
def test_cw1_data_snapshot_hash_stable(data_loader):
    """Calling the snapshot hash twice in quick succession must return the same value."""
    h1 = data_loader.data_snapshot_sha256()
    # Reset cache for second call
    data_loader._data_snapshot_sha256 = None
    h2 = data_loader.data_snapshot_sha256()
    assert h1 == h2, "data_snapshot_sha256 not stable across calls on unchanged DB"
    assert len(h1) == 64, "SHA-256 hex digest should be 64 chars"
