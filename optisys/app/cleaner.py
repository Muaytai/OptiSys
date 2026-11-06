"""Анализ и безопасная очистка временных файлов, кэша браузеров и системного мусора."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Callable, Iterable

from .models import CleanCategoryResult, CleanSummary, CleanTarget
from .utils import fmt_bytes

logger = logging.getLogger(__name__)

ProgressStepCb = Callable[[int, int, str, str], None]
LogCb = Callable[[str], None]

# Файлы в %TEMP% и Windows\Temp моложе 24 часов не трогаем,
# чтобы не сбивать активные сессии, установщики, сокеты и пайпы запущенных программ
_TEMP_SAFE_AGE_SECONDS = 24 * 3600

# Имена директорий внутри %TEMP%, которые нельзя удалять ни при каких условиях
_PROTECTED_DIR_NAMES = {
    "gen_py",
    "node-compile-cache",
    "wsldvcplugin",
    "diagoutputdir",
    "optisys",
    "optisys-build",
}

_PROTECTED_DIR_PREFIXES = (
    "_mei",
    "antigravity",
    "vscode-",
    "cursor-",
    "pyinstaller",
    "pip-",
    "dotnet-",
)


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


_SHERB_NOCONFIRMATION = 0x00000001
_SHERB_NOPROGRESSUI = 0x00000002
_SHERB_NOSOUND = 0x00000004


def _query_recycle_bin() -> tuple[int, int]:
    """Возвращает (количество элементов, размер в байтах) в Корзине."""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    try:
        res = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if res == 0:
            return max(0, int(info.i64NumItems)), max(0, int(info.i64Size))
    except OSError:
        logger.debug("Не удалось опросить Корзину")
    return 0, 0


def _empty_recycle_bin() -> bool:
    flags = _SHERB_NOCONFIRMATION | _SHERB_NOPROGRESSUI | _SHERB_NOSOUND
    try:
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        return res == 0
    except OSError:
        return False


def _running_process_names() -> set[str]:
    """Возвращает множество имён запущенных процессов в нижнем регистре (например, 'chrome.exe')."""
    try:
        proc = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        names: set[str] = set()
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith('"'):
                parts = line.split('","', 1)
                if parts:
                    names.add(parts[0].strip('"').lower())
        return names
    except (OSError, subprocess.SubprocessError):
        return set()


def _protected_roots() -> list[Path]:
    """Папки внутри %TEMP%, которые нельзя удалять (в т.ч. распакованный PyInstaller _MEIPASS)."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            roots.append(Path(meipass).resolve())
        except OSError:
            pass
    try:
        roots.append(Path(sys.executable).resolve().parent)
    except OSError:
        pass
    return roots


def _is_under_protected(path: Path, protected: list[Path]) -> bool:
    for part in path.parts:
        low = part.lower()
        if low in _PROTECTED_DIR_NAMES or low.startswith(_PROTECTED_DIR_PREFIXES):
            return True
    if not protected:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for p in protected:
        if resolved == p or p in resolved.parents or resolved in p.parents:
            return True
    return False


def _browser_cache_dirs(user_data_dir: Path) -> list[Path]:
    """Находит только безопасные кэш-папки Chromium-браузера (куки, сессии и пароли не затрагиваются)."""
    if not user_data_dir.is_dir():
        return []
    dirs: list[Path] = []
    # Только чистый дисковый кэш медиа/страниц и шейдеров (без Network и без Storage)
    sub_names = ("Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache")
    try:
        entries = [user_data_dir / "Default"] + [
            p for p in user_data_dir.iterdir() if p.is_dir() and p.name.startswith("Profile ")
        ]
    except OSError:
        return []
    for prof in entries:
        if not prof.is_dir():
            continue
        for sub in sub_names:
            cand = prof / sub
            if cand.is_dir():
                dirs.append(cand)
        # В современных Chromium HTTP-кэш лежит в Network/Cache, но остальное в Network трогать нельзя
        net_cache = prof / "Network" / "Cache"
        if net_cache.is_dir():
            dirs.append(net_cache)
    return dirs


def _firefox_cache_dirs() -> list[Path]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
    if not base.is_dir():
        return []
    result: list[Path] = []
    try:
        for prof in base.iterdir():
            if prof.is_dir():
                for sub in ("cache2", "startupCache", "jumpListCache"):
                    cand = prof / sub
                    if cand.is_dir():
                        result.append(cand)
    except OSError:
        pass
    return result


