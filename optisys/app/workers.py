"""Фоновые потоки Qt: COM инициализируется внутри каждого потока."""
from __future__ import annotations

import logging

import pythoncom
import pywintypes
from PySide6.QtCore import QThread, Signal

from .cleaner import analyze_junk, clean_junk
from .drivers import WindowsUpdateError, install_driver_updates, search_driver_updates
from .models import RegistryIssue
from .optimizer import run_optimization_task
from .registry_cleaner import fix_registry_issues, scan_registry_issues
from .scanner import scan_system

logger = logging.getLogger(__name__)


class ScanWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)  # SystemReport
    failed = Signal(str)

    def run(self) -> None:
        pythoncom.CoInitialize()
        try:
            self.succeeded.emit(scan_system(self.progress.emit))
        except pywintypes.com_error as exc:
            logger.exception("Сканирование не удалось")
            self.failed.emit(f"Ошибка WMI: {exc}")
        finally:
            pythoncom.CoUninitialize()


class SearchUpdatesWorker(QThread):
    succeeded = Signal(list)  # list[DriverUpdate]
    failed = Signal(str)

    def run(self) -> None:
        pythoncom.CoInitialize()
        try:
            self.succeeded.emit(search_driver_updates())
        except (WindowsUpdateError, pywintypes.com_error) as exc:
            logger.exception("Поиск обновлений не удался")
            self.failed.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


class InstallUpdatesWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    succeeded = Signal(object)  # InstallSummary
    failed = Signal(str)

    def __init__(self, update_ids: list[str]) -> None:
        super().__init__()
        self._ids = update_ids

    def run(self) -> None:
        pythoncom.CoInitialize()
        try:
            summary = install_driver_updates(self._ids, self.progress.emit, self.log.emit)
            self.succeeded.emit(summary)
        except (WindowsUpdateError, pywintypes.com_error) as exc:
            logger.exception("Установка обновлений не удалась")
            self.failed.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()


class AnalyzeJunkWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(list)  # list[CleanTarget]
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.succeeded.emit(analyze_junk(self.progress.emit))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка анализа мусора")
            self.failed.emit(str(exc))


class CleanJunkWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)  # CleanSummary
    failed = Signal(str)

    def __init__(self, keys: list[str]) -> None:
        super().__init__()
        self._keys = keys

    def run(self) -> None:
        try:
            self.succeeded.emit(clean_junk(self._keys, self.progress.emit))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка очистки мусора")
            self.failed.emit(str(exc))


class ScanRegistryWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(list)  # list[RegistryIssue]
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.succeeded.emit(scan_registry_issues(self.progress.emit))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка сканирования реестра")
            self.failed.emit(str(exc))


class FixRegistryWorker(QThread):
    succeeded = Signal(object)  # RegistryFixSummary
    failed = Signal(str)

    def __init__(self, issues: list[RegistryIssue], selected_ids: list[str]) -> None:
        super().__init__()
        self._issues = issues
        self._selected = selected_ids

    def run(self) -> None:
        try:
            self.succeeded.emit(fix_registry_issues(self._issues, self._selected))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка очистки реестра")
            self.failed.emit(str(exc))


class OptimizeTaskWorker(QThread):
    succeeded = Signal(object)  # OptimizeResult
    failed = Signal(str)

    def __init__(self, task_key: str) -> None:
        super().__init__()
        self._task_key = task_key

    def run(self) -> None:
        try:
            self.succeeded.emit(run_optimization_task(self._task_key))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка задачи оптимизации")
            self.failed.emit(str(exc))
