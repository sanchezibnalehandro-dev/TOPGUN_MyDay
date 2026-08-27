from __future__ import annotations

import html
import os
import re
import tempfile
from pathlib import Path

from .analytics import (
    AnalyticsModelV02,
    ChangeDirection,
    ComparisonItem,
    NeutralFact,
    ReportModel,
)
from .metrics import MetricComparison, MetricProfile, MetricResult, MetricStatus, MetricUnit
from .rules import BusinessLogicStatus, RuleEngineResult


SYNTHETIC_WARNING = (
    'SYNTHETIC DEMO DATA. Структура и формулы не являются моделью аналитики “Заботы”.'
)

V02_KPI_LABELS = {
    "average_check": "Средний чек",
    "extra_service_visit_share": "Визиты с допуслугой",
    "product_sales": "Продажи товаров",
    "prebooking_rate": "Предварительная запись",
}

CONTEXT_LABELS = {
    "visits_count": "Визиты",
    "unique_clients_count": "Уникальные клиенты",
    "revenue_total": "Выручка",
}

PROFILE_LABELS = {
    MetricProfile.LEGACY_V01: "Демонстрационный формат v0.1",
    MetricProfile.SYNTHETIC_V02: "Синтетический демо-набор v0.2",
}


def _rubles(value: float | None) -> str:
    if value is None:
        return "Нет данных"
    return f"{round(value):,} ₽".replace(",", " ")


def _percent(value: float | None) -> str:
    if value is None:
        return "Нет данных"
    return f"{value * 100:.1f}%".replace(".", ",")


def _rating(value: float | None) -> str:
    if value is None:
        return "Нет данных"
    return f"{value:.1f}".replace(".", ",")


def _metric_value(item: ComparisonItem) -> str:
    if item.key == "next_booking_rate":
        return _percent(item.current)
    return _rubles(item.current)


def _delta(item: ComparisonItem) -> tuple[str, str]:
    if item.delta is None:
        return "Недостаточно данных", "neutral"
    sign = "+" if item.delta > 0 else "−" if item.delta < 0 else ""
    text = f"{sign}{abs(item.delta) * 100:.1f}%".replace(".", ",")
    css = "positive" if item.delta >= 0.05 else "negative" if item.delta <= -0.05 else "neutral"
    return text, css


def _comparison_html(items: tuple[ComparisonItem, ...], suffix: str) -> str:
    cards: list[str] = []
    for item in items:
        delta_text, css = _delta(item)
        cards.append(
            '<div class="compare-item">'
            f'<div class="compare-name">{html.escape(item.label)}</div>'
            f'<div class="compare-value">{html.escape(_metric_value(item))}</div>'
            f'<div class="delta {css}">{html.escape(delta_text)} {html.escape(suffix)}</div>'
            "</div>"
        )
    return "".join(cards)


