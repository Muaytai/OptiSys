"""Поиск битых ссылок в реестре Windows и безопасное исправление с авто-бэкапом (.reg)."""
from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import re
import winreg
from collections.abc import Callable, Iterable

from .models import RegistryFixSummary, RegistryIssue
from .utils import log_dir

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_HIVE_MAP: dict[str, int] = {
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
}

_HIVE_FULL_NAME: dict[str, str] = {
    "HKCU": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
}

_EXE_PATH_RE = re.compile(r'^\s*"?([A-Za-z]:\\[^"]*?\.(?:exe|dll|ocx|bat|cmd|scr|msi))"?', re.IGNORECASE)


def extract_executable_path(raw: str) -> Path | None:
    """Извлекает путь к файлу из командной строки реестра с раскрытием переменных среды."""
    if not raw:
        return None
    expanded = os.path.expandvars(str(raw).strip())
    # Для MuiCache суффиксы вида .FriendlyAppName или .ApplicationCompany
    for suffix in (".FriendlyAppName", ".ApplicationCompany"):
        if expanded.endswith(suffix):
            expanded = expanded[: -len(suffix)]
            break
    if expanded.startswith("@"):
        expanded = expanded[1:].split(",", 1)[0]

    match = _EXE_PATH_RE.match(expanded)
    candidate = match.group(1) if match else expanded.strip('"').strip()
    if len(candidate) >= 3 and candidate[1:3] == ":\\":
        try:
            return Path(candidate)
        except OSError:
            return None
    return None


def _file_missing(path: Path | None) -> bool:
    if path is None:
        return False
    # Проверяем, что диск вообще существует (не съёмный отключённый диск)
    drive = Path(f"{path.drive}\\") if path.drive else None
    if drive and not drive.exists():
        return False
    try:
        return not path.exists()
    except OSError:
        return False


def _scan_value_paths(
    hive_name: str,
    subkey: str,
    category: str,
    reason_label: str,
    check_value_name_as_path: bool = False,
) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    hive = _HIVE_MAP[hive_name]
    try:
        key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
    except OSError:
        return issues

    with key:
        idx = 0
        while True:
            try:
                val_name, val_data, _ = winreg.EnumValue(key, idx)
            except OSError:
                break
            idx += 1
            target_str = val_name if check_value_name_as_path else str(val_data or "")
            exe_path = extract_executable_path(target_str)
            if _file_missing(exe_path):
                issue_id = f"val|{hive_name}|{subkey}|{val_name}"
                issues.append(
                    RegistryIssue(
                        issue_id=issue_id,
                        category=category,
                        hive_name=hive_name,
                        subkey=subkey,
                        value_name=val_name,
                        value_data=str(val_data or ""),
                        reason=f"{reason_label}: не найден {exe_path}",
                    )
                )
    return issues


def _scan_app_paths(hive_name: str) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    base_subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    hive = _HIVE_MAP[hive_name]
    try:
        root = winreg.OpenKey(hive, base_subkey, 0, winreg.KEY_READ)
    except OSError:
        return issues

    with root:
        idx = 0
        while True:
            try:
                app_key_name = winreg.EnumKey(root, idx)
            except OSError:
                break
            idx += 1
            full_subkey = f"{base_subkey}\\{app_key_name}"
            try:
                with winreg.OpenKey(hive, full_subkey, 0, winreg.KEY_READ) as sub:
                    default_val, _ = winreg.QueryValueEx(sub, "")
            except OSError:
                continue
            exe_path = extract_executable_path(str(default_val or ""))
            if _file_missing(exe_path):
                issue_id = f"key|{hive_name}|{full_subkey}|"
                issues.append(
                    RegistryIssue(
                        issue_id=issue_id,
                        category="Нерабочий ярлык программы",
                        hive_name=hive_name,
                        subkey=full_subkey,
                        value_name="(По умолчанию)",
                        value_data=str(default_val or ""),
                        reason=f"Программа была удалена ({exe_path.name if exe_path else app_key_name})",
                    )
                )
    return issues


def scan_registry_issues(progress: ProgressCb | None = None) -> list[RegistryIssue]:
    """Сканирует реестр на безопасные для удаления битые ссылки."""
    issues: list[RegistryIssue] = []

    if progress:
        progress("Поиск следов удалённых программ…")
    issues.extend(
        _scan_value_paths(
            "HKCU",
            r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
            "Следы удалённых программ",
            "Программа давно удалена",
            check_value_name_as_path=True,
        )
    )

    if progress:
        progress("Проверка ярлыков и путей программ…")
    issues.extend(_scan_app_paths("HKCU"))
    issues.extend(_scan_app_paths("HKLM"))

    if progress:
        progress("Проверка записей автозапуска…")
    issues.extend(
        _scan_value_paths(
            "HKCU",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            "Удалённая программа в автозапуске",
            "Файл автозапуска больше не существует",
        )
    )
    issues.extend(
        _scan_value_paths(
            "HKLM",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            "Удалённая программа в автозапуске",
            "Файл автозапуска больше не существует",
        )
    )

    if progress:
        progress("Проверка отсутствующих библиотек…")
    issues.extend(
        _scan_value_paths(
            "HKLM",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDLLs",
            "Отсутствующий системный компонент",
            "Файл библиотеки удалён",
            check_value_name_as_path=True,
        )
    )

    return issues


def _reg_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _export_backup(issues: list[RegistryIssue]) -> Path:
    """Создаёт .reg файл резервной копии перед удалением записей."""
    backups = log_dir() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    path = backups / f"registry_backup_{datetime.now():%Y%m%d_%H%M%S}.reg"

    lines = ["Windows Registry Editor Version 5.00", ""]
    by_key: dict[tuple[str, str], list[RegistryIssue]] = {}
    for item in issues:
        by_key.setdefault((item.hive_name, item.subkey), []).append(item)

    for (hive_name, subkey), group in by_key.items():
        full_hive = _HIVE_FULL_NAME.get(hive_name, hive_name)
        lines.append(f"[{full_hive}\\{subkey}]")
        for it in group:
            val_escaped = _reg_escape(it.value_data)
            if it.value_name == "(По умолчанию)" or not it.value_name:
                lines.append(f'@="{val_escaped}"')
            else:
                name_escaped = _reg_escape(it.value_name)
                lines.append(f'"{name_escaped}"="{val_escaped}"')
        lines.append("")

    path.write_text("\r\n".join(lines), encoding="utf-16")
    return path


def fix_registry_issues(
    issues: list[RegistryIssue],
    selected_ids: Iterable[str],
) -> RegistryFixSummary:
    """Создаёт резервную копию .reg и удаляет выбранные битые записи."""
    wanted = set(selected_ids)
    to_fix = [i for i in issues if i.issue_id in wanted]
    summary = RegistryFixSummary()
    if not to_fix:
        return summary

    backup_file = _export_backup(to_fix)
    summary.backup_path = str(backup_file)

    for item in to_fix:
        hive = _HIVE_MAP.get(item.hive_name)
        if hive is None:
            continue
        kind = item.issue_id.split("|", 1)[0]
        try:
            if kind == "key":
                winreg.DeleteKey(hive, item.subkey)
            else:
                with winreg.OpenKey(hive, item.subkey, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, item.value_name)
            summary.fixed_count += 1
        except OSError as exc:
            summary.errors.append(f"{item.hive_name}\\{item.subkey} ({item.value_name}): {exc}")

    return summary

