"""
data_loader.py -- v4 Alpha Engine data access layer.

All data access goes through DataLoader to enforce:
  - PIT lag (45 days) on fundamentals AND company_ratios
  - FX conversion for USD-denominated returns
  - No book_value / price hack (value uses _hist fields only)
  - Proper caching for repeated calls within backtest loop
"""

import pandas as pd
import numpy as np
from pathlib import Path


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------
_SUFFIX_TO_CCY = {
    ".L":  "GBP",
    ".PA": "EUR",
    ".AS": "EUR",
    ".DE": "EUR",
    ".TO": "CAD",
    ".SW": "CHF",
    ".S":  "CHF",
}

_CCY_TO_PAIR = {
    "GBP": "GBPUSD=X",
    "EUR": "EURUSD=X",
    "CAD": "CADUSD=X",
    "CHF": "CHFUSD=X",
}


def _infer_currency(symbol: str) -> str:
    for suffix, ccy in _SUFFIX_TO_CCY.items():
        if symbol.endswith(suffix):
            return ccy
    return "USD"


class DataLoader:
    """Central data access with all bug fixes baked in."""

    def __init__(self, data_dir: str = "../data"):
        self.data_dir = Path(data_dir)
        self._prices = None
        self._fundamentals = None
        self._company_ratios_raw = None
        self._vix = None
        self._fx = None
        self._benchmark = None
        self._rf = None
        self._company_static = None
        self._news_sentiment = None
        self._returns_usd = None
        self._price_matrix = None
        self._volume_matrix = None

    # ------------------------------------------------------------------
    # Raw loaders (cached)
    # ------------------------------------------------------------------
    def get_prices(self) -> pd.DataFrame:
        if self._prices is None:
            self._prices = pd.read_parquet(self.data_dir / "daily_prices.parquet")
            self._prices["date"] = pd.to_datetime(self._prices["date"])
            self._prices.sort_values(["symbol", "date"], inplace=True)
            self._prices.reset_index(drop=True, inplace=True)
        return self._prices

    def get_fundamentals_raw(self) -> pd.DataFrame:
        if self._fundamentals is None:
            self._fundamentals = pd.read_parquet(
                self.data_dir / "fundamentals.parquet"
            )
            self._fundamentals["report_date"] = pd.to_datetime(
                self._fundamentals["report_date"]
            )
        return self._fundamentals

    def _get_company_ratios_raw(self) -> pd.DataFrame:
        if self._company_ratios_raw is None:
            pq = self.data_dir / "company_ratios.parquet"
            if not pq.exists():
                self._company_ratios_raw = pd.DataFrame()
            else:
                cr = pd.read_parquet(pq)
                cr["snapshot_date"] = pd.to_datetime(cr["snapshot_date"])
                self._company_ratios_raw = cr
        return self._company_ratios_raw

    def get_vix(self) -> pd.DataFrame:
        if self._vix is None:
            self._vix = pd.read_parquet(self.data_dir / "vix.parquet")
            self._vix["date"] = pd.to_datetime(self._vix["date"])
            self._vix.sort_values("date", inplace=True)
        return self._vix

    def get_fx_rates(self) -> pd.DataFrame:
        if self._fx is None:
            self._fx = pd.read_parquet(self.data_dir / "fx_rates.parquet")
            self._fx["date"] = pd.to_datetime(self._fx["date"])
        return self._fx

    def get_benchmark(self) -> pd.DataFrame:
        if self._benchmark is None:
            self._benchmark = pd.read_parquet(self.data_dir / "benchmark.parquet")
            self._benchmark["date"] = pd.to_datetime(self._benchmark["date"])
            self._benchmark.sort_values("date", inplace=True)
        return self._benchmark

    def get_risk_free_rate(self) -> pd.Series:
        """Time-varying rf from FRED data, not a scalar."""
        if self._rf is None:
            rf = pd.read_parquet(self.data_dir / "risk_free_rate.parquet")
            rf["date"] = pd.to_datetime(rf["date"])
            self._rf = rf.set_index("date")["rate_pct"].sort_index()
        return self._rf

    def get_company_static(self) -> pd.DataFrame:
        if self._company_static is None:
            pq = self.data_dir / "company_static.parquet"
            csv = self.data_dir / "company_static.csv"
            if pq.exists():
                self._company_static = pd.read_parquet(pq)
            else:
                self._company_static = pd.read_csv(csv)
        return self._company_static

    def get_news_sentiment(self) -> pd.DataFrame:
        """Load and cache news_sentiment.parquet."""
        if self._news_sentiment is None:
            p = self.data_dir / "news_sentiment.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                df["cob_date"] = pd.to_datetime(df["cob_date"])
                self._news_sentiment = df
            else:
                self._news_sentiment = pd.DataFrame()
        return self._news_sentiment

    # ------------------------------------------------------------------
    # FX conversion -- returns in USD
    # ------------------------------------------------------------------
    def get_returns_usd(self) -> pd.DataFrame:
        """Compute daily USD-denominated returns for every stock."""
        if self._returns_usd is not None:
            return self._returns_usd

        prices = self.get_prices().copy()
        prices.sort_values(["symbol", "date"], inplace=True)
        prices["ret_local"] = prices.groupby("symbol")["adj_close"].pct_change()

        sym_ccy = (
            prices[["symbol"]]
            .drop_duplicates()
            .assign(currency=lambda d: d["symbol"].map(_infer_currency))
        )
        prices = prices.merge(
            sym_ccy.rename(columns={"currency": "ccy_inferred"}),
            on="symbol", how="left",
        )

        fx = self.get_fx_rates()
        fx_pivot = fx.pivot_table(
            index="date", columns="pair", values="adj close"
        ).sort_index()
        fx_ret = fx_pivot.pct_change()

        prices["fx_pair"] = prices["ccy_inferred"].map(_CCY_TO_PAIR)
        fx_long = fx_ret.stack().reset_index()
        fx_long.columns = ["date", "fx_pair", "ret_fx"]

        prices = prices.merge(fx_long, on=["date", "fx_pair"], how="left")
        prices["ret_fx"] = prices["ret_fx"].fillna(0.0)
        prices["ret_usd"] = (1 + prices["ret_local"]) * (1 + prices["ret_fx"]) - 1

        self._returns_usd = prices[["date", "symbol", "ret_usd"]].dropna(
            subset=["ret_usd"]
        )
        return self._returns_usd

    # ------------------------------------------------------------------
    # PIT-safe wide DataFrames
    # ------------------------------------------------------------------
    def get_fundamentals_wide(
        self, as_of: pd.Timestamp, pit_lag_days: int = 45
    ) -> pd.DataFrame:
        """Pivot fundamentals using ONLY data where report_date <= as_of - pit_lag.

        FIX (Codex audit #2): Applies 45-day PIT lag on fundamentals.
        FIX (Codex audit #3): NO book_value -> book_value_per_share hack.
        """
        raw = self.get_fundamentals_raw()
        pit_cutoff = as_of - pd.Timedelta(days=pit_lag_days)
        filtered = raw[raw["report_date"] <= pit_cutoff].copy()
        if filtered.empty:
            return pd.DataFrame()
        idx = filtered.groupby(["symbol", "field_name"])["report_date"].idxmax()
        latest = filtered.loc[idx]
        wide = latest.pivot_table(
            index="symbol", columns="field_name",
            values="field_value", aggfunc="first",
        )

        # EPS normalisation only -- NO book_value hack
        if "eps" not in wide.columns:
            if "diluted_eps" in wide.columns:
                wide["eps"] = wide["diluted_eps"]
            elif "basic_eps" in wide.columns:
                wide["eps"] = wide["basic_eps"]

        return wide

    def get_company_ratios_wide(
        self, as_of: pd.Timestamp, pit_lag_days: int = 45
    ) -> pd.DataFrame:
        """Load company_ratios as wide DataFrame, with PIT lag.

        FIX (Codex audit #2): Applies 45-day PIT lag on company_ratios too.
        """
        cr = self._get_company_ratios_raw()
        if cr.empty:
            return pd.DataFrame()
        pit_cutoff = as_of - pd.Timedelta(days=pit_lag_days)
        filtered = cr[cr["snapshot_date"] <= pit_cutoff]
        if filtered.empty:
            return pd.DataFrame()
        idx = filtered.groupby(["symbol", "field_name"])["snapshot_date"].idxmax()
        latest = filtered.loc[idx]
        wide = latest.pivot_table(
            index="symbol", columns="field_name",
            values="field_value", aggfunc="first",
        )
        return wide

    # ------------------------------------------------------------------
    # Convenience matrices (cached)
    # ------------------------------------------------------------------
    def get_price_matrix(self) -> pd.DataFrame:
        """Return date x symbol matrix of adj_close (local currency)."""
        if self._price_matrix is None:
            p = self.get_prices()
            self._price_matrix = p.pivot_table(
                index="date", columns="symbol", values="adj_close"
            )
        return self._price_matrix

    def get_volume_matrix(self) -> pd.DataFrame:
        """Return date x symbol matrix of daily volume."""
        if self._volume_matrix is None:
            p = self.get_prices()
            self._volume_matrix = p.pivot_table(
                index="date", columns="symbol", values="volume"
            )
        return self._volume_matrix

    def get_daily_returns_wide(self) -> pd.DataFrame:
        """Return date x symbol matrix of daily USD returns."""
        daily = self.get_returns_usd()
        return daily.pivot_table(
            index="date", columns="symbol", values="ret_usd"
        ).sort_index()