def _chart_svg(report: ReportModel) -> str:
    points = report.daily_revenue
    width, height, pad = 760, 220, 30
    maximum = max((value for _, value in points), default=0.0)
    scale_max = maximum * 1.1 if maximum else 1.0
    xy: list[tuple[float, float]] = []
    for index, (_, value) in enumerate(points):
        x = width / 2 if len(points) == 1 else pad + index * (width - 2 * pad) / (len(points) - 1)
        y = height - pad - value / scale_max * (height - 2 * pad)
        xy.append((x, y))
    path = " ".join(
        ("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}"
        for index, (x, y) in enumerate(xy)
    )
    area = ""
    if xy:
        area = f'{path} L {xy[-1][0]:.1f} {height-pad} L {xy[0][0]:.1f} {height-pad} Z'
    grids = "".join(
        f'<line x1="{pad}" x2="{width-pad}" y1="{pad + i*(height-2*pad)/3:.1f}" '
        f'y2="{pad + i*(height-2*pad)/3:.1f}" class="grid"/>'
        for i in range(4)
    )
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in xy)
    label_indexes = sorted({0, max(0, (len(points) - 1) // 2), max(0, len(points) - 1)})
    labels = "".join(
        f'<text x="{xy[i][0]:.1f}" y="{height-6}" text-anchor="middle">'
        f'{points[i][0].strftime("%d.%m")}</text>'
        for i in label_indexes
        if points
    )
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="Динамика выручки">
    <defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#FFB31F" stop-opacity=".28"/><stop offset="100%" stop-color="#FFB31F" stop-opacity="0"/></linearGradient></defs>
    {grids}<path d="{area}" class="area"/><path d="{path}" class="chart-line"/>{dots}{labels}
    </svg>"""


def render_html(report: ReportModel) -> str:
    branch = report.branch or "Все филиалы"
    sheet = f" · лист {report.sheet_name}" if report.sheet_name else ""
    metrics = report.metrics
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOPGUN · Мой день · {html.escape(report.barber)}</title>
<style>
:root{{--bg:#08111f;--panel:#10213a;--panel2:#132946;--line:#263f61;--text:#f4f7fb;--muted:#91a4be;--amber:#ffb31f;--green:#47d28b;--red:#ff6b6b}}
*{{box-sizing:border-box}} body{{margin:0;background:#08111f;color:var(--text);font-family:"Segoe UI",Arial,sans-serif}}
.shell{{max-width:1320px;margin:auto;padding:32px}} header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:24px}}
.eyebrow{{color:var(--amber);font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:12px}} h1{{font-size:42px;margin:6px 0 8px}} .lead,.muted{{color:var(--muted)}}
.demo{{border:1px solid #765b20;background:#302816;color:#ffd168;border-radius:999px;padding:9px 12px;font-weight:800;font-size:12px}}
.meta{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}} .meta span{{border:1px solid #263f61;border-radius:999px;padding:7px 10px;color:var(--muted);font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}} .card,.insight,.focus{{background:linear-gradient(180deg,#132946,#0d1d32);border:1px solid #263f61;border-radius:18px;padding:18px}}
.kpi-label,.compare-name{{color:var(--muted);font-size:12px;text-transform:uppercase;font-weight:800;letter-spacing:.06em}} .kpi-value{{font-size:27px;font-weight:900;margin-top:7px}}
.layout{{display:grid;grid-template-columns:1.3fr .7fr;gap:16px;margin-top:16px}} .stack{{display:grid;gap:16px}} h2{{font-size:22px;margin:0 0 14px}} h3{{margin:0 0 8px}} p{{line-height:1.5;margin:0;color:#dce7f5}}
.compare-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}} .compare-item{{background:#11243d;border:1px solid #263f61;border-radius:14px;padding:14px}} .compare-value{{font-size:20px;font-weight:850;margin-top:6px}}
.delta{{font-weight:800;font-size:13px;margin-top:6px}} .positive{{color:var(--green)}} .negative{{color:var(--red)}} .neutral{{color:var(--muted)}}
.strength{{border-color:#24684d}} .attention{{border-color:#765b20}} .focus{{border-color:#765b20}} .focus small{{color:var(--amber);font-weight:900;text-transform:uppercase;letter-spacing:.1em}} .focus h2{{font-size:27px;margin:8px 0}}
svg{{width:100%;height:240px}} .grid{{stroke:#203653;stroke-width:1}} .area{{fill:url(#area)}} .chart-line{{fill:none;stroke:var(--amber);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}} circle{{fill:#08111f;stroke:var(--amber);stroke-width:2}} text{{fill:#91a4be;font-size:11px}}
footer{{margin-top:18px;border-top:1px solid #263f61;padding-top:16px;color:var(--muted);font-size:12px}}
@media(max-width:1000px){{.kpis{{grid-template-columns:repeat(3,1fr)}}.layout{{grid-template-columns:1fr}}}} @media(max-width:620px){{.shell{{padding:18px}}header{{display:block}}.demo{{display:inline-block;margin-top:12px}}.kpis,.compare-grid{{grid-template-columns:1fr}}}}
@media print{{body{{background:white;color:#12233b}}.shell{{max-width:none;padding:12mm}}.card,.insight,.focus,.compare-item{{background:white;color:#12233b;border-color:#ccd5e0;break-inside:avoid}}p{{color:#26384d}}.lead,.muted,.kpi-label,.compare-name,footer{{color:#51627a}}}}
</style></head><body><main class="shell">
<header><div><div class="eyebrow">TOPGUN · внутренний инструмент</div><h1>Мой день</h1><div class="lead">Персональный разбор результатов барбера: факты → сравнение → один понятный фокус.</div></div><div class="demo">DEMO · локальный отчёт</div></header>
<div class="meta"><span>{html.escape(report.barber)}</span><span>{report.current_start:%d.%m.%Y} → {report.current_end:%d.%m.%Y}</span><span>{html.escape(branch)}</span><span>{metrics.visits} визитов</span></div>
<section class="kpis">
<div class="card"><div class="kpi-label">Визиты</div><div class="kpi-value">{metrics.visits}</div></div>
<div class="card"><div class="kpi-label">Выручка</div><div class="kpi-value">{_rubles(metrics.revenue)}</div></div>
<div class="card"><div class="kpi-label">Средний чек</div><div class="kpi-value">{_rubles(metrics.average_check)}</div></div>
<div class="card"><div class="kpi-label">Следующая запись</div><div class="kpi-value">{_percent(metrics.next_booking_rate)}</div></div>
<div class="card"><div class="kpi-label">Товары / визит</div><div class="kpi-value">{_rubles(metrics.product_per_visit)}</div></div>
<div class="card"><div class="kpi-label">Оценка</div><div class="kpi-value">{_rating(metrics.average_rating)}</div></div>
</section>
<section class="layout"><div class="stack">
<div class="card"><h2>Динамика выручки по дням</h2>{_chart_svg(report)}</div>
<div class="card"><h2>Сравнение с собой</h2><div class="compare-grid">{_comparison_html(report.self_comparison, 'к предыдущему периоду')}</div></div>
<div class="card"><h2>Сравнение с командой</h2><div class="compare-grid">{_comparison_html(report.team_comparison, 'к среднему команды')}</div></div>
</div><aside class="stack">
<div class="insight strength"><h3>{html.escape(report.insights.strength_title)}</h3><p>{html.escape(report.insights.strength_text)}</p></div>
<div class="insight attention"><h3>{html.escape(report.insights.attention_title)}</h3><p>{html.escape(report.insights.attention_text)}</p></div>
<div class="focus"><small>Фокус на следующий день</small><h2>{html.escape(report.insights.focus_title)}</h2><p>{html.escape(report.insights.focus_text)}</p></div>
</aside></section>
<footer>Источник: {html.escape(report.source_name)}{html.escape(sheet)} · {report.source_rows} строк. Расчёт локальный, без внешних сервисов. Отчёт оценивает показатели, а не личность сотрудника.</footer>
</main></body></html>"""


def safe_report_filename(report: ReportModel) -> str:
    barber = re.sub(r'[<>:"/\\|?*]+', "_", report.barber).strip(" .") or "барбер"
    barber = re.sub(r"\s+", "_", barber)
    return (
        f"TOPGUN_Мой_день_{barber}_{report.current_start.isoformat()}_"
        f"{report.current_end.isoformat()}.html"
    )


def export_html(
    report: ReportModel, path: str | Path, *, overwrite: bool = False
) -> Path:
    return _write_html(render_html(report), path, overwrite=overwrite)


def _write_html(content: str, path: str | Path, *, overwrite: bool) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Файл уже существует: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".tmp", dir=destination.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination.resolve()


def _decimal(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _signed(value: float, formatted_absolute: str) -> str:
    if value > 0:
        return f"+{formatted_absolute}"
    if value < 0:
        return f"−{formatted_absolute}"
    return formatted_absolute


def human_reason(reason: str | None) -> str:
    """Скрыть внутренние имена полей, сохранив смысл причины."""

    if not reason:
        return ""
    replacements = {
        "extra_service_visit_share": "доля визитов с допуслугой",
        "prebooking_rate": "предварительная запись",
        "average_check": "средний чек",
        "product_sales": "продажи товаров",
        "revenue_total": "выручка",
        "unique_clients_count": "уникальные клиенты",
        "visits_count": "визиты",
        "service_revenue": "выручка от услуг",
        "product_revenue": "продажи товаров",
        "has_extra_service": "признак дополнительной услуги",
        "prebooked": "признак предварительной записи",
        "next_booking": "признак следующей записи",
        "client_id": "идентификатор клиента",
        "visit_id": "идентификатор визита",
    }
    result = reason
    for technical, friendly in replacements.items():
        result = result.replace(f"`{technical}`", friendly)
        result = result.replace(technical, friendly)
    return result.replace("`", "")


def metric_value_text(result: MetricResult) -> str:
    if result.status != MetricStatus.AVAILABLE or result.value is None:
        return "—"
    value = float(result.value)
    if result.unit == MetricUnit.RUB:
        return _rubles(value)
    if result.unit == MetricUnit.SHARE:
        return _percent(value)
    if value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    return _decimal(value)


def unavailable_text(status: MetricStatus, reason: str | None) -> str:
    if status == MetricStatus.DATA_GAP:
        title = "Формула / поле ещё не подтверждены"
    elif status == MetricStatus.INSUFFICIENT_DATA:
        title = "Недостаточно данных"
    else:
        return ""
    friendly = human_reason(reason)
    return f"{title}. {friendly}" if friendly else title


def comparison_texts(comparison: MetricComparison) -> tuple[str, str, str, str]:
    current = metric_value_text(comparison.current)
    previous = metric_value_text(comparison.previous)
    if comparison.status != MetricStatus.AVAILABLE:
        return current, previous, "—", unavailable_text(
            comparison.status, comparison.reason
        )

    if comparison.unit == MetricUnit.SHARE:
        delta = comparison.percentage_point_delta
        if delta is None:
            return current, previous, "—", "Недостаточно данных"
        delta_text = _signed(delta, f"{_decimal(abs(delta))} п.п.")
        return current, previous, delta_text, ""

    delta = comparison.absolute_delta
    if delta is None:
        return current, previous, "—", "Недостаточно данных"
    if comparison.unit == MetricUnit.RUB:
        absolute = _rubles(abs(float(delta)))
    else:
        absolute = f"{abs(float(delta)):,.0f}".replace(",", " ")
    delta_text = _signed(float(delta), absolute)
    if comparison.relative_delta_percent is None:
        delta_text += " · Относительное изменение не рассчитывается"
    else:
        relative = comparison.relative_delta_percent
        delta_text += " · " + _signed(relative, f"{_decimal(abs(relative))}%")
    return current, previous, delta_text, ""


def neutral_fact_text(fact: NeutralFact) -> str:
    label = V02_KPI_LABELS[fact.metric_id]
    if fact.status != MetricStatus.AVAILABLE:
        return f"{label}: {unavailable_text(fact.status, fact.reason)}."
    _current, _previous, delta, _state = comparison_texts(fact.comparison)
    directions = {
        ChangeDirection.INCREASED: "вырос",
        ChangeDirection.DECREASED: "снизился",
        ChangeDirection.UNCHANGED: "не изменился",
        ChangeDirection.UNAVAILABLE: "недоступен для сравнения",
    }
    direction = directions[fact.direction]
    if fact.direction in (ChangeDirection.UNCHANGED, ChangeDirection.UNAVAILABLE):
        return f"{label} {direction}."
    ending = "" if delta.endswith(".") else "."
    return f"{label} {direction}. Изменение: {delta}{ending}"


def rule_section_texts(result: RuleEngineResult) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if result.status == BusinessLogicStatus.BUSINESS_LOGIC_NOT_CONFIGURED:
        return (
            (
                "Правила интерпретации ещё не подтверждены. "
                "Система не делает оценочных выводов автоматически.",
            ),
            ("Появится после подключения первого ACTIVE-правила.",),
        )
    fired = tuple(item for item in result.results if item.fired)
    if not fired:
        return (
            ("Подтверждённые условия не сформировали зону внимания за выбранный период.",),
            ("Фокус за выбранный период не сформирован.",),
        )
    interpretations = tuple(
        item.interpretation for item in fired if item.interpretation
    )
    recommendations = tuple(
        item.recommendation for item in fired if item.recommendation
    )
    if not recommendations:
        recommendations = (
            "Рекомендация для сработавших правил пока не настроена.",
        )
    return interpretations, recommendations


def _list_html(items: tuple[str, ...]) -> str:
    return '<ul class="plain-list">' + "".join(
        f"<li>{html.escape(item)}</li>" for item in items
    ) + "</ul>"


def _v02_metric_card(metric_id: str, comparison: MetricComparison) -> str:
    current, previous, delta, state = comparison_texts(comparison)
    unavailable = comparison.status != MetricStatus.AVAILABLE
    status_html = (
        '<div class="state-short">Показатель пока не подтверждён</div>'
        if comparison.status == MetricStatus.DATA_GAP
        else '<div class="state-short">Недостаточно данных для сравнения</div>'
        if comparison.status == MetricStatus.INSUFFICIENT_DATA
        else ""
    )
    return (
        '<article class="card kpi">'
        f'<div class="kpi-label">{html.escape(V02_KPI_LABELS[metric_id])}</div>'
        f'<div class="kpi-value">{html.escape("Н/Д" if unavailable else current)}</div>'
        + (f'<div class="previous">Было: {html.escape(previous)}</div><div class="delta-v02">Изменение: {html.escape(delta)}</div>' if not unavailable else "")
        + f"{status_html}</article>"
    )


def _render_html_v02_legacy_layout(model: AnalyticsModelV02, rule_result: RuleEngineResult) -> str:
    profile = PROFILE_LABELS[model.profile]
    branch = model.branch or "Все филиалы"
    window = model.period.window
    synthetic = (
        f'<div class="synthetic">{html.escape(SYNTHETIC_WARNING)}</div>'
        if model.profile == MetricProfile.SYNTHETIC_V02
        else ""
    )
    kpis = "".join(
        _v02_metric_card(metric_id, model.period.comparisons[metric_id])
        for metric_id in V02_KPI_LABELS
    )
    dynamic_rows: list[str] = []
    for metric_id in V02_KPI_LABELS:
        current, previous, delta, state = comparison_texts(
            model.period.comparisons[metric_id]
        )
        state_html = f"<small>{html.escape(state)}</small>" if state else ""
        dynamic_rows.append(
            '<div class="dynamic-row">'
            f'<strong>{html.escape(V02_KPI_LABELS[metric_id])}</strong>'
            f'<span>{html.escape(current)}</span>'
            f'<span class="muted">было {html.escape(previous)}</span>'
            f'<span>{html.escape(delta)}</span>{state_html}</div>'
        )
    dynamics = "".join(dynamic_rows)
    context_results = {
        "visits_count": model.context.visits_count,
        "unique_clients_count": model.context.unique_clients_count,
        "revenue_total": model.context.revenue_total,
    }
    context_parts: list[str] = []
    for metric_id, result in context_results.items():
        state_html = (
            f'<div class="state">{html.escape(unavailable_text(result.status, result.reason))}</div>'
            if result.status != MetricStatus.AVAILABLE
            else ""
        )
        context_parts.append(
            '<article class="card context-card">'
            f'<div class="kpi-label">{html.escape(CONTEXT_LABELS[metric_id])}</div>'
            f'<div class="context-value">{html.escape(metric_value_text(result))}</div>'
            f"{state_html}</article>"
        )
    context = "".join(context_parts)
    facts = _list_html(tuple(neutral_fact_text(fact) for fact in model.neutral_facts))
    attention, focus = rule_section_texts(rule_result)
    sheet = f" · лист «{model.sheet_name}»" if model.sheet_name else ""
    method_warning = (
        f"<p>{html.escape(SYNTHETIC_WARNING)}</p>" if synthetic else ""
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOPGUN · Мой день · {html.escape(model.barber)}</title>
<style>
:root{{--bg:#08111f;--panel:#10213a;--panel2:#132946;--line:#263f61;--text:#f4f7fb;--muted:#91a4be;--amber:#ffb31f;--amber-soft:#ffd168}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:"Segoe UI",Arial,sans-serif}}.shell{{max-width:1180px;margin:auto;padding:30px}}
header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}.eyebrow{{color:var(--amber);font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:12px}}h1{{font-size:40px;margin:5px 0 7px}}h2{{font-size:21px;margin:0 0 14px}}p{{line-height:1.55;margin:0}}.muted,.previous,small{{color:var(--muted)}}
.demo{{border:1px solid #765b20;background:#302816;color:var(--amber-soft);border-radius:999px;padding:9px 12px;font-weight:800;font-size:12px}}.meta{{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0 14px}}.meta span{{border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--muted);font-size:13px}}
.synthetic{{border:1px solid #765b20;background:#302816;color:var(--amber-soft);padding:11px 14px;border-radius:12px;font-weight:750;margin-bottom:14px}}.grid{{display:grid;gap:12px}}.kpis{{grid-template-columns:repeat(2,minmax(0,1fr))}}.context{{grid-template-columns:repeat(3,minmax(0,1fr))}}
.card,.section{{background:linear-gradient(180deg,var(--panel2),#0d1d32);border:1px solid var(--line);border-radius:16px;padding:17px}}.section{{margin-top:14px}}.kpi-label{{color:var(--muted);font-size:12px;text-transform:uppercase;font-weight:800;letter-spacing:.05em}}.kpi-value{{font-size:27px;font-weight:900;margin:7px 0}}.context-value{{font-size:23px;font-weight:850;margin-top:7px}}.previous,.delta-v02{{font-size:13px;margin-top:5px}}.delta-v02{{font-weight:750}}.state{{color:var(--amber-soft);border-top:1px solid var(--line);margin-top:10px;padding-top:9px;line-height:1.4}}
.dynamic-row{{display:grid;grid-template-columns:1.2fr .75fr .85fr 1.3fr;gap:12px;padding:12px 0;border-top:1px solid var(--line);align-items:center}}.dynamic-row:first-of-type{{border-top:0}}.dynamic-row small{{grid-column:1/-1;color:var(--amber-soft)}}.plain-list{{margin:0;padding-left:20px}}.plain-list li{{margin:8px 0;line-height:1.5}}.method{{display:grid;gap:7px;color:var(--muted)}}
@media(max-width:760px){{.shell{{padding:18px}}header{{display:block}}.demo{{display:inline-block;margin-top:12px}}.context{{grid-template-columns:1fr}}.dynamic-row{{grid-template-columns:1fr 1fr}}}}
@media(max-width:520px){{.kpis{{grid-template-columns:1fr}}}}
@media print{{body{{background:white;color:#12233b}}.shell{{max-width:none;padding:12mm}}.card,.section{{background:white;color:#12233b;border-color:#ccd5e0;break-inside:avoid}}.muted,.previous,small,.method{{color:#51627a}}}}
</style></head><body><main class="shell">
<header><div><div class="eyebrow">TOPGUN · внутренний инструмент</div><h1>Мой день</h1><p class="muted">Персональная динамика мастера по сопоставимым периодам.</p></div><div class="demo">DEMO · локально</div></header>
<div class="meta"><span>Мастер: {html.escape(model.barber)}</span><span>Филиал: {html.escape(branch)}</span><span>Профиль: {html.escape(profile)}</span><span>Источник: {html.escape(model.source_name)}</span></div>
{synthetic}
<section class="grid kpis">{kpis}</section>
<section class="section"><h2>Твоя динамика</h2><p class="muted">Текущий период сравнивается с непосредственно предыдущим периодом той же длины.</p>{dynamics}</section>
<section class="section"><h2>Контекст периода</h2><div class="grid context">{context}</div></section>
<section class="section"><h2>Ориентир</h2><p>{html.escape(model.orientir.reason)}</p></section>
<section class="section"><h2>Что изменилось</h2>{facts}</section>
<section class="section"><h2>Зона внимания</h2>{_list_html(attention)}</section>
<section class="section"><h2>Фокус</h2>{_list_html(focus)}</section>
<section class="section"><h2>Методика и ограничения</h2><div class="method"><p>Источник: {html.escape(model.source_name)}{html.escape(sheet)} · {model.source_rows} строк.</p><p>Профиль: {html.escape(profile)}.</p><p>Текущий период: {window.current_start:%d.%m.%Y}–{window.current_end:%d.%m.%Y}. Предыдущий период: {window.previous_start:%d.%m.%Y}–{window.previous_end:%d.%m.%Y}.</p><p>Неподтверждённые поля и формулы показаны как ограничения, без подстановки значений.</p>{method_warning}<p>Расчёт выполнен локально. Отчёт описывает показатели, а не личность сотрудника.</p></div></section>
</main></body></html>"""


def safe_report_filename_v02(model: AnalyticsModelV02) -> str:
    barber = re.sub(r'[<>:"/\\|?*]+', "_", model.barber).strip(" .") or "барбер"
    barber = re.sub(r"\s+", "_", barber)
    window = model.period.window
    return (
        f"TOPGUN_Мой_день_{barber}_{window.current_start.isoformat()}_"
        f"{window.current_end.isoformat()}.html"
    )


def export_html_v02(
    model: AnalyticsModelV02,
    rule_result: RuleEngineResult,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    return _write_html(
        render_html_v02(model, rule_result), path, overwrite=overwrite
    )


def render_html_v02(model: AnalyticsModelV02, rule_result: RuleEngineResult) -> str:
    """Компактный статичный counterpart интерактивного Tkinter-dashboard."""

    profile = PROFILE_LABELS[model.profile]
    branch = model.branch or "Все филиалы"
    window = model.period.window
    synthetic = (
        f'<div class="synthetic">{html.escape(SYNTHETIC_WARNING)}</div>'
        if model.profile == MetricProfile.SYNTHETIC_V02
        else ""
    )
    kpis = "".join(
        _v02_metric_card(metric_id, model.period.comparisons[metric_id])
        for metric_id in V02_KPI_LABELS
    )
    comparison_rows: list[str] = []
    limitations: list[str] = []
    for metric_id, label in V02_KPI_LABELS.items():
        comparison = model.period.comparisons[metric_id]
        current, previous, delta, _reason = comparison_texts(comparison)
        if comparison.status == MetricStatus.AVAILABLE:
            state = ""
        elif comparison.status == MetricStatus.DATA_GAP:
            state = "Сравнение пока недоступно"
            limitations.append(f"{label}: {unavailable_text(comparison.status, comparison.reason)}")
        else:
            state = "Недостаточно данных"
            limitations.append(f"{label}: {unavailable_text(comparison.status, comparison.reason)}")
        comparison_rows.append(
            "<tr>"
            f"<th>{html.escape(label)}</th><td>{html.escape(current)}</td>"
            f"<td>{html.escape(previous)}</td><td>{html.escape(delta)}</td>"
            f"<td class=\"state-cell\">{html.escape(state)}</td></tr>"
        )
    context_results = {
        "visits_count": model.context.visits_count,
        "unique_clients_count": model.context.unique_clients_count,
        "revenue_total": model.context.revenue_total,
    }
    context_items: list[str] = []
    for metric_id, result in context_results.items():
        if result.status != MetricStatus.AVAILABLE:
            limitations.append(
                f"{CONTEXT_LABELS[metric_id]}: "
                f"{unavailable_text(result.status, result.reason)}"
            )
        context_items.append(
            '<div class="context-line">'
            f"<span>{html.escape(CONTEXT_LABELS[metric_id])}</span>"
            f"<strong>{html.escape(metric_value_text(result))}</strong></div>"
        )
    attention, focus = rule_section_texts(rule_result)
    available_facts = tuple(
        neutral_fact_text(fact)
        for fact in model.neutral_facts
        if fact.status == MetricStatus.AVAILABLE
    )
    facts = _list_html(available_facts) if available_facts else "<p class=\"muted\">Доступных изменений для описания пока нет.</p>"
    limitation_html = _list_html(tuple(limitations)) if limitations else "<p>Ограничений для показанных метрик нет.</p>"
    sheet = f" · лист «{model.sheet_name}»" if model.sheet_name else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOPGUN · Мой день · {html.escape(model.barber)}</title>
<style>
:root{{--bg:#08111f;--panel:#10213a;--panel2:#132946;--line:#263f61;--text:#f4f7fb;--muted:#91a4be;--amber:#ffb31f;--amber-soft:#ffd168;--blue:#4c8dff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:"Segoe UI",Arial,sans-serif}}.shell{{max-width:1180px;margin:auto;padding:24px 28px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}.eyebrow{{color:var(--amber);font-size:12px;font-weight:800;letter-spacing:.12em}}h1{{font-size:44px;margin:4px 0}}h2{{font-size:22px;margin:0 0 12px}}p{{line-height:1.5;margin:0}}.muted,.previous{{color:var(--muted)}}.demo{{border:1px solid #765b20;background:#302816;color:var(--amber-soft);border-radius:999px;padding:9px 12px;font-weight:800;font-size:12px}}.meta{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 10px}}.meta span{{border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--muted);font-size:13px}}.synthetic{{border:1px solid #765b20;background:#302816;color:var(--amber-soft);padding:10px 13px;border-radius:12px;font-weight:750;margin-bottom:12px}}.grid{{display:grid;gap:12px}}.kpis{{grid-template-columns:repeat(4,minmax(0,1fr))}}.dashboard{{grid-template-columns:minmax(0,3fr) minmax(240px,1fr)}}.three{{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:12px}}.card,.section{{background:linear-gradient(180deg,var(--panel2),#0d1d32);border:1px solid var(--line);border-radius:16px;padding:16px}}.soft-section{{padding:7px 8px}}.kpi-label{{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.05em}}.kpi-value{{font-size:29px;font-weight:900;margin:7px 0}}.previous,.delta-v02{{font-size:14px;margin-top:5px}}.delta-v02{{font-weight:750}}.state-short,.state-cell{{color:var(--amber-soft);font-size:12px;line-height:1.4;margin-top:8px}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px 7px;border-top:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--text)}}td{{color:var(--muted)}}.context-line{{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid var(--line);color:var(--muted)}}.context-line:first-of-type{{border-top:0}}.context-line strong{{color:var(--text);font-size:17px}}.orientir{{border-top:1px solid var(--line);margin-top:10px;padding-top:12px}}.plain-list{{margin:0;padding-left:18px}}.plain-list li{{margin:7px 0;line-height:1.45}}details{{margin-top:12px;background:#0c192b;border:1px solid var(--line);border-radius:12px;padding:12px;color:var(--muted)}}summary{{cursor:pointer;color:var(--text);font-weight:800}}details div{{padding-top:12px}}@media(max-width:900px){{.kpis{{grid-template-columns:repeat(2,minmax(0,1fr))}}.dashboard,.three{{grid-template-columns:1fr}}}}@media(max-width:560px){{.shell{{padding:18px}}header{{display:block}}.demo{{display:inline-block;margin-top:10px}}table{{font-size:12px}}th,td{{padding:8px 4px}}}}@media print{{body{{background:white;color:#12233b}}.shell{{max-width:none;padding:12mm}}.card,.section,details{{background:white;color:#12233b;border-color:#ccd5e0;break-inside:avoid}}.muted,.previous,td,.context-line{{color:#51627a}}}}
</style></head><body><main class="shell">
<header><div><div class="eyebrow">TOPGUN · ВНУТРЕННИЙ ИНСТРУМЕНТ</div><h1>Мой день</h1><p class="muted">Персональная динамика мастера за сопоставимые периоды.</p></div><div class="demo">DEMO · ЛОКАЛЬНО</div></header>
<div class="meta"><span>Мастер: {html.escape(model.barber)}</span><span>Филиал: {html.escape(branch)}</span><span>Профиль: {html.escape(profile)}</span><span>Источник: {html.escape(model.source_name)}</span></div>{synthetic}
<section class="grid kpis">{kpis}</section>
<section class="grid dashboard" style="margin-top:12px"><div class="section"><h2>Динамика показателя</h2><p class="muted" style="margin-bottom:10px">Текущий период: {window.current_start:%d.%m.%Y}–{window.current_end:%d.%m.%Y}. Предыдущий: {window.previous_start:%d.%m.%Y}–{window.previous_end:%d.%m.%Y}.</p><table><thead><tr><th>Показатель</th><th>Текущий</th><th>Предыдущий</th><th>Изменение</th><th></th></tr></thead><tbody>{''.join(comparison_rows)}</tbody></table></div><aside class="section"><h2>Контекст периода</h2>{''.join(context_items)}<div class="orientir"><div class="kpi-label">ОРИЕНТИР</div><p style="margin-top:6px">Пока не задан. На этом этапе сравниваем мастера только с самим собой.</p></div></aside></section>
<section class="grid three"><div class="soft-section"><h2>Что изменилось</h2>{facts}</div><div class="soft-section"><h2>Зона внимания</h2>{_list_html(attention)}</div><div class="soft-section"><h2>Фокус</h2>{_list_html(focus)}</div></section>
<details><summary>Методика и ограничения</summary><div><p>Источник: {html.escape(model.source_name)}{html.escape(sheet)} · {model.source_rows} строк.</p><p>Профиль: {html.escape(profile)}.</p>{limitation_html}{'<p>'+html.escape(SYNTHETIC_WARNING)+'</p>' if model.profile == MetricProfile.SYNTHETIC_V02 else ''}</div></details>
</main></body></html>"""
