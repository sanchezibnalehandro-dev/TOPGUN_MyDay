from __future__ import annotations

import ctypes
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .analytics import AnalysisError, AnalyticsModelV02
from .analytics import build_analysis_v02
from .config import COLORS
from .data_loader import InputDataError, LoadedDataset, load_file
from .metrics import MetricComparison, MetricProfile, MetricStatus
from .report import (
    CONTEXT_LABELS,
    PROFILE_LABELS,
    SYNTHETIC_WARNING,
    V02_KPI_LABELS,
    comparison_texts,
    export_html_v02,
    metric_value_text,
    neutral_fact_text,
    rule_section_texts,
    safe_report_filename_v02,
    unavailable_text,
)
from .rules import (
    RuleEngineResult,
    RulesConfigError,
    apply_active_rules,
    load_rules_config,
)


RULES_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "business_rules.json"
)
_KPI_IDS = tuple(V02_KPI_LABELS)


def _enable_windows_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_enable_windows_dpi_awareness()


class PeriodComparisonChart(tk.Canvas):
    """Нейтральное current/previous-сравнение без оценочной окраски."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=COLORS["panel"], highlightthickness=0, height=198)
        self._comparison: MetricComparison | None = None
        self.bind("<Configure>", lambda _event: self._draw())

    def set_comparison(self, comparison: MetricComparison) -> None:
        self._comparison = comparison
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 420)
        if self._comparison is None:
            return
        comparison = self._comparison
        if comparison.status != MetricStatus.AVAILABLE:
            self.create_text(
                width / 2,
                84,
                text="Сравнение пока недоступно",
                fill=COLORS["amber_soft"],
                font=("Segoe UI", 11, "bold"),
            )
            return
        current = float(comparison.current.value or 0)
        previous = float(comparison.previous.value or 0)
        scale = max(abs(current), abs(previous), 1.0)
        left, right = 26, width - 26
        bar_width = right - left
        rows = (
            ("ПРЕДЫДУЩИЙ ПЕРИОД", previous, 52, "#4C8DFF"),
            ("ТЕКУЩИЙ ПЕРИОД", current, 122, COLORS["amber"]),
        )
        for label, value, y, color in rows:
            self.create_text(
                left, y - 19, text=label, anchor="w", fill=COLORS["muted"],
                font=("Segoe UI", 8, "bold"),
            )
            self.create_rectangle(left, y, right, y + 18, fill="#0B182A", outline="")
            value_width = 0 if value == 0 else max(5, bar_width * abs(value) / scale)
            self.create_rectangle(left, y, left + value_width, y + 18, fill=color, outline="")


class MyDayApp(tk.Tk):
    PERIODS = {
        "Последний доступный день": 1,
        "Последние 7 дней": 7,
        "Последние 14 дней": 14,
    }
    ALL_BRANCHES = "Все филиалы"

    def __init__(self) -> None:
        super().__init__()
        self.title("TOPGUN · Мой день")
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(1180, max(760, screen_width - 80)), min(820, max(640, screen_height - 80))
        self.geometry(f"{width}x{height}+{max(0, (screen_width-width)//2)}+{max(0, (screen_height-height)//2)}")
        self.minsize(min(720, screen_width - 40), min(640, screen_height - 40))
        self.configure(bg=COLORS["bg"])

        self.dataset: LoadedDataset | None = None
        self.analysis: AnalyticsModelV02 | None = None
        self.rule_result: RuleEngineResult | None = None
        self.selected_metric_id = "average_check"
        self._wrappable: list[tk.Label] = []
        self._kpi_widgets: dict[str, dict[str, tk.Widget]] = {}
        self._context_widgets: dict[str, tuple[tk.Label, tk.Label]] = {}
        self._wide_layout: bool | None = None
        self._method_visible = False

        self._configure_styles()
        self._build_shell()
        self._build_content()
        self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.TCombobox", fieldbackground=COLORS["bg"], background=COLORS["panel_alt"], foreground=COLORS["text"], arrowcolor=COLORS["amber"], bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=8)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", COLORS["bg"])], foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])])
        style.configure("Amber.TButton", background=COLORS["amber"], foreground="#10141B", borderwidth=0, padding=(16, 10), font=("Segoe UI", 10, "bold"))
        style.map("Amber.TButton", background=[("active", COLORS["amber_soft"])])
        style.configure("Secondary.TButton", background=COLORS["panel_alt"], foreground=COLORS["text"], bordercolor=COLORS["line"], padding=(14, 9), font=("Segoe UI", 9, "bold"))

    def _build_shell(self) -> None:
        self.shell_canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.shell_canvas.yview)
        self.shell_canvas.configure(yscrollcommand=scrollbar.set)
        self.shell_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.content = tk.Frame(self.shell_canvas, bg=COLORS["bg"], padx=28, pady=18)
        self._window = self.shell_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _event: self.shell_canvas.configure(scrollregion=self.shell_canvas.bbox("all")))
        self.shell_canvas.bind("<Configure>", self._resize_content)

    def _resize_content(self, event: tk.Event) -> None:
        self.shell_canvas.itemconfigure(self._window, width=event.width)
        for label in self._wrappable:
            label.configure(wraplength=max(280, event.width - 92))
        self._apply_responsive_layout(event.width)
        self.after_idle(self._refresh_local_wraps)

    def _refresh_local_wraps(self) -> None:
        """Переносить длинное имя выбранного KPI по фактической ширине панели."""

        if hasattr(self, "comparison_panel"):
            width = self.comparison_panel.winfo_width()
            if width > 1:
                self.comparison_metric_title.configure(wraplength=max(120, width - 32))

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.shell_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _panel(self, master: tk.Misc, *, color: str | None = None, padding: int = 16) -> tk.Frame:
        return tk.Frame(master, bg=color or COLORS["panel"], highlightbackground=COLORS["line"], highlightthickness=1, padx=padding, pady=padding)

    def _soft_section(self, master: tk.Misc) -> tk.Frame:
        """Лёгкая текстовая секция для вторичных продуктовых состояний."""

        return tk.Frame(master, bg=COLORS["bg"], padx=8, pady=6)

    def _body(self, master: tk.Misc, text: str = "", *, color: str | None = None, font: tuple[object, ...] = ("Segoe UI", 10)) -> tk.Label:
        label = tk.Label(master, text=text, fg=color or COLORS["text"], bg=master.cget("bg"), font=font, anchor="w", justify="left")
        label.pack(fill="x")
        self._wrappable.append(label)
        return label

    def _section_title(self, master: tk.Misc, title: str, subtitle: str = "") -> None:
        tk.Label(master, text=title, fg=COLORS["text"], bg=master.cget("bg"), font=("Segoe UI", 15, "bold"), anchor="w").pack(fill="x")
        if subtitle:
            label = self._body(master, subtitle, color=COLORS["muted"], font=("Segoe UI", 9))
            label.pack_configure(pady=(3, 10))

    def _build_content(self) -> None:
        self.content.grid_columnconfigure(0, weight=1)
        self._build_header()
        self._build_file_controls()
        self._build_summary()
        self._build_kpis()
        self._build_dashboard()
        self._build_lower_sections()
        self._build_methodology()
        self._apply_responsive_layout(1180)

    def _build_header(self) -> None:
        header = tk.Frame(self.content, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text="TOPGUN · ВНУТРЕННИЙ ИНСТРУМЕНТ", fg=COLORS["amber"], bg=COLORS["bg"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Мой день", fg=COLORS["text"], bg=COLORS["bg"], font=("Segoe UI", 33, "bold")).grid(row=1, column=0, sticky="w")
        self.header_subtitle = tk.Label(
            header,
            text="Персональная динамика мастера за сопоставимые периоды",
            fg=COLORS["muted"],
            bg=COLORS["bg"],
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
        )
        self.header_subtitle.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._wrappable.append(self.header_subtitle)
        tk.Label(header, text="DEMO · ЛОКАЛЬНО", fg=COLORS["amber_soft"], bg="#302816", padx=12, pady=7, font=("Segoe UI", 9, "bold")).grid(row=0, column=1, rowspan=2, sticky="ne")

    def _build_file_controls(self) -> None:
        file_panel = self._panel(self.content, padding=12)
        file_panel.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        file_panel.grid_columnconfigure(1, weight=1)
        ttk.Button(file_panel, text="Выбрать XLSX / CSV", style="Amber.TButton", command=self._choose_file).grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 16))
        self.file_title = tk.Label(file_panel, text="Файл ещё не выбран", fg=COLORS["text"], bg=COLORS["panel"], font=("Segoe UI", 11, "bold"), anchor="w")
        self.file_title.grid(row=0, column=1, sticky="ew")
        self.file_status = tk.Label(file_panel, text="Загрузите выгрузку — данные останутся на этом компьютере.", fg=COLORS["muted"], bg=COLORS["panel"], font=("Segoe UI", 9), anchor="w", justify="left")
        self.file_status.grid(row=1, column=1, sticky="ew", pady=(5, 0))
        self._wrappable.append(self.file_status)

        controls = self._panel(self.content, padding=12)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        for column in range(3):
            controls.grid_columnconfigure(column, weight=1)
        self.barber_var, self.period_var, self.branch_var = tk.StringVar(), tk.StringVar(value="Последние 7 дней"), tk.StringVar(value=self.ALL_BRANCHES)
        self.barber_combo = self._control(controls, 0, "Барбер", self.barber_var)
        self.period_combo = self._control(controls, 1, "Период", self.period_var, tuple(self.PERIODS))
        self.branch_combo = self._control(controls, 2, "Филиал", self.branch_var, (self.ALL_BRANCHES,))
        actions = tk.Frame(controls, bg=COLORS["panel"])
        actions.grid(row=2, column=0, columnspan=3, sticky="e", pady=(8, 0))
        self.build_button = ttk.Button(actions, text="Собрать разбор", style="Amber.TButton", command=self._on_build, state="disabled")
        self.build_button.pack(side="left", padx=(0, 8))
        self.save_button = ttk.Button(actions, text="Сохранить отчёт", style="Secondary.TButton", command=self._save_report, state="disabled")
        self.save_button.pack(side="left")

    def _control(self, master: tk.Misc, column: int, title: str, variable: tk.StringVar, values: tuple[str, ...] = ()) -> ttk.Combobox:
        frame = tk.Frame(master, bg=COLORS["panel"])
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        tk.Label(frame, text=title.upper(), fg=COLORS["muted"], bg=COLORS["panel"], font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(0, 5))
        combo = ttk.Combobox(frame, textvariable=variable, values=values, state="disabled", style="Dark.TCombobox", font=("Segoe UI", 10))
        combo.pack(fill="x")
        return combo

    def _build_summary(self) -> None:
        panel = self._panel(self.content, color=COLORS["panel_alt"], padding=12)
        panel.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.summary_text = self._body(panel, "Выберите файл и соберите разбор.", font=("Segoe UI", 10, "bold"))
        self.synthetic_warning = self._body(panel, SYNTHETIC_WARNING, color=COLORS["amber_soft"], font=("Segoe UI", 9, "bold"))
        self.synthetic_warning.pack_forget()

    def _make_clickable(self, widget: tk.Widget, metric_id: str) -> None:
        widget.bind("<Button-1>", lambda _event, key=metric_id: self._select_metric(key))
        widget.bind("<Enter>", lambda _event: widget.configure(cursor="hand2"))
        widget.bind("<Leave>", lambda _event: widget.configure(cursor=""))

    def _build_kpis(self) -> None:
        self.kpi_grid = tk.Frame(self.content, bg=COLORS["bg"])
        self.kpi_grid.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        for column in range(2):
            self.kpi_grid.grid_columnconfigure(column, weight=1, uniform="kpi")
        for index, (metric_id, title) in enumerate(V02_KPI_LABELS.items()):
            row, column = divmod(index, 2)
            card = self._panel(self.kpi_grid, padding=14)
            card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 5 if column == 0 else 0), pady=(0 if row == 0 else 5, 5 if row == 0 else 0))
            title_label = tk.Label(card, text=title.upper(), fg=COLORS["muted"], bg=COLORS["panel"], font=("Segoe UI", 8, "bold"), anchor="w")
            title_label.pack(fill="x")
            current = self._body(card, "—", font=("Segoe UI", 21, "bold"))
            current.pack_configure(pady=(5, 1))
            previous = self._body(card, "Было: —", color=COLORS["muted"], font=("Segoe UI", 9))
            delta = self._body(card, "Изменение: —", font=("Segoe UI", 9, "bold"))
            status = self._body(card, "", color=COLORS["amber_soft"], font=("Segoe UI", 8, "bold"))
            for widget in (card, title_label, current, previous, delta, status):
                self._make_clickable(widget, metric_id)
            self._kpi_widgets[metric_id] = {"card": card, "current": current, "previous": previous, "delta": delta, "status": status}

    def _build_dashboard(self) -> None:
        self.dashboard = tk.Frame(self.content, bg=COLORS["bg"])
        self.dashboard.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        self.comparison_panel = self._panel(self.dashboard)
        self._section_title(self.comparison_panel, "Динамика показателя")
        self.comparison_instruction = self._body(
            self.comparison_panel,
            "Выберите KPI сверху, чтобы посмотреть одну динамику подробно.",
            color=COLORS["muted"],
            font=("Segoe UI", 9),
        )
        self.comparison_metric_title = self._body(self.comparison_panel, "Выберите показатель", font=("Segoe UI", 21, "bold"))
        self.comparison_window = self._body(self.comparison_panel, "—", color=COLORS["muted"], font=("Segoe UI", 10))
        self.comparison_chart = PeriodComparisonChart(self.comparison_panel)
        self.comparison_chart.pack(fill="x", pady=(8, 8))
        self.comparison_values = self._body(self.comparison_panel, "—", font=("Segoe UI", 11, "bold"))
        self.comparison_fact = self._body(self.comparison_panel, "", color=COLORS["muted"], font=("Segoe UI", 9))

        self.context_panel = self._panel(self.dashboard, color=COLORS["panel_alt"])
        self._section_title(self.context_panel, "Контекст периода")
        for metric_id, title in CONTEXT_LABELS.items():
            row = tk.Frame(self.context_panel, bg=COLORS["panel_alt"])
            row.pack(fill="x", pady=(0, 7))
            tk.Label(row, text=title, fg=COLORS["muted"], bg=COLORS["panel_alt"], font=("Segoe UI", 9), anchor="w").pack(side="left")
            value = tk.Label(row, text="—", fg=COLORS["text"], bg=COLORS["panel_alt"], font=("Segoe UI", 13, "bold"), anchor="e")
            value.pack(side="right")
            state = self._body(self.context_panel, "", color=COLORS["amber_soft"], font=("Segoe UI", 8))
            state.pack_configure(pady=(0, 5))
            self._context_widgets[metric_id] = (value, state)
        tk.Frame(self.context_panel, bg=COLORS["line"], height=1).pack(fill="x", pady=(5, 10))
        tk.Label(self.context_panel, text="ОРИЕНТИР", fg=COLORS["amber"], bg=COLORS["panel_alt"], font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
        self.orientir_text = self._body(self.context_panel, "Пока не задан. На этом этапе сравниваем мастера только с самим собой.", font=("Segoe UI", 9))

    def _build_lower_sections(self) -> None:
        self.lower = tk.Frame(self.content, bg=COLORS["bg"])
        self.lower.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        self.facts_panel = self._soft_section(self.lower)
        self._section_title(self.facts_panel, "Что изменилось")
        self.facts_text = self._body(self.facts_panel, "Выберите показатель, чтобы увидеть нейтральный факт.", font=("Segoe UI", 9))
        self.attention_panel = self._soft_section(self.lower)
        self._section_title(self.attention_panel, "Зона внимания")
        self.attention_text = self._body(self.attention_panel, "", font=("Segoe UI", 9))
        self.focus_panel = self._soft_section(self.lower)
        self._section_title(self.focus_panel, "Фокус")
        self.focus_text = self._body(self.focus_panel, "", font=("Segoe UI", 9))

    def _build_methodology(self) -> None:
        self.method_panel = self._panel(self.content, color="#0C192B", padding=12)
        self.method_panel.grid(row=7, column=0, sticky="ew")
        self.method_toggle = ttk.Button(self.method_panel, text="Показать методику и ограничения", style="Secondary.TButton", command=self._toggle_methodology)
        self.method_toggle.pack(anchor="w")
        self.method_text = self._body(self.method_panel, "", color=COLORS["muted"], font=("Segoe UI", 9))
        self.method_text.pack_forget()

    def _toggle_methodology(self) -> None:
        self._method_visible = not self._method_visible
        if self._method_visible:
            self.method_text.pack(fill="x", pady=(10, 0))
            self.method_toggle.configure(text="Скрыть методику и ограничения")
        else:
            self.method_text.pack_forget()
            self.method_toggle.configure(text="Показать методику и ограничения")

    def _apply_responsive_layout(self, width: int) -> None:
        wide = width >= 900
        if self._wide_layout == wide:
            return
        self._wide_layout = wide
        for widget in (self.comparison_panel, self.context_panel, self.facts_panel, self.attention_panel, self.focus_panel):
            widget.grid_forget()
        if wide:
            self.dashboard.grid_columnconfigure(0, weight=3)
            self.dashboard.grid_columnconfigure(1, weight=1)
            self.comparison_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
            self.context_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
            for column in range(3):
                self.lower.grid_columnconfigure(column, weight=1, uniform="lower")
            self.facts_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            self.attention_panel.grid(row=0, column=1, sticky="nsew", padx=4)
            self.focus_panel.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        else:
            self.dashboard.grid_columnconfigure(0, weight=1)
            self.comparison_panel.grid(row=0, column=0, sticky="ew", pady=(0, 5))
            self.context_panel.grid(row=1, column=0, sticky="ew", pady=(5, 0))
            for panel, row in ((self.facts_panel, 0), (self.attention_panel, 1), (self.focus_panel, 2)):
                panel.grid(row=row, column=0, sticky="ew", pady=(0 if row == 0 else 5, 5 if row != 2 else 0))

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="Выберите выгрузку TOPGUN", filetypes=(("Таблицы", "*.xlsx *.csv"), ("Excel", "*.xlsx"), ("CSV", "*.csv")))
        if path:
            self.load_path(path)

    def load_path(self, path: str | Path) -> None:
        try:
            dataset = load_file(path)
        except (InputDataError, OSError) as exc:
            self.file_title.configure(text="Файл не загружен", fg=COLORS["red"])
            self.file_status.configure(text=str(exc))
            if self.state() != "withdrawn":
                messagebox.showerror("Не удалось прочитать файл", str(exc), parent=self)
            return
        self.dataset = dataset
        self.file_title.configure(text=dataset.source_path.name, fg=COLORS["text"])
        sheet = f" · лист «{dataset.sheet_name}»" if dataset.sheet_name else ""
        self.file_status.configure(text=f"{dataset.row_count} строк · барберов: {len(dataset.barbers)} · {dataset.date_min:%d.%m.%Y}–{dataset.date_max:%d.%m.%Y}{sheet}")
        self.barber_combo.configure(values=dataset.barbers, state="readonly")
        self.barber_var.set(dataset.barbers[0])
        self.period_combo.configure(state="readonly")
        if dataset.branches:
            self.branch_combo.configure(values=(self.ALL_BRANCHES,) + dataset.branches, state="readonly")
        else:
            self.branch_combo.configure(values=(self.ALL_BRANCHES,), state="disabled")
        self.branch_var.set(self.ALL_BRANCHES)
        self.build_button.configure(state="normal")
        self._on_build()

    def _on_build(self) -> None:
        if self.dataset is None:
            return
        self.analysis, self.rule_result = None, None
        self.save_button.configure(state="disabled")
        try:
            analysis = build_analysis_v02(self.dataset, self.barber_var.get(), self.PERIODS[self.period_var.get()], None if self.branch_var.get() == self.ALL_BRANCHES else self.branch_var.get())
            rules = apply_active_rules(analysis, load_rules_config(RULES_CONFIG_PATH))
        except (AnalysisError, KeyError, RulesConfigError) as exc:
            if self.state() != "withdrawn":
                messagebox.showwarning("Разбор не собран", str(exc), parent=self)
            return
        self.analysis, self.rule_result = analysis, rules
        self._apply_analysis()
        self.save_button.configure(state="normal")

    def _short_status(self, comparison: MetricComparison) -> str:
        if comparison.status == MetricStatus.DATA_GAP:
            return "Показатель пока не подтверждён"
        if comparison.status == MetricStatus.INSUFFICIENT_DATA:
            return "Недостаточно данных для сравнения"
        return ""

    def _apply_analysis(self) -> None:
        if self.analysis is None or self.rule_result is None:
            return
        model = self.analysis
        window = model.period.window
        branch = model.branch or self.ALL_BRANCHES
        self.summary_text.configure(text=f"Мастер: {model.barber} · Филиал: {branch} · {PROFILE_LABELS[model.profile]} · {window.current_start:%d.%m}–{window.current_end:%d.%m} / {window.previous_start:%d.%m}–{window.previous_end:%d.%m}")
        if model.profile == MetricProfile.SYNTHETIC_V02:
            self.synthetic_warning.pack(fill="x", pady=(7, 0))
        else:
            self.synthetic_warning.pack_forget()
        self.comparison_instruction.pack_forget()
        for metric_id in _KPI_IDS:
            comparison = model.period.comparisons[metric_id]
            current, previous, delta, _reason = comparison_texts(comparison)
            widgets = self._kpi_widgets[metric_id]
            is_available = comparison.status == MetricStatus.AVAILABLE
            widgets["current"].configure(text=current if is_available else "Н/Д")
            widgets["previous"].configure(text=f"Было: {previous}" if is_available else "")
            widgets["delta"].configure(text=f"Изменение: {delta}" if is_available else "")
            widgets["status"].configure(text=self._short_status(comparison))
        context = {"visits_count": model.context.visits_count, "unique_clients_count": model.context.unique_clients_count, "revenue_total": model.context.revenue_total}
        for metric_id, result in context.items():
            value, state = self._context_widgets[metric_id]
            value.configure(text=metric_value_text(result))
            state.configure(text=unavailable_text(result.status, result.reason) if result.status != MetricStatus.AVAILABLE else "")
        self.orientir_text.configure(text="Пока не задан. На этом этапе сравниваем мастера только с самим собой.")
        attention, focus = rule_section_texts(self.rule_result)
        self.attention_text.configure(text="\n".join(f"• {item}" for item in attention))
        self.focus_text.configure(text="\n".join(f"• {item}" for item in focus))
        self._update_methodology()
        self._select_metric(self.selected_metric_id)

    def _select_metric(self, metric_id: str) -> None:
        if self.analysis is None or metric_id not in _KPI_IDS:
            return
        self.selected_metric_id = metric_id
        for key, widgets in self._kpi_widgets.items():
            active = key == metric_id
            card = widgets["card"]
            card.configure(highlightbackground=COLORS["amber"] if active else COLORS["line"], highlightthickness=2 if active else 1, bg=COLORS["panel_alt"] if active else COLORS["panel"])
            for name in ("current", "previous", "delta", "status"):
                widgets[name].configure(bg=card.cget("bg"))
            for child in card.winfo_children():
                child.configure(bg=card.cget("bg"))
        comparison = self.analysis.period.comparisons[metric_id]
        current, previous, delta, _reason = comparison_texts(comparison)
        self.comparison_metric_title.configure(text=V02_KPI_LABELS[metric_id])
        if comparison.status == MetricStatus.AVAILABLE:
            self.comparison_window.configure(text=f"Текущий: {current} · Предыдущий: {previous} · Изменение: {delta}")
        else:
            self.comparison_window.configure(text="Сравнение пока недоступно")
        self.comparison_chart.set_comparison(comparison)
        fact = next(item for item in self.analysis.neutral_facts if item.metric_id == metric_id)
        if comparison.status == MetricStatus.AVAILABLE:
            self.comparison_values.configure(text=f"Текущий период: {current}   |   Предыдущий период: {previous}")
            self.comparison_fact.configure(text=neutral_fact_text(fact))
        elif comparison.status == MetricStatus.DATA_GAP:
            self.comparison_values.configure(text="Показатель не поддерживается текущим форматом данных.")
            self.comparison_fact.configure(text="")
        else:
            self.comparison_values.configure(text="Данных выбранного периода недостаточно для сравнения.")
            self.comparison_fact.configure(text="")
        available_facts = tuple(
            neutral_fact_text(item)
            for item in self.analysis.neutral_facts
            if item.status == MetricStatus.AVAILABLE
        )
        self.facts_text.configure(
            text="\n".join(f"• {item}" for item in available_facts)
            or "Доступных изменений для описания пока нет."
        )

    def _update_methodology(self) -> None:
        if self.analysis is None:
            return
        model = self.analysis
        window = model.period.window
        limitations: list[str] = []
        for metric_id in _KPI_IDS:
            comparison = model.period.comparisons[metric_id]
            if comparison.status != MetricStatus.AVAILABLE:
                limitations.append(f"{V02_KPI_LABELS[metric_id]}: {unavailable_text(comparison.status, comparison.reason)}")
        limitation_text = "\n".join(f"• {item}" for item in limitations) or "• Ограничений для показанных метрик нет."
        synthetic = f"\n{SYNTHETIC_WARNING}" if model.profile == MetricProfile.SYNTHETIC_V02 else ""
        sheet = f" · лист «{model.sheet_name}»" if model.sheet_name else ""
        self.method_text.configure(text=(f"Источник: {model.source_name}{sheet} · {model.source_rows} строк.\nПрофиль: {PROFILE_LABELS[model.profile]}.\nПериоды: {window.current_start:%d.%m.%Y}–{window.current_end:%d.%m.%Y} и {window.previous_start:%d.%m.%Y}–{window.previous_end:%d.%m.%Y}.\nОграничения:\n{limitation_text}{synthetic}"))

    def _save_report(self) -> None:
        if self.analysis is None or self.rule_result is None:
            return
        output_dir = Path(__file__).resolve().parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(title="Сохранить отчёт", initialdir=output_dir, initialfile=safe_report_filename_v02(self.analysis), defaultextension=".html", filetypes=(("Печатный HTML", "*.html"),))
        if not path:
            return
        destination = Path(path)
        overwrite = False
        if destination.exists():
            overwrite = messagebox.askyesno("Файл уже существует", f"Перезаписать файл?\n{destination}", parent=self)
            if not overwrite:
                return
        try:
            saved = export_html_v02(self.analysis, self.rule_result, destination, overwrite=overwrite)
        except OSError as exc:
            messagebox.showerror("Не удалось сохранить отчёт", str(exc), parent=self)
            return
        messagebox.showinfo("Отчёт сохранён", f"Файл создан:\n{saved}", parent=self)
