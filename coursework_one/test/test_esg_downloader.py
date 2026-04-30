"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Unit tests for ESG downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Covers both LSEG Data Platform (primary) and yfinance (fallback) paths,
as well as the clean_esg_record utility.

"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import modules.input.esg_downloader as esg_mod
from modules.input.esg_downloader import EsgDownloader, clean_esg_record

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_lseg_state():
    """Reset module-level LSEG session flags between tests."""
    esg_mod._LSEG_SESSION = None
    esg_mod._LSEG_INIT_DONE = False
    esg_mod._LSEG_AVAILABLE = False
    esg_mod._YF_DEPRECATION_LOGGED = False


def _lseg_df(total=22.5, env=15.0, soc=25.0, gov=27.0, grade="B+"):
    """Build a fake lseg.data.get_data() response DataFrame.

    Column names match the human-readable names returned by lseg.data.get_data().
    """
    return pd.DataFrame(
        [
            {
                "Instrument": "AAPL.O",
                "ESG Score": total,
                "Environmental Pillar Score": env,
                "Social Pillar Score": soc,
                "Governance Pillar Score": gov,
                "ESG Score Grade": grade,
            }
        ]
    )


def _make_mock_ld(df=None, open_error=None):
    """Create a mock lseg.data module."""
    mock_ld = MagicMock()
    if open_error:
        mock_ld.open_session.side_effect = open_error
    else:
        mock_ld.open_session.return_value = MagicMock()
    mock_ld.get_data.return_value = df if df is not None else _lseg_df()
    return mock_ld


# ---------------------------------------------------------------------------
# _init_lseg
# ---------------------------------------------------------------------------


class TestInitLseg:
    """Tests for the one-time LSEG session initialisation helper."""

    def setup_method(self):
        _reset_lseg_state()

    def test_missing_credentials_returns_false(self):
        import os

        for key in ("REFINITIV_USERNAME", "REFINITIV_PASSWORD", "REFINITIV_APP_KEY"):
            os.environ.pop(key, None)
        result = esg_mod._init_lseg()
        assert result is False
        assert esg_mod._LSEG_AVAILABLE is False

    def test_partial_credentials_returns_false(self):
        with patch.dict(
            "os.environ",
            {
                "REFINITIV_USERNAME": "user@example.com",
                "REFINITIV_PASSWORD": "",
                "REFINITIV_APP_KEY": "abc123",
            },
        ):
            result = esg_mod._init_lseg()
        assert result is False

    def test_lseg_data_not_installed_returns_false(self):
        with patch.dict(
            "os.environ",
            {
                "REFINITIV_USERNAME": "u",
                "REFINITIV_PASSWORD": "p",
                "REFINITIV_APP_KEY": "k",
            },
        ):
            with patch.dict("sys.modules", {"lseg": None, "lseg.data": None}):
                result = esg_mod._init_lseg()
        assert result is False

    def test_session_open_error_returns_false(self):
        mock_ld = _make_mock_ld(open_error=Exception("Auth failed"))
        with patch.dict(
            "os.environ",
            {
                "REFINITIV_USERNAME": "u",
                "REFINITIV_PASSWORD": "p",
                "REFINITIV_APP_KEY": "k",
            },
        ):
            with patch.dict(
                "sys.modules",
                {
                    "lseg.data": mock_ld,
                    "lseg": MagicMock(data=mock_ld),
                },
            ):
                result = esg_mod._init_lseg()
        assert result is False

    def test_successful_session_returns_true(self):
        mock_ld = _make_mock_ld()
        with patch.dict(
            "os.environ",
            {
                "REFINITIV_USERNAME": "u",
                "REFINITIV_PASSWORD": "p",
                "REFINITIV_APP_KEY": "k",
            },
        ):
            with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
                result = esg_mod._init_lseg()
        assert result is True
        assert esg_mod._LSEG_AVAILABLE is True

    def test_init_called_only_once(self):
        """Module caches the result; second call skips re-initialisation."""
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = True
        result = esg_mod._init_lseg()
        assert result is True


# ---------------------------------------------------------------------------
# EsgDownloader — LSEG path
# ---------------------------------------------------------------------------


