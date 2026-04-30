"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : ESG sustainability score downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads Environmental, Social, and Governance (ESG) scores using
the LSEG Data Library (lseg-data) with a platform session as the
primary source, with a fallback to ``yfinance.Ticker.sustainability``.

Primary source — LSEG Data Platform (REST API, no desktop required):
  Connects directly to the LSEG Data Platform via OAuth2 using:
  - ``REFINITIV_USERNAME`` — LSEG account e-mail
  - ``REFINITIV_PASSWORD`` — LSEG account password
  - ``REFINITIV_APP_KEY``  — App key from LSEG App Key Generator
  Fields fetched: TR.TRESGScore, TR.EnvironmentPillarScore,
  TR.SocialPillarScore, TR.GovernancePillarScore, TR.TRESGScoreGrade.

Fallback source — Yahoo Finance (yfinance):
  Used when LSEG credentials are absent or the session cannot be
  established. Note: Yahoo Finance deprecated Sustainalytics ESG data
  in late 2024, so this fallback typically yields no records.

ESG data enhances the quality factor in the Vayanos & Woolley (2012)
flow-based multi-factor framework:

  quality_enhanced = f(ROE, D/E, FCF_yield, ESG_total_score)

Data sources (in priority order):
  1. LSEG Data Platform — TR.TRESGScore pillar fields (platform session)
  2. yfinance Ticker.sustainability — Sustainalytics (deprecated 2024)
  3. yfinance Ticker.info ESG fields (secondary fallback)

References:
  Vayanos, D. & Woolley, P. (2012). An Institutional Theory of Momentum
  and Reversal. Review of Financial Studies, 26(5), 1087-1145.

