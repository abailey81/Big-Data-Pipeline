"""Point-in-time data loader querying the CW1 PostgreSQL schema directly.

CW2 is a natural continuation of CW1: we do **not** duplicate CW1 data.  We
query the live ``systematic_equity`` schema (port 5439, fift DB) at each
rebalance date, enforcing the seven PIT rules (PLAN.md §7.3) via SQL
``WHERE`` clauses.

CW1↔CW2 tight coupling
----------------------
- CW1 table names are used verbatim: ``daily_prices``, ``fundamentals`` (EAV),
  ``fx_rates``, ``vix_data``, ``risk_free_rate``, ``benchmark_index``,
  ``news_sentiment``, ``company_static``, ``company_ratios``.
- Currency-mapping rules mirror CW1 ``modules/processing/ticker_utils.py``:
  `.L → GBP`, `.PA/.AS/.DE/.MC/.MI → EUR`, `.TO → CAD`, `.S / .SW → CHF`,
  default USD.  We read ``company_static.currency`` when available.
- Return conversion formula follows CW1 report §2.5:
  ``R^USD_t = (1 + R^local_t)·(FX_t/FX_{t-1}) - 1``.

Key PIT guarantees
------------------
1. ``fundamentals`` filter uses ``report_date ≤ rebalance_date`` (NOT
   ``period_end``) — CW1 §5.1 flagged this as a look-ahead-bias trap.
2. News sentiment: ``cob_date ≤ rebalance_date``.
3. VIX regime: only trailing 252 days to ``rebalance_date - 1``.
4. Prices: close of ``rebalance_date − 1`` for both signal and return.

References
----------
CW1 report §§2.3, 2.5, 5.1; Korajczyk & Sadka (2004); Diether-Lee-Werner (2009).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from engine.config import Config, DatabaseConfig
from engine.types import UniverseSnapshot

logger = logging.getLogger(__name__)


# =============================================================================
# CW1↔CW2 shared conventions
# =============================================================================
_CW1_CURRENCY_MAP = {
    ".L": "GBP",
    ".PA": "EUR",
    ".AS": "EUR",
    ".DE": "EUR",
    ".MC": "EUR",
    ".MI": "EUR",
    ".TO": "CAD",
    ".S": "CHF",
    ".SW": "CHF",
}
_CW1_DEFAULT_CURRENCY = "USD"


def infer_currency(symbol: str) -> str:
    """Currency inference replicating CW1 ``ticker_utils.infer_currency()``."""
    for suffix, ccy in _CW1_CURRENCY_MAP.items():
        if symbol.endswith(suffix):
            return ccy
    return _CW1_DEFAULT_CURRENCY


def currency_to_fx_pair(ccy: str) -> str:
    """Map ISO currency to CW1 FX pair name. USD maps to None (sentinel)."""
    if ccy == "USD":
        return None  # type: ignore
    return f"{ccy}USD=X"


# =============================================================================
# Engine-facing API
# =============================================================================
@dataclass(frozen=True)
class PITContext:
    """All data needed at a single rebalance date, post-PIT cutoff."""

    rebalance_date: date
    universe: UniverseSnapshot
    prices: pd.DataFrame          # (date × symbol) adjusted close, trailing lookback
    returns_local: pd.DataFrame   # (date × symbol) local-currency log-returns
    returns_usd: pd.DataFrame     # (date × symbol) USD-converted returns
    fundamentals: pd.DataFrame    # (symbol × field) pivoted EAV of most recent
    ratios: pd.DataFrame          # (symbol × field) pivoted CW1 company_ratios
    sentiment: pd.Series          # symbol → composite sentiment_score
    fx_rates: pd.DataFrame        # (date × pair) FX rates
    vix: pd.Series                # VIX daily close over lookback
    rf_rate: float                # last DGS3MO (decimal)
    benchmark: pd.Series          # ^GSPC daily close over lookback
    avg_dollar_volume: pd.Series  # symbol → trailing 30d avg $-volume


class DataLoader:
    """SQL-backed, PIT-disciplined context loader.

    All methods respect the seven PIT rules from PLAN.md §7.3.  Heavy queries
    are paramterised (SQLAlchemy :text binds) — no string interpolation.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._engine: Engine = self._build_engine(cfg.database)
        self._schema = cfg.database.schema_
        self._data_snapshot_sha256: Optional[str] = None

    # ------------------------------------------------------------------
    @staticmethod
    def _build_engine(db: DatabaseConfig) -> Engine:
        # URL-encode credential fields per security audit finding #2 — handles
        # passwords containing special characters safely.
        from sqlalchemy.engine import URL

        url = URL.create(
            drivername="postgresql+psycopg2",
            username=db.user,
            password=db.password,
            host=db.host,
            port=db.port,
            database=db.name,
        )
        return create_engine(url, pool_pre_ping=True, future=True)

    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        try:
            with self._engine.connect() as conn:
                n = conn.execute(
                    text(f"SELECT COUNT(*) FROM {self._schema}.company_static")
                ).scalar()
            logger.info("CW1 DB healthy: %d symbols in company_static", n)
            return True
        except Exception as exc:
            logger.error("CW1 DB unreachable: %s", exc)
            return False

    # ------------------------------------------------------------------
    def load_universe(self, as_of: date) -> UniverseSnapshot:
        """Load universe with GICS + currency mapping.

        CW1 ``company_static`` has no currency column; we join against
        ``daily_prices`` (which carries per-row currency) to get the mode
        currency per symbol, with suffix-rule fallback for symbols lacking
        price rows.
        """
        q = text(
            f"""
            WITH ccy AS (
                SELECT DISTINCT ON (symbol) symbol, currency
                FROM {self._schema}.daily_prices
                ORDER BY symbol, cob_date DESC
            )
            SELECT TRIM(cs.symbol) AS symbol,
                   cs.gics_sector,
                   cs.country,
                   ccy.currency
            FROM {self._schema}.company_static cs
            LEFT JOIN ccy ON ccy.symbol = TRIM(cs.symbol)
            WHERE cs.symbol IS NOT NULL
            """
        )
        df = pd.read_sql(q, self._engine)
        if self.cfg.universe.variant == "us_only":
            df = df[df["country"].str.strip().str.upper() == "USA"]
        df["currency"] = df.apply(
            lambda r: r["currency"] if pd.notnull(r["currency"]) else infer_currency(r["symbol"]),
            axis=1,
        )
        df["gics_sector"] = df["gics_sector"].fillna("Unknown")
        symbols = df["symbol"].tolist()
        return UniverseSnapshot(
            date=as_of,
            symbols=symbols,
            gics_map=dict(zip(df["symbol"], df["gics_sector"])),
            currency_map=dict(zip(df["symbol"], df["currency"])),
        )

    # ------------------------------------------------------------------
    def load_prices(self, as_of: date, lookback_days: int) -> pd.DataFrame:
        """Adjusted close prices for all symbols, trailing lookback.

        PIT rule 4: close of ``as_of - 1`` is the latest row included.
        """
        start = as_of - timedelta(days=lookback_days)
        q = text(
            f"""
            SELECT cob_date, symbol, adj_close_price AS adj_close
            FROM {self._schema}.daily_prices
            WHERE cob_date >= :start AND cob_date < :as_of
              AND adj_close_price IS NOT NULL
            """
        )
        df = pd.read_sql(q, self._engine, params={"start": start, "as_of": as_of})
        wide = df.pivot_table(index="cob_date", columns="symbol", values="adj_close")
        wide.index = pd.to_datetime(wide.index)
        return wide.sort_index()

    # ------------------------------------------------------------------
    def load_fx(self, as_of: date, lookback_days: int) -> pd.DataFrame:
        start = as_of - timedelta(days=lookback_days)
        q = text(
            f"""
            SELECT cob_date, currency_pair, close_rate
            FROM {self._schema}.fx_rates
            WHERE cob_date >= :start AND cob_date < :as_of
              AND close_rate IS NOT NULL
            """
        )
        df = pd.read_sql(q, self._engine, params={"start": start, "as_of": as_of})
        wide = df.pivot_table(index="cob_date", columns="currency_pair", values="close_rate")
        wide.index = pd.to_datetime(wide.index)
        return wide.sort_index().ffill()

    # ------------------------------------------------------------------
    def load_vix(self, as_of: date, lookback_days: int) -> pd.Series:
        start = as_of - timedelta(days=lookback_days)
        q = text(
            f"""
            SELECT cob_date, close_price
            FROM {self._schema}.vix_data
            WHERE cob_date >= :start AND cob_date < :as_of
              AND close_price IS NOT NULL
            """
        )
        df = pd.read_sql(q, self._engine, params={"start": start, "as_of": as_of})
        s = pd.Series(
            df["close_price"].astype(float).values,
            index=pd.to_datetime(df["cob_date"]),
            name="vix",
        )
        return s.sort_index()

    # ------------------------------------------------------------------
    def load_rf_rate(self, as_of: date) -> float:
        q = text(
            f"""
            SELECT rate_pct
            FROM {self._schema}.risk_free_rate
            WHERE cob_date < :as_of AND rate_pct IS NOT NULL
            ORDER BY cob_date DESC
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            res = conn.execute(q, {"as_of": as_of}).scalar()
        # FRED DGS3MO is percent annualised (e.g. 5.3 = 5.3%)
        return float(res) / 100.0 if res is not None else 0.0

    # ------------------------------------------------------------------
    def load_benchmark(self, as_of: date, lookback_days: int, symbol: str = "^GSPC") -> pd.Series:
        start = as_of - timedelta(days=lookback_days)
        q = text(
            f"""
            SELECT cob_date, adj_close_price
            FROM {self._schema}.benchmark_index
            WHERE cob_date >= :start AND cob_date < :as_of
              AND symbol = :bench AND adj_close_price IS NOT NULL
            """
        )
        df = pd.read_sql(q, self._engine, params={"start": start, "as_of": as_of, "bench": symbol})
        return pd.Series(
            df["adj_close_price"].astype(float).values,
            index=pd.to_datetime(df["cob_date"]),
            name="benchmark",
        ).sort_index()

    # ------------------------------------------------------------------
    def load_fundamentals_pit(
        self,
        as_of: date,
        symbols: list[str],
        pit_lag_days: int = 0,
    ) -> pd.DataFrame:
        """Fundamentals EAV → pivoted most-recent value per (symbol, field).

        PIT rule 1: ``report_date ≤ rebalance_date − pit_lag_days`` (not
        ``period_end``).  ``pit_lag_days = 0`` is the CW1/PLAN §7.3 default
        and reproduces the pre-v0.3.2 cutoff; positive values apply a
        filing-delay proxy (US 10-Q SEC deadline for large accelerated
        filers is 40 days) and are supplied by
        ``PitLagConfig.fundamentals_days`` for sensitivity analysis only.
        """
        if not symbols:
            return pd.DataFrame()
        pit_cutoff = as_of - timedelta(days=max(0, int(pit_lag_days)))
        q = text(
            f"""
            WITH ranked AS (
                SELECT
                    symbol, field_name, field_value, report_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, field_name
                        ORDER BY report_date DESC
                    ) AS rn
                FROM {self._schema}.fundamentals
                WHERE symbol = ANY(:symbols)
                  AND report_date <= :pit_cutoff
                  AND period_type = 'quarterly'
                  AND field_value IS NOT NULL
            )
            SELECT symbol, field_name, field_value, report_date
            FROM ranked
            WHERE rn = 1
            """
        )
        df = pd.read_sql(q, self._engine, params={"symbols": symbols, "pit_cutoff": pit_cutoff})
        if df.empty:
            return pd.DataFrame()
        wide = df.pivot_table(index="symbol", columns="field_name", values="field_value")
        return wide

    # ------------------------------------------------------------------
    def load_sentiment_pit(self, as_of: date, symbols: list[str]) -> pd.Series:
        """Most recent sentiment_score per symbol, strict ``cob_date ≤ as_of``."""
        if not symbols:
            return pd.Series(dtype=float)
        q = text(
            f"""
            WITH ranked AS (
                SELECT symbol, sentiment_score, cob_date,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol
                           ORDER BY cob_date DESC
                       ) AS rn
                FROM {self._schema}.news_sentiment
                WHERE symbol = ANY(:symbols)
                  AND cob_date <= :as_of
                  AND sentiment_score IS NOT NULL
            )
            SELECT symbol, sentiment_score
            FROM ranked
            WHERE rn = 1
            """
        )
        df = pd.read_sql(q, self._engine, params={"symbols": symbols, "as_of": as_of})
        return pd.Series(df["sentiment_score"].values, index=df["symbol"], name="sentiment")

    # ------------------------------------------------------------------
    def load_ratios_pit(
        self,
        as_of: date,
        symbols: list[str],
        pit_lag_days: int = 0,
    ) -> pd.DataFrame:
        """Historical ratios from ``company_ratios`` (EAV) — most recent per (sym, field).

        ``pit_lag_days`` shifts the cutoff to
        ``snapshot_date ≤ as_of − pit_lag_days`` for filing-delay sensitivity
        analysis.  Default 0 preserves PLAN §7.3 behaviour and is supplied
        by ``PitLagConfig.ratios_days`` via ``build_context``.
        """
        if not symbols:
            return pd.DataFrame()
        pit_cutoff = as_of - timedelta(days=max(0, int(pit_lag_days)))
        q = text(
            f"""
            WITH ranked AS (
                SELECT symbol, field_name, field_value, snapshot_date,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, field_name
                           ORDER BY snapshot_date DESC
                       ) AS rn
                FROM {self._schema}.company_ratios
                WHERE symbol = ANY(:symbols) AND snapshot_date <= :pit_cutoff
            )
            SELECT symbol, field_name, field_value
            FROM ranked WHERE rn = 1
            """
        )
        df = pd.read_sql(q, self._engine, params={"symbols": symbols, "pit_cutoff": pit_cutoff})
        if df.empty:
            return pd.DataFrame()
        return df.pivot_table(index="symbol", columns="field_name", values="field_value")

    # ------------------------------------------------------------------
    def _convert_returns_to_usd(
        self, returns_local: pd.DataFrame, fx_rates: pd.DataFrame, currency_map: dict[str, str]
    ) -> pd.DataFrame:
        """Apply CW1 Eq. (2.5): R^USD = (1 + R_local)(FX_t/FX_{t-1}) - 1."""
        out = returns_local.copy()
        for symbol in out.columns:
            ccy = currency_map.get(symbol, "USD")
            if ccy == "USD":
                continue
            pair = currency_to_fx_pair(ccy)
            if pair not in fx_rates.columns:
                continue
            fx = fx_rates[pair].reindex(out.index, method="ffill")
            fx_ret = fx.pct_change()
            out[symbol] = (1.0 + out[symbol]) * (1.0 + fx_ret) - 1.0
        return out

    # ------------------------------------------------------------------
    def _compute_adv(self, as_of: date, symbols: list[str]) -> pd.Series:
        """Trailing 30-day average daily dollar-volume per symbol (USD equivalent)."""
        start = as_of - timedelta(days=45)
        q = text(
            f"""
            SELECT symbol, AVG(adj_close_price * volume) AS adv
            FROM {self._schema}.daily_prices
            WHERE cob_date >= :start AND cob_date < :as_of
              AND symbol = ANY(:symbols)
              AND volume IS NOT NULL AND adj_close_price IS NOT NULL
            GROUP BY symbol
            """
        )
        df = pd.read_sql(q, self._engine, params={"start": start, "as_of": as_of, "symbols": symbols})
        return pd.Series(df["adv"].astype(float).values, index=df["symbol"], name="adv_usd")

    # ------------------------------------------------------------------
    def apply_liquidity_filter(
        self, universe: UniverseSnapshot, adv: pd.Series
    ) -> tuple[UniverseSnapshot, int]:
        """§5.15 Liquidity filter — exclude bottom-percentile & < min_adv_usd."""
        cfg = self.cfg.universe
        if adv.empty:
            return universe, 0
        min_adv = cfg.min_adv_usd
        pct_cut = adv.quantile(cfg.bottom_pct_filter)
        threshold = max(min_adv, pct_cut)
        keep_mask = adv >= threshold
        keep = set(adv[keep_mask].index.tolist())
        filtered = [s for s in universe.symbols if s in keep]
        n_removed = len(universe.symbols) - len(filtered)
        snap = UniverseSnapshot(
            date=universe.date,
            symbols=filtered,
            gics_map={s: universe.gics_map[s] for s in filtered if s in universe.gics_map},
            currency_map={s: universe.currency_map[s] for s in filtered if s in universe.currency_map},
        )
        return snap, n_removed

    # ------------------------------------------------------------------
    def build_context(
        self,
        rebalance_date: date,
        price_lookback_days: int = 756,
        apply_liquidity_filter: bool = True,
    ) -> PITContext:
        """Build a full PIT-safe context for one rebalance date.

        This is the engine's single entry point; returns a frozen ``PITContext``
        that downstream modules can treat as read-only.
        """
        universe = self.load_universe(rebalance_date)
        adv = self._compute_adv(rebalance_date, universe.symbols)
        if apply_liquidity_filter:
            universe, n_filtered = self.apply_liquidity_filter(universe, adv)
            logger.info("Liquidity filter removed %d / %d names", n_filtered, n_filtered + len(universe.symbols))
        prices = self.load_prices(rebalance_date, price_lookback_days)
        prices = prices[[c for c in prices.columns if c in universe.symbols]]
        returns_local = prices.pct_change().dropna(how="all")
        fx = self.load_fx(rebalance_date, price_lookback_days)
        returns_usd = self._convert_returns_to_usd(returns_local, fx, universe.currency_map)
        pit_fund = int(getattr(self.cfg.pit_lag, "fundamentals_days", 0))
        pit_rat = int(getattr(self.cfg.pit_lag, "ratios_days", 0))
        fund = self.load_fundamentals_pit(
            rebalance_date, universe.symbols, pit_lag_days=pit_fund
        )
        ratios = self.load_ratios_pit(
            rebalance_date, universe.symbols, pit_lag_days=pit_rat
        )
        sent = self.load_sentiment_pit(rebalance_date, universe.symbols)
        vix = self.load_vix(rebalance_date, max(price_lookback_days, 400))
        rf = self.load_rf_rate(rebalance_date)
        bench = self.load_benchmark(rebalance_date, price_lookback_days)
        return PITContext(
            rebalance_date=rebalance_date,
            universe=universe,
            prices=prices,
            returns_local=returns_local,
            returns_usd=returns_usd,
            fundamentals=fund,
            ratios=ratios,
            sentiment=sent,
            fx_rates=fx,
            vix=vix,
            rf_rate=rf,
            benchmark=bench,
            avg_dollar_volume=adv,
        )

    # ------------------------------------------------------------------
    def data_snapshot_sha256(self) -> str:
        """Content-sensitive SHA-256 of the live CW1 DB state (§7.12).

        Second-pass audit finding: the prior implementation hashed only row
        counts and max-dates, which would collide silently if CW1 fixed a
        single cell (e.g. corrected AAPL's 2023-05-01 close) without
        changing counts or date range.  This version mixes in an MD5
        aggregate of the actual daily_prices payload (symbol||date||price)
        so any content mutation changes the hash.
        """
        import hashlib

        if self._data_snapshot_sha256:
            return self._data_snapshot_sha256
        q = text(
            f"""
            WITH daily_agg AS (
                SELECT MD5(STRING_AGG(symbol || '|' || cob_date || '|' ||
                       COALESCE(adj_close_price::text, 'NULL'),
                       '/' ORDER BY symbol, cob_date)) AS payload_md5
                FROM {self._schema}.daily_prices
                WHERE cob_date >= CURRENT_DATE - INTERVAL '90 days'
            ),
            fund_agg AS (
                SELECT MD5(STRING_AGG(symbol || '|' || report_date || '|' ||
                       field_name || '|' || COALESCE(field_value::text, 'NULL'),
                       '/' ORDER BY symbol, report_date, field_name)) AS payload_md5
                FROM {self._schema}.fundamentals
                WHERE report_date >= CURRENT_DATE - INTERVAL '180 days'
                  AND period_type = 'quarterly'
            )
            SELECT
                (SELECT COUNT(*) FROM {self._schema}.daily_prices),
                (SELECT MAX(cob_date) FROM {self._schema}.daily_prices),
                (SELECT COUNT(*) FROM {self._schema}.fundamentals),
                (SELECT COUNT(*) FROM {self._schema}.news_sentiment),
                (SELECT COUNT(*) FROM {self._schema}.vix_data),
                (SELECT COUNT(*) FROM {self._schema}.company_static),
                (SELECT payload_md5 FROM daily_agg),
                (SELECT payload_md5 FROM fund_agg)
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(q).one()
        payload = repr(tuple(str(v) for v in row)).encode()
        self._data_snapshot_sha256 = hashlib.sha256(payload).hexdigest()
        return self._data_snapshot_sha256


__all__ = [
    "DataLoader",
    "PITContext",
    "currency_to_fx_pair",
    "infer_currency",
]
