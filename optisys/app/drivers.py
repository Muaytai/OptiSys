"""Поиск и установка драйверов через Windows Update Agent API (COM).

COM-объекты WUA нельзя передавать между потоками, поэтому установка
повторно выполняет поиск и выбирает обновления по UpdateID.
Вызывать только из потока с pythoncom.CoInitialize(); нужны права администратора.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

import pywintypes
import win32com.client

from .models import DriverUpdate, InstallSummary
from .utils import to_str

logger = logging.getLogger(__name__)

SEARCH_CRITERIA = "IsInstalled=0 and IsHidden=0 and Type='Driver'"
CLIENT_ID = "optisys"
SS_WINDOWS_UPDATE = 2  # ServerSelection: напрямую Windows Update (минуя WSUS)

# OperationResultCode
_RC_SUCCEEDED = 2
_RC_SUCCEEDED_WITH_ERRORS = 3
_RC_NAMES = {0: "не начато", 1: "выполняется", 2: "успешно", 3: "успешно с ошибками", 4: "ошибка", 5: "прервано"}

ProgressCb = Callable[[int, int, str], None]
LogCb = Callable[[str], None]


class WindowsUpdateError(RuntimeError):
    """Ошибка WUA с понятным текстом."""


def _session() -> Any:
    session = win32com.client.Dispatch("Microsoft.Update.Session")
    session.ClientApplicationID = CLIENT_ID
    return session


def _search_raw(session: Any) -> list[Any]:
    searcher = session.CreateUpdateSearcher()
    searcher.ServerSelection = SS_WINDOWS_UPDATE
    try:
        result = searcher.Search(SEARCH_CRITERIA)
    except pywintypes.com_error as exc:
        raise WindowsUpdateError(f"Поиск обновлений не удался: {_com_message(exc)}") from exc
    return [result.Updates.Item(i) for i in range(result.Updates.Count)]


def _com_message(exc: pywintypes.com_error) -> str:
    hresult = exc.args[0] if exc.args else 0
    return f"HRESULT 0x{hresult & 0xFFFFFFFF:08X} {exc}"


def _attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except (AttributeError, pywintypes.com_error):
        return None


def _to_model(update: Any) -> DriverUpdate:
    ver_date = _attr(update, "DriverVerDate")
    return DriverUpdate(
        update_id=to_str(update.Identity.UpdateID),
        title=to_str(update.Title),
        driver_class=to_str(_attr(update, "DriverClass")),
        manufacturer=to_str(_attr(update, "DriverManufacturer") or _attr(update, "DriverProvider")),
        model=to_str(_attr(update, "DriverModel")),
        version_date=ver_date.strftime("%Y-%m-%d") if hasattr(ver_date, "strftime") else to_str(ver_date),
        size_bytes=int(_attr(update, "MaxDownloadSize") or 0),
    )


def search_driver_updates() -> list[DriverUpdate]:
    """Список доступных (не установленных) обновлений драйверов."""
    return [_to_model(u) for u in _search_raw(_session())]


def install_driver_updates(
    update_ids: Iterable[str],
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> InstallSummary:
    """Скачивает и устанавливает выбранные обновления по одному (для прогресса)."""
    wanted = set(update_ids)
    summary = InstallSummary()
    session = _session()
    targets = [u for u in _search_raw(session) if to_str(u.Identity.UpdateID) in wanted]

    missing = len(wanted) - len(targets)
    if missing and log:
        log(f"Не найдено при повторном поиске: {missing} (возможно, уже установлены)")

    total = len(targets)
    for index, update in enumerate(targets, start=1):
        title = to_str(update.Title)
        if progress:
            progress(index - 1, total, f"Скачивание: {title}")
        try:
            _install_one(session, update, title, summary, progress, index, total, log)
        except pywintypes.com_error as exc:
            msg = _com_message(exc)
            logger.exception("Ошибка установки %s", title)
            summary.failed.append(f"{title} — {msg}")
            if log:
                log(f"✖ {title}: {msg}")
    if progress:
        progress(total, total, "Готово")
    return summary


def _install_one(
    session: Any,
    update: Any,
    title: str,
    summary: InstallSummary,
    progress: ProgressCb | None,
    index: int,
    total: int,
    log: LogCb | None,
) -> None:
    if not update.EulaAccepted:
        update.AcceptEula()

    coll = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
    coll.Add(update)

    downloader = session.CreateUpdateDownloader()
    downloader.Updates = coll
    dl = downloader.Download()
    if dl.ResultCode not in (_RC_SUCCEEDED, _RC_SUCCEEDED_WITH_ERRORS):
        summary.failed.append(f"{title} — скачивание: {_RC_NAMES.get(dl.ResultCode, dl.ResultCode)}")
        if log:
            log(f"✖ Скачивание не удалось: {title}")
        return

    if progress:
        progress(index - 1, total, f"Установка: {title}")
    installer = session.CreateUpdateInstaller()
    installer.Updates = coll
    res = installer.Install()

    if res.RebootRequired:
        summary.reboot_required = True
    if res.ResultCode in (_RC_SUCCEEDED, _RC_SUCCEEDED_WITH_ERRORS):
        summary.installed.append(title)
        if log:
            log(f"✔ Установлено: {title}")
    else:
        summary.failed.append(f"{title} — {_RC_NAMES.get(res.ResultCode, res.ResultCode)}")
        if log:
            log(f"✖ Установка не удалась: {title}")