def _user_temp_dirs() -> list[Path]:
    local_temp = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp"
    env_temp = Path(os.environ.get("TEMP", str(local_temp)))
    dirs: list[Path] = []
    seen: set[str] = set()
    for d in (env_temp, local_temp):
        try:
            norm = str(d.resolve()).lower()
        except OSError:
            norm = str(d).lower()
        if norm not in seen:
            seen.add(norm)
            dirs.append(d)
    return dirs


def _target_directories(key: str) -> tuple[list[Path], str | None]:
    """Возвращает (список папок, опциональная маска файлов) для категории."""
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    win = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))

    mapping: dict[str, tuple[list[Path], str | None]] = {
        "user_temp": (_user_temp_dirs(), None),
        "win_temp": ([win / "Temp"], None),
        "prefetch": ([win / "Prefetch"], None),
        "wu_cache": ([win / "SoftwareDistribution" / "Download"], None),
        "wer_dumps": (
            [
                local / "CrashDumps",
                local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                local / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                prog_data / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                prog_data / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                win / "Minidump",
            ],
            None,
        ),
        "thumbs": ([local / "Microsoft" / "Windows" / "Explorer"], "thumbcache_*.db"),
        "chrome_cache": (_browser_cache_dirs(local / "Google" / "Chrome" / "User Data"), None),
        "edge_cache": (_browser_cache_dirs(local / "Microsoft" / "Edge" / "User Data"), None),
        "firefox_cache": (_firefox_cache_dirs(), None),
        "dev_cache": ([local / "pip" / "cache", local / "npm-cache"], None),
    }
    return mapping.get(key, ([], None))


def catalog_definitions() -> list[CleanTarget]:
    return [
        CleanTarget(
            "user_temp",
            "Временные файлы пользователя (%TEMP%)",
            "Система Windows",
            "Устаревшие временные файлы приложений (старше 24 ч, активные сессии не затрагиваются)",
            checked_by_default=True,
            min_age_seconds=_TEMP_SAFE_AGE_SECONDS,
        ),
        CleanTarget(
            "win_temp",
            "Системные временные файлы (Windows\\Temp)",
            "Система Windows",
            "Устаревшие временные файлы служб и установщиков Windows (старше 24 ч)",
            checked_by_default=True,
            min_age_seconds=_TEMP_SAFE_AGE_SECONDS,
        ),
        CleanTarget(
            "wu_cache",
            "Кэш обновлений Windows Update",
            "Система Windows",
            "Скачанные установочные пакеты в SoftwareDistribution\\Download",
            checked_by_default=True,
        ),
        CleanTarget(
            "wer_dumps",
            "Отчёты об ошибках и дампы памяти (WER)",
            "Система Windows",
            "Архивы отчётов о сбоях программ и системные минидампы",
            checked_by_default=True,
        ),
        CleanTarget(
            "recycle_bin",
            "Корзина Windows",
            "Система Windows",
            "Удалённые файлы во всех разделах Корзины",
            checked_by_default=True,
        ),
        CleanTarget(
            "thumbs",
            "Кэш эскизов Проводника",
            "Система Windows",
            "Файлы предпросмотра изображений thumbcache_*.db",
            checked_by_default=False,
        ),
        CleanTarget(
            "prefetch",
            "Кэш предзагрузки (Prefetch)",
            "Система Windows",
            "Трассировки запуска программ в Windows\\Prefetch",
            checked_by_default=False,
        ),
        CleanTarget(
            "chrome_cache",
            "Кэш Google Chrome",
            "Браузеры (только кэш)",
            "Дисковый кэш и шейдеры (пароли, куки и сессии сохраняются; требует закрытия Chrome)",
            checked_by_default=False,
            process_names=("chrome.exe",),
        ),
        CleanTarget(
            "edge_cache",
            "Кэш Microsoft Edge",
            "Браузеры (только кэш)",
            "Дисковый кэш и шейдеры (пароли, куки и сессии сохраняются; требует закрытия Edge)",
            checked_by_default=False,
            process_names=("msedge.exe",),
        ),
        CleanTarget(
            "firefox_cache",
            "Кэш Mozilla Firefox",
            "Браузеры (только кэш)",
            "Дисковый кэш профилей Firefox (пароли, куки и сессии сохраняются; требует закрытия Firefox)",
            checked_by_default=False,
            process_names=("firefox.exe",),
        ),
        CleanTarget(
            "dev_cache",
            "Кэш пакетов pip и npm",
            "Инструменты разработки",
            "Скачанные архивы пакетов Python (pip) и Node.js (npm) — можно очистить для освобождения места",
            checked_by_default=False,
        ),
    ]


