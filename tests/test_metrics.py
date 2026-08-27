from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from topgun_myday.data_loader import load_file
from topgun_myday.metrics import (
    METRIC_IDS,
    MetricProfile,
    MetricStatus,
    MetricUnit,
    MetricsInputError,
    build_period_window,
    calculate_metrics,
    calculate_period_comparison,
)


SYNTHETIC_COLUMNS = (
    "date",
    "barber",
    "visit_id",
    "client_id",
    "branch",
    "service_revenue",
    "product_revenue",
    "has_extra_service",
    "prebooked",
)


def small_synthetic_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-08", "2026-08-09"]),
            "barber": ["Демо", "Демо"],
            "visit_id": ["V1", "V2"],
            "client_id": ["C1", "C1"],
            "branch": ["SYNTHETIC", "SYNTHETIC"],
            "service_revenue": [1000, 2000],
            "product_revenue": [0, 500],
            "has_extra_service": ["Нет", "Да"],
            "prebooked": ["Да", "Да"],
        }
    )


GOLDEN = {
    "Демо-мастер А": {
        1: {
            "current": (2, 2, 7450, 3725, 1 / 2, 0, 2 / 2),
            "previous": (2, 2, 7550, 3775, 0 / 2, 600, 1 / 2),
        },
        7: {
            "current": (14, 12, 50350, 50350 / 14, 8 / 14, 2700, 10 / 14),
            "previous": (14, 12, 50450, 50450 / 14, 5 / 14, 3300, 9 / 14),
        },
        14: {
            "current": (28, 12, 100800, 100800 / 28, 13 / 28, 6000, 19 / 28),
            "previous": (28, 12, 98950, 98950 / 28, 11 / 28, 6150, 19 / 28),
        },
    },
    "Демо-мастер Б": {
        1: {
            "current": (2, 2, 7850, 3925, 1 / 2, 950, 1 / 2),
            "previous": (2, 2, 8100, 4050, 2 / 2, 0, 1 / 2),
        },
        7: {
            "current": (14, 12, 56350, 56350 / 14, 7 / 14, 4450, 9 / 14),
            "previous": (14, 12, 56600, 56600 / 14, 9 / 14, 3500, 9 / 14),
        },
        14: {
            "current": (28, 12, 112950, 112950 / 28, 16 / 28, 7950, 18 / 28),
            "previous": (28, 12, 113150, 113150 / 28, 15 / 28, 8750, 19 / 28),
        },
    },
}


