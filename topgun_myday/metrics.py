from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Mapping

import pandas as pd


class MetricsInputError(ValueError):
    """Структурная ошибка входа, не позволяющая выбрать расчётный период."""


class MetricStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DATA_GAP = "DATA_GAP"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class MetricUnit(str, Enum):
    COUNT = "count"
    RUB = "rub"
    SHARE = "share"


class MetricProfile(str, Enum):
    """Явный демонстрационный контракт расчёта, а не методика «Заботы»."""

    SYNTHETIC_V02 = "synthetic_v02"
    LEGACY_V01 = "legacy_v01"


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    status: MetricStatus
    value: int | float | None
    unit: MetricUnit
    numerator: int | float | None
    denominator: int | float | None
    reason: str | None


@dataclass(frozen=True)
class PeriodWindow:
    days: int
    anchor_date: date
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date


@dataclass(frozen=True)
class MetricComparison:
    metric_id: str
    status: MetricStatus
    unit: MetricUnit
    current: MetricResult
    previous: MetricResult
    absolute_delta: int | float | None
    relative_delta_percent: float | None
    percentage_point_delta: float | None
    reason: str | None


@dataclass(frozen=True)
class PeriodComparisonResult:
    profile: MetricProfile
    window: PeriodWindow
    current_metrics: Mapping[str, MetricResult]
    previous_metrics: Mapping[str, MetricResult]
    comparisons: Mapping[str, MetricComparison]


METRIC_IDS = (
    "visits_count",
    "unique_clients_count",
    "revenue_total",
    "average_check",
    "extra_service_visit_share",
    "product_sales",
    "prebooking_rate",
)

_ABSOLUTE_METRICS = {
    "visits_count",
    "unique_clients_count",
    "revenue_total",
    "average_check",
    "product_sales",
}
_SHARE_METRICS = {"extra_service_visit_share", "prebooking_rate"}


def _normalise_profile(profile: MetricProfile | str) -> MetricProfile:
    try:
        return MetricProfile(profile)
    except ValueError as exc:
        raise MetricsInputError(f"Неизвестный профиль метрик: {profile!r}.") from exc


def _data_gap(metric_id: str, unit: MetricUnit, reason: str) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        status=MetricStatus.DATA_GAP,
        value=None,
        unit=unit,
        numerator=None,
        denominator=None,
        reason=reason,
    )


def _insufficient(
    metric_id: str,
    unit: MetricUnit,
    *,
    numerator: int | float | None,
    denominator: int | float | None,
    reason: str,
) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        status=MetricStatus.INSUFFICIENT_DATA,
        value=None,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        reason=reason,
    )


def _available(
    metric_id: str,
    unit: MetricUnit,
    value: int | float,
    *,
    numerator: int | float,
    denominator: int | float | None,
) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        reason=None,
    )


def _invalid_row_numbers(mask: pd.Series, limit: int = 5) -> str:
    positions = [str(position + 2) for position, invalid in enumerate(mask.tolist()) if invalid]
    return ", ".join(positions[:limit])


def _numeric_column(
    rows: pd.DataFrame, field: str
) -> tuple[pd.Series | None, str | None]:
    if field not in rows.columns:
        return None, f"Отсутствует обязательное числовое поле `{field}`."

    original = rows[field]
    parsed = pd.to_numeric(original, errors="coerce")
    finite = parsed.map(
        lambda value: bool(pd.notna(value) and math.isfinite(float(value)))
    )
    invalid = parsed.isna() | ~finite
    if invalid.any():
        return (
            None,
            f"Поле `{field}` содержит пустое, NaN или нечисловое значение "
            f"в строках периода: {_invalid_row_numbers(invalid)}.",
        )
    return parsed.astype(float), None


