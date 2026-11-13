"""Управление автозагрузкой Windows (через StartupApproved) и задачи оптимизации системы."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess
import winreg

from .models import OptimizeResult, StartupItem, SystemTweak
from .utils import fmt_bytes

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
        ok, _ = _run_cmd(["ipconfig", "/flushdns"], timeout=30)
        msg = (
            "Сетевой кэш DNS успешно обновлён — стабильность связи сохранена"
            if ok
            else "Не удалось обновить сетевой кэш"
        )
        return OptimizeResult(key, "Сброс сетевых задержек (DNS)", ok, msg)
    if key == "free_ram":
        trimmed, freed_bytes = free_memory_working_sets()
        freed_str = fmt_bytes(freed_bytes) if freed_bytes > 1024 * 1024 else "кэш уже оптимален"
        return OptimizeResult(
            key,
            "Освобождение оперативной памяти (RAM)",
            trimmed > 0,
            f"Освобождено ОЗУ: {freed_str} (оптимизировано процессов: {trimmed})",
        )
    if key == "trim_ssd":
        # Включаем аппаратный TRIM в NTFS и запускаем быстрый retrim (/L)
        _run_cmd(["fsutil", "behavior", "set", "DisableDeleteNotify", "0"], timeout=15)
        ok, detail = _run_cmd(["defrag", sys_drive, "/L"], timeout=120)
        msg = (
            f"Системный диск {sys_drive} оптимизирован (TRIM активен)"
            if ok
            else f"Не удалось оптимизировать диск {sys_drive}: {detail}"
        )
        return OptimizeResult(key, f"Ускорение работы системного диска ({sys_drive})", ok, msg)
    if key == "winsxs_clean":
        ok, detail = _run_cmd(
            ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
            timeout=900,
        )
        msg = (
            "Устаревшие копии обновлений Windows удалены"
            if ok
            else f"Не удалось очистить хранилище обновлений: {detail}"
        )
        return OptimizeResult(key, "Очистка старых копий обновлений Windows", ok, msg)
    if key == "all_fast":
        ok_dns, _ = _run_cmd(["ipconfig", "/flushdns"], timeout=30)
        _run_cmd(["fsutil", "behavior", "set", "DisableDeleteNotify", "0"], timeout=15)
        ok_ssd, _ = _run_cmd(["defrag", sys_drive, "/L"], timeout=120)
        _, freed_ram = free_memory_working_sets()
        done_parts = ["сеть и DNS"]
        if ok_ssd:
            done_parts.append(f"диск {sys_drive}")
        if freed_ram > 1024 * 1024:
            done_parts.append(f"ОЗУ (+{fmt_bytes(freed_ram)})")
        return OptimizeResult(
            key,
            "Комплексное ускорение ПК в 1 клик",
            ok_dns,
            f"Оптимизировано: {', '.join(done_parts)}"
            + ("" if ok_ssd else " (диск не удалось оптимизировать)"),
        )
    return OptimizeResult(key, key, False, "Неизвестная задача")


# ---------- Оперативная память (WinAPI) ----------


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_memory_usage() -> tuple[float, float, float]:
    """Возвращает (процент_загрузки, занято_ГБ, всего_ГБ) через GlobalMemoryStatusEx."""
    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return 0.0, 0.0, 0.0
    total_gb = stat.ullTotalPhys / 1024**3
    avail_gb = stat.ullAvailPhys / 1024**3
    used_gb = max(0.0, total_gb - avail_gb)
    return float(stat.dwMemoryLoad), used_gb, total_gb


# Процессы, выгрузка памяти которых опасна для сетевого стека, VPN и стабильности Windows
_PROTECTED_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        # Системное ядро и критические службы Windows
        "system",
        "registry",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
        "svchost.exe",
        "dwm.exe",
        "winlogon.exe",
        "fontdrvhost.exe",
        "sihost.exe",
        "explorer.exe",
        "taskhostw.exe",
        "spoolsv.exe",
        "wudfhost.exe",
        "searchindexer.exe",
        "audiodg.exe",
        "securityhealthservice.exe",
        "securityhealthsystray.exe",
        "msmpeng.exe",
        "nissrv.exe",
        "ctfmon.exe",
        "conhost.exe",
        # Сетевые службы, VPN, прокси, туннели и DNS
        "ag_dns.exe",
        "hiddify.exe",
        "hiddifyadmin.exe",
        "sing-box.exe",
        "wintun.exe",
        "openvpn.exe",
        "wireguard.exe",
        "clash.exe",
        "clash-meta.exe",
        "mihomo.exe",
        "v2ray.exe",
        "xray.exe",
        "nekoray.exe",
        "nekobox.exe",
        "tun2socks.exe",
        "goodbyedpi.exe",
        "zapret.exe",
        "tailscale.exe",
        "zerotier-one_x64.exe",
    }
)

_VPN_OR_NET_SUBSTRINGS: tuple[str, ...] = (
    "vpn",
    "proxy",
    "tunnel",
    "wintun",
    "sing-box",
    "clash",
    "wireguard",
    "openvpn",
    "v2ray",
    "xray",
    "shadowsocks",
    "dns",
)


def _get_process_name(handle: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    size = wintypes.DWORD(512)
    if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return Path(buf.value).name.lower()
    return ""


def _is_safe_to_trim(proc_name: str) -> bool:
    if not proc_name:
        return False
    low = proc_name.lower().strip()
    if low in _PROTECTED_PROCESS_NAMES:
        return False
    if any(m in low for m in _VPN_OR_NET_SUBSTRINGS):
        return False
    return True


def free_memory_working_sets() -> tuple[int, int]:
    """Сбрасывает неиспользуемые рабочие наборы фоновых пользовательских приложений (EmptyWorkingSet).

    Системные службы Windows, сеть, DNS и VPN-туннели безопасно исключаются.
    Возвращает (число_процессов, освобождено_байт).
    """
    stat_before = _MEMORYSTATUSEX()
    stat_before.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat_before))

    arr = (wintypes.DWORD * 4096)()
    cb_needed = wintypes.DWORD()
    if not ctypes.windll.psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(cb_needed)):
        return 0, 0

    count = cb_needed.value // ctypes.sizeof(wintypes.DWORD)
    current_pid = os.getpid()
    # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) | PROCESS_SET_QUOTA (0x0100)
    access = 0x1000 | 0x0100
    trimmed = 0
    for i in range(count):
        pid = int(arr[i])
        if pid <= 4 or pid == current_pid:
            continue
        handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
        if handle:
            try:
                proc_name = _get_process_name(handle)
                if not _is_safe_to_trim(proc_name):
                    continue
                if ctypes.windll.psapi.EmptyWorkingSet(handle):
                    trimmed += 1
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)

    stat_after = _MEMORYSTATUSEX()
    stat_after.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat_after))
    freed = max(0, int(stat_after.ullAvailPhys) - int(stat_before.ullAvailPhys))
    return trimmed, freed


# ---------- Безопасные переключатели настроек Windows 11 ----------

_GUID_HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
_GUID_ULTIMATE_PERF = "e9a42b02-d5df-448d-aa00-03f14749eb61"
_GUID_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"


def _reg_get_dword(hive: int, subkey: str, name: str, default: int = 0) -> int:
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, name)
            return int(val)
    except (OSError, ValueError, TypeError):
        return default


def _reg_set_dword(hive: int, subkey: str, name: str, value: int) -> None:
    with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))


def _is_high_perf_power() -> bool:
    ok, out = _run_cmd(["powercfg", "/getactivescheme"], timeout=10)
    if not ok:
        return False
    low = out.lower()
    return _GUID_HIGH_PERF in low or _GUID_ULTIMATE_PERF in low


def list_system_tweaks() -> list[SystemTweak]:
    """Возвращает список безопасных настроек Windows с их текущим состоянием."""
    bing_off = _reg_get_dword(
        winreg.HKEY_CURRENT_USER,
        r"Software\Policies\Microsoft\Windows\Explorer",
        "DisableSearchBoxSuggestions",
        0,
    ) == 1
    tips_off = _reg_get_dword(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
        "SubscribedContent-338389Enabled",
        1,
    ) == 0
    show_ext = _reg_get_dword(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        "HideFileExt",
        1,
    ) == 0
    return [
        SystemTweak(
            "high_perf_power",
            "Схема питания «Высокая производительность»",
            "Убирает троттлинг частоты процессора при работе от сети.",
            enabled=_is_high_perf_power(),
        ),
        SystemTweak(
            "disable_bing_search",
            "Отключить веб-поиск Bing в меню «Пуск»",
            "Ускоряет локальный поиск программ и файлов без обращения в интернет.",
            enabled=bing_off,
        ),
        SystemTweak(
            "disable_win_tips",
            "Отключить рекламные советы и рекомендации Windows",
            "Убирает навязчивые всплывающие подсказки и рекламу в меню «Пуск».",
            enabled=tips_off,
        ),
        SystemTweak(
            "show_file_ext",
            "Показывать расширения всех файлов (.exe, .txt, .json)",
            "Повышает удобство и защищает от файлов с двойным расширением.",
            enabled=show_ext,
        ),
    ]


def set_system_tweak(key: str, enabled: bool) -> tuple[bool, str]:
    """Переключает выбранную настройку Windows и возвращает (успех, сообщение)."""
    try:
        if key == "high_perf_power":
            target = _GUID_HIGH_PERF if enabled else _GUID_BALANCED
            ok, detail = _run_cmd(["powercfg", "/setactive", target], timeout=10)
            return ok, (
                "Включена схема питания «Высокая производительность»"
                if enabled and ok
                else ("Включена сбалансированная схема питания" if ok else detail)
            )
        if key == "disable_bing_search":
            _reg_set_dword(
                winreg.HKEY_CURRENT_USER,
                r"Software\Policies\Microsoft\Windows\Explorer",
                "DisableSearchBoxSuggestions",
                1 if enabled else 0,
            )
            return True, "Веб-поиск Bing в меню «Пуск» отключён" if enabled else "Веб-поиск в меню «Пуск» включён"
        if key == "disable_win_tips":
            sub = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
            val = 0 if enabled else 1
            for name in ("SubscribedContent-338389Enabled", "SoftLandingEnabled", "SystemPaneSuggestionsEnabled"):
                _reg_set_dword(winreg.HKEY_CURRENT_USER, sub, name, val)
            return True, "Рекламные рекомендации Windows отключены" if enabled else "Рекомендации Windows включены"
        if key == "show_file_ext":
            _reg_set_dword(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "HideFileExt",
                0 if enabled else 1,
            )
            return True, "Показ расширений файлов включён" if enabled else "Расширения файлов скрыты"
    except OSError as exc:
        return False, str(exc)
    return False, "Неизвестная настройка"