class TestEsgDownloaderLseg:
    """Tests for the LSEG Data Platform download path."""

    def setup_method(self):
        _reset_lseg_state()
        # Skip real _init_lseg — control _use_lseg directly
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = True

    def _make_dl(self, mock_ld):
        dl = EsgDownloader()
        dl._use_lseg = True
        return dl

    def test_lseg_returns_valid_record(self):
        mock_ld = _make_mock_ld(_lseg_df())
        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            dl = self._make_dl(mock_ld)
            result = dl._download_lseg("AAPL")

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["total_esg"] == 22.5
        assert result["environment_score"] == 15.0
        assert result["social_score"] == 25.0
        assert result["governance_score"] == 27.0
        assert result["peer_group"] == "B+"
        assert result["source"] == "lseg_platform"

    def test_lseg_empty_dataframe_returns_none(self):
        mock_ld = _make_mock_ld(pd.DataFrame())
        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            dl = self._make_dl(mock_ld)
            result = dl._download_lseg("AAPL")

        assert result is None

    def test_lseg_all_null_scores_returns_none(self):
        mock_ld = _make_mock_ld(_lseg_df(total=None, env=None, soc=None, gov=None))
        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            dl = self._make_dl(mock_ld)
            result = dl._download_lseg("AAPL")

        assert result is None

    def test_lseg_exception_returns_none(self):
        mock_ld = _make_mock_ld()
        mock_ld.get_data.side_effect = Exception("Network error")
        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            dl = self._make_dl(mock_ld)
            result = dl._download_lseg("AAPL")

        assert result is None

    def test_download_full_cycle_lseg(self):
        """End-to-end: download() routes through LSEG and updates stats."""
        mock_ld = _make_mock_ld(_lseg_df())
        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            dl = self._make_dl(mock_ld)
            result = dl.download("AAPL")

        assert result is not None
        assert result["source"] == "lseg_platform"
        assert dl._download_count == 1
        assert dl._success_count == 1
        assert dl._failure_count == 0

    def test_lseg_falls_back_to_yfinance_on_empty(self):
        """If LSEG returns None for a ticker, yfinance is tried next."""
        mock_ld = _make_mock_ld(pd.DataFrame())
        sus_data = pd.DataFrame(
            {"Value": [17.2, 0.5, 7.9, 8.8, 28.6, "Technology"]},
            index=[
                "totalEsg",
                "environmentScore",
                "socialScore",
                "governanceScore",
                "percentile",
                "peerGroup",
            ],
        )
        mock_ticker = MagicMock()
        mock_ticker.sustainability = sus_data

        with patch.dict("sys.modules", {"lseg.data": mock_ld}):
            with patch("modules.input.esg_downloader.yf.Ticker", return_value=mock_ticker):
                dl = self._make_dl(mock_ld)
                result = dl.download("AAPL")

        assert result is not None
        assert result["source"] == "yfinance_sustainability"

    def test_ric_conversion(self):
        """US tickers get .O suffix; non-US suffixes pass through unchanged."""
        assert esg_mod._yf_to_ric("AAPL") == "AAPL.O"
        assert esg_mod._yf_to_ric("MSFT") == "MSFT.O"
        assert esg_mod._yf_to_ric("BP.L") == "BP.L"
        assert esg_mod._yf_to_ric("SIE.DE") == "SIE.DE"
        assert esg_mod._yf_to_ric("7203.T") == "7203.T"


# ---------------------------------------------------------------------------
# EsgDownloader — yfinance fallback path
# ---------------------------------------------------------------------------


class TestEsgDownloaderYFinance:
    """Tests for the yfinance fallback path (LSEG not configured)."""

    def setup_method(self):
        _reset_lseg_state()
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = False

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_sustainability_returns_record(self, mock_ticker_cls):
        sus_data = pd.DataFrame(
            {"Value": [17.2, 0.5, 7.9, 8.8, 28.6, "Technology"]},
            index=[
                "totalEsg",
                "environmentScore",
                "socialScore",
                "governanceScore",
                "percentile",
                "peerGroup",
            ],
        )
        mock_ticker_cls.return_value.sustainability = sus_data

        dl = EsgDownloader()
        result = dl._execute_download(symbol="AAPL")

        assert result is not None
        assert result["total_esg"] == 17.2
        assert result["environment_score"] == 0.5
        assert result["source"] == "yfinance_sustainability"

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_no_data_returns_none(self, mock_ticker_cls):
        mock_ticker_cls.return_value.sustainability = None
        mock_ticker_cls.return_value.info = {}

        dl = EsgDownloader()
        result = dl._execute_download(symbol="UNKNOWN")

        assert result is None

    @patch("modules.input.esg_downloader.time.sleep")
    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_download_no_data_counts_as_success(self, mock_ticker_cls, mock_sleep):
        mock_ticker_cls.return_value.sustainability = None
        mock_ticker_cls.return_value.info = {}

        dl = EsgDownloader()
        result = dl.download("UNKNOWN")

        assert result is None
        assert dl._success_count == 1
        assert dl._failure_count == 0

    @patch("modules.input.esg_downloader.time.sleep")
    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_download_retries_exhausted_increments_failure(self, mock_ticker_cls, mock_sleep):
        mock_ticker_cls.side_effect = Exception("API error")

        dl = EsgDownloader(max_retries=2)
        result = dl.download("AAPL")

        assert result is None
        assert dl._failure_count == 1