def _yes_no_column(
    rows: pd.DataFrame, field: str
) -> tuple[pd.Series | None, str | None]:
    if field not in rows.columns:
        return None, f"Отсутствует обязательное поле `{field}` со значениями «Да/Нет»."

    original = rows[field]
    normalised = original.astype("string").str.strip().str.casefold()
    mapped = normalised.map({"да": "Да", "нет": "Нет"})
    invalid = original.isna() | mapped.isna()
    if invalid.any():
        return (
            None,
            f"Поле `{field}` содержит пустое или неизвестное значение вместо "
            f"«Да/Нет» в строках периода: {_invalid_row_numbers(invalid)}.",
        )
    return mapped, None


def _visit_ids(rows: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
    field = "visit_id"
    if field not in rows.columns:
        return None, "Отсутствует обязательное поле `visit_id`."
    original = rows[field]
    invalid = original.isna() | original.astype("string").str.strip().eq("")
    if invalid.any():
        return (
            None,
            "Поле `visit_id` содержит пустое значение в строках периода: "
            f"{_invalid_row_numbers(invalid)}.",
        )
    return original.astype(str).str.strip(), None


def _client_ids(rows: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
    """Проверить demo client_id без переноса контракта на будущие профили."""

    field = "client_id"
    if field not in rows.columns:
        return None, "Отсутствует обязательное демонстрационное поле `client_id`."
    original = rows[field]
    invalid = original.isna() | original.astype("string").str.strip().eq("")
    if invalid.any():
        return (
            None,
            "Поле `client_id` содержит пустое значение в строках периода: "
            f"{_invalid_row_numbers(invalid)}.",
        )
    return original.astype(str).str.strip(), None


def _unique_clients(rows: pd.DataFrame) -> MetricResult:
    client_ids, reason = _client_ids(rows)
    if client_ids is None:
        return _data_gap(
            "unique_clients_count",
            MetricUnit.COUNT,
            reason or "Нет подтверждённого демонстрационного client_id.",
        )
    count = int(client_ids.nunique())
    return _available(
        "unique_clients_count",
        MetricUnit.COUNT,
        count,
        numerator=count,
        denominator=None,
    )


def _dependency_gap(
    metric_id: str,
    unit: MetricUnit,
    dependencies: tuple[MetricResult, ...],
) -> MetricResult | None:
    failed = [item for item in dependencies if item.status == MetricStatus.DATA_GAP]
    if not failed:
        return None
    details = "; ".join(item.reason or item.metric_id for item in failed)
    return _data_gap(metric_id, unit, f"Метрика недоступна: {details}")


def _calculate_synthetic(rows: pd.DataFrame) -> dict[str, MetricResult]:
    visit_ids, visit_reason = _visit_ids(rows)
    if visit_ids is None:
        visits = _data_gap("visits_count", MetricUnit.COUNT, visit_reason or "Нет visit_id.")
    else:
        visit_count = int(visit_ids.nunique())
        visits = _available(
            "visits_count",
            MetricUnit.COUNT,
            visit_count,
            numerator=visit_count,
            denominator=None,
        )

    unique_clients = _unique_clients(rows)

    services, service_reason = _numeric_column(rows, "service_revenue")
    products, product_reason = _numeric_column(rows, "product_revenue")

    if products is None:
        product_sales = _data_gap(
            "product_sales", MetricUnit.RUB, product_reason or "Нет product_revenue."
        )
    else:
        product_total = float(products.sum())
        product_sales = _available(
            "product_sales",
            MetricUnit.RUB,
            product_total,
            numerator=product_total,
            denominator=None,
        )

    revenue_reasons = [reason for reason in (service_reason, product_reason) if reason]
    if services is None or products is None:
        revenue = _data_gap(
            "revenue_total",
            MetricUnit.RUB,
            "Метрика недоступна: " + "; ".join(revenue_reasons),
        )
    else:
        revenue_total = float((services + products).sum())
        revenue = _available(
            "revenue_total",
            MetricUnit.RUB,
            revenue_total,
            numerator=revenue_total,
            denominator=None,
        )

    average_gap = _dependency_gap(
        "average_check", MetricUnit.RUB, (revenue, visits)
    )
    if average_gap is not None:
        average_check = average_gap
    elif visits.value == 0:
        average_check = _insufficient(
            "average_check",
            MetricUnit.RUB,
            numerator=revenue.value,
            denominator=0,
            reason="Средний чек нельзя рассчитать: в периоде нет визитов.",
        )
    else:
        average_check = _available(
            "average_check",
            MetricUnit.RUB,
            float(revenue.value) / int(visits.value),
            numerator=float(revenue.value),
            denominator=int(visits.value),
        )

    extra_values, extra_reason = _yes_no_column(rows, "has_extra_service")
    if visit_ids is None or extra_values is None:
        reasons = [reason for reason in (visit_reason, extra_reason) if reason]
        extra_share = _data_gap(
            "extra_service_visit_share",
            MetricUnit.SHARE,
            "Метрика недоступна: " + "; ".join(reasons),
        )
    elif visits.value == 0:
        extra_share = _insufficient(
            "extra_service_visit_share",
            MetricUnit.SHARE,
            numerator=0,
            denominator=0,
            reason="Долю допуслуг нельзя рассчитать: в периоде нет визитов.",
        )
    else:
        numerator = int(visit_ids.loc[extra_values.eq("Да")].nunique())
        denominator = int(visits.value)
        extra_share = _available(
            "extra_service_visit_share",
            MetricUnit.SHARE,
            numerator / denominator,
            numerator=numerator,
            denominator=denominator,
        )

    prebooked_values, prebooked_reason = _yes_no_column(rows, "prebooked")
    if visit_ids is None or prebooked_values is None:
        reasons = [reason for reason in (visit_reason, prebooked_reason) if reason]
        prebooking = _data_gap(
            "prebooking_rate",
            MetricUnit.SHARE,
            "Метрика недоступна: " + "; ".join(reasons),
        )
    elif visits.value == 0:
        prebooking = _insufficient(
            "prebooking_rate",
            MetricUnit.SHARE,
            numerator=0,
            denominator=0,
            reason="Предварительную запись нельзя рассчитать: в периоде нет визитов.",
        )
    else:
        numerator = int(visit_ids.loc[prebooked_values.eq("Да")].nunique())
        denominator = int(visits.value)
        prebooking = _available(
            "prebooking_rate",
            MetricUnit.SHARE,
            numerator / denominator,
            numerator=numerator,
            denominator=denominator,
        )

    return {
        "visits_count": visits,
        "unique_clients_count": unique_clients,
        "revenue_total": revenue,
        "average_check": average_check,
        "extra_service_visit_share": extra_share,
        "product_sales": product_sales,
        "prebooking_rate": prebooking,
    }


def _calculate_legacy(rows: pd.DataFrame) -> dict[str, MetricResult]:
    visit_count = len(rows)
    visits = _available(
        "visits_count",
        MetricUnit.COUNT,
        visit_count,
        numerator=visit_count,
        denominator=None,
    )
    unique_clients = _unique_clients(rows)

    services, service_reason = _numeric_column(rows, "service_revenue")
    products, product_reason = _numeric_column(rows, "product_revenue")
    discount: pd.Series | None
    discount_reason: str | None
    if "discount" in rows.columns:
        discount, discount_reason = _numeric_column(rows, "discount")
    else:
        discount = pd.Series(0.0, index=rows.index, dtype=float)
        discount_reason = None

    if products is None:
        product_sales = _data_gap(
            "product_sales", MetricUnit.RUB, product_reason or "Нет product_revenue."
        )
    else:
        product_total = float(products.sum())
        product_sales = _available(
            "product_sales",
            MetricUnit.RUB,
            product_total,
            numerator=product_total,
            denominator=None,
        )

    revenue_reasons = [
        reason
        for reason in (service_reason, product_reason, discount_reason)
        if reason
    ]
    if services is None or products is None or discount is None:
        revenue = _data_gap(
            "revenue_total",
            MetricUnit.RUB,
            "Метрика недоступна: " + "; ".join(revenue_reasons),
        )
    else:
        revenue_total = float((services + products - discount).sum())
        revenue = _available(
            "revenue_total",
            MetricUnit.RUB,
            revenue_total,
            numerator=revenue_total,
            denominator=None,
        )

    average_gap = _dependency_gap(
        "average_check", MetricUnit.RUB, (revenue, visits)
    )
    if average_gap is not None:
        average_check = average_gap
    elif visit_count == 0:
        average_check = _insufficient(
            "average_check",
            MetricUnit.RUB,
            numerator=revenue.value,
            denominator=0,
            reason="Средний чек нельзя рассчитать: в периоде нет визитов.",
        )
    else:
        average_check = _available(
            "average_check",
            MetricUnit.RUB,
            float(revenue.value) / visit_count,
            numerator=float(revenue.value),
            denominator=visit_count,
        )

    extra_share = _data_gap(
        "extra_service_visit_share",
        MetricUnit.SHARE,
        "В формате legacy v0.1 нет подтверждённого признака дополнительной услуги.",
    )

    booking_values, booking_reason = _yes_no_column(rows, "next_booking")
    if booking_values is None:
        prebooking = _data_gap(
            "prebooking_rate",
            MetricUnit.SHARE,
            booking_reason or "Нет next_booking.",
        )
    elif visit_count == 0:
        prebooking = _insufficient(
            "prebooking_rate",
            MetricUnit.SHARE,
            numerator=0,
            denominator=0,
            reason="Предварительную запись нельзя рассчитать: в периоде нет визитов.",
        )
    else:
        numerator = int(booking_values.eq("Да").sum())
        prebooking = _available(
            "prebooking_rate",
            MetricUnit.SHARE,
            numerator / visit_count,
            numerator=numerator,
            denominator=visit_count,
        )

    return {
        "visits_count": visits,
        "unique_clients_count": unique_clients,
        "revenue_total": revenue,
        "average_check": average_check,
        "extra_service_visit_share": extra_share,
        "product_sales": product_sales,
        "prebooking_rate": prebooking,
    }


def calculate_metrics(
    rows: pd.DataFrame, profile: MetricProfile | str
) -> dict[str, MetricResult]:
    """Рассчитать demo-метрики без округления и скрытого пропуска NaN."""

    if not isinstance(rows, pd.DataFrame):
        raise MetricsInputError("Для расчёта метрик требуется pandas.DataFrame.")
    resolved_profile = _normalise_profile(profile)
    if resolved_profile == MetricProfile.SYNTHETIC_V02:
        return _calculate_synthetic(rows)
    return _calculate_legacy(rows)


def build_period_window(
    anchor_date: date | pd.Timestamp, days: int
) -> PeriodWindow:
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise MetricsInputError("Длина периода должна быть положительным целым числом дней.")
    try:
        anchor = pd.Timestamp(anchor_date).normalize()
    except (TypeError, ValueError) as exc:
        raise MetricsInputError("Anchor date должна быть корректной датой.") from exc
    if pd.isna(anchor):
        raise MetricsInputError("Anchor date должна быть корректной датой.")

    current_end = anchor.date()
    current_start = current_end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return PeriodWindow(
        days=days,
        anchor_date=current_end,
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
    )


def _validated_dates(rows: pd.DataFrame) -> pd.Series:
    if "date" not in rows.columns:
        raise MetricsInputError("Для выбора периода отсутствует поле `date`.")
    parsed = pd.to_datetime(rows["date"], errors="coerce").dt.normalize()
    invalid = parsed.isna()
    if invalid.any():
        raise MetricsInputError(
            "Поле `date` содержит пустую или некорректную дату в строках: "
            f"{_invalid_row_numbers(invalid)}."
        )
    return parsed


def _unavailable_comparison(
    metric_id: str, current: MetricResult, previous: MetricResult
) -> MetricComparison:
    statuses = {current.status, previous.status}
    if MetricStatus.DATA_GAP in statuses:
        status = MetricStatus.DATA_GAP
    else:
        status = MetricStatus.INSUFFICIENT_DATA
    reasons = []
    if current.status != MetricStatus.AVAILABLE:
        reasons.append(f"текущий период: {current.reason}")
    if previous.status != MetricStatus.AVAILABLE:
        reasons.append(f"предыдущий период: {previous.reason}")
    return MetricComparison(
        metric_id=metric_id,
        status=status,
        unit=current.unit,
        current=current,
        previous=previous,
        absolute_delta=None,
        relative_delta_percent=None,
        percentage_point_delta=None,
        reason="; ".join(reasons),
    )


def _compare_metric(
    metric_id: str, current: MetricResult, previous: MetricResult
) -> MetricComparison:
    if (
        current.status != MetricStatus.AVAILABLE
        or previous.status != MetricStatus.AVAILABLE
    ):
        return _unavailable_comparison(metric_id, current, previous)
    if current.value is None or previous.value is None:
        return _unavailable_comparison(
            metric_id,
            _insufficient(
                metric_id,
                current.unit,
                numerator=current.numerator,
                denominator=current.denominator,
                reason="У текущей метрики отсутствует значение.",
            ),
            previous,
        )

    if metric_id in _SHARE_METRICS:
        return MetricComparison(
            metric_id=metric_id,
            status=MetricStatus.AVAILABLE,
            unit=current.unit,
            current=current,
            previous=previous,
            absolute_delta=None,
            relative_delta_percent=None,
            percentage_point_delta=(float(current.value) - float(previous.value)) * 100,
            reason=None,
        )

    absolute_delta = current.value - previous.value
    if previous.value == 0:
        relative_delta = None
        reason = (
            "Предыдущее значение равно нулю; относительная дельта не рассчитывается."
        )
    else:
        relative_delta = absolute_delta / previous.value * 100
        reason = None
    return MetricComparison(
        metric_id=metric_id,
        status=MetricStatus.AVAILABLE,
        unit=current.unit,
        current=current,
        previous=previous,
        absolute_delta=absolute_delta,
        relative_delta_percent=relative_delta,
        percentage_point_delta=None,
        reason=reason,
    )


def calculate_period_comparison(
    rows: pd.DataFrame,
    profile: MetricProfile | str,
    days: int,
    *,
    anchor_date: date | pd.Timestamp | None = None,
) -> PeriodComparisonResult:
    """Рассчитать current/previous для уже выбранных мастера и филиала."""

    if not isinstance(rows, pd.DataFrame):
        raise MetricsInputError("Для расчёта периода требуется pandas.DataFrame.")
    resolved_profile = _normalise_profile(profile)
    dates = _validated_dates(rows)
    if anchor_date is None:
        if rows.empty:
            raise MetricsInputError(
                "Нельзя определить anchor date по пустому набору; передайте её явно."
            )
        resolved_anchor = dates.max()
    else:
        resolved_anchor = anchor_date
    window = build_period_window(resolved_anchor, days)

    current_mask = dates.between(
        pd.Timestamp(window.current_start), pd.Timestamp(window.current_end)
    )
    previous_mask = dates.between(
        pd.Timestamp(window.previous_start), pd.Timestamp(window.previous_end)
    )
    current_rows = rows.loc[current_mask]
    previous_rows = rows.loc[previous_mask]

    current_metrics = calculate_metrics(current_rows, resolved_profile)
    previous_metrics = calculate_metrics(previous_rows, resolved_profile)
    comparisons = {
        metric_id: _compare_metric(
            metric_id,
            current_metrics[metric_id],
            previous_metrics[metric_id],
        )
        for metric_id in METRIC_IDS
    }
    return PeriodComparisonResult(
        profile=resolved_profile,
        window=window,
        current_metrics=current_metrics,
        previous_metrics=previous_metrics,
        comparisons=comparisons,
    )
