from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .config import (
    CHANGE_THRESHOLD,
    INSUFFICIENT_FOCUS,
    KEY_METRICS,
    METRIC_LABELS,
    RECOMMENDATIONS,
    SIGNIFICANT_CHANGE_THRESHOLD,
    STABLE_FOCUS,
)
from .metrics import METRIC_IDS, MetricProfile, MetricStatus

if TYPE_CHECKING:
    from .analytics import AnalyticsModelV02


class RulesConfigError(ValueError):
    """Ошибка структуры или содержимого исполняемой конфигурации правил."""


class RuleStatus(str, Enum):
    DRAFT = "DRAFT"
    NEEDS_DATA = "NEEDS_DATA"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class BusinessLogicStatus(str, Enum):
    BUSINESS_LOGIC_NOT_CONFIGURED = "BUSINESS_LOGIC_NOT_CONFIGURED"
    EVALUATED = "EVALUATED"


@dataclass(frozen=True)
class RuleCondition:
    field: str
    operator: str
    value: int | float | str


@dataclass(frozen=True)
class RuleExample:
    case: str
    inputs: Mapping[str, int | float | str | None]
    expected_fired: bool


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    version: int
    status: RuleStatus
    owner: str
    confirmed_at: str | None
    metric_id: str
    minimum_visits: int | None
    conditions: tuple[RuleCondition, ...]
    interpretation: str
    recommendation: str | None
    examples: tuple[RuleExample, ...]


@dataclass(frozen=True)
class RulesConfig:
    schema_version: int
    rules: tuple[RuleDefinition, ...]


@dataclass(frozen=True)
class RuleExampleResult:
    case: str
    expected_fired: bool
    actual_fired: bool
    applicable: bool
    passed: bool
    reason: str


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: RuleStatus
    applicable: bool
    fired: bool
    interpretation: str | None
    recommendation: str | None
    reason: str


@dataclass(frozen=True)
class RuleEngineResult:
    status: BusinessLogicStatus
    results: tuple[RuleResult, ...]
    reason: str | None


_ALLOWED_FIELDS = frozenset(
    {
        "metric.current",
        "metric.previous",
        "delta.absolute",
        "delta.relative_percent",
        "delta.percentage_points",
        "context.visits_count",
        "context.unique_clients_count",
        "dataset.profile",
    }
)
_NUMERIC_FIELDS = _ALLOWED_FIELDS - {"dataset.profile"}
_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})
_EXAMPLE_CASES = frozenset({"fires", "does_not_fire", "boundary"})


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(left: int | float | str, operator: str, right: int | float | str) -> bool:
    if operator == "gt":
        return left > right  # type: ignore[operator]
    if operator == "gte":
        return left >= right  # type: ignore[operator]
    if operator == "lt":
        return left < right  # type: ignore[operator]
    if operator == "lte":
        return left <= right  # type: ignore[operator]
    return left == right


def _required_input_fields(rule: RuleDefinition) -> set[str]:
    fields = {condition.field for condition in rule.conditions}
    if rule.minimum_visits is not None:
        fields.add("context.visits_count")
    return fields


def _evaluate_inputs(
    rule: RuleDefinition,
    inputs: Mapping[str, int | float | str | None],
) -> tuple[bool, bool, str]:
    for field in sorted(_required_input_fields(rule)):
        if field not in inputs:
            return False, False, f"Не предоставлено разрешённое входное поле `{field}`."
        if inputs[field] is None:
            return False, False, f"Входное поле `{field}` недоступно."

    if rule.minimum_visits is not None:
        visits = inputs["context.visits_count"]
        if not _is_number(visits):
            return False, False, "Количество визитов недоступно или не является числом."
        if visits < rule.minimum_visits:
            return (
                False,
                False,
                f"Недостаточно визитов: {visits} при минимуме {rule.minimum_visits}.",
            )

    for condition in rule.conditions:
        left = inputs[condition.field]
        if left is None:
            return False, False, f"Входное поле `{condition.field}` недоступно."
        try:
            matched = _compare(left, condition.operator, condition.value)
        except TypeError:
            return (
                False,
                False,
                f"Значение поля `{condition.field}` несовместимо с условием.",
            )
        if not matched:
            return True, False, f"Условие `{condition.field} {condition.operator}` не выполнено."
    return True, True, "Все условия правила выполнены."


def check_rule_examples(rule: RuleDefinition) -> tuple[RuleExampleResult, ...]:
    """Проверить examples тем же AND-evaluator, что используется для модели."""

    results: list[RuleExampleResult] = []
    for example in rule.examples:
        applicable, fired, reason = _evaluate_inputs(rule, example.inputs)
        passed = applicable and fired == example.expected_fired
        results.append(
            RuleExampleResult(
                case=example.case,
                expected_fired=example.expected_fired,
                actual_fired=fired,
                applicable=applicable,
                passed=passed,
                reason=reason,
            )
        )
    return tuple(results)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RulesConfigError(f"{label} должен быть JSON-объектом.")
    return value


