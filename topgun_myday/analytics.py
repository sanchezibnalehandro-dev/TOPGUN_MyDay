from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd

from .config import KEY_METRICS, METRIC_LABELS
from .data_loader import LoadedDataset
from .metrics import (
    MetricComparison,
    MetricProfile,
    MetricResult,
    MetricStatus,
    PeriodComparisonResult,
    calculate_metrics as calculate_metric_results,
    calculate_period_comparison,
)
from .rules import InsightResult, build_insights


class AnalysisError(ValueError):
    """Ошибка выбранного среза данных."""


@dataclass(frozen=True)
class Metrics:
    visits: int
    revenue: float
    average_check: float | None
    next_booking_rate: float | None
    product_per_visit: float | None
    average_rating: float | None


@dataclass(frozen=True)
class ComparisonItem:
    key: str
    label: str
    current: float | None
    baseline: float | None
    delta: float | None


@dataclass(frozen=True)
class ReportModel:
    barber: str
    branch: str | None
    period_days: int
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    metrics: Metrics
    previous_metrics: Metrics
    team_metrics: Metrics
    self_comparison: tuple[ComparisonItem, ...]
    team_comparison: tuple[ComparisonItem, ...]
    insights: InsightResult
    daily_revenue: tuple[tuple[date, float], ...]
    source_name: str
    source_rows: int
    sheet_name: str | None


class ChangeDirection(str, Enum):
    INCREASED = "INCREASED"
    DECREASED = "DECREASED"
    UNCHANGED = "UNCHANGED"
    UNAVAILABLE = "UNAVAILABLE"


class OrientirStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class PeriodContext:
    visits_count: MetricResult
    unique_clients_count: MetricResult
    revenue_total: MetricResult


@dataclass(frozen=True)
class NeutralFact:
    metric_id: str
    status: MetricStatus
    direction: ChangeDirection
    comparison: MetricComparison
    reason: str | None


@dataclass(frozen=True)
class OrientirResult:
    status: OrientirStatus
    reason: str


@dataclass(frozen=True)
class AnalyticsModelV02:
    profile: MetricProfile
    barber: str
    branch: str | None
    period: PeriodComparisonResult
    context: PeriodContext
    current_period_status: MetricStatus
    current_period_reason: str | None
    previous_period_status: MetricStatus
    previous_period_reason: str | None
    neutral_facts: tuple[NeutralFact, ...]
    orientir: OrientirResult
    source_name: str
    source_rows: int
    sheet_name: str | None


def calculate_metrics(rows: pd.DataFrame) -> Metrics:
    """Legacy v0.1 adapter; утверждённые KPI делегируются metrics.py."""

    visits = len(rows)
    results = calculate_metric_results(rows, MetricProfile.LEGACY_V01)

    def value(metric_id: str) -> float | None:
        result = results[metric_id]
        if result.status != MetricStatus.AVAILABLE or result.value is None:
            return None
        return float(result.value)

    revenue = value("revenue_total")
    average_check = value("average_check")
    next_booking_rate = value("prebooking_rate")
    product_sales = value("product_sales")
    product_per_visit = (
        product_sales / visits if product_sales is not None and visits > 0 else None
    )
    rating: float | None = None
    if "rating" in rows.columns and rows["rating"].notna().any():
        rating = float(rows["rating"].mean(skipna=True))
    return Metrics(
        visits=visits,
        revenue=revenue or 0.0,
        average_check=average_check,
        next_booking_rate=next_booking_rate,
        product_per_visit=product_per_visit,
        average_rating=rating,
    )


def relative_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return (current - baseline) / baseline


def _comparison(current: Metrics, baseline: Metrics) -> tuple[ComparisonItem, ...]:
    items: list[ComparisonItem] = []
    for key in KEY_METRICS:
        current_value = getattr(current, key)
        baseline_value = getattr(baseline, key)
        items.append(
            ComparisonItem(
                key=key,
                label=METRIC_LABELS[key],
                current=current_value,
                baseline=baseline_value,
                delta=relative_delta(current_value, baseline_value),
            )
        )
    return tuple(items)