def _is_too_recent(st: os.stat_result, min_age_seconds: int, now: float) -> bool:
    if min_age_seconds <= 0:
        return False
    latest_touch = max(st.st_mtime, getattr(st, "st_ctime", st.st_mtime))
    return (now - latest_touch) < min_age_seconds


def _scan_dir_size(
    folders: list[Path],
    pattern: str | None,
    protected: list[Path],
    min_age_seconds: int = 0,
) -> tuple[int, int]:
    files_count = 0
    total_bytes = 0
    now = time.time()
    for folder in folders:
        if not folder.is_dir():
            continue
        try:
            iterator = folder.glob(pattern) if pattern else folder.rglob("*")
            for item in iterator:
                if _is_under_protected(item, protected):
                    continue
                try:
                    if item.is_file() and not item.is_symlink():
                        st = item.stat()
                        if _is_too_recent(st, min_age_seconds, now):
                            continue
                        files_count += 1
                        total_bytes += st.st_size
                except OSError:
                    continue
        except OSError:
            continue
    return files_count, total_bytes


def analyze_junk(progress: ProgressStepCb | None = None) -> list[CleanTarget]:
    """Сканирует все категории мусора и возвращает список с размерами."""
    protected = _protected_roots()
    targets = catalog_definitions()
    total = len(targets)
    for idx, target in enumerate(targets, start=1):
        if progress:
            progress(idx, total, target.key, f"Анализ: {target.title}…")
        if target.key == "recycle_bin":
            cnt, sz = _query_recycle_bin()
            target.files_count = cnt
            target.size_bytes = sz
        else:
            folders, pattern = _target_directories(target.key)
            cnt, sz = _scan_dir_size(folders, pattern, protected, target.min_age_seconds)
            target.files_count = cnt
            target.size_bytes = sz
    return targets


