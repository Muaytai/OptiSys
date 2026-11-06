"""Модели данных, которые передаются между потоками (только чистый Python, без COM-объектов)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

KV_COLUMNS: list[str] = ["Параметр", "Значение"]


@dataclass(slots=True)
class Section:
    """Табличный раздел отчёта (одна вкладка/подтаблица)."""

    key: str
    title: str
    columns: list[str]
    rows: list[list[str]]
    error: str | None = None


@dataclass(slots=True)
class GpuInfo:
    """Видеокарта и данные для ссылки на сайт производителя."""

    name: str
    driver_version: str
    driver_date: str
    vendor: str | None
    download_url: str | None


@dataclass(slots=True)
class SystemReport:
    """Полный результат сканирования."""

    scanned_at: datetime
    sections: list[Section] = field(default_factory=list)
    gpus: list[GpuInfo] = field(default_factory=list)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)


@dataclass(slots=True)
class DriverUpdate:
    """Доступное обновление драйвера из Windows Update."""

    update_id: str
    title: str
    driver_class: str
    manufacturer: str
    model: str
    version_date: str
    size_bytes: int


@dataclass(slots=True)
class InstallSummary:
    """Итог установки обновлений."""

    installed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reboot_required: bool = False


@dataclass(slots=True)
class CleanTarget:
    """Категория мусора/кэша для анализа и очистки."""

    key: str
    title: str
    group: str
    description: str
    files_count: int = 0
    size_bytes: int = 0
    checked_by_default: bool = True
    process_names: tuple[str, ...] = ()
    min_age_seconds: int = 0


@dataclass(slots=True)
class CleanCategoryResult:
    """Результат очистки одной категории."""

    key: str
    title: str
    freed_bytes: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    remaining_bytes: int = 0
    remaining_files: int = 0
    status: str = ""
    skipped_reason: str = ""


@dataclass(slots=True)
class CleanSummary:
    """Результат очистки мусора."""

    freed_bytes: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    details: list[str] = field(default_factory=list)
    categories: dict[str, CleanCategoryResult] = field(default_factory=dict)


@dataclass(slots=True)
class RegistryIssue:
    """Проблема в реестре Windows (битая ссылка/устаревшая запись)."""

    issue_id: str
    category: str
    hive_name: str
    subkey: str
    value_name: str
    value_data: str
    reason: str


@dataclass(slots=True)
class RegistryFixSummary:
    """Результат очистки реестра с путём к резервной копии."""

    fixed_count: int = 0
    backup_path: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StartupItem:
    """Элемент автозагрузки Windows."""

    item_id: str
    name: str
    command: str
    source: str
    enabled: bool
    hive: int = 0
    subkey: str = ""
    is_file: bool = False
    file_path: str = ""


@dataclass(slots=True)
class OptimizeResult:
    """Итог выполнения системной задачи оптимизации."""

    key: str
    title: str
    success: bool
    message: str
