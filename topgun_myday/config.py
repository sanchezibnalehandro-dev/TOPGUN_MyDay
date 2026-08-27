from __future__ import annotations

REQUIRED_FIELDS = (
    "date",
    "barber",
    "client_id",
    "service",
    "service_revenue",
    "product_revenue",
    "next_booking",
)

NUMERIC_REQUIRED_FIELDS = ("service_revenue", "product_revenue")
NUMERIC_OPTIONAL_FIELDS = ("discount", "rating")

KEY_METRICS = ("average_check", "next_booking_rate", "product_per_visit")
METRIC_LABELS = {
    "average_check": "Средний чек",
    "next_booking_rate": "Следующая запись",
    "product_per_visit": "Товары / визит",
}

CHANGE_THRESHOLD = 0.05
SIGNIFICANT_CHANGE_THRESHOLD = 0.10

RECOMMENDATIONS = {
    "average_check": (
        "Средний чек",
        "Разбери структуру услуг и мягко предлагай уместные дополнительные "
        "услуги там, где они действительно усиливают визит.",
    ),
    "next_booking_rate": (
        "Следующая запись",
        "До расчёта с клиентом возвращайся к следующему визиту и предлагай "
        "понятный ориентир по сроку.",
    ),
    "product_per_visit": (
        "Домашний уход",
        "После услуги чаще давай конкретную рекомендацию по домашнему уходу, "
        "если она уместна для клиента.",
    ),
}

STABLE_FOCUS = (
    "Удержать текущий подход",
    "Продолжай текущий подход и следи за устойчивостью ключевых показателей "
    "без резких изменений процесса.",
)

INSUFFICIENT_FOCUS = (
    "Собрать сопоставимые данные",
    "Для выбора фактического фокуса нужен предыдущий период с ненулевыми "
    "значениями ключевых показателей.",
)

COLORS = {
    "bg": "#08111F",
    "bg_deep": "#050B13",
    "panel": "#10213A",
    "panel_alt": "#132946",
    "line": "#263F61",
    "text": "#F4F7FB",
    "muted": "#91A4BE",
    "amber": "#FFB31F",
    "amber_soft": "#FFD168",
    "green": "#47D28B",
    "red": "#FF6B6B",
    "blue": "#4C8DFF",
}
