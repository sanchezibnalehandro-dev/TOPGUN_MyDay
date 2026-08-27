from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from topgun_myday.analytics import build_analysis_v02, build_report
from topgun_myday.data_loader import load_file
from topgun_myday.report import (
    SYNTHETIC_WARNING,
    export_html,
    export_html_v02,
    render_html,
    render_html_v02,
    rule_section_texts,
    safe_report_filename,
    safe_report_filename_v02,
)
from topgun_myday.rules import (
    BusinessLogicStatus,
    RuleEngineResult,
    RuleResult,
    RuleStatus,
    apply_active_rules,
    build_insights,
    validate_rules_config,
)


class RulesAndReportTests(unittest.TestCase):
    def test_rules_choose_largest_change_and_matching_focus(self) -> None:
        result = build_insights(
            {
                "average_check": 0.06,
                "next_booking_rate": -0.08,
                "product_per_visit": 0.12,
            }
        )
        self.assertIn("Товары / визит", result.strength_title)
        self.assertIn("Следующая запись", result.attention_title)
        self.assertEqual(result.focus_title, "Следующая запись")

    def test_tie_breaking_uses_metric_priority(self) -> None:
        result = build_insights(
            {
                "average_check": 0.07,
                "next_booking_rate": 0.07,
                "product_per_visit": 0.01,
            }
        )
        self.assertIn("Средний чек", result.strength_title)

    def test_small_changes_are_stable(self) -> None:
        result = build_insights(
            {
                "average_check": 0.049,
                "next_booking_rate": -0.049,
                "product_per_visit": 0.0,
            }
        )
        self.assertIn("стабильность", result.strength_title.casefold())
        self.assertIn("без выраженного", result.attention_title.casefold())
        self.assertEqual(result.focus_title, "Удержать текущий подход")

    def test_html_export_contains_summary_not_client_rows(self) -> None:
        dataset = load_file(Path("data") / "demo_topgun.xlsx")
        report = build_report(dataset, "Артём", 7)
        content = render_html(report)
        self.assertIn("131&nbsp;100", content.replace(" ", "&nbsp;"))
        self.assertNotIn("client_id", content)
        self.assertNotIn("А001", content)
        self.assertTrue(safe_report_filename(report).endswith(".html"))

    def test_export_refuses_silent_overwrite(self) -> None:
        dataset = load_file(Path("data") / "demo_topgun.xlsx")
        report = build_report(dataset, "Артём", 7)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "отчёт с пробелом.html"
            saved = export_html(report, destination)
            self.assertTrue(saved.exists())
            with self.assertRaises(FileExistsError):
                export_html(report, destination)
            export_html(report, destination, overwrite=True)


class ReportV02Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synthetic = load_file(Path("data") / "demo_topgun_v02.xlsx")
        cls.legacy = load_file(Path("data") / "demo_topgun.xlsx")
        cls.empty_rules = validate_rules_config({"schema_version": 1, "rules": []})

    def render_synthetic(self, barber: str = "Демо-мастер А", days: int = 7) -> str:
        model = build_analysis_v02(self.synthetic, barber, days)
        rules = apply_active_rules(model, self.empty_rules)
        return render_html_v02(model, rules)

    def test_synthetic_html_has_v02_sections_and_no_available_badge(self) -> None:
        content = self.render_synthetic()
        for text in (
            "Средний чек",
            "Визиты с допуслугой",
            "Продажи товаров",
            "Предварительная запись",
            "Динамика показателя",
            "Контекст периода",
            "ОРИЕНТИР",
            "Что изменилось",
            "Зона внимания",
            "Фокус",
            "Методика и ограничения",
            SYNTHETIC_WARNING,
        ):
            self.assertIn(text, content)
        self.assertNotIn("Значение доступно", content)
        self.assertIn("<table>", content)
        self.assertIn("<details>", content)
        self.assertNotIn("Сравнение периодов", content)
        self.assertNotIn("Сравнение с командой", content)
        self.assertNotIn("Сильная сторона", content)

    def test_legacy_uses_v02_report_with_data_gap_and_no_synthetic_warning(self) -> None:
        model = build_analysis_v02(self.legacy, "Артём", 7, branch="Демо · Центр")
        content = render_html_v02(model, apply_active_rules(model, self.empty_rules))
        self.assertIn("Сравнение пока недоступно", content)
        self.assertIn("Формула / поле ещё не подтверждены", content)
        self.assertIn("дополнительной услуги", content)
        self.assertLess(
            content.index("Сравнение пока недоступно"),
            content.index("Методика и ограничения"),
        )
        self.assertNotIn(SYNTHETIC_WARNING, content)
        self.assertNotIn("team", content.casefold())

    def test_money_and_share_deltas_follow_different_contracts(self) -> None:
        content = self.render_synthetic("Демо-мастер Б", 1)
        self.assertIn("+950 ₽ · Относительное изменение не рассчитывается", content)
        self.assertIn("−50,0 п.п.", content)
        self.assertNotIn("inf", content.casefold())

    def test_rule_sections_preserve_order_and_null_recommendation_contract(self) -> None:
        result = RuleEngineResult(
            status=BusinessLogicStatus.EVALUATED,
            results=(
                RuleResult("one", RuleStatus.ACTIVE, True, True, "Первое", None, "ok"),
                RuleResult("two", RuleStatus.ACTIVE, True, True, "Второе", "Фокус 2", "ok"),
            ),
            reason=None,
        )
        attention, focus = rule_section_texts(result)
        self.assertEqual(attention, ("Первое", "Второе"))
        self.assertEqual(focus, ("Фокус 2",))

        only_null = RuleEngineResult(
            status=BusinessLogicStatus.EVALUATED,
            results=(
                RuleResult("one", RuleStatus.ACTIVE, True, True, "Первое", None, "ok"),
            ),
            reason=None,
        )
        self.assertEqual(
            rule_section_texts(only_null)[1],
            ("Рекомендация для сработавших правил пока не настроена.",),
        )

    def test_html_escapes_rule_text_and_hides_rule_internals_and_client_rows(self) -> None:
        model = build_analysis_v02(self.synthetic, "Демо-мастер А", 7)
        rules = RuleEngineResult(
            status=BusinessLogicStatus.EVALUATED,
            results=(
                RuleResult(
                    "secret_rule_id",
                    RuleStatus.ACTIVE,
                    True,
                    True,
                    "<b>Подтверждённый факт</b>",
                    "<script>Фокус</script>",
                    "metric.current gte 1000",
                ),
            ),
            reason=None,
        )
        content = render_html_v02(model, rules)
        self.assertIn("&lt;b&gt;Подтверждённый факт&lt;/b&gt;", content)
        self.assertIn("&lt;script&gt;Фокус&lt;/script&gt;", content)
        for forbidden in (
            "client_id",
            "visit_id",
            "secret_rule_id",
            "metric.current",
            '"conditions"',
            "SYN-CLIENT-01",
            "SYN-VISIT",
        ):
            self.assertNotIn(forbidden, content)

    def test_v02_export_filename_utf8_and_overwrite_protection(self) -> None:
        model = build_analysis_v02(self.synthetic, "Демо-мастер А", 7)
        rules = apply_active_rules(model, self.empty_rules)
        self.assertTrue(safe_report_filename_v02(model).endswith(".html"))
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "отчёт v02.html"
            saved = export_html_v02(model, rules, destination)
            self.assertIn("Динамика показателя", saved.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                export_html_v02(model, rules, destination)
            export_html_v02(model, rules, destination, overwrite=True)


if __name__ == "__main__":
    unittest.main()
