"""Вспомогательные функции форматирования и работы с правами."""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def fmt_bytes(value: Any) -> str:
    """Человекочитаемый размер. Принимает int/str (WMI отдаёт uint64 строкой)."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
        size /= 1024
    return ""


def fmt_cim_date(value: Any, with_time: bool = False) -> str:
    """CIM datetime ('20240131123000.000000+180') -> '2024-01-31[ 12:30]'."""
    if not value:
        return ""
    s = str(value)
    if len(s) < 8 or not s[:8].isdigit():
        return s
    date = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if with_time and len(s) >= 12 and s[8:12].isdigit():
        return f"{date} {s[8:10]}:{s[10:12]}"
    return date


def to_str(value: Any) -> str:
    """Безопасное приведение значения WMI/COM к строке."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(to_str(v) for v in value if v is not None)
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value).strip()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        logger.exception("Не удалось проверить права администратора")
        return False


def relaunch_as_admin() -> bool:
    """Перезапуск процесса с запросом UAC. True — если запуск инициирован."""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe = sys.executable
        params = f'"{Path(sys.argv[0]).resolve()}"'
    # ShellExecuteW возвращает > 32 при успехе
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if rc <= 32:
        logger.error("ShellExecuteW runas вернул код %s", rc)
        return False
    return True


def log_dir() -> Path:
    base = Path.home() / "AppData" / "Local" / "optisys"
    base.mkdir(parents=True, exist_ok=True)
    return base

