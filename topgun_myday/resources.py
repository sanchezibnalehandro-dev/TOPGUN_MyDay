"""Пути к ресурсам приложения и к доступной пользователю папке для экспорта."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Вернуть корень встроенных ресурсов в исходниках или PyInstaller-пакете."""

    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Вернуть путь к поставляемому вместе с приложением ресурсу."""

    return resource_root().joinpath(*parts)


def default_export_directory() -> Path:
    """Выбрать понятную пользователю папку, не зависящую от пакета приложения."""

    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()
