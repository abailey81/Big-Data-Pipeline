from pathlib import Path

import pandas as pd

from analytics.performance import PERIODS_PER_YEAR, compute_headline_metrics
from analytics.validation import validate_returns


def load_rf_period_series(return_dates: pd.Series) -> pd.Series:
    rf_df = pd.read_parquet("data/risk_free_rate.parquet")
    rf_df["date"] = pd.to_datetime(rf_df["date"])
    rf_df = rf_df.sort_values("date").set_index("date")

    rf_ann = rf_df["rate_pct"].astype(float) / 100.0

    rf_period_values = []
    return_index = pd.DatetimeIndex(return_dates)

    for d in return_index:
        hist = rf_ann.loc[rf_ann.index <= d]
        annual = float(hist.iloc[-1]) if not hist.empty else 0.0
        period_rate = (1.0 + annual) ** (1.0 / PERIODS_PER_YEAR) - 1.0
        rf_period_values.append(period_rate)

    return pd.Series(rf_period_values, index=return_index, name="rf_period")


def main() -> None:
    path = Path("v6_long_biased/output/monthly_returns.csv")

    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date")
    metrics_df = df.set_index("date", drop=False)

    rf_series = load_rf_period_series(df["date"])

    print("=== Raw data preview ===")
    print(df.head())
    print(df.columns.tolist())
    print(df.shape)

    print("\n=== Risk-free preview ===")
    print(rf_series.head())

    metrics = compute_headline_metrics(metrics_df, rf_series=rf_series)
    print("\n=== Headline metrics ===")
    print(metrics)

    issues = validate_returns(df)
    print("\n=== Validation issues ===")
    if issues.empty:
        print("No issues found.")
    else:
        print(issues)

    Path("reports").mkdir(exist_ok=True)
    metrics.to_csv("reports/v6_headline_metrics.csv", index=False)
    issues.to_csv("reports/v6_return_validation.csv", index=False)


if __name__ == "__main__":
    main()
