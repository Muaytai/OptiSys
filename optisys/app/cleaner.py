"""Анализ и безопасная очистка временных файлов, кэша браузеров и системного мусора."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import shutil
import sys
from collections.abc import Callable, Iterable

from .models import CleanSummary, CleanTarget
from .utils import fmt_bytes

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]


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


def _protected_roots() -> list[Path]:
    """Папки внутри %TEMP%, которые нельзя удалять (в т.ч. распакованный PyInstaller _MEIPASS)."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            roots.append(Path(meipass).resolve())
        except OSError:
            pass
    return roots


def _is_under_protected(path: Path, protected: list[Path]) -> bool:
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
    """Находит только кэш-папки Chromium-браузера во всех профилях (куки и пароли не затрагиваются)."""
    if not user_data_dir.is_dir():
        return []
    dirs: list[Path] = []
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
            net_cand = prof / "Network" / sub
            if net_cand.is_dir():
                dirs.append(net_cand)
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


def _target_directories(key: str) -> tuple[list[Path], str | None]:
    """Возвращает (список папок, опциональная маска файлов) для категории."""
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    win = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    temp = Path(os.environ.get("TEMP", str(local / "Temp")))

    mapping: dict[str, tuple[list[Path], str | None]] = {
        "user_temp": ([temp], None),
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


def _catalog_definitions() -> list[CleanTarget]:
    return [
        CleanTarget("user_temp", "Временные файлы пользователя (%TEMP%)", "Система", "Временные файлы приложений текущего пользователя", checked_by_default=True),
        CleanTarget("win_temp", "Системные временные файлы (Windows\\Temp)", "Система", "Временные файлы служб и установщиков Windows", checked_by_default=True),
        CleanTarget("wu_cache", "Кэш обновлений Windows Update", "Система", "Скачанные пакеты в SoftwareDistribution\\Download", checked_by_default=True),
        CleanTarget("wer_dumps", "Отчёты об ошибках и дампы памяти (WER)", "Система", "Архивы сбоев программ и минидампы", checked_by_default=True),
        CleanTarget("thumbs", "Кэш эскизов Проводника", "Система", "Файлы предпросмотра изображений thumbcache_*.db", checked_by_default=False),
        CleanTarget("prefetch", "Кэш предзагрузки (Prefetch)", "Система", "Трассировки запуска программ в Windows\\Prefetch", checked_by_default=False),
        CleanTarget("recycle_bin", "Корзина Windows", "Система", "Удалённые файлы во всех разделах Корзины", checked_by_default=True),
        CleanTarget("chrome_cache", "Кэш Google Chrome", "Браузеры", "Кэш страниц и шейдеров (пароли, куки и история сохраняются)", checked_by_default=True),
        CleanTarget("edge_cache", "Кэш Microsoft Edge", "Браузеры", "Кэш страниц и шейдеров (пароли, куки и история сохраняются)", checked_by_default=True),
        CleanTarget("firefox_cache", "Кэш Mozilla Firefox", "Браузеры", "Дисковый кэш профилей Firefox", checked_by_default=True),
        CleanTarget("dev_cache", "Кэш пакетов pip и npm", "Разработка", "Скачанные архивы колёс Python (pip) и пакетов Node.js (npm)", checked_by_default=False),
    ]


def _scan_dir_size(folders: list[Path], pattern: str | None, protected: list[Path]) -> tuple[int, int]:
    files_count = 0
    total_bytes = 0
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
                        files_count += 1
                        total_bytes += st.st_size
                except OSError:
                    continue
        except OSError:
            continue
    return files_count, total_bytes


def analyze_junk(progress: ProgressCb | None = None) -> list[CleanTarget]:
    """Сканирует все категории мусора и возвращает список с размерами."""
    protected = _protected_roots()
    targets = _catalog_definitions()
    for target in targets:
        if progress:
            progress(f"Анализ: {target.title}…")
        if target.key == "recycle_bin":
            cnt, sz = _query_recycle_bin()
            target.files_count = cnt
            target.size_bytes = sz
        else:
            folders, pattern = _target_directories(target.key)
            cnt, sz = _scan_dir_size(folders, pattern, protected)
            target.files_count = cnt
            target.size_bytes = sz
    return targets


def _clean_directory_contents(folder: Path, pattern: str | None, protected: list[Path]) -> tuple[int, int, int]:
    """Удаляет файлы в папке. Возвращает (освобождено_байт, удалено_файлов, пропущено)."""
    freed = 0
    deleted = 0
    skipped = 0
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
                sz = fpath.stat().st_size
                fpath.unlink()
                freed += sz
                deleted += 1
            except OSError:
                skipped += 1
        return freed, deleted, skipped

    # Рекурсивно удаляем файлы, затем пустые подпапки
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
                sz = item.stat().st_size if not item.is_symlink() else 0
                item.unlink()
                freed += sz
                deleted += 1
            elif item.is_dir():
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
    progress: ProgressCb | None = None,
) -> CleanSummary:
    """Очищает выбранные категории мусора и возвращает сводку."""
    wanted = set(selected_keys)
    protected = _protected_roots()
    summary = CleanSummary()
    catalog = {t.key: t for t in _catalog_definitions()}

    for key in wanted:
        target = catalog.get(key)
        if not target:
            continue
        if progress:
            progress(f"Очистка: {target.title}…")

        if key == "recycle_bin":
            cnt, sz = _query_recycle_bin()
            if sz > 0 or cnt > 0:
                if _empty_recycle_bin():
                    summary.freed_bytes += sz
                    summary.deleted_count += max(cnt, 1)
                    summary.details.append(f"✔ {target.title}: освобождено {fmt_bytes(sz)}")
                else:
                    summary.skipped_count += cnt
                    summary.details.append(f"⚠ {target.title}: не удалось полностью очистить")
            continue

        folders, pattern = _target_directories(key)
        cat_freed = 0
        cat_del = 0
        cat_skip = 0
        for folder in folders:
            f_bytes, d_cnt, s_cnt = _clean_directory_contents(folder, pattern, protected)
            cat_freed += f_bytes
            cat_del += d_cnt
            cat_skip += s_cnt

        summary.freed_bytes += cat_freed
        summary.deleted_count += cat_del
        summary.skipped_count += cat_skip
        summary.details.append(
            f"✔ {target.title}: удалено {cat_del} файлов ({fmt_bytes(cat_freed)}), занято процессами: {cat_skip}"
        )

    return summary

