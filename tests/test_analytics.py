from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from topgun_myday.analytics import (
    ChangeDirection,
    OrientirStatus,
    build_analysis_v02,
    build_report,
    calculate_metrics,
    relative_delta,
)
from topgun_myday.data_loader import load_file
from topgun_myday.metrics import MetricProfile, MetricStatus, calculate_period_comparison


ANALYTICS_GOLDEN = {
    "Демо-мастер А": {
        1: ((2, 2, 7450, 3725, 1 / 2, 0, 1), (2, 2, 7550, 3775, 0, 600, 1 / 2)),
        7: (
            (14, 12, 50350, 50350 / 14, 8 / 14, 2700, 10 / 14),
            (14, 12, 50450, 50450 / 14, 5 / 14, 3300, 9 / 14),
        ),
        14: (
            (28, 12, 100800, 3600, 13 / 28, 6000, 19 / 28),
            (28, 12, 98950, 98950 / 28, 11 / 28, 6150, 19 / 28),
        ),
    },
    "Демо-мастер Б": {
        1: ((2, 2, 7850, 3925, 1 / 2, 950, 1 / 2), (2, 2, 8100, 4050, 1, 0, 1 / 2)),
        7: (
            (14, 12, 56350, 4025, 1 / 2, 4450, 9 / 14),
            (14, 12, 56600, 56600 / 14, 9 / 14, 3500, 9 / 14),
        ),
        14: (
            (28, 12, 112950, 112950 / 28, 16 / 28, 7950, 18 / 28),
            (28, 12, 113150, 113150 / 28, 15 / 28, 8750, 19 / 28),
        ),
    },
}


def synthetic_dataset(frame: pd.DataFrame):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "synthetic.csv"
        frame.to_csv(path, index=False)
        return load_file(path)