def _period_rows(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    barber: str | None = None,
    branch: str | None = None,
) -> pd.DataFrame:
    mask = frame["date"].between(start, end)
    if barber is not None:
        mask &= frame["barber"].eq(barber)
    if branch is not None:
        if "branch" not in frame.columns:
            return frame.iloc[0:0]
        mask &= frame["branch"].eq(branch)
    return frame.loc[mask]


def _daily_revenue(
    rows: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[tuple[date, float], ...]:
    if rows.empty:
        totals = pd.Series(dtype=float)
    else:
        totals = rows.groupby("date", sort=False).apply(
            lambda group: calculate_metric_results(
                group, MetricProfile.LEGACY_V01
            )["revenue_total"].value,
            include_groups=False,
        )
    days = pd.date_range(start, end, freq="D")
    return tuple((day.date(), float(totals.get(day, 0.0))) for day in days)


_V02_KPI_IDS = (
    "average_check",
    "extra_service_visit_share",
    "product_sales",
    "prebooking_rate",
)

_ORIENTIR_REASON = "Ориентир не настроен. База сравнения должна быть подтверждена."


def _period_availability(
    visits: MetricResult, period_name: str
) -> tuple[MetricStatus, str | None]:
    if visits.status != MetricStatus.AVAILABLE:
        return visits.status, visits.reason
    if visits.value == 0:
        return (
            MetricStatus.INSUFFICIENT_DATA,
            f"В {period_name} периоде нет визитов выбранного мастера.",
        )
    return MetricStatus.AVAILABLE, None


def _neutral_facts(
    period: PeriodComparisonResult,
    current_status: MetricStatus,
    current_reason: str | None,
    previous_status: MetricStatus,
    previous_reason: str | None,
) -> tuple[NeutralFact, ...]:
    facts: list[NeutralFact] = []
    period_unavailable = (
        current_status != MetricStatus.AVAILABLE
        or previous_status != MetricStatus.AVAILABLE
    )
    for metric_id in _V02_KPI_IDS:
        comparison = period.comparisons[metric_id]
        if period_unavailable:
            status = (
                MetricStatus.DATA_GAP
                if MetricStatus.DATA_GAP in (current_status, previous_status)
                else MetricStatus.INSUFFICIENT_DATA
            )
            reasons = [reason for reason in (current_reason, previous_reason) if reason]
            facts.append(
                NeutralFact(
                    metric_id=metric_id,
                    status=status,
                    direction=ChangeDirection.UNAVAILABLE,
                    comparison=comparison,
                    reason="; ".join(reasons),
                )
            )
            continue
        if comparison.status != MetricStatus.AVAILABLE:
            facts.append(
                NeutralFact(
                    metric_id=metric_id,
                    status=comparison.status,
                    direction=ChangeDirection.UNAVAILABLE,
                    comparison=comparison,
                    reason=comparison.reason,
                )
            )
            continue
        delta = (
            comparison.percentage_point_delta
            if comparison.percentage_point_delta is not None
            else comparison.absolute_delta
        )
        if delta is None:
            direction = ChangeDirection.UNAVAILABLE
        elif delta > 0:
            direction = ChangeDirection.INCREASED
        elif delta < 0:
            direction = ChangeDirection.DECREASED
        else:
            direction = ChangeDirection.UNCHANGED
        facts.append(
            NeutralFact(
                metric_id=metric_id,
                status=comparison.status,
                direction=direction,
                comparison=comparison,
                reason=comparison.reason,
            )
        )
    return tuple(facts)


def build_analysis_v02(
    dataset: LoadedDataset,
    barber: str,
    days: int,
    branch: str | None = None,
) -> AnalyticsModelV02:
    """Собрать нейтральную v0.2-модель без команды, оценок и business rules."""

    if barber not in dataset.barbers:
        raise AnalysisError(f"Барбер «{barber}» не найден в загруженном файле.")
    if days not in (1, 7, 14):
        raise AnalysisError("Поддерживаются периоды 1, 7 и 14 дней.")
    if branch is not None and branch not in dataset.branches:
        raise AnalysisError(f"Филиал «{branch}» не найден в загруженном файле.")

    selected = dataset.frame.loc[dataset.frame["barber"].eq(barber)]
    if branch is not None:
        selected = selected.loc[selected["branch"].eq(branch)]

    period = calculate_period_comparison(
        selected,
        dataset.profile,
        days,
        anchor_date=dataset.date_max,
    )
    current_visits = period.current_metrics["visits_count"]
    previous_visits = period.previous_metrics["visits_count"]
    current_status, current_reason = _period_availability(
        current_visits, "текущем"
    )
    previous_status, previous_reason = _period_availability(
        previous_visits, "предыдущем"
    )
    current = period.current_metrics
    return AnalyticsModelV02(
        profile=dataset.profile,
        barber=barber,
        branch=branch,
        period=period,
        context=PeriodContext(
            visits_count=current["visits_count"],
            unique_clients_count=current["unique_clients_count"],
            revenue_total=current["revenue_total"],
        ),
        current_period_status=current_status,
        current_period_reason=current_reason,
        previous_period_status=previous_status,
        previous_period_reason=previous_reason,
        neutral_facts=_neutral_facts(
            period,
            current_status,
            current_reason,
            previous_status,
            previous_reason,
        ),
        orientir=OrientirResult(
            status=OrientirStatus.NOT_CONFIGURED,
            reason=_ORIENTIR_REASON,
        ),
        source_name=dataset.source_path.name,
        source_rows=dataset.row_count,
        sheet_name=dataset.sheet_name,
    )


def build_report(
    dataset: LoadedDataset,
    barber: str,
    days: int,
    branch: str | None = None,
) -> ReportModel:
    if barber not in dataset.barbers:
        raise AnalysisError(f"Барбер «{barber}» не найден в загруженном файле.")
    if days not in (1, 7, 14):
        raise AnalysisError("Поддерживаются периоды 1, 7 и 14 дней.")
    if branch is not None and branch not in dataset.branches:
        raise AnalysisError(f"Филиал «{branch}» не найден в загруженном файле.")

    current_end = pd.Timestamp(dataset.date_max).normalize()
    current_start = current_end - pd.DateOffset(days=days - 1)
    previous_end = current_start - pd.DateOffset(days=1)
    previous_start = previous_end - pd.DateOffset(days=days - 1)

    current_rows = _period_rows(
        dataset.frame, current_start, current_end, barber=barber, branch=branch
    )
    if current_rows.empty:
        label = f" в филиале «{branch}»" if branch else ""
        raise AnalysisError(
            f"У барбера «{barber}»{label} нет визитов за выбранный период."
        )
    previous_rows = _period_rows(
        dataset.frame, previous_start, previous_end, barber=barber, branch=branch
    )
    team_rows = _period_rows(
        dataset.frame, current_start, current_end, branch=branch
    )

    current_metrics = calculate_metrics(current_rows)
    previous_metrics = calculate_metrics(previous_rows)
    team_metrics = calculate_metrics(team_rows)
    self_comparison = _comparison(current_metrics, previous_metrics)
    team_comparison = _comparison(current_metrics, team_metrics)
    insights = build_insights({item.key: item.delta for item in self_comparison})

    return ReportModel(
        barber=barber,
        branch=branch,
        period_days=days,
        current_start=current_start.date(),
        current_end=current_end.date(),
        previous_start=previous_start.date(),
        previous_end=previous_end.date(),
        metrics=current_metrics,
        previous_metrics=previous_metrics,
        team_metrics=team_metrics,
        self_comparison=self_comparison,
        team_comparison=team_comparison,
        insights=insights,
        daily_revenue=_daily_revenue(current_rows, current_start, current_end),
        source_name=dataset.source_path.name,
        source_rows=dataset.row_count,
        sheet_name=dataset.sheet_name,
    )
