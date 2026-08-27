from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from topgun_myday.analytics import build_analysis_v02
from topgun_myday.data_loader import load_file
from topgun_myday.rules import (
    BusinessLogicStatus,
    RuleStatus,
    RulesConfigError,
    apply_active_rules,
    build_insights,
    check_rule_examples,
    load_rules_config,
    validate_rules_config,
)


WORKING_CONFIG = Path("config") / "business_rules.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def operator_values(operator: str, boundary: float) -> tuple[float, float, bool]:
    if operator == "gt":
        return boundary + 1, boundary - 1, False
    if operator == "gte":
        return boundary + 1, boundary - 1, True
    if operator == "lt":
        return boundary - 1, boundary + 1, False
    if operator == "lte":
        return boundary - 1, boundary + 1, True
    return boundary, boundary + 1, True


def rule_payload(
    *,
    rule_id: str = "synthetic_test_rule",
    status: str = "DRAFT",
    metric_id: str = "product_sales",
    field: str = "metric.current",
    operator: str = "gte",
    value: float = 1000,
    minimum_visits: int | None = None,
    recommendation: str | None = None,
) -> dict[str, object]:
    fires, does_not_fire, boundary_expected = operator_values(operator, value)
    examples = []
    for case, input_value, expected in (
        ("fires", fires, True),
        ("does_not_fire", does_not_fire, False),
        ("boundary", value, boundary_expected),
    ):
        inputs: dict[str, object] = {field: input_value}
        if minimum_visits is not None:
            inputs["context.visits_count"] = minimum_visits
        examples.append(
            {"case": case, "inputs": inputs, "expected_fired": expected}
        )
    payload: dict[str, object] = {
        "id": rule_id,
        "version": 1,
        "status": status,
        "owner": "SYNTHETIC TEST ONLY",
        "confirmed_at": (
            "2026-08-16T18:00:00+03:00" if status == "ACTIVE" else None
        ),
        "metric_id": metric_id,
        "conditions": [{"field": field, "operator": operator, "value": value}],
        "interpretation": "SYNTHETIC TEST ONLY: условие выполнено.",
        "recommendation": recommendation,
        "examples": examples,
    }
    if minimum_visits is not None:
        payload["minimum_visits"] = minimum_visits
    return payload


def e2e_rule(status: str = "DRAFT") -> dict[str, object]:
    rule = rule_payload(status=status, minimum_visits=10)
    rule["id"] = "synthetic_test_product_sales_gte_1000"
    rule["conditions"] = [
        {"field": "dataset.profile", "operator": "eq", "value": "synthetic_v02"},
        {"field": "metric.current", "operator": "gte", "value": 1000},
    ]
    rule["examples"] = [
        {
            "case": "fires",
            "inputs": {
                "dataset.profile": "synthetic_v02",
                "context.visits_count": 12,
                "metric.current": 1200,
            },
            "expected_fired": True,
        },
        {
            "case": "does_not_fire",
            "inputs": {
                "dataset.profile": "synthetic_v02",
                "context.visits_count": 12,
                "metric.current": 900,
            },
            "expected_fired": False,
        },
        {
            "case": "boundary",
            "inputs": {
                "dataset.profile": "synthetic_v02",
                "context.visits_count": 10,
                "metric.current": 1000,
            },
            "expected_fired": True,
        },
    ]
    return rule


def config_with(*rules: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "rules": list(rules)}


def custom_synthetic_dataset(frame: pd.DataFrame):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "synthetic.csv"
        frame.to_csv(path, index=False)
        return load_file(path)


def two_day_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
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
    )


class RulesEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_file(Path("data") / "demo_topgun_v02.xlsx")
        cls.model_a7 = build_analysis_v02(cls.dataset, "Демо-мастер А", 7)
        cls.model_b1 = build_analysis_v02(cls.dataset, "Демо-мастер Б", 1)

    def test_empty_rules_returns_not_configured(self) -> None:
        config = validate_rules_config({"schema_version": 1, "rules": []})
        result = apply_active_rules(self.model_a7, config)
        self.assertEqual(
            result.status, BusinessLogicStatus.BUSINESS_LOGIC_NOT_CONFIGURED
        )
        self.assertEqual(result.results, ())
        self.assertIn("не настроены", result.reason or "")

    def test_inactive_statuses_are_not_executed(self) -> None:
        for status in (
            "DRAFT",
            "NEEDS_DATA",
            "NEEDS_CONFIRMATION",
            "RETIRED",
        ):
            with self.subTest(status=status):
                config = validate_rules_config(config_with(rule_payload(status=status)))
                result = apply_active_rules(self.model_a7, config)
                self.assertEqual(
                    result.status,
                    BusinessLogicStatus.BUSINESS_LOGIC_NOT_CONFIGURED,
                )
                self.assertEqual(result.results, ())

    def test_active_rule_fires_with_nullable_recommendation(self) -> None:
        config = validate_rules_config(config_with(e2e_rule("ACTIVE")))
        result = apply_active_rules(self.model_a7, config)
        self.assertEqual(result.status, BusinessLogicStatus.EVALUATED)
        item = result.results[0]
        self.assertEqual(item.status, RuleStatus.ACTIVE)
        self.assertTrue(item.applicable)
        self.assertTrue(item.fired)
        self.assertIn("SYNTHETIC TEST ONLY", item.interpretation or "")
        self.assertIsNone(item.recommendation)

    def test_active_rule_returns_configured_recommendation(self) -> None:
        rule = e2e_rule("ACTIVE")
        rule["recommendation"] = "SYNTHETIC TEST ONLY: тестовый фокус."
        item = apply_active_rules(
            self.model_a7, validate_rules_config(config_with(rule))
        ).results[0]
        self.assertTrue(item.fired)
        self.assertEqual(item.recommendation, rule["recommendation"])

    def test_unknown_status_operator_metric_and_field_are_rejected(self) -> None:
        cases = []
        unknown_status = rule_payload()
        unknown_status["status"] = "UNKNOWN"
        cases.append((unknown_status, "status"))
        unknown_operator = rule_payload()
        unknown_operator["conditions"][0]["operator"] = "contains"  # type: ignore[index]
        cases.append((unknown_operator, "operator"))
        unknown_metric = rule_payload()
        unknown_metric["metric_id"] = "invented_metric"
        cases.append((unknown_metric, "metric_id"))
        unknown_field = rule_payload()
        unknown_field["conditions"][0]["field"] = "model.__class__"  # type: ignore[index]
        cases.append((unknown_field, "входное поле"))
        for rule, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RulesConfigError, message):
                    validate_rules_config(config_with(rule))

    def test_missing_extra_keys_and_duplicate_ids_are_rejected(self) -> None:
        missing = rule_payload()
        del missing["interpretation"]
        with self.assertRaisesRegex(RulesConfigError, "interpretation"):
            validate_rules_config(config_with(missing))
        extra = rule_payload()
        extra["python"] = "eval('1+1')"
        with self.assertRaisesRegex(RulesConfigError, "неизвестные ключи"):
            validate_rules_config(config_with(extra))
        first = rule_payload(rule_id="duplicate")
        second = rule_payload(rule_id="duplicate")
        with self.assertRaisesRegex(RulesConfigError, "уникальны"):
            validate_rules_config(config_with(first, second))

    def test_corrupted_json_and_schema_version_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(RulesConfigError, "повреждённый JSON"):
                load_rules_config(path)
        with self.assertRaisesRegex(RulesConfigError, "schema_version"):
            validate_rules_config({"schema_version": 2, "rules": []})

    def test_active_requires_confirmed_at_and_interpretation(self) -> None:
        missing_confirmation = e2e_rule("ACTIVE")
        missing_confirmation["confirmed_at"] = None
        with self.assertRaisesRegex(RulesConfigError, "confirmed_at"):
            validate_rules_config(config_with(missing_confirmation))
        missing_interpretation = e2e_rule("ACTIVE")
        missing_interpretation["interpretation"] = " "
        with self.assertRaisesRegex(RulesConfigError, "interpretation"):
            validate_rules_config(config_with(missing_interpretation))
        invalid_recommendation = e2e_rule("ACTIVE")
        invalid_recommendation["recommendation"] = ""
        with self.assertRaisesRegex(RulesConfigError, "recommendation"):
            validate_rules_config(config_with(invalid_recommendation))

    def test_data_gap_metric_is_not_applicable(self) -> None:
        frame = two_day_frame()
        frame.loc[1, "has_extra_service"] = "Возможно"
        model = build_analysis_v02(custom_synthetic_dataset(frame), "Демо", 1)
        rule = rule_payload(
            status="ACTIVE",
            metric_id="extra_service_visit_share",
            value=0,
        )
        result = apply_active_rules(
            model, validate_rules_config(config_with(rule))
        ).results[0]
        self.assertFalse(result.applicable)
        self.assertFalse(result.fired)
        self.assertIn("Да/Нет", result.reason)

    def test_insufficient_previous_metric_is_not_applicable(self) -> None:
        frame = two_day_frame().iloc[[1]].reset_index(drop=True)
        model = build_analysis_v02(custom_synthetic_dataset(frame), "Демо", 1)
        rule = rule_payload(
            status="ACTIVE",
            metric_id="average_check",
            field="metric.previous",
            value=0,
        )
        result = apply_active_rules(
            model, validate_rules_config(config_with(rule))
        ).results[0]
        self.assertFalse(result.applicable)
        self.assertIn("нет визитов", result.reason)

    def test_unavailable_relative_delta_when_previous_is_zero(self) -> None:
        rule = rule_payload(
            status="ACTIVE",
            field="delta.relative_percent",
            value=0,
        )
        result = apply_active_rules(
            self.model_b1, validate_rules_config(config_with(rule))
        ).results[0]
        self.assertFalse(result.applicable)
        self.assertIn("нулю", result.reason)

    def test_minimum_visits_below_boundary_and_above(self) -> None:
        for minimum, applicable in ((3, False), (2, True), (1, True)):
            with self.subTest(minimum=minimum):
                rule = rule_payload(
                    status="ACTIVE", minimum_visits=minimum, value=0
                )
                result = apply_active_rules(
                    self.model_b1, validate_rules_config(config_with(rule))
                ).results[0]
                self.assertEqual(result.applicable, applicable)

    def test_operator_boundaries_use_expected_semantics(self) -> None:
        for operator in ("gt", "gte", "lt", "lte", "eq"):
            with self.subTest(operator=operator):
                rule = rule_payload(operator=operator, value=10)
                parsed = validate_rules_config(config_with(rule)).rules[0]
                results = {item.case: item for item in check_rule_examples(parsed)}
                self.assertTrue(all(item.passed for item in results.values()))
                expected_boundary = operator in ("gte", "lte", "eq")
                self.assertEqual(
                    results["boundary"].actual_fired, expected_boundary
                )

    def test_multiple_and_conditions(self) -> None:
        rule = e2e_rule("ACTIVE")
        fired = apply_active_rules(
            self.model_a7, validate_rules_config(config_with(rule))
        ).results[0]
        self.assertTrue(fired.fired)
        false_profile = copy.deepcopy(rule)
        false_profile["conditions"][0]["value"] = "legacy_v01"  # type: ignore[index]
        false_profile["examples"] = [
            {
                **example,
                "inputs": {**example["inputs"], "dataset.profile": "legacy_v01"},
            }
            for example in false_profile["examples"]  # type: ignore[union-attr]
        ]
        not_fired = apply_active_rules(
            self.model_a7, validate_rules_config(config_with(false_profile))
        ).results[0]
        self.assertTrue(not_fired.applicable)
        self.assertFalse(not_fired.fired)

    def test_multiple_active_rules_preserve_json_order(self) -> None:
        first = e2e_rule("ACTIVE")
        first["id"] = "first"
        second = rule_payload(
            rule_id="second", status="ACTIVE", operator="gt", value=99999
        )
        result = apply_active_rules(
            self.model_a7, validate_rules_config(config_with(first, second))
        )
        self.assertEqual(tuple(item.rule_id for item in result.results), ("first", "second"))
        self.assertEqual(tuple(item.fired for item in result.results), (True, False))

    def test_active_rule_with_failing_example_is_rejected(self) -> None:
        rule = e2e_rule("ACTIVE")
        rule["examples"][0]["expected_fired"] = False  # type: ignore[index]
        with self.assertRaisesRegex(RulesConfigError, "не прошло examples"):
            validate_rules_config(config_with(rule))

    def test_arbitrary_code_and_object_access_are_not_supported(self) -> None:
        for field in ("__class__", "metric.__class__.__mro__", "filesystem.path"):
            rule = rule_payload()
            rule["conditions"][0]["field"] = field  # type: ignore[index]
            with self.subTest(field=field):
                with self.assertRaises(RulesConfigError):
                    validate_rules_config(config_with(rule))

    def test_legacy_build_insights_remains_available(self) -> None:
        result = build_insights(
            {
                "average_check": 0.06,
                "next_booking_rate": -0.08,
                "product_per_visit": 0.12,
            }
        )
        self.assertIn("Сильная сторона", result.strength_title)

    def test_end_to_end_draft_examples_json_only_activation(self) -> None:
        working_hash_before = sha256(WORKING_CONFIG)
        python_hash_before = sha256(Path("topgun_myday") / "rules.py")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "business_rules.json"
            draft_raw = config_with(e2e_rule("DRAFT"))
            path.write_text(
                json.dumps(draft_raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            draft_config = load_rules_config(path)
            draft_result = apply_active_rules(self.model_a7, draft_config)
            self.assertEqual(
                draft_result.status,
                BusinessLogicStatus.BUSINESS_LOGIC_NOT_CONFIGURED,
            )
            examples = check_rule_examples(draft_config.rules[0])
            self.assertEqual(
                tuple(item.case for item in examples),
                ("fires", "does_not_fire", "boundary"),
            )
            self.assertTrue(all(item.passed for item in examples))

            active_raw = copy.deepcopy(draft_raw)
            active_rule = active_raw["rules"][0]  # type: ignore[index]
            active_rule["status"] = "ACTIVE"
            active_rule["confirmed_at"] = "2026-08-16T18:00:00+03:00"
            path.write_text(
                json.dumps(active_raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            active_config = load_rules_config(path)
            active_result = apply_active_rules(self.model_a7, active_config)
            self.assertEqual(active_result.status, BusinessLogicStatus.EVALUATED)
            self.assertTrue(active_result.results[0].fired)
            self.assertIsNone(active_result.results[0].recommendation)

        self.assertEqual(sha256(WORKING_CONFIG), working_hash_before)
        self.assertEqual(sha256(Path("topgun_myday") / "rules.py"), python_hash_before)
        self.assertEqual(load_rules_config(WORKING_CONFIG).rules, ())


if __name__ == "__main__":
    unittest.main()
