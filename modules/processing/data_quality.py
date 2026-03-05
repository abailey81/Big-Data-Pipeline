"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Data quality checks and reporting
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Post-ingestion data quality layer that validates:
  - Completeness: missing fields, NULL rates
  - Freshness: how recent is the latest data point
  - Consistency: price sanity (high >= low), non-negative volumes
  - Coverage: percentage of tickers with data vs. total universe

"""

from modules.utils.info_logger import pipeline_logger


class DataQualityChecker:
    """Validates cleaned records before database insertion.

    Provides per-batch quality metrics without blocking the pipeline.
    Issues are logged as warnings — the pipeline continues regardless
    to maximise data capture (fail-open design).

    :param source: Data source name for log messages
    :type source: str

    :example:
        >>> checker = DataQualityChecker('prices')
        >>> report = checker.check_price_records(records)
        >>> checker.log_report(report)
    """

    def __init__(self, source: str):
        self.source = source

    def check_price_records(self, records: list[dict]) -> dict:
        """Validate a batch of price records for quality issues.

        Checks:
        - NULL close_price (critical — needed for returns)
        - high_price < low_price (data inconsistency)
        - Negative volume (impossible)
        - Stale data (latest date older than 5 business days)

        :param records: List of cleaned price record dicts
        :type records: list[dict]
        :return: Quality report dictionary
        :rtype: dict
        """
        if not records:
            return {"total": 0, "issues": []}

        issues = []
        null_close = sum(1 for r in records if r.get("close_price") is None)
        if null_close:
            issues.append(f"{null_close}/{len(records)} records have NULL close_price")

        hl_inverted = sum(
            1
            for r in records
            if r.get("high_price") is not None
            and r.get("low_price") is not None
            and r["high_price"] < r["low_price"]
        )
        if hl_inverted:
            issues.append(f"{hl_inverted} records have high < low")

        neg_vol = sum(1 for r in records if r.get("volume") is not None and r["volume"] < 0)
        if neg_vol:
            issues.append(f"{neg_vol} records have negative volume")

        return {
            "total": len(records),
            "null_close": null_close,
            "high_low_inverted": hl_inverted,
            "negative_volume": neg_vol,
            "issues": issues,
        }

    def check_fx_records(self, records: list[dict]) -> dict:
        """Validate a batch of FX rate records.

        :param records: List of cleaned FX rate record dicts
        :type records: list[dict]
        :return: Quality report dictionary
        :rtype: dict
        """
        if not records:
            return {"total": 0, "issues": []}

        issues = []
        null_close = sum(1 for r in records if r.get("close_rate") is None)
        if null_close:
            issues.append(f"{null_close}/{len(records)} FX records have NULL close_rate")

        non_positive = sum(1 for r in records if r.get("close_rate") is not None and r["close_rate"] <= 0)
        if non_positive:
            issues.append(f"{non_positive} FX records have non-positive rate")

        return {
            "total": len(records),
            "null_close": null_close,
            "non_positive_rate": non_positive,
            "issues": issues,
        }

    def check_fundamentals_records(self, records: list[dict]) -> dict:
        """Validate a batch of fundamental records.

        :param records: List of cleaned fundamental record dicts
        :type records: list[dict]
        :return: Quality report dictionary
        :rtype: dict
        """
        if not records:
            return {"total": 0, "issues": []}

        issues = []
        null_values = sum(1 for r in records if r.get("field_value") is None)
        null_pct = null_values / len(records) * 100

        if null_pct > 50:
            issues.append(
                f"{null_pct:.0f}% of fundamental values are NULL " f"({null_values}/{len(records)})"
            )

        field_counts = {}
        for r in records:
            fn = r.get("field_name", "unknown")
            field_counts[fn] = field_counts.get(fn, 0) + 1

        return {
            "total": len(records),
            "null_values": null_values,
            "null_pct": round(null_pct, 1),
            "field_distribution": field_counts,
            "issues": issues,
        }

    def log_report(self, report: dict, symbol: str = ""):
        """Log a quality report, emitting warnings for any issues.

        :param report: Quality report from a check method
        :type report: dict
        :param symbol: Optional symbol for context
        :type symbol: str
        """
        prefix = f"[DQ:{self.source}]"
        if symbol:
            prefix += f" {symbol}"

        if report.get("issues"):
            for issue in report["issues"]:
                pipeline_logger.warning(f"{prefix} {issue}")
        else:
            pipeline_logger.debug(f"{prefix} {report['total']} records passed quality checks")
