from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


WEIGHT_TOL = 1e-6
ZSCORE_TOL = 0.10
MAX_WEIGHT_CAP = 0.015  # v6.5: 1.5% per-stock cap
MAX_ABS_RETURN = 0.50
EXPECTED_MONTH_GAP = 2


def _issue(issue_type: str, message: str, **details: Any) -> dict[str, Any]:
    row = {"type": issue_type, "message": message}
    row.update(details)
    return row


def _as_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _as_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _find_first_present(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _month_diff(dates: pd.Series) -> pd.Series:
    """
    Compute month gaps between consecutive timestamps.
    Example:
    2024-01-31 -> 2024-03-31 gives 2
    """
    return (dates.dt.year * 12 + dates.dt.month).diff()


def validate_weights(
    weights_df: pd.DataFrame,
    max_weight_cap: float = MAX_WEIGHT_CAP,
    require_long_only: bool = True,
) -> pd.DataFrame:
    """
    Validate portfolio weights for v6 long-only output.

    Expected minimum columns:
    - date
    - symbol
    - weight

    Optional columns:
    - exposure / target_exposure: used to check whether per-date weights sum to
      the intended portfolio exposure instead of always summing to 1.0
    - leg: if present in v6 output, it should effectively be long-only
    """
    issues: list[dict[str, Any]] = []

    required = {"date", "symbol", "weight"}
    missing = required - set(weights_df.columns)
    if missing:
        return pd.DataFrame(
            [_issue("schema", "Missing required columns in weights_df", missing=sorted(missing))]
        )

    df = weights_df.copy()
    df["date"] = _as_datetime(df["date"])
    df["weight"] = _as_numeric(df["weight"])

    if df["date"].isna().any():
        issues.append(_issue("weights", "Invalid or missing dates found in weights_df"))

    if df["symbol"].isna().any():
        issues.append(_issue("weights", "Missing symbol values found in weights_df"))

    if df["weight"].isna().any():
        issues.append(_issue("weights", "Non-numeric or missing weight values found in weights_df"))

    dupes = df.duplicated(subset=["date", "symbol"], keep=False)
    if dupes.any():
        dup_rows = df.loc[dupes, ["date", "symbol"]].astype(str).to_dict("records")
        issues.append(
            _issue(
                "weights",
                "Duplicate date-symbol rows found in weights_df",
                duplicates=dup_rows,
            )
        )

    negative = df[df["weight"] < -WEIGHT_TOL]
    for _, row in negative.iterrows():
        issues.append(
            _issue(
                "weights",
                "Negative weight found in long-only portfolio",
                date=row["date"],
                symbol=row["symbol"],
                weight=float(row["weight"]),
            )
        )

    over_cap = df[df["weight"] > max_weight_cap + WEIGHT_TOL]
    for _, row in over_cap.iterrows():
        issues.append(
            _issue(
                "weights",
                "Weight above configured cap",
                date=row["date"],
                symbol=row["symbol"],
                weight=float(row["weight"]),
                cap=float(max_weight_cap),
            )
        )

    if require_long_only and "leg" in df.columns:
        bad_leg = df["leg"].astype(str).str.lower().isin({"short", "sell", "-1"})
        for _, row in df.loc[bad_leg].iterrows():
            issues.append(
                _issue(
                    "weights",
                    "Short leg label found in v6 long-only output",
                    date=row["date"],
                    symbol=row["symbol"],
                    leg=row["leg"],
                )
            )

    exposure_col = _find_first_present(df, ["target_exposure", "exposure"])
    per_date_weight_sum = df.groupby("date", dropna=True)["weight"].sum()

    if exposure_col is not None:
        df[exposure_col] = _as_numeric(df[exposure_col])

        exposure_nunique = df.groupby("date", dropna=True)[exposure_col].nunique(dropna=True)
        bad_exposure_nunique = exposure_nunique[exposure_nunique > 1]
        for date, nunique in bad_exposure_nunique.items():
            issues.append(
                _issue(
                    "weights",
                    "Multiple exposure targets found within the same date",
                    date=date,
                    distinct_values=int(nunique),
                    exposure_col=exposure_col,
                )
            )

        expected_by_date = df.groupby("date", dropna=True)[exposure_col].first()
        for date, weight_sum in per_date_weight_sum.items():
            expected = expected_by_date.get(date, np.nan)
            if pd.isna(expected):
                issues.append(
                    _issue(
                        "weights",
                        "Missing exposure target for date",
                        date=date,
                        exposure_col=exposure_col,
                    )
                )
                continue

            if not np.isclose(weight_sum, expected, atol=WEIGHT_TOL):
                issues.append(
                    _issue(
                        "weights",
                        "Per-date weights do not sum to target exposure",
                        date=date,
                        weight_sum=float(weight_sum),
                        target_exposure=float(expected),
                    )
                )
    else:
        for date, weight_sum in per_date_weight_sum.items():
            if not np.isclose(weight_sum, 1.0, atol=WEIGHT_TOL):
                issues.append(
                    _issue(
                        "weights",
                        "Per-date weights do not sum to 1",
                        date=date,
                        weight_sum=float(weight_sum),
                    )
                )

    return pd.DataFrame(issues)


def validate_returns(
    returns_df: pd.DataFrame,
    expected_month_gap: int = EXPECTED_MONTH_GAP,
    max_abs_return: float = MAX_ABS_RETURN,
) -> pd.DataFrame:
    """
    Validate v6 return output.

    Expected v6 columns usually include:
    - date
    - gross_return
    - net_return
    - exposure
    """
    issues: list[dict[str, Any]] = []

    if "date" not in returns_df.columns:
        return pd.DataFrame([_issue("schema", "Missing required column 'date' in returns_df")])

    df = returns_df.copy()
    df["date"] = _as_datetime(df["date"])

    if df["date"].isna().any():
        issues.append(_issue("returns", "Invalid or missing dates found in returns_df"))

    if df["date"].duplicated().any():
        dup_dates = df.loc[df["date"].duplicated(keep=False), "date"].astype(str).tolist()
        issues.append(
            _issue(
                "returns",
                "Duplicate dates found in returns_df",
                dates=dup_dates,
            )
        )

    if not df["date"].is_monotonic_increasing:
        issues.append(_issue("returns", "Dates are not sorted in ascending order"))

    # Check approximate v6 bi-monthly frequency using unique sorted dates.
    unique_dates = pd.Series(sorted(df["date"].dropna().unique()))
    if len(unique_dates) >= 2:
        month_gaps = _month_diff(unique_dates).dropna()
        bad_gap_mask = month_gaps != expected_month_gap
        if bad_gap_mask.any():
            bad_pairs = []
            bad_idx = month_gaps.index[bad_gap_mask]
            for idx in bad_idx:
                bad_pairs.append(
                    {
                        "prev_date": str(unique_dates.iloc[idx - 1].date()),
                        "date": str(unique_dates.iloc[idx].date()),
                        "month_gap": int(month_gaps.loc[idx]),
                    }
                )
            issues.append(
                _issue(
                    "returns",
                    f"Unexpected rebalance frequency found; expected {expected_month_gap}-month gaps",
                    bad_periods=bad_pairs,
                )
            )

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    exposure_col = "exposure" if "exposure" in df.columns else None
    return_cols = [col for col in numeric_cols if col != exposure_col]

    for col in return_cols:
        if df[col].isna().any():
            issues.append(_issue("returns", f"NaN values found in column {col}"))

        extreme_mask = df[col].abs() > max_abs_return
        if extreme_mask.any():
            bad_dates = df.loc[extreme_mask, "date"].astype(str).tolist()
            issues.append(
                _issue(
                    "returns",
                    f"Extreme value above {max_abs_return:.0%} found in column {col}",
                    column=col,
                    dates=bad_dates,
                )
            )

    # v6 main comparison: net should not exceed gross.
    paired_return_cols = [
        ("gross_return", "net_return"),
        ("dynamic_gross", "dynamic_net_20bp"),  # backward-compatible fallback
    ]
    for gross_col, net_col in paired_return_cols:
        if {gross_col, net_col}.issubset(df.columns):
            bad = df[net_col] > df[gross_col] + WEIGHT_TOL
            if bad.any():
                bad_dates = df.loc[bad, "date"].astype(str).tolist()
                issues.append(
                    _issue(
                        "returns",
                        "Net return exceeds gross return on some dates",
                        gross_col=gross_col,
                        net_col=net_col,
                        dates=bad_dates,
                    )
                )

    if exposure_col is not None:
        bad_exposure = ~df[exposure_col].between(0.0, 1.0, inclusive="both")
        if bad_exposure.any():
            bad_rows = (
                df.loc[bad_exposure, ["date", exposure_col]]
                .astype({exposure_col: float}, errors="ignore")
                .astype(str)
                .to_dict("records")
            )
            issues.append(
                _issue(
                    "returns",
                    "Exposure outside [0, 1] range",
                    rows=bad_rows,
                )
            )

    return pd.DataFrame(issues)


def validate_factors(scores_df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate factor-score output.

    This function is kept generic so it still works whether v6 exports only
    momentum/value/composite z-scores or a wider diagnostic table.
    """
    issues: list[dict[str, Any]] = []

    required = {"date", "symbol"}
    missing = required - set(scores_df.columns)
    if missing:
        return pd.DataFrame(
            [_issue("schema", "Missing required columns in scores_df", missing=sorted(missing))]
        )

    df = scores_df.copy()
    df["date"] = _as_datetime(df["date"])

    if df["date"].isna().any():
        issues.append(_issue("factors", "Invalid or missing dates found in scores_df"))

    dupes = df.duplicated(subset=["date", "symbol"], keep=False)
    if dupes.any():
        dup_rows = df.loc[dupes, ["date", "symbol"]].astype(str).to_dict("records")
        issues.append(
            _issue(
                "factors",
                "Duplicate date-symbol rows found in scores_df",
                duplicates=dup_rows,
            )
        )

    factor_cols = [col for col in df.columns if col.endswith("_z")]

    for col in factor_cols:
        numeric_col = _as_numeric(df[col])
        if numeric_col.isna().any() and not df[col].isna().equals(numeric_col.isna()):
            issues.append(
                _issue(
                    "factors",
                    "Non-numeric values found in factor z-score column",
                    factor=col,
                )
            )

        inf_mask = np.isinf(numeric_col)
        if inf_mask.any():
            bad_rows = df.loc[inf_mask, ["date", "symbol"]].astype(str).to_dict("records")
            issues.append(
                _issue(
                    "factors",
                    "Infinite values found in factor z-score column",
                    factor=col,
                    rows=bad_rows,
                )
            )

    # Sector-level z-score sanity check, only if sector data exists.
    if "gics_sector" in df.columns and factor_cols:
        grouped = df.groupby(["date", "gics_sector"])
        for (date, sector), group in grouped:
            if len(group) < 5:
                continue

            for col in factor_cols:
                values = _as_numeric(group[col]).dropna()
                if values.empty:
                    continue

                mean_val = values.mean()
                std_val = values.std(ddof=1) if len(values) >= 2 else np.nan

                if pd.notna(mean_val) and abs(mean_val) > ZSCORE_TOL:
                    issues.append(
                        _issue(
                            "factors",
                            "Sector z-score mean too far from 0",
                            date=date,
                            sector=sector,
                            factor=col,
                            mean=float(mean_val),
                        )
                    )

                if pd.notna(std_val) and abs(std_val - 1.0) > ZSCORE_TOL:
                    issues.append(
                        _issue(
                            "factors",
                            "Sector z-score std too far from 1",
                            date=date,
                            sector=sector,
                            factor=col,
                            std=float(std_val),
                        )
                    )

    composite_col = _find_first_present(
        df,
        ["composite_z", "composite_score_z", "combined_score_z"],
    )
    if composite_col is not None:
        df = df.sort_values(["symbol", "date"]).copy()
        composite = _as_numeric(df[composite_col])

        zero_mask = composite.eq(0.0)
        df["zero_run"] = zero_mask.groupby(df["symbol"]).transform(
            lambda s: s.groupby((s != s.shift()).cumsum()).cumcount() + 1
        )

        bad = df.loc[zero_mask & (df["zero_run"] > 3), ["date", "symbol", composite_col]]
        for _, row in bad.iterrows():
            issues.append(
                _issue(
                    "factors",
                    "Composite score equals 0 for more than 3 consecutive periods",
                    date=row["date"],
                    symbol=row["symbol"],
                    composite_col=composite_col,
                    composite_value=float(row[composite_col]),
                )
            )

    return pd.DataFrame(issues)


def validate_regime(regime_df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate v6 regime / risk-overlay diagnostics.

    Supports:
    - vix_percentile in [0, 1]
    - optional regime labels: low / normal / high
    - optional boolean-like trigger signals
    - optional trigger_count consistency
    - optional exposure / target_exposure checks
    - backward-compatible support for old weight columns if present
    """
    issues: list[dict[str, Any]] = []

    df = regime_df.copy()

    if "date" in df.columns:
        df["date"] = _as_datetime(df["date"])
        if df["date"].isna().any():
            issues.append(_issue("regime", "Invalid or missing dates found in regime_df"))

    if "vix_percentile" in df.columns:
        df["vix_percentile"] = _as_numeric(df["vix_percentile"])
        bad = ~df["vix_percentile"].between(0, 1, inclusive="both")
        for _, row in df.loc[bad].iterrows():
            issues.append(
                _issue(
                    "regime",
                    "VIX percentile out of range",
                    date=row.get("date"),
                    vix_percentile=row.get("vix_percentile"),
                )
            )

    if "regime" in df.columns:
        allowed = {"low", "normal", "high"}
        bad = ~df["regime"].astype(str).str.lower().isin(allowed)
        for _, row in df.loc[bad].iterrows():
            issues.append(
                _issue(
                    "regime",
                    "Invalid regime label",
                    date=row.get("date"),
                    regime=row.get("regime"),
                )
            )

    signal_candidates = [
        "signal_200dma",
        "signal_12m",
        "signal_vix",
        "ma_signal",
        "momentum_signal",
        "vix_signal",
        "below_200dma",
        "negative_12m",
        "high_vix",
    ]
    signal_cols = [col for col in signal_candidates if col in df.columns]

    for col in signal_cols:
        values = df[col].dropna()
        valid = values.isin([0, 1, True, False])
        if not valid.all():
            bad_rows = df.loc[~df[col].isin([0, 1, True, False]), ["date", col]].astype(str).to_dict("records")
            issues.append(
                _issue(
                    "regime",
                    "Non-boolean signal value found",
                    signal_col=col,
                    rows=bad_rows,
                )
            )

    if signal_cols and "trigger_count" in df.columns:
        df["trigger_count"] = _as_numeric(df["trigger_count"])
        signal_sum = df[signal_cols].replace({True: 1, False: 0}).fillna(0).sum(axis=1)
        bad = ~np.isclose(signal_sum, df["trigger_count"], atol=WEIGHT_TOL)
        for idx in df.index[bad]:
            issues.append(
                _issue(
                    "regime",
                    "trigger_count does not match sum of overlay signals",
                    date=df.loc[idx].get("date"),
                    trigger_count=float(df.loc[idx, "trigger_count"]),
                    signal_sum=float(signal_sum.loc[idx]),
                )
            )

    exposure_col = _find_first_present(df, ["target_exposure", "exposure"])
    if exposure_col is not None:
        df[exposure_col] = _as_numeric(df[exposure_col])
        bad = ~df[exposure_col].between(0, 1, inclusive="both")
        for _, row in df.loc[bad].iterrows():
            issues.append(
                _issue(
                    "regime",
                    "Exposure target out of range",
                    date=row.get("date"),
                    exposure_col=exposure_col,
                    exposure=row.get(exposure_col),
                )
            )

    if {"risk_off", "exposure"}.issubset(df.columns):
        risk_off_numeric = _as_numeric(df["risk_off"])
        exposure_numeric = _as_numeric(df["exposure"])
        bad = risk_off_numeric.eq(1) & np.isclose(exposure_numeric, 1.0, atol=WEIGHT_TOL)
        for _, row in df.loc[bad].iterrows():
            issues.append(
                _issue(
                    "regime",
                    "risk_off indicates reduced exposure, but exposure is still 1.0",
                    date=row.get("date"),
                    exposure=row.get("exposure"),
                )
            )

    # Backward-compatible fallback if old dynamic factor-weight tables still appear.
    weight_cols = [col for col in df.columns if col.startswith("w_")]
    if weight_cols:
        sums = df[weight_cols].sum(axis=1)
        bad_sum = ~np.isclose(sums, 1.0, atol=WEIGHT_TOL)
        for idx in df.index[bad_sum]:
            issues.append(
                _issue(
                    "regime",
                    "Dynamic factor weights do not sum to 1",
                    date=df.loc[idx].get("date"),
                    weight_sum=float(sums.loc[idx]),
                )
            )

        bad_neg = (df[weight_cols] < 0).any(axis=1)
        for idx in df.index[bad_neg]:
            issues.append(
                _issue(
                    "regime",
                    "Negative dynamic factor weight found",
                    date=df.loc[idx].get("date"),
                )
            )

    return pd.DataFrame(issues)