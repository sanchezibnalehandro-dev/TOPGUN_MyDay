from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .config import NUMERIC_REQUIRED_FIELDS, REQUIRED_FIELDS
from .metrics import MetricProfile


SYNTHETIC_REQUIRED_FIELDS = (
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

_PROFILE_FIELDS = {
    MetricProfile.LEGACY_V01: frozenset(REQUIRED_FIELDS),
    MetricProfile.SYNTHETIC_V02: frozenset(SYNTHETIC_REQUIRED_FIELDS),
}


class InputDataError(ValueError):
    """Понятная пользователю ошибка входного файла."""


@dataclass(frozen=True)
class LoadedDataset:
    frame: pd.DataFrame
    profile: MetricProfile
    source_path: Path
    sheet_name: str | None
    warnings: tuple[str, ...]
    barbers: tuple[str, ...]
    branches: tuple[str, ...]
    date_min: pd.Timestamp
    date_max: pd.Timestamp

    @property
    def row_count(self) -> int:
        return len(self.frame)


def _normalise_header(value: object) -> str:
    return str(value).lstrip("\ufeff").strip().casefold()


def _normalised_headers(columns: Iterable[object]) -> list[str]:
    headers = [_normalise_header(column) for column in columns]
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise InputDataError(
            "После очистки заголовков найдены дублирующиеся поля: "
            + ", ".join(f"`{name}`" for name in duplicates)
            + ". Переименуйте столбцы и загрузите файл снова."
        )
    return headers


def _normalise_headers(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = _normalised_headers(result.columns)
    return result


def _matching_profiles(columns: Iterable[object]) -> tuple[MetricProfile, ...]:
    available = set(_normalised_headers(columns))
    return tuple(
        profile
        for profile, required in _PROFILE_FIELDS.items()
        if required.issubset(available)
    )


def _format_unknown_structure(header_sets: Sequence[set[str]]) -> str:
    details: list[str] = []
    for profile, required in _PROFILE_FIELDS.items():
        best_missing = min(
            (required - headers for headers in header_sets),
            key=len,
            default=set(required),
        )
        missing = ", ".join(f"`{field}`" for field in sorted(best_missing))
        details.append(f"{profile.name}: отсутствуют {missing or 'нет'}")
    return (
        "Структура данных не соответствует ни одному поддерживаемому профилю. "
        + "; ".join(details)
        + ". Сопоставление похожих названий полей не выполняется."
    )


def _detect_profile(columns: Iterable[object]) -> MetricProfile:
    column_list = list(columns)
    profiles = _matching_profiles(column_list)
    if len(profiles) == 1:
        return profiles[0]
    if len(profiles) > 1:
        raise InputDataError(
            "Структура одновременно соответствует LEGACY_V01 и SYNTHETIC_V02. "
            "Нельзя однозначно определить профиль данных."
        )
    raise InputDataError(
        _format_unknown_structure([set(_normalised_headers(column_list))])
    )


def _read_xlsx(
    path: Path,
) -> tuple[pd.DataFrame, MetricProfile, str, tuple[str, ...]]:
    try:
        workbook = pd.ExcelFile(path, engine="openpyxl")
    except Exception as exc:
        raise InputDataError(f"Не удалось открыть XLSX: {exc}") from exc

    try:
        matches: dict[MetricProfile, list[str]] = {
            MetricProfile.LEGACY_V01: [],
            MetricProfile.SYNTHETIC_V02: [],
        }
        inspected_headers: list[set[str]] = []
        for sheet in workbook.sheet_names:
            try:
                headers = workbook.parse(sheet_name=sheet, nrows=0)
            except Exception:
                continue
            normalised = _normalised_headers(headers.columns)
            inspected_headers.append(set(normalised))
            for profile in _matching_profiles(normalised):
                matches[profile].append(sheet)

        legacy_sheets = matches[MetricProfile.LEGACY_V01]
        synthetic_sheets = matches[MetricProfile.SYNTHETIC_V02]
        if legacy_sheets and synthetic_sheets:
            raise InputDataError(
                "В книге одновременно найдены листы LEGACY_V01 и SYNTHETIC_V02. "
                "Нельзя однозначно выбрать таблицу данных."
            )
        if len(synthetic_sheets) > 1:
            raise InputDataError(
                "В книге найдено несколько листов SYNTHETIC_V02 "
                f"({', '.join(synthetic_sheets)}). Оставьте один лист с данными."
            )
        if synthetic_sheets:
            profile = MetricProfile.SYNTHETIC_V02
            selected = synthetic_sheets[0]
            warnings: list[str] = []
        elif legacy_sheets:
            profile = MetricProfile.LEGACY_V01
            selected = legacy_sheets[0]
            warnings = []
            if len(legacy_sheets) > 1:
                warnings.append(
                    f"Найдено несколько подходящих листов ({', '.join(legacy_sheets)}). "
                    f"Использован первый: {selected}."
                )
        else:
            raise InputDataError(_format_unknown_structure(inspected_headers))

        try:
            frame = workbook.parse(sheet_name=selected, dtype=object)
        except Exception as exc:
            raise InputDataError(f"Не удалось прочитать лист «{selected}»: {exc}") from exc
    finally:
        workbook.close()
    return frame, profile, selected, tuple(warnings)


def _decode_csv(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise InputDataError("CSV должен быть сохранён в UTF-8 или Windows-1251.")


def _read_csv(
    path: Path,
) -> tuple[pd.DataFrame, MetricProfile, None, tuple[str, ...]]:
    text, encoding = _decode_csv(path)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    try:
        # Читаем CSV как текст: pandas не должен превратить ID `00123` в число.
        frame = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
        )
    except Exception as exc:
        raise InputDataError(f"Не удалось прочитать CSV: {exc}") from exc
    profile = _detect_profile(frame.columns)
    return frame, profile, None, ()


def _row_examples(mask: pd.Series, limit: int = 5) -> str:
    rows = [str(int(index) + 2) for index in mask[mask].index[:limit]]
    return ", ".join(rows)


def _non_empty_mask(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype(str).str.strip().ne("")


def _normalise_dates(frame: pd.DataFrame) -> None:
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce", dayfirst=True)
    invalid_dates = parsed_dates.isna()
    if invalid_dates.any():
        raise InputDataError(
            f"Поле `date` содержит {int(invalid_dates.sum())} некорректных дат "
            f"(например, строки: {_row_examples(invalid_dates)})."
        )
    frame["date"] = parsed_dates.dt.normalize()


def _validate_and_normalise_legacy(frame: pd.DataFrame) -> pd.DataFrame:
    text_fields = ("barber", "client_id", "service", "next_booking")
    for field in text_fields:
        invalid = ~_non_empty_mask(frame[field])
        if invalid.any():
            raise InputDataError(
                f"Поле `{field}` не заполнено в {int(invalid.sum())} строках "
                f"(например: {_row_examples(invalid)})."
            )
        frame[field] = frame[field].astype(str).str.strip()

    _normalise_dates(frame)

    for field in NUMERIC_REQUIRED_FIELDS:
        parsed = pd.to_numeric(frame[field], errors="coerce")
        invalid = parsed.isna()
        if invalid.any():
            raise InputDataError(
                f"Поле `{field}` должно содержать числа. Ошибка в "
                f"{int(invalid.sum())} строках (например: {_row_examples(invalid)})."
            )
        frame[field] = parsed.astype(float)

    if "discount" in frame.columns:
        parsed_discount = pd.to_numeric(frame["discount"], errors="coerce")
        invalid = parsed_discount.isna()
        if invalid.any():
            raise InputDataError(
                f"Поле `discount` присутствует, но не содержит число в "
                f"{int(invalid.sum())} строках (например: {_row_examples(invalid)})."
            )
        frame["discount"] = parsed_discount.astype(float)

    if "rating" in frame.columns:
        original_rating = frame["rating"]
        parsed_rating = pd.to_numeric(original_rating, errors="coerce")
        invalid = _non_empty_mask(original_rating) & parsed_rating.isna()
        if invalid.any():
            raise InputDataError(
                f"Поле `rating` содержит нечисловые значения в "
                f"{int(invalid.sum())} строках (например: {_row_examples(invalid)})."
            )
        frame["rating"] = parsed_rating.astype(float)

    booking = frame["next_booking"].astype(str).str.strip().str.casefold()
    mapped = booking.map({"да": "Да", "нет": "Нет"})
    invalid_booking = mapped.isna()
    if invalid_booking.any():
        raise InputDataError(
            "Поле `next_booking` принимает только «Да» или «Нет». "
            f"Ошибка в {int(invalid_booking.sum())} строках "
            f"(например: {_row_examples(invalid_booking)})."
        )
    frame["next_booking"] = mapped

    for field in ("barber", "service", "client_id"):
        frame[field] = frame[field].astype(str).str.strip()
    if "branch" in frame.columns:
        frame["branch"] = frame["branch"].where(
            _non_empty_mask(frame["branch"]), None
        )
        frame.loc[frame["branch"].notna(), "branch"] = (
            frame.loc[frame["branch"].notna(), "branch"].astype(str).str.strip()
        )
    return frame


def _normalise_text_preserving_missing(series: pd.Series) -> pd.Series:
    result = series.copy()
    mask = _non_empty_mask(result)
    result.loc[mask] = result.loc[mask].astype(str).str.strip()
    return result


def _normalise_numeric_preserving_invalid(series: pd.Series) -> pd.Series:
    def convert(value: object) -> object:
        if pd.isna(value):
            return value
        candidate = value.strip() if isinstance(value, str) else value
        if candidate == "":
            return value
        parsed = pd.to_numeric(candidate, errors="coerce")
        if pd.isna(parsed):
            return value
        return float(parsed)

    return series.map(convert)


def _normalise_flag_preserving_unknown(series: pd.Series) -> pd.Series:
    def convert(value: object) -> object:
        if pd.isna(value):
            return value
        cleaned = str(value).strip()
        canonical = {"да": "Да", "нет": "Нет"}.get(cleaned.casefold())
        return canonical if canonical is not None else cleaned

    return series.map(convert)


def _validate_and_normalise_synthetic(frame: pd.DataFrame) -> pd.DataFrame:
    invalid_barbers = ~_non_empty_mask(frame["barber"])
    if invalid_barbers.any():
        raise InputDataError(
            f"Поле `barber` не заполнено в {int(invalid_barbers.sum())} строках "
            f"(например: {_row_examples(invalid_barbers)})."
        )

    _normalise_dates(frame)
    for field in ("barber", "visit_id", "client_id", "branch"):
        frame[field] = _normalise_text_preserving_missing(frame[field])
    for field in ("service_revenue", "product_revenue"):
        frame[field] = _normalise_numeric_preserving_invalid(frame[field])
    for field in ("has_extra_service", "prebooked"):
        frame[field] = _normalise_flag_preserving_unknown(frame[field])
    return frame


def _validate_and_normalise(
    frame: pd.DataFrame, profile: MetricProfile
) -> pd.DataFrame:
    frame = _normalise_headers(frame)
    if frame.empty:
        raise InputDataError("В рабочем листе нет строк с визитами.")
    if profile == MetricProfile.LEGACY_V01:
        normalised = _validate_and_normalise_legacy(frame)
    else:
        normalised = _validate_and_normalise_synthetic(frame)
    return normalised.reset_index(drop=True)


def load_file(path: str | Path) -> LoadedDataset:
    source = Path(path)
    if not source.exists():
        raise InputDataError(f"Файл не найден: {source}")
    suffix = source.suffix.casefold()
    if suffix == ".xlsx":
        frame, profile, sheet_name, warnings = _read_xlsx(source)
    elif suffix == ".csv":
        frame, profile, sheet_name, warnings = _read_csv(source)
    else:
        raise InputDataError("Поддерживаются только файлы XLSX и CSV.")

    normalised = _validate_and_normalise(frame, profile)
    barbers = tuple(sorted(normalised["barber"].unique().tolist()))
    branches: Iterable[str] = ()
    if "branch" in normalised.columns:
        branch_mask = _non_empty_mask(normalised["branch"])
        branches = sorted(
            normalised.loc[branch_mask, "branch"].astype(str).str.strip().unique().tolist()
        )
    return LoadedDataset(
        frame=normalised,
        profile=profile,
        source_path=source.resolve(),
        sheet_name=sheet_name,
        warnings=warnings,
        barbers=barbers,
        branches=tuple(branches),
        date_min=normalised["date"].min(),
        date_max=normalised["date"].max(),
    )
