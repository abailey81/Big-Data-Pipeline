"""
Tests for modules.processing.ticker_utils

Covers Spec §7.2 Issues 1, 2, and 3:
  - Issue 1: Trailing whitespace in ticker symbols
  - Issue 2: Currency inference from exchange suffix
  - Issue 3: Swiss ticker .S → .SW remap
"""

from unittest.mock import patch

import pytest

# ── clean_ticker tests (Spec §7.2 Issue 1) ──────────────────────────────


class TestCleanTicker:

    def test_strips_trailing_whitespace(self):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker("AAPL   ") == "AAPL"

    def test_strips_heavy_whitespace(self):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker("MMM         ") == "MMM"

    def test_preserves_clean_symbol(self):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker("MSFT") == "MSFT"

    def test_handles_suffix_with_whitespace(self):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker("VOD.L    ") == "VOD.L"

    def test_handles_empty_string(self):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker("") == ""

    def test_handles_none_gracefully(self):
        from modules.processing.ticker_utils import clean_ticker

        result = clean_ticker(None)
        assert result is None


# ── get_exchange_suffix tests ────────────────────────────────────────────


class TestGetExchangeSuffix:

    def test_london_suffix(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("VOD.L") == ".L"

    def test_paris_suffix(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("TTE.PA") == ".PA"

    def test_swiss_suffix(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("NOVN.S") == ".S"

    def test_no_suffix_us(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("AAPL") == ""

    def test_empty_string(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("") == ""

    def test_double_dot(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("BRK.B") == ".B"

    def test_toronto_suffix(self):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix("RY.TO") == ".TO"


# ── infer_currency tests (Spec §7.2 Issue 2) ────────────────────────────


class TestInferCurrency:

    def test_gbp_london(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("VOD.L") == "GBP"

    def test_eur_paris(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("TTE.PA") == "EUR"

    def test_eur_amsterdam(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("ASML.AS") == "EUR"

    def test_eur_frankfurt(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("SAP.DE") == "EUR"

    def test_cad_toronto(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("RY.TO") == "CAD"

    def test_chf_swiss(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("NOVN.S") == "CHF"

    def test_usd_default_bare(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("AAPL") == "USD"

    def test_custom_mapping(self):
        from modules.processing.ticker_utils import infer_currency

        custom = {".HK": "HKD"}
        assert infer_currency("0005.HK", custom) == "HKD"

    def test_unknown_suffix_defaults_usd(self):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency("TEST.XX") == "USD"


# ── remap_swiss_ticker tests (Spec §7.2 Issue 3) ────────────────────────


class TestRemapSwissTicker:

    def test_novn_s_to_sw(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("NOVN.S") == "NOVN.SW"

    def test_nesn_s_to_sw(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("NESN.S") == "NESN.SW"

    def test_ro_s_to_sw(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("RO.S") == "RO.SW"

    def test_no_change_for_us(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("AAPL") == "AAPL"

    def test_no_change_for_london(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("VOD.L") == "VOD.L"

    def test_already_sw_unchanged(self):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker("NOVN.SW") == "NOVN.SW"


# ── remap_share_class_ticker tests ────────────────────────────────────


class TestRemapShareClassTicker:

    def test_brk_b_remapped(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("BRK.B") == "BRK-B"

    def test_bf_b_remapped(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("BF.B") == "BF-B"

    def test_exchange_suffix_not_remapped(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("VOD.L") == "VOD.L"

    def test_swiss_sw_not_remapped(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("NOVN.SW") == "NOVN.SW"

    def test_us_ticker_unchanged(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("AAPL") == "AAPL"

    def test_toronto_not_remapped(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("RY.TO") == "RY.TO"

    def test_empty_string(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker("") == ""

    def test_none_returns_none(self):
        from modules.processing.ticker_utils import remap_share_class_ticker

        assert remap_share_class_ticker(None) is None


# ── prepare_yfinance_ticker tests (full pipeline) ───────────────────────


class TestPrepareYfinanceTicker:

    def test_us_ticker_with_whitespace(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("AAPL     ")
        assert db == "AAPL"
        assert yf == "AAPL"
        assert ccy == "USD"

    def test_london_ticker(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("VOD.L   ")
        assert db == "VOD.L"
        assert yf == "VOD.L"
        assert ccy == "GBP"

    def test_swiss_ticker_remapped(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("NOVN.S    ")
        assert db == "NOVN.S"
        assert yf == "NOVN.SW"
        assert ccy == "CHF"

    def test_toronto_ticker(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("RY.TO  ")
        assert db == "RY.TO"
        assert yf == "RY.TO"
        assert ccy == "CAD"

    def test_custom_currency_map(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("VOD.L  ", {".L": "GBP"})
        assert ccy == "GBP"

    def test_share_class_b_remapped(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("BRK.B  ")
        assert db == "BRK.B"
        assert yf == "BRK-B"
        assert ccy == "USD"

    def test_share_class_bf_b_remapped(self):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        db, yf, ccy = prepare_yfinance_ticker("BF.B  ")
        assert db == "BF.B"
        assert yf == "BF-B"
        assert ccy == "USD"