def _clean_directory_contents(
    folder: Path,
    pattern: str | None,
    protected: list[Path],
    min_age_seconds: int = 0,
) -> tuple[int, int, int]:
    """Удаляет файлы в папке. Возвращает (освобождено_байт, удалено_файлов, пропущено_активных)."""
    freed = 0
    deleted = 0
    skipped = 0
    now = time.time()
    if not folder.is_dir():
        return 0, 0, 0

    if pattern:
        try:
            items = list(folder.glob(pattern))
        except OSError:
            return 0, 0, 0
        for fpath in items:
            if _is_under_protected(fpath, protected):
                continue
            try:
                st = fpath.stat()
                if _is_too_recent(st, min_age_seconds, now):
                    continue
                sz = st.st_size
                fpath.unlink()
                freed += sz
                deleted += 1
            except OSError:
                skipped += 1
        return freed, deleted, skipped

    try:
        all_items = list(folder.rglob("*"))
    except OSError:
        return 0, 0, 0

    dirs_to_remove: list[Path] = []
    for item in all_items:
        if _is_under_protected(item, protected):
            continue
        try:
            if item.is_file() or item.is_symlink():
                if not item.is_symlink():
                    st = item.stat()
                    if _is_too_recent(st, min_age_seconds, now):
                        continue
                    sz = st.st_size
                else:
                    sz = 0
                item.unlink()
                freed += sz
                deleted += 1
            elif item.is_dir():
                if min_age_seconds > 0:
                    st_dir = item.stat()
                    if _is_too_recent(st_dir, min_age_seconds, now):
                        continue
                dirs_to_remove.append(item)
        except OSError:
            skipped += 1

    for d in sorted(dirs_to_remove, key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass

    return freed, deleted, skipped


def clean_junk(
    selected_keys: Iterable[str],
    progress: ProgressStepCb | None = None,
    log: LogCb | None = None,
) -> CleanSummary:
    """Очищает выбранные категории мусора с детальным отчётом и проверкой запущенных браузеров."""
    wanted = list(dict.fromkeys(selected_keys))
    protected = _protected_roots()
    running_procs = _running_process_names()
    summary = CleanSummary()
    catalog = {t.key: t for t in catalog_definitions()}
    total = len(wanted)

    for idx, key in enumerate(wanted, start=1):
        target = catalog.get(key)
        if not target:
            continue
        if progress:
            progress(idx - 1, total, key, f"Очистка: {target.title}…")

        cat_res = CleanCategoryResult(key=key, title=target.title)

        # Проверяем, не запущен ли браузер/процесс данной категории
        active_browsers = [p for p in target.process_names if p.lower() in running_procs]
        if active_browsers:
            folders, pattern = _target_directories(key)
            rem_cnt, rem_sz = _scan_dir_size(folders, pattern, protected, target.min_age_seconds)
            cat_res.skipped_count = rem_cnt
            cat_res.remaining_files = rem_cnt
            cat_res.remaining_bytes = rem_sz
            cat_res.skipped_reason = f"Закройте {', '.join(active_browsers)} для очистки"
            cat_res.status = f"⚠ Пропущено ({cat_res.skipped_reason})"
            msg = f"⚠ {target.title}: пропущено, так как процесс {', '.join(active_browsers)} сейчас запущен"
            summary.details.append(msg)
            summary.categories[key] = cat_res
            if log:
                log(msg)
            continue

        if key == "recycle_bin":
            cnt, sz = _query_recycle_bin()
            if sz > 0 or cnt > 0:
                if _empty_recycle_bin():
                    cat_res.freed_bytes = sz
                    cat_res.deleted_count = max(cnt, 1)
                    cat_res.status = f"✔ Очищено ({fmt_bytes(sz) or '0 Б'})"
                    summary.freed_bytes += sz
                    summary.deleted_count += cat_res.deleted_count
                    msg = f"✔ {target.title}: освобождено {fmt_bytes(sz) or '0 Б'}"
                else:
                    rem_cnt, rem_sz = _query_recycle_bin()
                    cat_res.skipped_count = rem_cnt
                    cat_res.remaining_files = rem_cnt
                    cat_res.remaining_bytes = rem_sz
                    cat_res.status = "⚠ Не удалось полностью очистить"
                    summary.skipped_count += rem_cnt
                    msg = f"⚠ {target.title}: не удалось полностью очистить"
            else:
                cat_res.status = "✔ Пусто"
                msg = f"✔ {target.title}: уже пуста"
            summary.details.append(msg)
            summary.categories[key] = cat_res
            if log:
                log(msg)
            continue

        folders, pattern = _target_directories(key)
        cat_freed = 0
        cat_del = 0
        cat_skip = 0
        for folder in folders:
            f_bytes, d_cnt, s_cnt = _clean_directory_contents(
                folder, pattern, protected, target.min_age_seconds
            )
            cat_freed += f_bytes
            cat_del += d_cnt
            cat_skip += s_cnt

        rem_cnt, rem_sz = _scan_dir_size(folders, pattern, protected, target.min_age_seconds)
        cat_res.freed_bytes = cat_freed
        cat_res.deleted_count = cat_del
        cat_res.skipped_count = cat_skip
        cat_res.remaining_files = rem_cnt
        cat_res.remaining_bytes = rem_sz

        if cat_del > 0 and cat_skip == 0:
            cat_res.status = f"✔ Очищено: {fmt_bytes(cat_freed) or '0 Б'} ({cat_del} ф.)"
        elif cat_del > 0 and cat_skip > 0:
            cat_res.status = f"✔ Удалено {fmt_bytes(cat_freed) or '0 Б'} (занято ОС: {cat_skip})"
        elif cat_del == 0 and cat_skip > 0:
            cat_res.status = f"🔒 Занято процессами ({cat_skip} ф.)"
        else:
            cat_res.status = "✔ Чисто"

        summary.freed_bytes += cat_freed
        summary.deleted_count += cat_del
        summary.skipped_count += cat_skip
        summary.categories[key] = cat_res

        msg = (
            f"✔ {target.title}: удалено {cat_del} ф. ({fmt_bytes(cat_freed) or '0 Б'})"
            + (f", пропущено занятых: {cat_skip}" if cat_skip else "")
        )
        summary.details.append(msg)
        if log:
            log(msg)

    if progress:
        progress(total, total, "", "Очистка завершена")

    return summary
