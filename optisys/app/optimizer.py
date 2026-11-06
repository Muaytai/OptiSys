"""Управление автозагрузкой Windows (через StartupApproved) и задачи оптимизации системы."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import winreg

from .models import OptimizeResult, StartupItem

logger = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_APPROVED_FOLDER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def _is_approved_enabled(hive: int, approved_subkey: str, name: str) -> bool:
    """Читает статус элемента из ветки StartupApproved (совместимо с Диспетчером задач Windows)."""
    try:
        with winreg.OpenKey(hive, approved_subkey, 0, winreg.KEY_READ) as key:
            data, _ = winreg.QueryValueEx(key, name)
            if isinstance(data, (bytes, bytearray)) and len(data) >= 1:
                # В Windows: чётный первый байт (0x02, 0x06) = включено, нечётный (0x03) = отключено
                return (data[0] & 1) == 0
    except OSError:
        pass
    return True


def _set_approved_state(hive: int, approved_subkey: str, name: str, enabled: bool) -> None:
    with winreg.CreateKeyEx(hive, approved_subkey, 0, winreg.KEY_SET_VALUE) as key:
        payload = (b"\x02" if enabled else b"\x03") + (b"\x00" * 11)
        winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, payload)


def estimate_startup_impact(name: str, command: str) -> str:
    """Оценивает влияние программы на скорость запуска ПК простыми словами."""
    combined = f"{name} {command}".lower()
    high_markers = (
        "steam",
        "epicgames",
        "docker",
        "discord",
        "teams",
        "skype",
        "onedrive",
        "yandex",
        "chrome",
        "msedge",
        "firefox",
        "opera",
        "adobe",
        "torrent",
        "utorrent",
        "origin",
        "battle.net",
        "riot",
        "spotify",
    )
    low_markers = (
        "securityhealth",
        "rtkngui",
        "rthdv",
        "realtek",
        "nvbackend",
        "igfx",
        "rundll32",
        "ctfmon",
    )
    if any(m in combined for m in high_markers):
        return "Высокая"
    if any(m in combined for m in low_markers):
        return "Низкая"
    return "Средняя"


def list_startup_items() -> list[StartupItem]:
    """Возвращает список программ автозагрузки из реестра (HKCU/HKLM) и папки автозагрузки."""
    items: list[StartupItem] = []

    for hive_label, hive in (
        ("Для вас (Пользователь)", winreg.HKEY_CURRENT_USER),
        ("Общая (Система Windows)", winreg.HKEY_LOCAL_MACHINE),
    ):
        try:
            key = winreg.OpenKey(hive, _RUN_KEY, 0, winreg.KEY_READ)
        except OSError:
            continue
        with key:
            idx = 0
            while True:
                try:
                    val_name, val_data, _ = winreg.EnumValue(key, idx)
                except OSError:
                    break
                idx += 1
                if not val_name:
                    continue
                enabled = _is_approved_enabled(hive, _APPROVED_RUN_KEY, val_name)
                items.append(
                    StartupItem(
                        item_id=f"reg|{hive}|{val_name}",
                        name=val_name,
                        command=str(val_data or ""),
                        source=hive_label,
                        enabled=enabled,
                        hive=hive,
                        subkey=_APPROVED_RUN_KEY,
                    )
                )

    startup_dir = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    if startup_dir.is_dir():
        try:
            for fpath in startup_dir.iterdir():
                if fpath.name.lower() == "desktop.ini" or not fpath.is_file():
                    continue
                enabled = _is_approved_enabled(winreg.HKEY_CURRENT_USER, _APPROVED_FOLDER_KEY, fpath.name)
                items.append(
                    StartupItem(
                        item_id=f"dir|{fpath.name}",
                        name=fpath.stem,
                        command=str(fpath),
                        source="Ярлык в меню «Пуск»",
                        enabled=enabled,
                        hive=winreg.HKEY_CURRENT_USER,
                        subkey=_APPROVED_FOLDER_KEY,
                        is_file=True,
                        file_path=fpath.name,
                    )
                )
        except OSError:
            pass

    return items


def set_startup_item_enabled(item: StartupItem, enabled: bool) -> None:
    """Включает или отключает элемент автозагрузки через стандартный механизм StartupApproved."""
    target_name = item.file_path if item.is_file else item.name
    _set_approved_state(item.hive, item.subkey, target_name, enabled)


def _run_cmd(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        short = lines[-1] if lines else ("Выполнено успешно" if proc.returncode == 0 else f"Код {proc.returncode}")
        return proc.returncode == 0, short
    except subprocess.TimeoutExpired:
        return False, "Превышено время ожидания команды"
    except OSError as exc:
        return False, str(exc)


def run_optimization_task(key: str) -> OptimizeResult:
    """Выполняет выбранную задачу системной оптимизации."""
    sys_drive = os.environ.get("SystemDrive", "C:")
    if key == "flush_dns":
        ok, _ = _run_cmd(["ipconfig", "/flushdns"], timeout=30)
        msg = "Адреса сайтов обновлены — страницы будут открываться быстрее" if ok else "Не удалось обновить кэш адресов"
        return OptimizeResult(key, "Ускорение интернета и открытия сайтов", ok, msg)
    if key == "arp_net_cache":
        ok1, _ = _run_cmd(["ipconfig", "/flushdns"], timeout=30)
        ok2, _ = _run_cmd(["netsh", "interface", "ip", "delete", "arpcache"], timeout=30)
        ok = ok1 and ok2
        msg = (
            "Сетевые маршруты успешно сброшены и обновлены"
            if ok
            else "Для полного сброса сетевых маршрутов запустите программу от имени администратора"
        )
        return OptimizeResult(key, "Сброс сетевых задержек и маршрутов", ok, msg)
    if key == "trim_ssd":
        ok, _ = _run_cmd(["defrag", sys_drive, "/O"], timeout=300)
        msg = (
            f"Системный диск {sys_drive} успешно оптимизирован"
            if ok
            else "Для оптимизации системного диска требуются права администратора"
        )
        return OptimizeResult(key, f"Ускорение работы системного диска ({sys_drive})", ok, msg)
    if key == "winsxs_clean":
        ok, _ = _run_cmd(
            ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
            timeout=600,
        )
        msg = (
            "Устаревшие копии обновлений Windows удалены"
            if ok
            else "Для очистки системного хранилища обновлений нужны права администратора"
        )
        return OptimizeResult(key, "Очистка старых копий обновлений Windows", ok, msg)
    if key == "all_fast":
        ok_dns, _ = _run_cmd(["ipconfig", "/flushdns"], timeout=30)
        ok_arp, _ = _run_cmd(["netsh", "interface", "ip", "delete", "arpcache"], timeout=30)
        ok_ssd, _ = _run_cmd(["defrag", sys_drive, "/O"], timeout=300)
        done_parts = ["интернет"]
        if ok_arp:
            done_parts.append("сеть")
        if ok_ssd:
            done_parts.append(f"диск {sys_drive}")
        return OptimizeResult(
            key,
            "Комплексное ускорение ПК в 1 клик",
            ok_dns,
            f"Оптимизировано: {', '.join(done_parts)}"
            + ("" if ok_ssd else " (для ускорения диска нужны права администратора)"),
        )
    return OptimizeResult(key, key, False, "Неизвестная задача")