def _exact_keys(
    value: Mapping[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    extra = sorted(keys - required - optional)
    if missing:
        raise RulesConfigError(
            f"{label}: отсутствуют обязательные ключи: {', '.join(missing)}."
        )
    if extra:
        raise RulesConfigError(f"{label}: неизвестные ключи: {', '.join(extra)}.")


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RulesConfigError(f"{label} должен быть непустой строкой.")
    return value.strip()


def _parse_iso_datetime(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RulesConfigError(f"{label} должен быть ISO-8601 строкой или null.")
    cleaned = value.strip()
    try:
        datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RulesConfigError(f"{label} содержит некорректную ISO-8601 дату.") from exc
    return cleaned


def _parse_condition(value: object, label: str) -> RuleCondition:
    raw = _mapping(value, label)
    _exact_keys(raw, {"field", "operator", "value"}, set(), label)
    field = _non_empty_string(raw["field"], f"{label}.field")
    if field not in _ALLOWED_FIELDS:
        raise RulesConfigError(f"{label}: неизвестное входное поле `{field}`.")
    operator = _non_empty_string(raw["operator"], f"{label}.operator")
    if operator not in _OPERATORS:
        raise RulesConfigError(f"{label}: неизвестный operator `{operator}`.")
    condition_value = raw["value"]
    if field == "dataset.profile":
        if operator != "eq":
            raise RulesConfigError("Для `dataset.profile` разрешён только operator `eq`.")
        profiles = {profile.value for profile in MetricProfile}
        if not isinstance(condition_value, str) or condition_value not in profiles:
            raise RulesConfigError("Условие `dataset.profile` содержит неизвестный профиль.")
    elif not _is_number(condition_value):
        raise RulesConfigError(f"{label}.value должен быть числом.")
    return RuleCondition(field=field, operator=operator, value=condition_value)


def _parse_example(value: object, label: str) -> RuleExample:
    raw = _mapping(value, label)
    _exact_keys(raw, {"case", "inputs", "expected_fired"}, set(), label)
    case = _non_empty_string(raw["case"], f"{label}.case")
    if case not in _EXAMPLE_CASES:
        raise RulesConfigError(f"{label}: неизвестный example case `{case}`.")
    inputs_raw = _mapping(raw["inputs"], f"{label}.inputs")
    inputs: dict[str, int | float | str | None] = {}
    for field, input_value in inputs_raw.items():
        if field not in _ALLOWED_FIELDS:
            raise RulesConfigError(f"{label}: неизвестное входное поле `{field}`.")
        if field == "dataset.profile":
            profiles = {profile.value for profile in MetricProfile}
            if not isinstance(input_value, str) or input_value not in profiles:
                raise RulesConfigError(f"{label}: неизвестный dataset profile.")
        elif input_value is not None and not _is_number(input_value):
            raise RulesConfigError(f"{label}: `{field}` должен быть числом или null.")
        inputs[field] = input_value
    expected = raw["expected_fired"]
    if not isinstance(expected, bool):
        raise RulesConfigError(f"{label}.expected_fired должен быть boolean.")
    return RuleExample(case=case, inputs=inputs, expected_fired=expected)


def _parse_rule(value: object, index: int) -> RuleDefinition:
    label = f"rules[{index}]"
    raw = _mapping(value, label)
    required = {
        "id",
        "version",
        "status",
        "owner",
        "confirmed_at",
        "metric_id",
        "conditions",
        "interpretation",
        "recommendation",
        "examples",
    }
    _exact_keys(raw, required, {"minimum_visits"}, label)
    rule_id = _non_empty_string(raw["id"], f"{label}.id")
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise RulesConfigError(f"{label}.version должен быть положительным целым числом.")
    try:
        status = RuleStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise RulesConfigError(f"{label}: неизвестный status `{raw['status']}`.") from exc
    owner = _non_empty_string(raw["owner"], f"{label}.owner")
    confirmed_at = _parse_iso_datetime(raw["confirmed_at"], f"{label}.confirmed_at")
    if status == RuleStatus.ACTIVE and confirmed_at is None:
        raise RulesConfigError(f"{label}: ACTIVE-правило требует confirmed_at.")
    metric_id = _non_empty_string(raw["metric_id"], f"{label}.metric_id")
    if metric_id not in METRIC_IDS:
        raise RulesConfigError(f"{label}: неизвестный metric_id `{metric_id}`.")
    minimum_visits = raw.get("minimum_visits")
    if minimum_visits is not None and (
        isinstance(minimum_visits, bool)
        or not isinstance(minimum_visits, int)
        or minimum_visits <= 0
    ):
        raise RulesConfigError(
            f"{label}.minimum_visits должен быть положительным целым числом."
        )
    conditions_raw = raw["conditions"]
    if not isinstance(conditions_raw, list) or not conditions_raw:
        raise RulesConfigError(f"{label}.conditions должен быть непустым списком.")
    conditions = tuple(
        _parse_condition(item, f"{label}.conditions[{condition_index}]")
        for condition_index, item in enumerate(conditions_raw)
    )
    interpretation_value = raw["interpretation"]
    if not isinstance(interpretation_value, str):
        raise RulesConfigError(f"{label}.interpretation должен быть строкой.")
    interpretation = interpretation_value.strip()
    if status == RuleStatus.ACTIVE and not interpretation:
        raise RulesConfigError(f"{label}: ACTIVE-правило требует interpretation.")
    recommendation_value = raw["recommendation"]
    if recommendation_value is None:
        recommendation = None
    elif isinstance(recommendation_value, str) and recommendation_value.strip():
        recommendation = recommendation_value.strip()
    else:
        raise RulesConfigError(
            f"{label}.recommendation должен быть непустой строкой или null."
        )
    examples_raw = raw["examples"]
    if not isinstance(examples_raw, list):
        raise RulesConfigError(f"{label}.examples должен быть списком.")
    examples = tuple(
        _parse_example(item, f"{label}.examples[{example_index}]")
        for example_index, item in enumerate(examples_raw)
    )
    cases = [example.case for example in examples]
    if len(cases) != 3 or set(cases) != _EXAMPLE_CASES:
        raise RulesConfigError(
            f"{label}.examples должен содержать fires, does_not_fire и boundary."
        )
    rule = RuleDefinition(
        id=rule_id,
        version=version,
        status=status,
        owner=owner,
        confirmed_at=confirmed_at,
        metric_id=metric_id,
        minimum_visits=minimum_visits,
        conditions=conditions,
        interpretation=interpretation,
        recommendation=recommendation,
        examples=examples,
    )
    if status == RuleStatus.ACTIVE:
        failed = [result for result in check_rule_examples(rule) if not result.passed]
        if failed:
            names = ", ".join(result.case for result in failed)
            raise RulesConfigError(
                f"{label}: ACTIVE-правило не прошло examples: {names}."
            )
    return rule


def validate_rules_config(raw: object) -> RulesConfig:
    root = _mapping(raw, "Конфигурация")
    _exact_keys(root, {"schema_version", "rules"}, set(), "Конфигурация")
    schema_version = root["schema_version"]
    if schema_version != 1 or isinstance(schema_version, bool):
        raise RulesConfigError(
            f"Неподдерживаемый schema_version: {schema_version!r}; ожидается 1."
        )
    rules_raw = root["rules"]
    if not isinstance(rules_raw, list):
        raise RulesConfigError("Поле `rules` должно быть списком.")
    rules = tuple(_parse_rule(item, index) for index, item in enumerate(rules_raw))
    ids = [rule.id for rule in rules]
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        raise RulesConfigError(
            "ID правил должны быть уникальны; повторяются: " + ", ".join(duplicates)
        )
    return RulesConfig(schema_version=1, rules=rules)


def load_rules_config(
    path: str | Path = Path("config") / "business_rules.json",
) -> RulesConfig:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesConfigError(f"Не удалось прочитать конфигурацию правил: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulesConfigError(
            f"Конфигурация правил содержит повреждённый JSON: {exc.msg}."
        ) from exc
    return validate_rules_config(raw)


def _resolve_model_field(
    model: "AnalyticsModelV02", rule: RuleDefinition, field: str
) -> tuple[int | float | str | None, str | None]:
    if field == "dataset.profile":
        return model.profile.value, None
    if field == "context.visits_count":
        result = model.context.visits_count
        if result.status != MetricStatus.AVAILABLE or result.value is None:
            return None, result.reason or "visits_count недоступен."
        return result.value, None
    if field == "context.unique_clients_count":
        result = model.context.unique_clients_count
        if result.status != MetricStatus.AVAILABLE or result.value is None:
            return None, result.reason or "unique_clients_count недоступен."
        return result.value, None

    comparison = model.period.comparisons[rule.metric_id]
    if field == "metric.current":
        result = comparison.current
        if result.status != MetricStatus.AVAILABLE or result.value is None:
            return None, result.reason or f"{rule.metric_id} за текущий период недоступен."
        return result.value, None
    if field == "metric.previous":
        result = comparison.previous
        if result.status != MetricStatus.AVAILABLE or result.value is None:
            return None, result.reason or f"{rule.metric_id} за предыдущий период недоступен."
        return result.value, None
    if comparison.status != MetricStatus.AVAILABLE:
        return None, comparison.reason or f"Сравнение {rule.metric_id} недоступно."
    if field == "delta.absolute":
        value = comparison.absolute_delta
    elif field == "delta.relative_percent":
        value = comparison.relative_delta_percent
    else:
        value = comparison.percentage_point_delta
    if value is None:
        return None, comparison.reason or f"Approved delta `{field}` недоступна."
    return value, None


def _apply_rule(model: "AnalyticsModelV02", rule: RuleDefinition) -> RuleResult:
    inputs: dict[str, int | float | str | None] = {}
    for field in sorted(_required_input_fields(rule)):
        value, reason = _resolve_model_field(model, rule, field)
        if reason is not None:
            return RuleResult(
                rule_id=rule.id,
                status=rule.status,
                applicable=False,
                fired=False,
                interpretation=None,
                recommendation=None,
                reason=f"Правило неприменимо: {reason}",
            )
        inputs[field] = value
    applicable, fired, reason = _evaluate_inputs(rule, inputs)
    return RuleResult(
        rule_id=rule.id,
        status=rule.status,
        applicable=applicable,
        fired=fired,
        interpretation=rule.interpretation if fired else None,
        recommendation=rule.recommendation if fired else None,
        reason=reason,
    )


def apply_active_rules(
    model: "AnalyticsModelV02", config: RulesConfig
) -> RuleEngineResult:
    active = tuple(rule for rule in config.rules if rule.status == RuleStatus.ACTIVE)
    if not active:
        return RuleEngineResult(
            status=BusinessLogicStatus.BUSINESS_LOGIC_NOT_CONFIGURED,
            results=(),
            reason="Активные бизнес-правила не настроены.",
        )
    return RuleEngineResult(
        status=BusinessLogicStatus.EVALUATED,
        results=tuple(_apply_rule(model, rule) for rule in active),
        reason=None,
    )


@dataclass(frozen=True)
class InsightResult:
    strength_title: str
    strength_text: str
    attention_title: str
    attention_text: str
    focus_title: str
    focus_text: str


def _format_delta(delta: float) -> str:
    return f"{abs(delta) * 100:.1f}%".replace(".", ",")


def _change_level(delta: float) -> str:
    if abs(delta) > SIGNIFICANT_CHANGE_THRESHOLD:
        return "существенное"
    if abs(delta) >= CHANGE_THRESHOLD:
        return "заметное"
    return "обычное"


def build_insights(deltas: Mapping[str, float | None]) -> InsightResult:
    available = [(key, deltas.get(key)) for key in KEY_METRICS]
    comparable = [(key, value) for key, value in available if value is not None]

    if not comparable:
        return InsightResult(
            strength_title="Сильная сторона · недостаточно данных",
            strength_text=(
                "Нет предыдущего сопоставимого периода с ненулевыми значениями. "
                "Сильная сторона по динамике не определяется."
            ),
            attention_title="Зона внимания · недостаточно данных",
            attention_text=(
                "Фактическое снижение нельзя подтвердить без корректного ориентира."
            ),
            focus_title=INSUFFICIENT_FOCUS[0],
            focus_text=INSUFFICIENT_FOCUS[1],
        )

    best_key, best_delta = max(comparable, key=lambda item: item[1])
    worst_key, worst_delta = min(comparable, key=lambda item: item[1])

    if best_delta >= CHANGE_THRESHOLD:
        strength_title = f"Сильная сторона · {METRIC_LABELS[best_key]}"
        strength_text = (
            f"Показатель вырос на {_format_delta(best_delta)} к предыдущему периоду. "
            f"Это {_change_level(best_delta)} позитивное изменение."
        )
    else:
        strength_title = "Сильная сторона · стабильность"
        strength_text = (
            "Стабильный период. Существенного роста по ключевым показателям нет."
        )

    if worst_delta <= -CHANGE_THRESHOLD:
        attention_title = f"Зона внимания · {METRIC_LABELS[worst_key]}"
        attention_text = (
            f"Показатель снизился на {_format_delta(worst_delta)} к предыдущему "
            f"периоду. Это {_change_level(worst_delta)} изменение показателя."
        )
        focus_title, focus_text = RECOMMENDATIONS[worst_key]
    else:
        attention_title = "Зона внимания · без выраженного снижения"
        attention_text = (
            "Ключевые показатели находятся в пределах обычного колебания. "
            "Искусственно создавать проблему не требуется."
        )
        focus_title, focus_text = STABLE_FOCUS

    return InsightResult(
        strength_title=strength_title,
        strength_text=strength_text,
        attention_title=attention_title,
        attention_text=attention_text,
        focus_title=focus_title,
        focus_text=focus_text,
    )