class MetricsUnitTests(unittest.TestCase):
    def test_metric_result_contract_and_formulas(self) -> None:
        metrics = calculate_metrics(
            small_synthetic_frame(), MetricProfile.SYNTHETIC_V02
        )
        self.assertEqual(tuple(metrics), METRIC_IDS)

        visits = metrics["visits_count"]
        self.assertEqual(visits.status, MetricStatus.AVAILABLE)
        self.assertEqual(visits.value, 2)
        self.assertEqual(visits.unit, MetricUnit.COUNT)
        self.assertEqual(visits.numerator, 2)
        self.assertIsNone(visits.denominator)
        self.assertIsNone(visits.reason)

        unique_clients = metrics["unique_clients_count"]
        self.assertEqual(unique_clients.status, MetricStatus.AVAILABLE)
        self.assertEqual(unique_clients.value, 1)
        self.assertEqual(unique_clients.unit, MetricUnit.COUNT)
        self.assertEqual(unique_clients.numerator, 1)
        self.assertIsNone(unique_clients.denominator)

        revenue = metrics["revenue_total"]
        self.assertEqual(revenue.value, 3500)
        self.assertEqual(revenue.numerator, 3500)
        self.assertIsNone(revenue.denominator)

        average = metrics["average_check"]
        self.assertEqual(average.value, 1750)
        self.assertEqual(average.numerator, 3500)
        self.assertEqual(average.denominator, 2)

        extra = metrics["extra_service_visit_share"]
        self.assertEqual(extra.value, 1 / 2)
        self.assertEqual(extra.numerator, 1)
        self.assertEqual(extra.denominator, 2)
        self.assertEqual(metrics["product_sales"].value, 500)
        self.assertEqual(metrics["prebooking_rate"].value, 1.0)

    def test_period_windows_have_exact_non_overlapping_boundaries(self) -> None:
        expected = {
            1: ("2026-08-09", "2026-08-09", "2026-08-08", "2026-08-08"),
            7: ("2026-08-03", "2026-08-09", "2026-07-27", "2026-08-02"),
            14: ("2026-07-27", "2026-08-09", "2026-07-13", "2026-07-26"),
        }
        for days, boundaries in expected.items():
            with self.subTest(days=days):
                window = build_period_window(date(2026, 8, 9), days)
                actual = (
                    window.current_start.isoformat(),
                    window.current_end.isoformat(),
                    window.previous_start.isoformat(),
                    window.previous_end.isoformat(),
                )
                self.assertEqual(actual, boundaries)
                self.assertEqual(
                    window.previous_end + timedelta(days=1), window.current_start
                )
                self.assertLess(window.previous_end, window.current_start)

    def test_non_positive_or_non_integer_period_is_rejected(self) -> None:
        for days in (0, -1, True, 7.0):
            with self.subTest(days=days):
                with self.assertRaises(MetricsInputError):
                    build_period_window(date(2026, 8, 9), days)  # type: ignore[arg-type]

    def test_anchor_defaults_to_maximum_date_and_endpoints_are_included(self) -> None:
        result = calculate_period_comparison(
            small_synthetic_frame(), MetricProfile.SYNTHETIC_V02, 1
        )
        self.assertEqual(result.window.anchor_date.isoformat(), "2026-08-09")
        self.assertEqual(result.current_metrics["visits_count"].value, 1)
        self.assertEqual(result.previous_metrics["visits_count"].value, 1)

    def test_empty_frame_without_anchor_is_rejected(self) -> None:
        empty = pd.DataFrame(columns=SYNTHETIC_COLUMNS)
        with self.assertRaisesRegex(MetricsInputError, "anchor"):
            calculate_period_comparison(
                empty, MetricProfile.SYNTHETIC_V02, 7
            )

    def test_empty_frame_with_anchor_has_zero_sums_and_insufficient_ratios(self) -> None:
        empty = pd.DataFrame(columns=SYNTHETIC_COLUMNS)
        result = calculate_period_comparison(
            empty,
            MetricProfile.SYNTHETIC_V02,
            7,
            anchor_date=date(2026, 8, 9),
        )
        current = result.current_metrics
        for metric_id in (
            "visits_count",
            "unique_clients_count",
            "revenue_total",
            "product_sales",
        ):
            self.assertEqual(current[metric_id].status, MetricStatus.AVAILABLE)
            self.assertEqual(current[metric_id].value, 0)
        for metric_id in (
            "average_check",
            "extra_service_visit_share",
            "prebooking_rate",
        ):
            self.assertEqual(
                current[metric_id].status, MetricStatus.INSUFFICIENT_DATA
            )
            self.assertIsNone(current[metric_id].value)

    def test_single_visit_is_sufficient_when_denominators_are_nonzero(self) -> None:
        one = small_synthetic_frame().iloc[[0]]
        metrics = calculate_metrics(one, MetricProfile.SYNTHETIC_V02)
        for metric_id in METRIC_IDS:
            self.assertEqual(metrics[metric_id].status, MetricStatus.AVAILABLE)

    def test_unique_clients_requires_complete_demo_client_id(self) -> None:
        metrics = calculate_metrics(
            small_synthetic_frame(), MetricProfile.SYNTHETIC_V02
        )
        self.assertEqual(metrics["unique_clients_count"].value, 1)
        for value in (None, ""):
            with self.subTest(value=value):
                frame = small_synthetic_frame()
                frame.loc[0, "client_id"] = value
                result = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
                self.assertEqual(
                    result["unique_clients_count"].status, MetricStatus.DATA_GAP
                )
                self.assertEqual(result["visits_count"].status, MetricStatus.AVAILABLE)

    def test_missing_client_id_only_blocks_unique_clients(self) -> None:
        frame = small_synthetic_frame().drop(columns="client_id")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(
            metrics["unique_clients_count"].status, MetricStatus.DATA_GAP
        )
        for metric_id in METRIC_IDS:
            if metric_id != "unique_clients_count":
                self.assertEqual(metrics[metric_id].status, MetricStatus.AVAILABLE)

    def test_missing_service_revenue_blocks_revenue_and_average_only(self) -> None:
        frame = small_synthetic_frame().drop(columns="service_revenue")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(metrics["revenue_total"].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["average_check"].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["visits_count"].status, MetricStatus.AVAILABLE)
        self.assertEqual(metrics["product_sales"].status, MetricStatus.AVAILABLE)
        self.assertIn("service_revenue", metrics["revenue_total"].reason or "")

    def test_service_revenue_nan_is_not_silently_skipped(self) -> None:
        frame = small_synthetic_frame()
        frame.loc[0, "service_revenue"] = float("nan")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(metrics["revenue_total"].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["average_check"].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["visits_count"].status, MetricStatus.AVAILABLE)
        self.assertIn("NaN", metrics["revenue_total"].reason or "")

    def test_product_revenue_nan_blocks_product_revenue_and_average(self) -> None:
        frame = small_synthetic_frame()
        frame.loc[1, "product_revenue"] = float("nan")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        for metric_id in ("product_sales", "revenue_total", "average_check"):
            self.assertEqual(metrics[metric_id].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["visits_count"].status, MetricStatus.AVAILABLE)
        self.assertEqual(metrics["visits_count"].value, 2)

    def test_non_numeric_money_value_becomes_data_gap(self) -> None:
        for field in ("service_revenue", "product_revenue"):
            with self.subTest(field=field):
                frame = small_synthetic_frame()
                frame[field] = frame[field].astype(object)
                frame.loc[0, field] = "не число"
                metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
                self.assertEqual(
                    metrics["revenue_total"].status, MetricStatus.DATA_GAP
                )
                self.assertEqual(
                    metrics["average_check"].status, MetricStatus.DATA_GAP
                )
                self.assertEqual(
                    metrics["visits_count"].status, MetricStatus.AVAILABLE
                )

    def test_missing_extra_service_field_does_not_block_other_metrics(self) -> None:
        frame = small_synthetic_frame().drop(columns="has_extra_service")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(
            metrics["extra_service_visit_share"].status, MetricStatus.DATA_GAP
        )
        for metric_id in (
            "visits_count",
            "revenue_total",
            "average_check",
            "product_sales",
            "prebooking_rate",
        ):
            self.assertEqual(metrics[metric_id].status, MetricStatus.AVAILABLE)

    def test_unknown_or_blank_yes_no_value_becomes_data_gap(self) -> None:
        for field, value, affected in (
            ("has_extra_service", "Возможно", "extra_service_visit_share"),
            ("has_extra_service", None, "extra_service_visit_share"),
            ("prebooked", "?", "prebooking_rate"),
            ("prebooked", "", "prebooking_rate"),
        ):
            with self.subTest(field=field, value=value):
                frame = small_synthetic_frame()
                frame.loc[0, field] = value
                metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
                self.assertEqual(metrics[affected].status, MetricStatus.DATA_GAP)
                self.assertIn("Да/Нет", metrics[affected].reason or "")

    def test_missing_visit_id_blocks_dependent_ratios_but_not_sums(self) -> None:
        frame = small_synthetic_frame().drop(columns="visit_id")
        metrics = calculate_metrics(frame, MetricProfile.SYNTHETIC_V02)
        for metric_id in (
            "visits_count",
            "average_check",
            "extra_service_visit_share",
            "prebooking_rate",
        ):
            self.assertEqual(metrics[metric_id].status, MetricStatus.DATA_GAP)
        self.assertEqual(metrics["revenue_total"].status, MetricStatus.AVAILABLE)
        self.assertEqual(metrics["product_sales"].status, MetricStatus.AVAILABLE)

    def test_zero_previous_product_keeps_absolute_delta_without_relative(self) -> None:
        result = calculate_period_comparison(
            small_synthetic_frame(),
            MetricProfile.SYNTHETIC_V02,
            1,
            anchor_date=date(2026, 8, 9),
        )
        comparison = result.comparisons["product_sales"]
        self.assertEqual(comparison.status, MetricStatus.AVAILABLE)
        self.assertEqual(comparison.current.value, 500)
        self.assertEqual(comparison.previous.value, 0)
        self.assertEqual(comparison.absolute_delta, 500)
        self.assertIsNone(comparison.relative_delta_percent)
        self.assertIn("нулю", comparison.reason or "")

    def test_unchanged_share_has_zero_percentage_points_only(self) -> None:
        frame = small_synthetic_frame()
        frame["has_extra_service"] = ["Да", "Да"]
        result = calculate_period_comparison(
            frame,
            MetricProfile.SYNTHETIC_V02,
            1,
            anchor_date=date(2026, 8, 9),
        )
        comparison = result.comparisons["extra_service_visit_share"]
        self.assertEqual(comparison.percentage_point_delta, 0)
        self.assertIsNone(comparison.absolute_delta)
        self.assertIsNone(comparison.relative_delta_percent)

    def test_data_gap_comparison_has_no_delta_and_independent_count_compares(self) -> None:
        frame = small_synthetic_frame()
        frame["service_revenue"] = frame["service_revenue"].astype(object)
        frame.loc[1, "service_revenue"] = "ошибка"
        result = calculate_period_comparison(
            frame,
            MetricProfile.SYNTHETIC_V02,
            1,
            anchor_date=date(2026, 8, 9),
        )
        revenue = result.comparisons["revenue_total"]
        self.assertEqual(revenue.status, MetricStatus.DATA_GAP)
        self.assertIsNone(revenue.absolute_delta)
        self.assertIsNone(revenue.relative_delta_percent)
        visits = result.comparisons["visits_count"]
        self.assertEqual(visits.status, MetricStatus.AVAILABLE)
        self.assertEqual(visits.absolute_delta, 0)

    def test_legacy_profile_preserves_demo_formulas_and_extra_is_data_gap(self) -> None:
        frame = pd.DataFrame(
            {
                "service_revenue": [1000, 2000],
                "product_revenue": [500, 0],
                "discount": [100, 0],
                "next_booking": ["Да", "Нет"],
                "client_id": ["C1", "C1"],
            }
        )
        metrics = calculate_metrics(frame, MetricProfile.LEGACY_V01)
        self.assertEqual(metrics["visits_count"].value, 2)
        self.assertEqual(metrics["unique_clients_count"].value, 1)
        self.assertEqual(metrics["revenue_total"].value, 3400)
        self.assertEqual(metrics["average_check"].value, 1700)
        self.assertEqual(metrics["product_sales"].value, 500)
        self.assertEqual(metrics["prebooking_rate"].value, 1 / 2)
        self.assertEqual(
            metrics["extra_service_visit_share"].status, MetricStatus.DATA_GAP
        )
        self.assertIn("legacy", metrics["extra_service_visit_share"].reason or "")

    def test_legacy_demo_topgun_control_values_still_calculate(self) -> None:
        dataset = load_file(Path("data") / "demo_topgun.xlsx")
        rows = dataset.frame.loc[
            dataset.frame["barber"].eq("Артём")
            & dataset.frame["branch"].eq("Демо · Центр")
        ]
        result = calculate_period_comparison(
            rows,
            MetricProfile.LEGACY_V01,
            7,
            anchor_date=dataset.date_max,
        )
        current = result.current_metrics
        self.assertEqual(current["visits_count"].value, 31)
        self.assertEqual(current["unique_clients_count"].status, MetricStatus.AVAILABLE)
        self.assertAlmostEqual(current["revenue_total"].value or 0, 131100)
        self.assertAlmostEqual(current["average_check"].value or 0, 131100 / 31)
        self.assertAlmostEqual(current["product_sales"].value or 0, 13250)
        self.assertAlmostEqual(current["prebooking_rate"].value or 0, 15 / 31)
        self.assertEqual(
            current["extra_service_visit_share"].status, MetricStatus.DATA_GAP
        )


class SyntheticGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = pd.read_excel(
            Path("data") / "demo_topgun_v02.xlsx",
            sheet_name="Визиты_SYNTHETIC",
            engine="openpyxl",
        )
        cls.frame["date"] = pd.to_datetime(cls.frame["date"])
        cls.anchor = cls.frame["date"].max()

    def assert_snapshot(
        self, actual: Mapping[str, object], expected: tuple[float, ...]
    ) -> None:
        metric_ids = METRIC_IDS
        for metric_id, expected_value in zip(metric_ids, expected):
            result = actual[metric_id]
            self.assertEqual(result.status, MetricStatus.AVAILABLE)
            self.assertAlmostEqual(result.value, expected_value)

    def test_golden_current_previous_and_deltas_for_both_barbers(self) -> None:
        for barber, periods in GOLDEN.items():
            barber_rows = self.frame.loc[self.frame["barber"].eq(barber)]
            for days, expected in periods.items():
                with self.subTest(barber=barber, days=days):
                    result = calculate_period_comparison(
                        barber_rows,
                        MetricProfile.SYNTHETIC_V02,
                        days,
                        anchor_date=self.anchor,
                    )
                    self.assert_snapshot(result.current_metrics, expected["current"])
                    self.assert_snapshot(result.previous_metrics, expected["previous"])

                    for index, metric_id in enumerate(METRIC_IDS):
                        current_value = expected["current"][index]
                        previous_value = expected["previous"][index]
                        comparison = result.comparisons[metric_id]
                        self.assertEqual(comparison.status, MetricStatus.AVAILABLE)
                        if metric_id in (
                            "extra_service_visit_share",
                            "prebooking_rate",
                        ):
                            self.assertAlmostEqual(
                                comparison.percentage_point_delta or 0,
                                (current_value - previous_value) * 100,
                            )
                            self.assertIsNone(comparison.absolute_delta)
                            self.assertIsNone(comparison.relative_delta_percent)
                        else:
                            self.assertAlmostEqual(
                                comparison.absolute_delta or 0,
                                current_value - previous_value,
                            )
                            if previous_value == 0:
                                self.assertIsNone(comparison.relative_delta_percent)
                            else:
                                self.assertAlmostEqual(
                                    comparison.relative_delta_percent or 0,
                                    (current_value - previous_value)
                                    / previous_value
                                    * 100,
                                )

    def test_golden_values_are_not_rounded_in_metrics_layer(self) -> None:
        rows = self.frame.loc[self.frame["barber"].eq("Демо-мастер А")]
        result = calculate_period_comparison(
            rows,
            MetricProfile.SYNTHETIC_V02,
            7,
            anchor_date=self.anchor,
        )
        value = result.current_metrics["average_check"].value
        self.assertEqual(value, 50350 / 14)
        self.assertNotEqual(value, round(value, 2))


if __name__ == "__main__":
    unittest.main()
