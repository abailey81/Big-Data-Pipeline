"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Pydantic validation models for pipeline data entities
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

All incoming data passes through these models before database insertion.
Uses ift_global.utils.string_utils.trim_string for symbol cleaning.

"""

from datetime import date
from typing import Literal, Optional

from ift_global.utils.string_utils import trim_string
from pydantic import BaseModel, Field, field_validator, model_validator


class DailyPrice(BaseModel):
    """Pydantic model for a single daily price record.

    Validates OHLCV + adjusted close data from Yahoo Finance.
    Handles NaN coercion and ticker whitespace (Spec §7.2 Issue 1).

    :param symbol: Ticker symbol (trailing whitespace stripped)
    :param cob_date: Close of business date
    :param open_price: Opening price in local currency
    :param high_price: Intraday high price
    :param low_price: Intraday low price
    :param close_price: Closing price
    :param adj_close_price: Adjusted close (accounts for splits/dividends)
    :param volume: Trading volume in shares
    :param currency: 3-letter ISO currency code (inferred from suffix)
    """

    symbol: str = Field(..., description="Ticker symbol")
    cob_date: date = Field(..., description="Close of business date")
    open_price: Optional[float] = Field(None, description="Opening price")
    high_price: Optional[float] = Field(None, description="High price")
    low_price: Optional[float] = Field(None, description="Low price")
    close_price: Optional[float] = Field(None, description="Closing price")
    adj_close_price: Optional[float] = Field(None, description="Adjusted close")
    volume: Optional[int] = Field(None, description="Trading volume")
    currency: str = Field("USD", description="ISO currency code")

    @field_validator("symbol", mode="before")
    @classmethod
    def clean_symbol(cls, v) -> str:
        """Strip trailing whitespace using ift_global.trim_string."""
        try:
            return trim_string(v, what="trailing")
        except (ValueError, TypeError):
            return str(v).strip() if v else v

    @field_validator("open_price", "high_price", "low_price", "close_price", "adj_close_price", mode="before")
    @classmethod
    def coerce_price(cls, v):
        """Coerce price to float; NaN and inf become None."""
        if v is None:
            return None
        try:
            val = float(v)
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None

    @field_validator("volume", mode="before")
    @classmethod
    def coerce_volume(cls, v):
        """Coerce volume to int; NaN becomes None."""
        if v is None:
            return None
        try:
            val = float(v)
            if val != val:
                return None
            return int(val)
        except (ValueError, TypeError):
            return None

    @model_validator(mode="after")
    def validate_price_consistency(self):
        """Cross-field validation: swap high/low if inverted.

        Yahoo Finance occasionally returns inverted high/low for illiquid
        securities. This auto-corrects rather than rejecting the record.
        """
        if self.high_price is not None and self.low_price is not None and self.high_price < self.low_price:
            self.high_price, self.low_price = self.low_price, self.high_price
        return self


class FundamentalRecord(BaseModel):
    """Pydantic model for a single fundamental data record (EAV pattern).

    Uses Entity-Attribute-Value design so any financial field can be
    stored without schema migration (Spec §7.2 Issue 6).

    :param symbol: Ticker symbol
    :param report_date: Quarterly report date
    :param field_name: Canonical name of the financial metric
    :param field_value: Numeric value (NULL for missing)
    :param period_type: 'quarterly' or 'annual'
    :param currency: Currency of the reported value
    """

    symbol: str = Field(..., description="Ticker symbol")
    report_date: date = Field(..., description="Quarterly report date")
    field_name: str = Field(..., description="Financial metric name")
    field_value: Optional[float] = Field(None, description="Metric value")
    period_type: str = Field(default="quarterly", description="Report period type")
    currency: Optional[str] = Field(None, description="Reporting currency")

    @field_validator("symbol", mode="before")
    @classmethod
    def clean_symbol(cls, v) -> str:
        try:
            return trim_string(v, what="trailing")
        except (ValueError, TypeError):
            return str(v).strip() if v else v

    @field_validator("field_value", mode="before")
    @classmethod
    def coerce_value(cls, v):
        if v is None:
            return None
        try:
            val = float(v)
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None


class FxRate(BaseModel):
    """Pydantic model for a daily FX rate record.

    :param currency_pair: Yahoo Finance pair ID (e.g. 'GBPUSD=X')
    :param cob_date: Close of business date
    :param open_rate: Opening exchange rate
    :param high_rate: Intraday high rate
    :param low_rate: Intraday low rate
    :param close_rate: Closing exchange rate
    """

    currency_pair: str = Field(..., description="Currency pair identifier")
    cob_date: date = Field(..., description="Close of business date")
    open_rate: Optional[float] = Field(None, description="Opening FX rate")
    high_rate: Optional[float] = Field(None, description="High FX rate")
    low_rate: Optional[float] = Field(None, description="Low FX rate")
    close_rate: Optional[float] = Field(None, description="Closing FX rate")

    @field_validator("open_rate", "high_rate", "low_rate", "close_rate", mode="before")
    @classmethod
    def coerce_rate(cls, v):
        if v is None:
            return None
        try:
            val = float(v)
            if val != val:
                return None
            return val
        except (ValueError, TypeError):
            return None


class VixRecord(BaseModel):
    """Pydantic model for a daily VIX index record.

    Required for volatility regime classification in Phase 2 (Spec §4.4).

    :param cob_date: Close of business date
    :param open_price: Opening VIX value
    :param high_price: Intraday high VIX
    :param low_price: Intraday low VIX
    :param close_price: Closing VIX value
    :param adj_close_price: Adjusted close VIX
    :param volume: VIX volume (may be None)
    """

    cob_date: date = Field(..., description="Close of business date")
    open_price: Optional[float] = Field(None, description="Opening VIX")
    high_price: Optional[float] = Field(None, description="High VIX")
    low_price: Optional[float] = Field(None, description="Low VIX")
    close_price: Optional[float] = Field(None, description="Closing VIX")
    adj_close_price: Optional[float] = Field(None, description="Adjusted close VIX")
    volume: Optional[int] = Field(None, description="VIX volume")

    @field_validator("open_price", "high_price", "low_price", "close_price", "adj_close_price", mode="before")
    @classmethod
    def coerce_price(cls, v):
        if v is None:
            return None
        try:
            val = float(v)
            if val != val:
                return None
            return val
        except (ValueError, TypeError):
            return None

    @field_validator("volume", mode="before")
    @classmethod
    def coerce_volume(cls, v):
        if v is None:
            return None
        try:
            val = float(v)
            if val != val:
                return None
            return int(val)
        except (ValueError, TypeError):
            return None


class RiskFreeRateRecord(BaseModel):
    """Pydantic model for a daily risk-free rate record from FRED.

    The 3-month US Treasury rate (DGS3MO) serves as the risk-free
    rate proxy for Sharpe ratio calculation in Phase 2 (Spec §7.3, P2).

    :param cob_date: Observation date
    :param rate_pct: Annual rate in percent (e.g. 4.25)
    :param series_id: FRED series identifier
    """

    cob_date: date = Field(..., description="Observation date")
    rate_pct: Optional[float] = Field(None, description="Rate in percent")
    series_id: str = Field(default="DGS3MO", description="FRED series ID")

    @field_validator("rate_pct", mode="before")
    @classmethod
    def coerce_rate(cls, v):
        if v is None or v == ".":
            return None
        try:
            val = float(v)
            if val != val:
                return None
            return val
        except (ValueError, TypeError):
            return None


class IngestionLogEntry(BaseModel):
    """Pydantic model for an ingestion log entry.

    :param run_id: Unique run identifier (UUID)
    :param data_source: Source of data (prices, fundamentals, fx, vix)
    :param symbol: Ticker symbol (optional)
    :param status: Ingestion status
    :param rows_affected: Number of records loaded
    :param error_message: Error message if failed
    :param run_frequency: Pipeline run frequency
    :param date_range_start: Start of date range processed
    :param date_range_end: End of date range processed
    """

    run_id: str = Field(..., description="Pipeline run identifier")
    data_source: str = Field(..., description="Data source name")
    symbol: Optional[str] = Field(None, description="Ticker symbol")
    status: Literal["SUCCESS", "FAILED", "PARTIAL", "SKIPPED"] = Field(..., description="Ingestion status")
    rows_affected: int = Field(default=0, description="Records processed")
    error_message: Optional[str] = Field(None, description="Error details")
    run_frequency: Optional[str] = Field(None, description="Run frequency")
    date_range_start: Optional[date] = Field(None, description="Range start")
    date_range_end: Optional[date] = Field(None, description="Range end")