# ---------------------------------------------------------------------------
# EsgDownloader — init defaults
# ---------------------------------------------------------------------------


class TestEsgDownloaderInit:
    """Tests for EsgDownloader constructor and stats property."""

    def setup_method(self):
        _reset_lseg_state()
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = False

    def test_init_defaults(self):
        dl = EsgDownloader()
        assert dl.source_name == "esg"
        assert dl.max_retries == 3
        assert dl._download_count == 0
        assert dl._success_count == 0

    def test_init_custom_params(self):
        dl = EsgDownloader(api_delay=1.0, max_retries=5, backoff_base=3.0)
        assert dl.max_retries == 5
        assert dl.backoff_base == 3.0

    def test_stats_property(self):
        dl = EsgDownloader()
        dl._download_count = 10
        dl._success_count = 8
        dl._failure_count = 2
        stats = dl.stats
        assert stats["source"] == "esg"
        assert stats["success_rate"] == 80.0


# ---------------------------------------------------------------------------
# clean_esg_record
# ---------------------------------------------------------------------------


class TestCleanEsgRecord:
    """Tests for clean_esg_record utility function."""

    def test_clean_valid_record(self):
        record = {
            "symbol": "AAPL",
            "cob_date": "2026-02-27",
            "total_esg": 17.2,
            "environment_score": 0.5,
            "social_score": 7.9,
            "governance_score": 8.8,
            "peer_percentile": 28.6,
            "peer_group": "Technology",
            "source": "lseg_platform",
        }
        result = clean_esg_record(record)
        assert result is not None
        assert result["total_esg"] == 17.2
        assert "source" not in result
        assert "peer_group" not in result

    def test_clean_none_record(self):
        assert clean_esg_record(None) is None

    def test_clean_record_missing_total_esg(self):
        record = {
            "symbol": "AAPL",
            "cob_date": "2026-02-27",
            "total_esg": None,
        }
        assert clean_esg_record(record) is None

    def test_clean_record_preserves_partial_scores(self):
        record = {
            "symbol": "MSFT",
            "cob_date": "2026-02-27",
            "total_esg": 22.1,
            "environment_score": None,
            "social_score": 10.0,
            "governance_score": None,
            "peer_percentile": None,
            "source": "lseg_platform",
        }
        result = clean_esg_record(record)
        assert result is not None
        assert result["environment_score"] is None
        assert result["social_score"] == 10.0

    def test_clean_lseg_record(self):
        record = {
            "symbol": "BP.L",
            "cob_date": date.today().isoformat(),
            "total_esg": 55.3,
            "environment_score": 60.1,
            "social_score": 52.0,
            "governance_score": 53.8,
            "peer_percentile": None,
            "peer_group": "A-",
            "source": "lseg_platform",
        }
        result = clean_esg_record(record)
        assert result is not None
        assert result["total_esg"] == 55.3
        assert "source" not in result


# ---------------------------------------------------------------------------
# EsgDownloader — download_batch (LSEG batch path)
# ---------------------------------------------------------------------------


class TestEsgDownloaderBatch:
    """Tests for the LSEG batch download path (download_batch)."""

    def setup_method(self):
        _reset_lseg_state()
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = True

    def test_batch_disabled_returns_empty(self):
        dl = EsgDownloader()
        dl._use_lseg = False
        result = dl.download_batch([("AAPL", "AAPL", "USD")])
        assert result == {}

    def test_batch_success(self):
        mock_ld = _make_mock_ld(_lseg_df(total=22.5, env=15.0, soc=25.0, gov=27.0, grade="B+"))
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert "AAPL" in result
        assert result["AAPL"]["total_esg"] == 22.5

    def test_batch_empty_df_returns_empty(self):
        mock_ld = _make_mock_ld(pd.DataFrame())
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert result == {}

    def test_batch_none_df_returns_empty(self):
        mock_ld = _make_mock_ld(None)
        mock_ld.get_data.return_value = None
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert result == {}

    def test_batch_all_null_scores(self):
        df = _lseg_df(total=None, env=None, soc=None, gov=None, grade=None)
        mock_ld = _make_mock_ld(df)
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert "AAPL" in result
        assert result["AAPL"] is None

    def test_batch_session_dead_marks_unavailable(self):
        mock_ld = _make_mock_ld()
        mock_ld.get_data.side_effect = Exception("Session is not opened")
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert result == {}
        assert dl._use_lseg is False

    def test_batch_generic_error_returns_empty(self):
        mock_ld = _make_mock_ld()
        mock_ld.get_data.side_effect = Exception("Network timeout")
        with patch.dict("sys.modules", {"lseg.data": mock_ld, "lseg": MagicMock(data=mock_ld)}):
            dl = EsgDownloader()
            dl._use_lseg = True
            result = dl.download_batch([("AAPL", "AAPL", "USD")])

        assert result == {}


