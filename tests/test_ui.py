from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from topgun_myday.metrics import MetricProfile
from topgun_myday.report import SYNTHETIC_WARNING
from topgun_myday.rules import (
    BusinessLogicStatus,
    RuleEngineResult,
    RuleResult,
    RuleStatus,
)
from topgun_myday.ui import MyDayApp


def widget_texts(widget: tk.Misc) -> tuple[str, ...]:
    values: list[str] = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
        except tk.TclError:
            text = ""
        if text:
            values.append(str(text))
        values.extend(widget_texts(child))
    return tuple(values)


class UiV02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MyDayApp()
        self.app.withdraw()

    def tearDown(self) -> None:
        if self.app.winfo_exists():
            self.app.destroy()

    def test_synthetic_dashboard_has_compact_v02_sections(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        self.app.update_idletasks()
        self.assertEqual(self.app.analysis.profile, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(self.app.synthetic_warning.cget("text"), SYNTHETIC_WARNING)
        self.assertEqual(self.app.synthetic_warning.winfo_manager(), "pack")
        self.assertEqual(self.app.selected_metric_id, "average_check")
        self.assertEqual(self.app.comparison_metric_title.cget("text"), "Средний чек")
        self.assertEqual(set(self.app._kpi_widgets), {
            "average_check",
            "extra_service_visit_share",
            "product_sales",
            "prebooking_rate",
        })
        all_text = "\n".join(widget_texts(self.app))
        for section in (
            "Динамика показателя",
            "Контекст периода",
            "ОРИЕНТИР",
            "Что изменилось",
            "Зона внимания",
            "Фокус",
            "Показать методику и ограничения",
        ):
            self.assertIn(section, all_text)
        self.assertNotIn("Твоя динамика", all_text)
        self.assertNotIn("Сравнение периодов", all_text)
        self.assertEqual(self.app.comparison_instruction.winfo_manager(), "")
        self.assertNotIn("Значение доступно", all_text)
        self.assertIn("Правила интерпретации ещё не подтверждены", self.app.attention_text.cget("text"))
        self.assertIn("Появится после подключения первого ACTIVE-правила", self.app.focus_text.cget("text"))

    def test_each_kpi_card_selects_its_own_comparison(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        for metric_id, title in (
            ("average_check", "Средний чек"),
            ("extra_service_visit_share", "Визиты с допуслугой"),
            ("product_sales", "Продажи товаров"),
            ("prebooking_rate", "Предварительная запись"),
        ):
            with self.subTest(metric_id=metric_id):
                self.app._kpi_widgets[metric_id]["card"].event_generate("<Button-1>")
                self.app.update_idletasks()
                self.assertEqual(self.app.selected_metric_id, metric_id)
                self.assertEqual(self.app.comparison_metric_title.cget("text"), title)
                self.assertIs(
                    self.app.comparison_chart._comparison,
                    self.app.analysis.period.comparisons[metric_id],
                )
                self.assertIn(title, self.app.facts_text.cget("text"))

    def test_legacy_data_gap_is_short_in_card_and_full_in_methodology(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun.xlsx")
        self.app.update_idletasks()
        self.assertEqual(self.app.analysis.profile, MetricProfile.LEGACY_V01)
        widgets = self.app._kpi_widgets["extra_service_visit_share"]
        self.assertEqual(widgets["current"].cget("text"), "Н/Д")
        self.assertIn("Показатель пока не подтверждён", widgets["status"].cget("text"))
        self.assertIn("Формула / поле ещё не подтверждены", self.app.method_text.cget("text"))
        self.assertEqual(self.app.synthetic_warning.winfo_manager(), "")
        self.app._select_metric("extra_service_visit_share")
        self.assertEqual(self.app.comparison_window.cget("text"), "Сравнение пока недоступно")
        self.assertEqual(
            self.app.comparison_values.cget("text"),
            "Показатель не поддерживается текущим форматом данных.",
        )
        self.assertEqual(self.app.comparison_fact.cget("text"), "")
        self.assertNotIn("дополнительной услуги", self.app.comparison_values.cget("text"))
        self.assertNotIn("Визиты с допуслугой", self.app.facts_text.cget("text"))

    def test_secondary_sections_are_visually_lightweight(self) -> None:
        self.assertEqual(int(self.app.facts_panel.cget("highlightthickness")), 0)
        self.assertEqual(int(self.app.attention_panel.cget("highlightthickness")), 0)
        self.assertEqual(int(self.app.focus_panel.cget("highlightthickness")), 0)

    def test_active_rule_text_and_nullable_recommendation_are_presented(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        mixed = RuleEngineResult(
            BusinessLogicStatus.EVALUATED,
            (
                RuleResult("one", RuleStatus.ACTIVE, True, True, "Первое", None, "ok"),
                RuleResult("two", RuleStatus.ACTIVE, True, True, "Второе", "Фокус 2", "ok"),
            ),
            None,
        )
        self.app.rule_result = mixed
        self.app._apply_analysis()
        self.assertEqual(self.app.attention_text.cget("text"), "• Первое\n• Второе")
        self.assertEqual(self.app.focus_text.cget("text"), "• Фокус 2")

        only_null = RuleEngineResult(
            BusinessLogicStatus.EVALUATED,
            (RuleResult("one", RuleStatus.ACTIVE, True, True, "Первое", None, "ok"),),
            None,
        )
        self.app.rule_result = only_null
        self.app._apply_analysis()
        self.assertEqual(
            self.app.focus_text.cget("text"),
            "• Рекомендация для сработавших правил пока не настроена.",
        )

    def test_invalid_rules_config_clears_previous_analysis(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        with tempfile.TemporaryDirectory() as folder:
            invalid = Path(folder) / "rules.json"
            invalid.write_text("{broken", encoding="utf-8")
            with patch("topgun_myday.ui.RULES_CONFIG_PATH", invalid):
                self.app._on_build()
        self.assertIsNone(self.app.analysis)
        self.assertIsNone(self.app.rule_result)
        self.assertEqual(str(self.app.save_button.cget("state")), "disabled")

    def test_narrow_window_stacks_dashboard_without_horizontal_overflow(self) -> None:
        self.app.deiconify()
        self.app.geometry("760x720")
        self.app.update()
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        self.app.update()
        self.assertFalse(self.app._wide_layout)
        self.assertLessEqual(self.app.content.winfo_width(), self.app.shell_canvas.winfo_width() + 1)
        self.assertEqual(self.app.comparison_panel.grid_info()["row"], 0)
        self.assertEqual(self.app.context_panel.grid_info()["row"], 1)
        self.assertGreater(self.app.shell_canvas.bbox("all")[3], 720)
        self.app.withdraw()

    def test_methodology_is_collapsible(self) -> None:
        self.app.load_path(Path("data") / "demo_topgun_v02.xlsx")
        self.assertEqual(self.app.method_text.winfo_manager(), "")
        self.app._toggle_methodology()
        self.assertEqual(self.app.method_text.winfo_manager(), "pack")
        self.app._toggle_methodology()
        self.assertEqual(self.app.method_text.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