"""

import math
import os
import time  # noqa: F401 — needed for test mocking (patch esg_downloader.time.sleep)
from datetime import date
from typing import Optional

import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.info_logger import pipeline_logger

# Module-level session state — initialised once per process
_LSEG_SESSION = None  # holds the open lseg.data session object
_LSEG_INIT_DONE: bool = False
_LSEG_AVAILABLE: bool = False
_YF_DEPRECATION_LOGGED: bool = False

# LSEG Refinitiv ESG fields to fetch
_LSEG_ESG_FIELDS = [
    "TR.TRESGScore",
    "TR.EnvironmentPillarScore",
    "TR.SocialPillarScore",
    "TR.GovernancePillarScore",
    "TR.TRESGScoreGrade",
]


def _init_lseg() -> bool:
    """Open an LSEG Data Platform session once per process.

    Reads credentials from the environment, opens a platform session
    (direct REST — no desktop app required), and caches the result.

    Environment variables required:
      - ``REFINITIV_USERNAME``
      - ``REFINITIV_PASSWORD``
      - ``REFINITIV_APP_KEY``

    :return: True if the session opened successfully, False otherwise.
    :rtype: bool
    """
    global _LSEG_SESSION, _LSEG_INIT_DONE, _LSEG_AVAILABLE

    if _LSEG_INIT_DONE:
        return _LSEG_AVAILABLE

    _LSEG_INIT_DONE = True

    username = os.environ.get("REFINITIV_USERNAME", "").strip()
    password = os.environ.get("REFINITIV_PASSWORD", "").strip()
    app_key = os.environ.get("REFINITIV_APP_KEY", "").strip()

    if not all([username, password, app_key]):
        pipeline_logger.info(
            "LSEG credentials not fully configured "
            "(REFINITIV_USERNAME / REFINITIV_PASSWORD / REFINITIV_APP_KEY). "
            "ESG will fall back to yfinance."
        )
        _LSEG_AVAILABLE = False
        return False

    try:
        import lseg.data as ld  # noqa: PLC0415 — intentional lazy import

        cfg = ld.get_config()
        cfg.set_param("sessions.default", "platform.default")
        cfg.set_param("sessions.platform.default.app-key", app_key, auto_create=True)
        cfg.set_param("sessions.platform.default.username", username, auto_create=True)
        cfg.set_param("sessions.platform.default.password", password, auto_create=True)
        cfg.set_param("sessions.platform.default.signon_control", True)

        # Retry session open up to 5 times with increasing backoff.
        # LSEG signon_control=True takes over stale sessions, which can
        # take several seconds on the server side. Aggressive retries
        # ensure the pipeline almost always gets an LSEG session.
        session = None
        import time as _t
        for _sess_attempt in range(5):
            try:
                session = ld.open_session()
                break
            except Exception as sess_exc:
                pipeline_logger.warning(
                    f"LSEG session attempt {_sess_attempt + 1}/5 failed: {sess_exc}"
                )
                if _sess_attempt < 4:
                    wait = 2 ** _sess_attempt  # 1s, 2s, 4s, 8s
                    pipeline_logger.info(f"Retrying LSEG session in {wait}s...")
                    _t.sleep(wait)

        if session is None:
            pipeline_logger.warning("LSEG session failed after 5 attempts. Falling back to yfinance.")
            _LSEG_AVAILABLE = False
            return False

        _LSEG_SESSION = session
        _LSEG_AVAILABLE = True
        pipeline_logger.info(
            "LSEG Data Platform session opened — "
            "ESG scores will use Refinitiv/LSEG data (no desktop required)."
        )
    except ImportError:
        pipeline_logger.warning(
            "lseg-data package not installed. Run: poetry add lseg-data. " "Falling back to yfinance."
        )
        _LSEG_AVAILABLE = False
    except Exception as exc:
        pipeline_logger.warning(
            f"LSEG platform session failed: {exc}. "
            "Check credentials and network access. Falling back to yfinance."
        )
        _LSEG_AVAILABLE = False

    return _LSEG_AVAILABLE


def _yf_to_ric(symbol: str) -> str:
    """Convert a Yahoo Finance ticker to a Refinitiv RIC.

    Yahoo Finance uses ``.L`` for LSE, ``.DE`` for XETRA, ``.T`` for TSE,
    etc., which match Refinitiv RIC suffixes directly. Plain US tickers
    (no suffix) require the ``.O`` suffix in LSEG to route correctly to
    NASDAQ/NYSE — e.g. ``AAPL`` → ``AAPL.O``.

    :param symbol: Yahoo Finance ticker symbol.
    :type symbol: str
    :return: Refinitiv Instrument Code (RIC).
    :rtype: str
    """
    if "." not in symbol:
        return symbol + ".O"
    return symbol


class EsgDownloader(BaseDownloader):
    """Downloads ESG sustainability scores from LSEG Data Platform (primary)
    or Yahoo Finance (fallback).

    Uses the LSEG Data Library platform session when
    ``REFINITIV_USERNAME``, ``REFINITIV_PASSWORD``, and
    ``REFINITIV_APP_KEY`` are all set in the environment. This connects
    directly to the Refinitiv REST API — no Eikon / Workspace desktop
    application required. Falls back to ``yfinance.Ticker.sustainability``
    when credentials are absent or the session cannot be established.

    :param api_delay: Delay between API calls in seconds.
    :type api_delay: float
    :param max_retries: Maximum retry attempts per ticker.
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier.
    :type backoff_base: float
    """

    def __init__(
        self,
        api_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        **kwargs,
    ):
        super().__init__(
            source_name="esg",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            **kwargs,
        )
        self._use_lseg: bool = _init_lseg()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def download(self, symbol: str) -> Optional[dict]:
        """Download ESG scores for *symbol* with retry and circuit breaker.

        :param symbol: Yahoo Finance ticker symbol (or compatible RIC).
        :type symbol: str
        :return: ESG record dictionary, or None if unavailable.
        :rtype: dict or None
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.debug(f"ESG: circuit breaker OPEN — skipping {symbol}")
            self._failure_count += 1
            return None

        self.rate_limiter.acquire()

        for attempt in range(self.max_retries):
            try:
                result = self._execute_download(symbol=symbol)
                self.circuit_breaker.record_success()
                self._success_count += 1
                return result
            except Exception as exc:
                self.circuit_breaker.record_failure()
                if attempt < self.max_retries - 1:
                    pipeline_logger.debug(
                        f"ESG retry {attempt + 1}/{self.max_retries} " f"for {symbol}: {exc}"
                    )
                    self._jitter_wait(attempt)

        self._failure_count += 1
        return None

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _execute_download(self, symbol: str, **kwargs) -> Optional[dict]:
        """Download ESG data, trying LSEG then yfinance.

        :param symbol: Ticker / RIC.
        :type symbol: str
        :return: ESG record dict or None.
        :rtype: dict or None
        """
        if self._use_lseg:
            result = self._download_lseg(symbol)
            if result is not None:
                return result
            pipeline_logger.debug(f"ESG: LSEG returned no data for {symbol}, trying yfinance.")

        return self._download_yfinance(symbol)

    def _download_lseg(self, symbol: str) -> Optional[dict]:
        """Fetch ESG pillars from LSEG Data Platform for *symbol*.

        Uses ``lseg.data.get_data()`` with the open platform session.
        No desktop application required.

        :param symbol: Ticker / RIC.
        :type symbol: str
        :return: Parsed ESG record or None.
        :rtype: dict or None
        """
        global _LSEG_AVAILABLE  # declared early so it can be written in except

        # Abort immediately if session has gone dead (avoids stdout spam)
        if not _LSEG_AVAILABLE:
            return None

        try:
            import lseg.data as ld  # noqa: PLC0415

            ric = _yf_to_ric(symbol)
            df = ld.get_data(universe=[ric], fields=_LSEG_ESG_FIELDS)

            if df is None or df.empty:
                return None

            row = df.iloc[0]

            def _safe(col):
                val = row.get(col)
                if val is None:
                    return None
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None

            # lseg.data.get_data() returns human-readable column names
            total = _safe("ESG Score")
            env = _safe("Environmental Pillar Score")
            soc = _safe("Social Pillar Score")
            gov = _safe("Governance Pillar Score")
            grade = row.get("ESG Score Grade")

            if total is None and env is None and soc is None and gov is None:
                return None

            return {
                "symbol": symbol,
                "cob_date": date.today().isoformat(),
                "total_esg": total,
                "environment_score": env,
                "social_score": soc,
                "governance_score": gov,
                "peer_percentile": None,
                "peer_group": str(grade) if grade else None,
                "source": "lseg_platform",
            }

        except Exception as exc:
            exc_str = str(exc)
            if "Session is not opened" in exc_str or "session quota" in exc_str.lower():
                _LSEG_AVAILABLE = False
                self._use_lseg = False
            pipeline_logger.debug(f"ESG LSEG error for {symbol}: {exc}")
            return None

    def download_batch(self, ticker_map: list) -> dict:
        """Fetch ESG scores for all tickers in a single LSEG batch API call.

        Sends the entire ticker universe to ``lseg.data.get_data()`` in one
        request rather than looping per-ticker, reducing API round-trips from
        N to 1 and eliminating ``api_delay`` stalls between tickers.

        Falls back gracefully: returns an empty dict if LSEG is not configured
        or the batch call fails, allowing ``_run_esg`` to fall back to the
        per-ticker yfinance path.

        :param ticker_map: List of ``(db_symbol, yf_ticker, currency)`` tuples.
        :type ticker_map: list
        :return: Dict mapping yf_ticker → raw record dict (or None if no data).
                 Empty dict if LSEG is unavailable or the batch call fails.
        :rtype: dict
        """
        global _LSEG_AVAILABLE  # declared early so it can be written in except

        if not self._use_lseg:
            return {}

        # Build RIC → (db_symbol, yf_ticker) mapping
        ric_to_info: dict = {}
        for db_symbol, yf_ticker, _currency in ticker_map:
            ric = _yf_to_ric(yf_ticker)
            ric_to_info[ric] = (db_symbol, yf_ticker)

        try:
            import lseg.data as ld  # noqa: PLC0415

            all_rics = list(ric_to_info.keys())
            pipeline_logger.info(
                f"ESG: batch-fetching {len(all_rics)} tickers via LSEG "
                f"(single API call — no per-ticker delay)..."
            )

            # Retry the batch call up to 5 times with increasing backoff.
            # LSEG batch can fail intermittently due to server load or
            # session takeover propagation delay.
            df = None
            for _attempt in range(5):
                try:
                    df = ld.get_data(universe=all_rics, fields=_LSEG_ESG_FIELDS)
                    if df is not None and not df.empty:
                        break
                    pipeline_logger.warning(
                        f"ESG batch attempt {_attempt + 1}/5: empty response — retrying..."
                    )
                except Exception as retry_exc:
                    pipeline_logger.warning(
                        f"ESG batch attempt {_attempt + 1}/5 failed: {retry_exc} — retrying..."
                    )
                if _attempt < 4:
                    wait = 2 ** _attempt  # 1s, 2s, 4s, 8s
                    time.sleep(wait)

            if df is None or df.empty:
                pipeline_logger.warning(
                    "ESG batch: LSEG returned empty after 5 attempts — falling back to per-ticker path."
                )
                self._use_lseg = False
                return {}

            results: dict = {}
            for _, row in df.iterrows():
                ric = row.get("Instrument")
                if ric not in ric_to_info:
                    continue
                _db_sym, yf_ticker = ric_to_info[ric]

                def _safe(col, _row=row):
                    val = _row.get(col)
                    try:
                        result = float(val)
                        return None if math.isnan(result) else result
                    except (TypeError, ValueError):
                        return None

                total = _safe("ESG Score")
                env = _safe("Environmental Pillar Score")
                soc = _safe("Social Pillar Score")
                gov = _safe("Governance Pillar Score")
                grade = row.get("ESG Score Grade")

                if total is None and env is None and soc is None and gov is None:
                    results[yf_ticker] = None
                else:
                    results[yf_ticker] = {
                        "symbol": yf_ticker,
                        "cob_date": date.today().isoformat(),
                        "total_esg": total,
                        "environment_score": env,
                        "social_score": soc,
                        "governance_score": gov,
                        "peer_percentile": None,
                        "peer_group": str(grade) if grade else None,
                        "source": "lseg_platform",
                    }

            found = sum(1 for v in results.values() if v is not None)
            pipeline_logger.info(f"ESG batch: {found}/{len(all_rics)} tickers returned data")
            return results

        except Exception as exc:
            exc_str = str(exc)
            # If the LSEG session is dead (quota exceeded or closed), mark it
            # unavailable immediately so per-ticker fallback doesn't trigger
            # 678 individual "Session is not opened" prints from the LSEG lib.
            if "Session is not opened" in exc_str or "session quota" in exc_str.lower():
                _LSEG_AVAILABLE = False
                self._use_lseg = False
                pipeline_logger.warning(
                    f"ESG batch failed — LSEG session dead ({exc_str[:120]}). "
                    "All tickers will be marked SKIPPED."
                )
            else:
                pipeline_logger.warning(
                    f"ESG batch download failed ({exc_str[:120]}) — " "falling back to per-ticker download."
                )
            return {}

    def _download_yfinance(self, symbol: str) -> Optional[dict]:
        """Fetch ESG scores from Yahoo Finance for *symbol* (fallback).

        :param symbol: Yahoo Finance ticker symbol.
        :type symbol: str
        :return: Parsed ESG record or None.
        :rtype: dict or None
        """
        global _YF_DEPRECATION_LOGGED

        ticker = yf.Ticker(symbol)

        try:
            sus = ticker.sustainability
            if sus is not None and not sus.empty:
                return self._parse_sustainability(sus, symbol)
        except Exception:
            pass

        try:
            info = ticker.info or {}
            if info.get("esgPopulated") is True:
                return self._parse_info_esg(info, symbol)
        except Exception:
            pass

        if not _YF_DEPRECATION_LOGGED:
            pipeline_logger.info(
                "Yahoo Finance ESG/Sustainalytics data is no longer available "
                "(deprecated late 2024). Configure REFINITIV_* env vars for "
                "live ESG data via LSEG Data Platform."
            )
            _YF_DEPRECATION_LOGGED = True

        return None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sustainability(sus, symbol: str) -> dict:
        """Parse yfinance sustainability DataFrame to a flat dict.

        :param sus: Sustainability DataFrame from yfinance.
        :param symbol: Ticker symbol.
        :type symbol: str
        :return: Parsed ESG record.
        :rtype: dict
        """

        def _safe_get(idx):
            try:
                if idx in sus.index:
                    val = sus.loc[idx, "Value"] if "Value" in sus.columns else sus.loc[idx].iloc[0]
                    return float(val) if val is not None else None
            except (ValueError, TypeError, IndexError):
                pass
            return None

        return {
            "symbol": symbol,
            "cob_date": date.today().isoformat(),
            "total_esg": _safe_get("totalEsg"),
            "environment_score": _safe_get("environmentScore"),
            "social_score": _safe_get("socialScore"),
            "governance_score": _safe_get("governanceScore"),
            "peer_percentile": _safe_get("percentile"),
            "peer_group": (
                sus.loc["peerGroup", "Value"] if "peerGroup" in sus.index and "Value" in sus.columns else None
            ),
            "source": "yfinance_sustainability",
        }

    @staticmethod
    def _parse_info_esg(info: dict, symbol: str) -> Optional[dict]:
        """Parse ESG-related fields from yfinance Ticker.info.

        :param info: Info dictionary from yfinance.
        :type info: dict
        :param symbol: Ticker symbol.
        :type symbol: str
        :return: Parsed ESG record or None.
        :rtype: dict or None
        """

        def _safe_float(key):
            val = info.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
            return None

        total = _safe_float("totalEsg")
        env = _safe_float("environmentScore")
        social = _safe_float("socialScore")
        gov = _safe_float("governanceScore")

        if total is None and env is None and social is None and gov is None:
            return None

        return {
            "symbol": symbol,
            "cob_date": date.today().isoformat(),
            "total_esg": total,
            "environment_score": env,
            "social_score": social,
            "governance_score": gov,
            "peer_percentile": _safe_float("peerEsgScorePerformance"),
            "peer_group": info.get("peerGroup"),
            "source": "yfinance_info",
        }


def clean_esg_record(record: dict) -> Optional[dict]:
    """Clean and validate an ESG record for PostgreSQL insertion.

    Strips metadata fields (``source``, ``peer_group``) not stored in
    the relational table, and rejects records without a total ESG score.

    :param record: Raw ESG record from downloader.
    :type record: dict
    :return: Cleaned record ready for upsert, or None if invalid.
    :rtype: dict or None
    """
    if record is None:
        return None
    if record.get("total_esg") is None:
        return None
    return {
        "symbol": record["symbol"],
        "cob_date": record["cob_date"],
        "total_esg": record.get("total_esg"),
        "environment_score": record.get("environment_score"),
        "social_score": record.get("social_score"),
        "governance_score": record.get("governance_score"),
        "peer_percentile": record.get("peer_percentile"),
    }