def synthetic_rows(**overrides: list[object]) -> pd.DataFrame:
    values: dict[str, list[object]] = {
        "date": ["08.08.2026", "09.08.2026"],
        "barber": ["Демо", "Демо"],
        "visit_id": ["V1", "V2"],
        "client_id": ["C1", "C2"],
        "branch": ["SYNTHETIC", "SYNTHETIC"],
        "service_revenue": [1000, 2000],
        "product_revenue": [0, 500],
        "has_extra_service": ["Нет", "Да"],
        "prebooked": ["Да", "Да"],
    }
    values.update(overrides)
    return pd.DataFrame(values)


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_file(Path("data") / "demo_topgun.xlsx")

    def test_demo_artem_seven_days_control_values(self) -> None:
        report = build_report(
            self.dataset, "Артём", 7, branch="Демо · Центр"
        )
        self.assertEqual(report.current_start.isoformat(), "2026-08-03")
        self.assertEqual(report.current_end.isoformat(), "2026-08-09")
        self.assertEqual(report.metrics.visits, 31)
        self.assertAlmostEqual(report.metrics.revenue, 131100.0)
        self.assertAlmostEqual(report.metrics.average_check or 0, 4229.032258064516)
        self.assertAlmostEqual(report.metrics.next_booking_rate or 0, 15 / 31)
        self.assertAlmostEqual(report.metrics.product_per_visit or 0, 13250 / 31)
        self.assertAlmostEqual(report.metrics.average_rating or 0, 4.733333333333333)

    def test_previous_period_and_team_are_aggregated_from_visits(self) -> None:
        report = build_report(
            self.dataset, "Артём", 7, branch="Демо · Центр"
        )
        self.assertEqual(report.previous_metrics.visits, 36)
        self.assertAlmostEqual(report.previous_metrics.revenue, 148300.0)
        self.assertEqual(report.team_metrics.visits, 95)
        self.assertAlmostEqual(report.team_metrics.revenue, 377100.0)
        self.assertAlmostEqual(report.team_metrics.average_check or 0, 3969.4736842105262)

    def test_daily_series_contains_every_calendar_day(self) -> None:
        report = build_report(self.dataset, "Максим", 14)
        self.assertEqual(len(report.daily_revenue), 14)
        self.assertEqual(report.daily_revenue[0][0].isoformat(), "2026-07-27")
        self.assertEqual(report.daily_revenue[-1][0].isoformat(), "2026-08-09")

    def test_rating_ignores_missing_values(self) -> None:
        rows = pd.DataFrame(
            {
                "service_revenue": [100, 100, 100],
                "product_revenue": [0, 0, 0],
                "next_booking": ["Да", "Нет", "Да"],
                "rating": [5.0, None, 4.0],
            }
        )
        metrics = calculate_metrics(rows)
        self.assertEqual(metrics.average_rating, 4.5)

    def test_zero_baseline_does_not_create_delta(self) -> None:
        self.assertIsNone(relative_delta(10.0, 0.0))
        self.assertIsNone(relative_delta(10.0, None))

    def test_no_previous_visits_returns_insufficient_insight(self) -> None:
        frame = pd.DataFrame(
            {
                "date": ["2026-08-09"],
                "barber": ["Новый барбер"],
                "client_id": ["X1"],
                "service": ["Стрижка"],
                "service_revenue": [3000],
                "product_revenue": [0],
                "next_booking": ["Да"],
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one.csv"
            frame.to_csv(path, index=False)
            report = build_report(load_file(path), "Новый барбер", 1)
        self.assertTrue(all(item.delta is None for item in report.self_comparison))
        self.assertIn("недостаточно данных", report.insights.strength_title.casefold())


class AnalyticsV02Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synthetic = load_file(Path("data") / "demo_topgun_v02.xlsx")
        cls.legacy = load_file(Path("data") / "demo_topgun.xlsx")

    def test_model_context_profile_orientir_and_no_legacy_team_fields(self) -> None:
        model = build_analysis_v02(self.synthetic, "Демо-мастер А", 7)
        self.assertEqual(model.profile, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(model.period.window.current_start.isoformat(), "2026-08-03")
        self.assertEqual(model.period.window.current_end.isoformat(), "2026-08-09")
        self.assertEqual(model.context.visits_count.value, 14)
        self.assertEqual(model.context.unique_clients_count.value, 12)
        self.assertEqual(model.context.revenue_total.value, 50350)
        self.assertEqual(model.current_period_status, MetricStatus.AVAILABLE)
        self.assertEqual(model.previous_period_status, MetricStatus.AVAILABLE)
        self.assertEqual(model.orientir.status, OrientirStatus.NOT_CONFIGURED)
        self.assertEqual(
            model.orientir.reason,
            "Ориентир не настроен. База сравнения должна быть подтверждена.",
        )
        for legacy_field in ("team_metrics", "team_comparison", "insights"):
            self.assertFalse(hasattr(model, legacy_field))

    def test_golden_current_previous_for_both_barbers_and_all_periods(self) -> None:
        metric_ids = (
            "visits_count",
            "unique_clients_count",
            "revenue_total",
            "average_check",
            "extra_service_visit_share",
            "product_sales",
            "prebooking_rate",
        )
        for barber, periods in ANALYTICS_GOLDEN.items():
            for days, (current, previous) in periods.items():
                with self.subTest(barber=barber, days=days):
                    model = build_analysis_v02(self.synthetic, barber, days)
                    for metric_id, current_value, previous_value in zip(
                        metric_ids, current, previous
                    ):
                        self.assertAlmostEqual(
                            model.period.current_metrics[metric_id].value,
                            current_value,
                        )
                        self.assertAlmostEqual(
                            model.period.previous_metrics[metric_id].value,
                            previous_value,
                        )

    def test_neutral_fact_directions_have_no_thresholds(self) -> None:
        model_a = build_analysis_v02(self.synthetic, "Демо-мастер А", 7)
        directions_a = {
            fact.metric_id: fact.direction for fact in model_a.neutral_facts
        }
        self.assertEqual(
            directions_a,
            {
                "average_check": ChangeDirection.DECREASED,
                "extra_service_visit_share": ChangeDirection.INCREASED,
                "product_sales": ChangeDirection.DECREASED,
                "prebooking_rate": ChangeDirection.INCREASED,
            },
        )
        model_b = build_analysis_v02(self.synthetic, "Демо-мастер Б", 7)
        directions_b = {
            fact.metric_id: fact.direction for fact in model_b.neutral_facts
        }
        self.assertEqual(directions_b["prebooking_rate"], ChangeDirection.UNCHANGED)
        share = model_b.period.comparisons["extra_service_visit_share"]
        self.assertIsNotNone(share.percentage_point_delta)
        self.assertIsNone(share.absolute_delta)
        self.assertIsNone(share.relative_delta_percent)

    def test_global_anchor_is_not_shifted_for_barber_without_recent_visits(self) -> None:
        dataset = synthetic_dataset(
            synthetic_rows(
                date=["01.08.2026", "09.08.2026"],
                barber=["Старый мастер", "Другой мастер"],
            )
        )
        model = build_analysis_v02(dataset, "Старый мастер", 1)
        self.assertEqual(model.period.window.anchor_date.isoformat(), "2026-08-09")
        self.assertEqual(model.period.current_metrics["visits_count"].value, 0)
        self.assertEqual(model.current_period_status, MetricStatus.INSUFFICIENT_DATA)
        self.assertEqual(model.previous_period_status, MetricStatus.INSUFFICIENT_DATA)
        self.assertTrue(
            all(
                fact.direction == ChangeDirection.UNAVAILABLE
                for fact in model.neutral_facts
            )
        )

    def test_missing_previous_period_returns_insufficient_dynamics(self) -> None:
        dataset = synthetic_dataset(
            synthetic_rows().iloc[[1]].reset_index(drop=True)
        )
        model = build_analysis_v02(dataset, "Демо", 1)
        self.assertEqual(model.current_period_status, MetricStatus.AVAILABLE)
        self.assertEqual(model.previous_period_status, MetricStatus.INSUFFICIENT_DATA)
        self.assertTrue(
            all(fact.status == MetricStatus.INSUFFICIENT_DATA for fact in model.neutral_facts)
        )

    def test_zero_previous_value_keeps_absolute_delta_and_reason(self) -> None:
        model = build_analysis_v02(
            synthetic_dataset(synthetic_rows()), "Демо", 1
        )
        comparison = model.period.comparisons["product_sales"]
        self.assertEqual(comparison.absolute_delta, 500)
        self.assertIsNone(comparison.relative_delta_percent)
        self.assertIn("нулю", comparison.reason or "")
        fact = next(
            item for item in model.neutral_facts if item.metric_id == "product_sales"
        )
        self.assertEqual(fact.direction, ChangeDirection.INCREASED)

    def test_unknown_flag_is_data_gap_without_blocking_context(self) -> None:
        frame = synthetic_rows(has_extra_service=["Нет", "Возможно"])
        model = build_analysis_v02(synthetic_dataset(frame), "Демо", 1)
        extra = model.period.comparisons["extra_service_visit_share"]
        self.assertEqual(extra.status, MetricStatus.DATA_GAP)
        self.assertEqual(model.context.visits_count.status, MetricStatus.AVAILABLE)
        self.assertEqual(
            model.context.unique_clients_count.status, MetricStatus.AVAILABLE
        )
        fact = next(
            item
            for item in model.neutral_facts
            if item.metric_id == "extra_service_visit_share"
        )
        self.assertEqual(fact.direction, ChangeDirection.UNAVAILABLE)
        self.assertEqual(fact.status, MetricStatus.DATA_GAP)

    def test_damaged_money_blocks_dependencies_but_not_counts(self) -> None:
        frame = synthetic_rows(service_revenue=[1000, "ошибка"])
        model = build_analysis_v02(synthetic_dataset(frame), "Демо", 1)
        self.assertEqual(model.context.revenue_total.status, MetricStatus.DATA_GAP)
        self.assertEqual(model.context.visits_count.value, 1)
        self.assertEqual(model.context.unique_clients_count.value, 1)
        self.assertEqual(
            model.period.current_metrics["average_check"].status,
            MetricStatus.DATA_GAP,
        )
        self.assertEqual(
            model.period.current_metrics["prebooking_rate"].status,
            MetricStatus.AVAILABLE,
        )

    def test_legacy_v02_model_knows_profile_and_demo_limitations(self) -> None:
        model = build_analysis_v02(
            self.legacy, "Артём", 7, branch="Демо · Центр"
        )
        self.assertEqual(model.profile, MetricProfile.LEGACY_V01)
        self.assertEqual(
            model.period.current_metrics["extra_service_visit_share"].status,
            MetricStatus.DATA_GAP,
        )
        self.assertEqual(
            model.period.current_metrics["prebooking_rate"].status,
            MetricStatus.AVAILABLE,
        )
        self.assertEqual(
            model.period.current_metrics["unique_clients_count"].status,
            MetricStatus.AVAILABLE,
        )

    def test_builder_delegates_once_with_profile_and_global_anchor(self) -> None:
        with patch(
            "topgun_myday.analytics.calculate_period_comparison",
            wraps=calculate_period_comparison,
        ) as delegated:
            build_analysis_v02(self.synthetic, "Демо-мастер А", 7)
        delegated.assert_called_once()
        args, kwargs = delegated.call_args
        self.assertEqual(args[1], MetricProfile.SYNTHETIC_V02)
        self.assertEqual(args[2], 7)
        self.assertEqual(kwargs["anchor_date"], self.synthetic.date_max)
        self.assertEqual(tuple(args[0]["barber"].unique()), ("Демо-мастер А",))


if __name__ == "__main__":
    unittest.main()