# ---------------------------------------------------------------------------
# EsgDownloader — _download_yfinance fallback
# ---------------------------------------------------------------------------


class TestEsgDownloaderYFinanceFallback:
    """Tests for the yfinance fallback download path."""

    def setup_method(self):
        _reset_lseg_state()
        esg_mod._LSEG_INIT_DONE = True
        esg_mod._LSEG_AVAILABLE = False

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_info_esg_path(self, mock_ticker_cls):
        """Test the .info-based ESG path (when sustainability is None)."""
        mock_ticker = MagicMock()
        mock_ticker.sustainability = None
        mock_ticker.info = {
            "esgPopulated": True,
            "totalEsg": 18.5,
            "environmentScore": 5.0,
            "socialScore": 6.5,
            "governanceScore": 7.0,
            "peerEsgScorePerformance": 42.0,
            "peerGroup": "Technology",
        }
        mock_ticker_cls.return_value = mock_ticker

        dl = EsgDownloader()
        result = dl._download_yfinance("AAPL")

        assert result is not None
        assert result["total_esg"] == 18.5
        assert result["source"] == "yfinance_info"

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_info_all_none_returns_none(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.sustainability = None
        mock_ticker.info = {"esgPopulated": True}
        mock_ticker_cls.return_value = mock_ticker

        dl = EsgDownloader()
        result = dl._download_yfinance("AAPL")

        assert result is None

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_sustainability_exception(self, mock_ticker_cls):
        """If sustainability raises, falls through to .info."""
        mock_ticker = MagicMock()
        type(mock_ticker).sustainability = property(
            lambda self: (_ for _ in ()).throw(Exception("API error"))
        )
        mock_ticker.info = {}
        mock_ticker_cls.return_value = mock_ticker

        dl = EsgDownloader()
        result = dl._download_yfinance("AAPL")
        assert result is None

    @patch("modules.input.esg_downloader.yf.Ticker")
    def test_yfinance_deprecation_logged_once(self, mock_ticker_cls):
        """Deprecation message only logged on first None result."""
        mock_ticker = MagicMock()
        mock_ticker.sustainability = None
        mock_ticker.info = {}
        mock_ticker_cls.return_value = mock_ticker

        esg_mod._YF_DEPRECATION_LOGGED = False
        dl = EsgDownloader()
        dl._download_yfinance("AAPL")
        assert esg_mod._YF_DEPRECATION_LOGGED is True

        # Second call should not re-log (flag stays True)
        dl._download_yfinance("MSFT")
        assert esg_mod._YF_DEPRECATION_LOGGED is True


# ---------------------------------------------------------------------------
# _parse_info_esg
# ---------------------------------------------------------------------------


class TestParseInfoEsg:

    def test_valid_info_returns_record(self):
        info = {
            "totalEsg": 20.5,
            "environmentScore": 8.0,
            "socialScore": 6.0,
            "governanceScore": 6.5,
            "peerEsgScorePerformance": 55.0,
            "peerGroup": "Financials",
        }
        result = EsgDownloader._parse_info_esg(info, "JPM")
        assert result is not None
        assert result["total_esg"] == 20.5
        assert result["peer_group"] == "Financials"
        assert result["source"] == "yfinance_info"

    def test_all_none_returns_none(self):
        info = {}
        result = EsgDownloader._parse_info_esg(info, "AAPL")
        assert result is None

    def test_partial_scores(self):
        info = {"totalEsg": 15.0}
        result = EsgDownloader._parse_info_esg(info, "AAPL")
        assert result is not None
        assert result["total_esg"] == 15.0
        assert result["environment_score"] is None

    def test_invalid_float_values(self):
        info = {"totalEsg": "not-a-number", "environmentScore": 5.0}
        result = EsgDownloader._parse_info_esg(info, "AAPL")
        assert result is not None
        assert result["total_esg"] is None
        assert result["environment_score"] == 5.0
