"""Главное окно приложения с современным дашбордом, очисткой, оптимизацией и анимацией."""
from __future__ import annotations

import html
import logging
import os
from pathlib import Path
import re
import subprocess

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .cleaner import catalog_definitions
from .models import (
    CleanSummary,
    CleanTarget,
    DriverUpdate,
    InstallSummary,
    LargeFileItem,
    OptimizeResult,
    RegistryFixSummary,
    RegistryIssue,
    Section,
    StartupItem,
    SystemReport,
)
from .optimizer import (
    estimate_startup_impact,
    get_memory_usage,
    list_startup_items,
    list_system_tweaks,
    set_startup_item_enabled,
    set_system_tweak,
)
from .registry_cleaner import extract_executable_path
from .report import export_html, export_json
from .theme import ACCENT, APP_STYLESHEET, DANGER, SUCCESS, TEXT_SECONDARY, WARNING
from .utils import fmt_bytes, is_admin, relaunch_as_admin
from .widgets import AnimatedRingGauge, AnimatedStackedWidget, HeroCard, SidebarButton, SpinnerWidget
from .workers import (
    AnalyzeJunkWorker,
    CleanJunkWorker,
    FixRegistryWorker,
    InstallUpdatesWorker,
    OptimizeTaskWorker,
    ScanLargeFilesWorker,
    ScanRegistryWorker,
    ScanWorker,
    SearchUpdatesWorker,
)

logger = logging.getLogger(__name__)

APP_TITLE = "OptiSys — диагностика, очистка и оптимизация системы"

_EXTRA_STYLESHEET = f"""
QMessageBox, QDialog {{
    background-color: #181f2c;
    color: #f1f5f9;
}}
QMessageBox QLabel {{
    color: #f1f5f9;
    font-size: 13px;
    background: transparent;
}}
QMessageBox QPushButton {{
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #60a5fa;
    border-radius: 7px;
    padding: 6px 18px;
    min-width: 80px;
    font-weight: 600;
}}
QMessageBox QPushButton:hover {{
    background-color: #1d4ed8;
    border-color: #93c5fd;
}}
QCheckBox {{
    color: #f1f5f9;
    font-size: 13px;
    font-weight: 600;
    spacing: 9px;
    background: transparent;
}}
QCheckBox:hover {{
    color: #60a5fa;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 2px solid #475569;
    background-color: #111620;
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: #93c5fd;
}}
QToolTip {{
    background-color: #1e293b;
    color: #f1f5f9;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}
"""


def _parse_size_gb(text: str) -> float:
    """Извлекает размер в гигабайтах из строки вида '238.5 ГБ' или '512.0 МБ'."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(Б|КБ|МБ|ГБ|ТБ)", text or "")
    if not match:
        return 0.0
    try:
        val = float(match.group(1).replace(",", "."))
    except ValueError:
        return 0.0
    unit = match.group(2)
    factors = {"Б": 1 / 1024**3, "КБ": 1 / 1024**2, "МБ": 1 / 1024, "ГБ": 1.0, "ТБ": 1024.0}
    return val * factors.get(unit, 0.0)


class DataTable(QWidget):
    """Современная таблица в карточке с опциональным поиском."""

    def __init__(self, title: str = "", with_filter: bool = False) -> None:
        super().__init__()
        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.horizontalHeader().setHighlightSections(False)

        self.error_label = QLabel()
        self.error_label.setStyleSheet(f"color: {DANGER}; font-weight: 600;")
        self.error_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if title or with_filter:
            top = QHBoxLayout()
            if title:
                lbl = QLabel(title)
                lbl.setObjectName("SectionTitle")
                top.addWidget(lbl)
            top.addStretch(1)
            if with_filter:
                search = QLineEdit()
                search.setPlaceholderText("🔍 Быстрый фильтр по всем колонкам…")
                search.setClearButtonEnabled(True)
                search.setMinimumWidth(320)
                search.textChanged.connect(self.proxy.setFilterFixedString)
                top.addWidget(search)
            layout.addLayout(top)

        layout.addWidget(self.error_label)
        layout.addWidget(self.view, stretch=1)

    def load(self, section: Section | None) -> None:
        self.model.clear()
        if section is None:
            return
        if section.error:
            self.error_label.setText(f"Ошибка сбора данных: {section.error}")
            self.error_label.show()
        else:
            self.error_label.hide()
        self.model.setHorizontalHeaderLabels(section.columns)
        for row in section.rows:
            items = [QStandardItem(v) for v in row]
            self.model.appendRow(items)
        self.view.resizeColumnsToContents()
        for col in range(self.model.columnCount() - 1):
            w = self.view.columnWidth(col)
            self.view.setColumnWidth(col, min(max(w + 20, 110), 420))


class MainWindow(QMainWindow):
    _NAV_ITEMS: list[tuple[str, str]] = [
        ("overview", "Обзор"),
        ("cleaner", "Очистка"),
        ("optimize", "Оптимизация"),
        ("cpu", "Процессор"),
        ("memory", "Память"),
        ("disks", "Диски"),
        ("video", "Видео"),
        ("monitors", "Мониторы"),
        ("network", "Сеть"),
        ("audio", "Звук"),
        ("drivers", "Драйверы"),
        ("updates", "Обновления"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(980, 620)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(1240, max(980, int(avail.width() * 0.92)))
            h = min(780, max(620, int(avail.height() * 0.90)))
            self.resize(w, h)
        else:
            self.resize(1200, 760)
        self.setStyleSheet(APP_STYLESHEET + _EXTRA_STYLESHEET)

        self._report: SystemReport | None = None
        self._updates: list[DriverUpdate] = []
        self._clean_targets: list[CleanTarget] = []
        self._reg_issues: list[RegistryIssue] = []
        self._startup_items: list[StartupItem] = []
        self._large_files: list[LargeFileItem] = []
        self._tweak_checks: dict[str, QCheckBox] = {}
        self._tweak_loading: bool = False
        self._session_freed_bytes: int = 0
        self._startup_loading: bool = False

        self._scan_worker: ScanWorker | None = None
        self._search_worker: SearchUpdatesWorker | None = None
        self._install_worker: InstallUpdatesWorker | None = None
        self._analyze_worker: AnalyzeJunkWorker | None = None
        self._clean_worker: CleanJunkWorker | None = None
        self._reg_scan_worker: ScanRegistryWorker | None = None
        self._reg_fix_worker: FixRegistryWorker | None = None
        self._opt_worker: OptimizeTaskWorker | None = None
        self._large_worker: ScanLargeFilesWorker | None = None

        self._tables: dict[str, DataTable] = {}
        self._nav_buttons: dict[str, SidebarButton] = {}
        self._page_indices: dict[str, int] = {}

        self._build_layout()
        self._update_admin_status()
        self._select_page("overview")
        self._refresh_live_ram()
        self._ram_timer = QTimer(self)
        self._ram_timer.setInterval(2500)
        self._ram_timer.timeout.connect(self._refresh_live_ram)
        self._ram_timer.start()

        self._search_elapsed_sec: int = 0
        self._search_ui_timer = QTimer(self)
        self._search_ui_timer.setInterval(1000)
        self._search_ui_timer.timeout.connect(self._on_search_timer_tick)
        self.start_scan()

    # ---------- Построение интерфейса ----------

    def _build_layout(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        main_h = QHBoxLayout(root)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        main_h.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_header())

        self.stack = AnimatedStackedWidget()
        content_wrap = QVBoxLayout()
        content_wrap.setContentsMargins(22, 18, 22, 18)
        content_wrap.addWidget(self.stack)
        right.addLayout(content_wrap, stretch=1)

        main_h.addLayout(right, stretch=1)
        self._populate_pages()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(195)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 16, 10, 14)
        layout.setSpacing(3)

        brand = QLabel("⚡ OPTISYS")
        brand.setStyleSheet("font-size: 16px; font-weight: 800; letter-spacing: 1.2px; padding: 4px 12px 10px 12px;")
        layout.addWidget(brand)

        for key, title in self._NAV_ITEMS:
            btn = SidebarButton(key, title)
            btn.clicked.connect(lambda _=False, k=key: self._select_page(k))
            self._nav_buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

        self.footer_lbl = QLabel("WMI · WUA · Cleaner")
        self.footer_lbl.setObjectName("MutedLabel")
        self.footer_lbl.setContentsMargins(12, 4, 12, 0)
        layout.addWidget(self.footer_lbl)
        return sidebar

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(60)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(12)

        self.spinner = SpinnerWidget(22)
        layout.addWidget(self.spinner)

        self.status_title = QLabel("Готов к работе")
        self.status_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.status_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_title, stretch=1)

        self.btn_scan = QPushButton("🔄 Сканировать")
        self.btn_scan.setObjectName("PrimaryBtn")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.clicked.connect(self.start_scan)

        self.btn_html = QPushButton("Экспорт HTML")
        self.btn_html.setObjectName("ActionBtn")
        self.btn_html.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_html.setEnabled(False)
        self.btn_html.clicked.connect(lambda: self._export("html"))

        self.btn_json = QPushButton("Экспорт JSON")
        self.btn_json.setObjectName("ActionBtn")
        self.btn_json.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_json.setEnabled(False)
        self.btn_json.clicked.connect(lambda: self._export("json"))

        for btn in (self.btn_scan, self.btn_html, self.btn_json):
            layout.addWidget(btn)
        return header

    def _populate_pages(self) -> None:
        self._register_page("overview", self._build_overview_page())
        self._register_page("cleaner", self._build_cleaner_page())
        self._register_page("optimize", self._build_optimize_page())
        for key, title in (
            ("cpu", "Процессор"),
            ("memory", "Оперативная память"),
        ):
            self._register_table_page(key, title)

        self._register_page("disks", self._build_disks_page())
        self._register_page("video", self._build_video_page())

        for key, title in (
            ("monitors", "Мониторы"),
            ("network", "Сетевые адаптеры"),
            ("audio", "Аудиоустройства"),
        ):
            self._register_table_page(key, title)

        self._register_table_page("drivers", "Установленные драйверы PnP", with_filter=True)
        self._register_page("updates", self._build_updates_page())

    def _register_page(self, key: str, widget: QWidget) -> None:
        idx = self.stack.addWidget(widget)
        self._page_indices[key] = idx

    def _register_table_page(self, key: str, title: str, with_filter: bool = False) -> None:
        table = DataTable(title=title, with_filter=with_filter)
        self._tables[key] = table
        self._register_page(key, table)

    def _select_page(self, key: str) -> None:
        for k, btn in self._nav_buttons.items():
            btn.set_active(k == key)
        if key in self._page_indices:
            self.stack.setCurrentIndex(self._page_indices[key])
        if key == "optimize":
            if not self._startup_items:
                self.refresh_startup_items()
            self.refresh_system_tweaks()

    def _any_worker_running(self) -> bool:
        workers = (
            self._scan_worker,
            self._search_worker,
            self._install_worker,
            self._analyze_worker,
            self._clean_worker,
            self._reg_scan_worker,
            self._reg_fix_worker,
            self._opt_worker,
            self._large_worker,
        )
        return any(w is not None and w.isRunning() for w in workers)

    def _stop_spinner_if_idle(self) -> None:
        if not self._any_worker_running():
            self.spinner.stop()

    # ---------- Страница «Обзор» (Дашборд с графикой) ----------

    def _build_overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        cards_grid = QGridLayout()
        cards_grid.setSpacing(12)
        self.card_os = HeroCard("overview", "Операционная система", ACCENT)
        self.card_cpu = HeroCard("cpu", "Процессор", "#8b5cf6")
        self.card_gpu = HeroCard("video", "Видеокарта", SUCCESS)
        self.card_ram = HeroCard("memory", "Память и драйверы", "#06b6d4")

        cards_grid.addWidget(self.card_os, 0, 0)
        cards_grid.addWidget(self.card_cpu, 0, 1)
        cards_grid.addWidget(self.card_gpu, 0, 2)
        cards_grid.addWidget(self.card_ram, 0, 3)
        for col in range(4):
            cards_grid.setColumnStretch(col, 1)
        layout.addLayout(cards_grid)

        mid_row = QHBoxLayout()
        mid_row.setSpacing(14)

        gauges_card = QFrame()
        gauges_card.setObjectName("Card")
        gauges_layout = QVBoxLayout(gauges_card)
        gauges_layout.setContentsMargins(16, 14, 16, 14)

        g_hdr = QHBoxLayout()
        g_title = QLabel("Визуальная сводка (Live)")
        g_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        g_hdr.addWidget(g_title)
        g_hdr.addStretch(1)

        btn_quick_ram = QPushButton("🧠 Освободить ОЗУ")
        btn_quick_ram.setObjectName("ActionBtn")
        btn_quick_ram.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_quick_ram.setToolTip("Мгновенно очистить рабочие наборы фоновых процессов в оперативной памяти")
        btn_quick_ram.clicked.connect(lambda: self.start_optimize_task("free_ram"))
        g_hdr.addWidget(btn_quick_ram)
        gauges_layout.addLayout(g_hdr)

        rings_row = QHBoxLayout()
        rings_row.setSpacing(6)
        self.gauge_ram = AnimatedRingGauge("Загрузка ОЗУ", "ожидание…", "#8b5cf6")
        self.gauge_sys_disk = AnimatedRingGauge("Системный диск", "ожидание…", ACCENT)
        self.gauge_all_disks = AnimatedRingGauge("Все разделы", "ожидание…", "#06b6d4")
        self.gauge_signed = AnimatedRingGauge("Подписаны WHQL", "ожидание…", SUCCESS, higher_is_better=True)
        rings_row.addWidget(self.gauge_ram)
        rings_row.addWidget(self.gauge_sys_disk)
        rings_row.addWidget(self.gauge_all_disks)
        rings_row.addWidget(self.gauge_signed)
        gauges_layout.addLayout(rings_row)
        gauges_layout.addStretch(1)

        mid_row.addWidget(gauges_card, stretch=6)

        overview_table = DataTable(title="Параметры системы")
        self._tables["overview"] = overview_table
        mid_row.addWidget(overview_table, stretch=5)

        layout.addLayout(mid_row, stretch=1)
        return page

    # ---------- Страница «Очистка» (в стиле CCleaner) ----------

    def _build_cleaner_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Сводные карточки очистки
        cards = QGridLayout()
        cards.setSpacing(12)
        self.card_junk_found = HeroCard("cleaner", "Обнаружено мусора", WARNING)
        self.card_junk_found.set_content("Не проверено", "Нажмите «Анализировать систему»")
        self.card_junk_selected = HeroCard("disks", "Выбрано к очистке", ACCENT)
        self.card_junk_selected.set_content("0 Б", "Отметьте нужные категории")
        self.card_junk_freed = HeroCard("optimize", "Освобождено за сессию", SUCCESS)
        self.card_junk_freed.set_content("0 Б", "Безопасное удаление кэша и старых Temp")

        cards.addWidget(self.card_junk_found, 0, 0)
        cards.addWidget(self.card_junk_selected, 0, 1)
        cards.addWidget(self.card_junk_freed, 0, 2)
        for c in range(3):
            cards.setColumnStretch(c, 1)
        layout.addLayout(cards)

        # Панель действий и управления выбором (CCleaner Pro style)
        bar = QFrame()
        bar.setObjectName("Card")
        bar_outer = QVBoxLayout(bar)
        bar_outer.setContentsMargins(16, 12, 16, 12)
        bar_outer.setSpacing(10)

        bar_layout = QHBoxLayout()
        bar_layout.setSpacing(10)

        lbl_hint = QLabel(
            "🛡 Активные сессии, куки, пароли и файлы моложе 24 ч в %TEMP% защищены."
        )
        lbl_hint.setObjectName("MutedLabel")
        lbl_hint.setWordWrap(True)
        lbl_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.btn_sel_rec = QPushButton("✔ Рекомендуемые")
        self.btn_sel_rec.setObjectName("ActionBtn")
        self.btn_sel_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sel_rec.setToolTip("Выбрать только безопасные системные категории (без браузеров и кэша пакетов)")
        self.btn_sel_rec.clicked.connect(lambda: self._select_cleaner_preset("recommended"))

        self.btn_sel_all = QPushButton("☑ Выбрать все")
        self.btn_sel_all.setObjectName("ActionBtn")
        self.btn_sel_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sel_all.clicked.connect(lambda: self._select_cleaner_preset("all"))

        self.btn_sel_none = QPushButton("☐ Снять выбор")
        self.btn_sel_none.setObjectName("ActionBtn")
        self.btn_sel_none.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sel_none.clicked.connect(lambda: self._select_cleaner_preset("none"))

        self.btn_analyze_junk = QPushButton("🔍 Анализировать систему")
        self.btn_analyze_junk.setObjectName("PrimaryBtn")
        self.btn_analyze_junk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze_junk.clicked.connect(self.start_analyze_junk)

        self.btn_clean_junk = QPushButton("🧹 Очистить выбранное")
        self.btn_clean_junk.setObjectName("SuccessBtn")
        self.btn_clean_junk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clean_junk.setEnabled(False)
        self.btn_clean_junk.clicked.connect(self.start_clean_junk)

        bar_layout.addWidget(lbl_hint, stretch=1)
        bar_layout.addWidget(self.btn_sel_rec)
        bar_layout.addWidget(self.btn_sel_all)
        bar_layout.addWidget(self.btn_sel_none)
        bar_layout.addWidget(self.btn_analyze_junk)
        bar_layout.addWidget(self.btn_clean_junk)
        bar_outer.addLayout(bar_layout)

        # Визуальный прогресс-бар анализа и очистки
        self.cleaner_progress = QProgressBar()
        self.cleaner_progress.setTextVisible(True)
        self.cleaner_progress.hide()
        bar_outer.addWidget(self.cleaner_progress)

        layout.addWidget(bar)

        # Таблица категорий
        self.cleaner_model = QStandardItemModel(self)
        self.cleaner_model.setHorizontalHeaderLabels(
            ["Категория", "Группа", "Описание", "Файлов", "Размер", "Статус / Результат"]
        )
        self.cleaner_model.itemChanged.connect(self._recalc_selected_junk)

        self.cleaner_view = QTableView()
        self.cleaner_view.setModel(self.cleaner_model)
        self.cleaner_view.setAlternatingRowColors(True)
        self.cleaner_view.setShowGrid(False)
        self.cleaner_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cleaner_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cleaner_view.verticalHeader().setVisible(False)
        self.cleaner_view.verticalHeader().setDefaultSectionSize(36)
        self.cleaner_view.horizontalHeader().setStretchLastSection(True)
        self.cleaner_view.horizontalHeader().setHighlightSections(False)
        layout.addWidget(self.cleaner_view, stretch=3)

        # Журнал операций очистки (как детальный отчёт в CCleaner Pro)
        self.cleaner_log = QPlainTextEdit()
        self.cleaner_log.setReadOnly(True)
        self.cleaner_log.setPlaceholderText(
            "Детальный журнал анализа и очистки (здесь отображается ход выполнения по каждой категории)…"
        )
        self.cleaner_log.setMaximumHeight(125)
        self.cleaner_log.setMaximumBlockCount(1000)
        layout.addWidget(self.cleaner_log)

        self._populate_initial_cleaner_catalog()
        return page

    # ---------- Страница «Оптимизация» (Реестр + Автозагрузка + Твики) ----------

    def _build_optimize_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Верхний блок: Наглядные карточки ускорения ПК (понятно для любого пользователя)
        quick_card = QFrame()
        quick_card.setObjectName("Card")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(16, 12, 16, 12)
        quick_layout.setSpacing(10)

        q_hdr = QHBoxLayout()
        q_title = QLabel("⚡ Ускорение работы компьютера и интернета")
        q_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.opt_status_lbl = QLabel("Выберите действие ниже или нажмите «Ускорить всё в 1 клик»")
        self.opt_status_lbl.setObjectName("MutedLabel")
        self.opt_status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.btn_opt_all = QPushButton("🚀 Ускорить всё в 1 клик")
        self.btn_opt_all.setObjectName("AccentPurpleBtn")
        self.btn_opt_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_opt_all.setToolTip("Выполнить быструю настройку интернета, сети и системного диска")
        self.btn_opt_all.clicked.connect(lambda: self.start_optimize_task("all_fast"))

        q_hdr.addWidget(q_title)
        q_hdr.addSpacing(12)
        q_hdr.addWidget(self.opt_status_lbl, stretch=1)
        q_hdr.addSpacing(10)
        q_hdr.addWidget(self.btn_opt_all)
        quick_layout.addLayout(q_hdr)

        cards_row = QGridLayout()
        cards_row.setSpacing(10)
        self._opt_buttons: list[QPushButton] = [self.btn_opt_all]
        self._opt_card_status: dict[str, QLabel] = {}

        opt_specs = [
            (
                "flush_dns",
                "🌐 Ускорение интернета",
                "Обновляет адреса сайтов. Помогает, если страницы долго загружаются или не открываются.",
                "Ускорить сайты",
                "PrimaryBtn",
            ),
            (
                "arp_net_cache",
                "📡 Сброс сетевых задержек",
                "Безопасно обновляет сетевой кэш и DNS для стабильной связи в онлайн-играх, VPN и звонках.",
                "Обновить сеть",
                "PrimaryBtn",
            ),
            (
                "free_ram",
                "🧠 Освобождение ОЗУ",
                "Сбрасывает неиспользуемый кэш фоновых процессов и мгновенно освобождает оперативную память.",
                "Освободить ОЗУ",
                "PrimaryBtn",
            ),
            (
                "trim_ssd",
                "💽 Ускорение диска (SSD)",
                "Восстанавливает скорость чтения и записи системного диска C: и продлевает срок его службы.",
                "Ускорить диск C:",
                "SuccessBtn",
            ),
            (
                "winsxs_clean",
                "📦 Очистка старых обновлений",
                "Удаляет ненужные резервные копии прошлых обновлений Windows (занимает 2–5 минут).",
                "Очистить обновления",
                "ActionBtn",
            ),
        ]

        for col, (task_key, title_txt, desc_txt, btn_txt, btn_style) in enumerate(opt_specs):
            item_frame = QFrame()
            item_frame.setObjectName("CardHover")
            item_v = QVBoxLayout(item_frame)
            item_v.setContentsMargins(12, 10, 12, 10)
            item_v.setSpacing(6)

            lbl_t = QLabel(title_txt)
            lbl_t.setStyleSheet("font-size: 13px; font-weight: 700;")
            item_v.addWidget(lbl_t)

            lbl_d = QLabel(desc_txt)
            lbl_d.setObjectName("MutedLabel")
            lbl_d.setWordWrap(True)
            item_v.addWidget(lbl_d, stretch=1)

            st_lbl = QLabel("Готово к запуску")
            st_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
            item_v.addWidget(st_lbl)
            self._opt_card_status[task_key] = st_lbl

            btn = QPushButton(btn_txt)
            btn.setObjectName(btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=task_key: self.start_optimize_task(k))
            item_v.addWidget(btn)
            self._opt_buttons.append(btn)

            cards_row.addWidget(item_frame, 0, col)
            cards_row.setColumnStretch(col, 1)

        quick_layout.addLayout(cards_row)

        self.opt_progress = QProgressBar()
        self.opt_progress.setTextVisible(True)
        self.opt_progress.hide()
        quick_layout.addWidget(self.opt_progress)

        layout.addWidget(quick_card)

        # Компактная панель быстрых переключателей Windows 11
        tweaks_card = QFrame()
        tweaks_card.setObjectName("Card")
        tweaks_layout = QVBoxLayout(tweaks_card)
        tweaks_layout.setContentsMargins(16, 10, 16, 10)
        tweaks_layout.setSpacing(6)

        tw_hdr = QHBoxLayout()
        tw_title = QLabel("⚙ Быстрые настройки Windows 11 (в 1 клик)")
        tw_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        self.tweak_status_lbl = QLabel("Изменения применяются мгновенно и безопасно")
        self.tweak_status_lbl.setObjectName("MutedLabel")
        tw_hdr.addWidget(tw_title)
        tw_hdr.addStretch(1)
        tw_hdr.addWidget(self.tweak_status_lbl)
        tweaks_layout.addLayout(tw_hdr)

        tw_grid = QGridLayout()
        tw_grid.setSpacing(8)
        for idx, tw in enumerate(list_system_tweaks()):
            chk = QCheckBox(tw.title)
            chk.setToolTip(tw.description)
            chk.setStyleSheet("color: #f1f5f9; font-size: 13px; font-weight: 600;")
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.setChecked(tw.enabled)
            chk.toggled.connect(lambda checked, k=tw.key: self._on_tweak_toggled(k, checked))
            self._tweak_checks[tw.key] = chk
            tw_grid.addWidget(chk, idx // 2, idx % 2)
        tw_grid.setColumnStretch(0, 1)
        tw_grid.setColumnStretch(1, 1)
        tweaks_layout.addLayout(tw_grid)
        layout.addWidget(tweaks_card)

        # Нижний сплит: слева Исправление ошибок Windows (Реестр), справа Программы при включении (Автозагрузка)
        split = QHBoxLayout()
        split.setSpacing(14)

        # --- Левая колонка: Безопасная очистка реестра ---
        reg_card = QFrame()
        reg_card.setObjectName("Card")
        reg_layout = QVBoxLayout(reg_card)
        reg_layout.setContentsMargins(16, 14, 16, 14)
        reg_layout.setSpacing(8)

        reg_hdr = QHBoxLayout()
        reg_title = QLabel("🛡 Устранение следов удалённых программ")
        reg_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        reg_hdr.addWidget(reg_title)
        reg_hdr.addStretch(1)

        self.btn_reg_scan = QPushButton("🔍 Найти ошибки")
        self.btn_reg_scan.setObjectName("PrimaryBtn")
        self.btn_reg_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reg_scan.clicked.connect(self.start_scan_registry)

        self.btn_reg_fix = QPushButton("✨ Исправить выбранные")
        self.btn_reg_fix.setObjectName("SuccessBtn")
        self.btn_reg_fix.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reg_fix.setEnabled(False)
        self.btn_reg_fix.clicked.connect(self.start_fix_registry)

        reg_hdr.addWidget(self.btn_reg_scan)
        reg_hdr.addWidget(self.btn_reg_fix)
        reg_layout.addLayout(reg_hdr)

        self.reg_status_lbl = QLabel(
            "Ищет оставшиеся записи от давно удалённых игр и программ. Перед очисткой создаётся резервная копия."
        )
        self.reg_status_lbl.setObjectName("MutedLabel")
        self.reg_status_lbl.setWordWrap(True)
        reg_layout.addWidget(self.reg_status_lbl)

        self.reg_model = QStandardItemModel(self)
        self.reg_model.setHorizontalHeaderLabels(["Тип проблемы", "Программа / Файл", "Что обнаружено"])
        self.reg_view = QTableView()
        self.reg_view.setModel(self.reg_model)
        self.reg_view.setAlternatingRowColors(True)
        self.reg_view.setShowGrid(False)
        self.reg_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reg_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.reg_view.verticalHeader().setVisible(False)
        self.reg_view.verticalHeader().setDefaultSectionSize(34)
        self.reg_view.horizontalHeader().setStretchLastSection(True)
        self.reg_view.horizontalHeader().setHighlightSections(False)
        self.reg_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.reg_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        reg_layout.addWidget(self.reg_view, stretch=1)

        split.addWidget(reg_card, stretch=6)

        # --- Правая колонка: Программы при включении ПК ---
        st_card = QFrame()
        st_card.setObjectName("Card")
        st_layout = QVBoxLayout(st_card)
        st_layout.setContentsMargins(16, 14, 16, 14)
        st_layout.setSpacing(8)

        st_hdr = QHBoxLayout()
        st_title = QLabel("🚀 Программы при включении компьютера")
        st_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        st_hdr.addWidget(st_title)
        st_hdr.addStretch(1)

        self.btn_st_refresh = QPushButton("🔄 Обновить список")
        self.btn_st_refresh.setObjectName("ActionBtn")
        self.btn_st_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_st_refresh.clicked.connect(self.refresh_startup_items)
        st_hdr.addWidget(self.btn_st_refresh)
        st_layout.addLayout(st_hdr)

        st_sub = QLabel(
            "Снимите галочку с программ, которые не нужны сразу после включения ПК — компьютер будет загружаться быстрее."
        )
        st_sub.setObjectName("MutedLabel")
        st_sub.setWordWrap(True)
        st_layout.addWidget(st_sub)

        self.startup_model = QStandardItemModel(self)
        self.startup_model.setHorizontalHeaderLabels(
            ["Программа", "Автозапуск", "Нагрузка на старт", "Где настроено"]
        )
        self.startup_model.itemChanged.connect(self._on_startup_item_toggled)

        self.startup_view = QTableView()
        self.startup_view.setModel(self.startup_model)
        self.startup_view.setAlternatingRowColors(True)
        self.startup_view.setShowGrid(False)
        self.startup_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.startup_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.startup_view.verticalHeader().setVisible(False)
        self.startup_view.verticalHeader().setDefaultSectionSize(34)
        self.startup_view.horizontalHeader().setStretchLastSection(True)
        self.startup_view.horizontalHeader().setHighlightSections(False)
        st_layout.addWidget(self.startup_view, stretch=1)

        split.addWidget(st_card, stretch=5)
        layout.addLayout(split, stretch=1)
        return page

    # ---------- Страница «Диски» + Поиск крупных файлов ----------

    def _build_disks_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        table = DataTable(title="Накопители, разделы и S.M.A.R.T.-статус")
        self._tables["disks"] = table
        layout.addWidget(table, stretch=2)

        lf_card = QFrame()
        lf_card.setObjectName("Card")
        lf_layout = QVBoxLayout(lf_card)
        lf_layout.setContentsMargins(16, 12, 16, 12)
        lf_layout.setSpacing(8)

        lf_hdr = QHBoxLayout()
        lf_title = QLabel("📦 Поиск крупных файлов («Пожиратели места» > 100 МБ)")
        lf_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.lf_status_lbl = QLabel(
            "Быстрый поиск забытых установщиков, архивов, видео и образов в папках пользователя"
        )
        self.lf_status_lbl.setObjectName("MutedLabel")

        self.btn_lf_scan = QPushButton("🔍 Найти крупные файлы")
        self.btn_lf_scan.setObjectName("PrimaryBtn")
        self.btn_lf_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lf_scan.clicked.connect(self.start_scan_large_files)

        self.btn_lf_open = QPushButton("📂 Показать в папке")
        self.btn_lf_open.setObjectName("ActionBtn")
        self.btn_lf_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lf_open.setEnabled(False)
        self.btn_lf_open.clicked.connect(self._reveal_selected_large_file)

        self.btn_lf_del = QPushButton("🗑 Удалить файл")
        self.btn_lf_del.setObjectName("ActionBtn")
        self.btn_lf_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lf_del.setEnabled(False)
        self.btn_lf_del.clicked.connect(self._delete_selected_large_file)

        lf_hdr.addWidget(lf_title)
        lf_hdr.addStretch(1)
        lf_hdr.addWidget(self.btn_lf_scan)
        lf_hdr.addWidget(self.btn_lf_open)
        lf_hdr.addWidget(self.btn_lf_del)
        lf_layout.addLayout(lf_hdr)

        lf_info_row = QHBoxLayout()
        lf_info_row.setSpacing(12)
        self.lf_status_lbl.setWordWrap(True)
        self.lf_hint_lbl = QLabel(
            "🟢 Безопасно удалить — кэш и установщики   ·   "
            "🟡 Проверить — личные файлы и моды   ·   "
            "🔴 Не трогать — файлы программ и диски WSL/Docker"
        )
        self.lf_hint_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self.lf_hint_lbl.setWordWrap(True)
        lf_info_row.addWidget(self.lf_status_lbl)
        lf_info_row.addStretch(1)
        lf_info_row.addWidget(self.lf_hint_lbl)
        lf_layout.addLayout(lf_info_row)

        self.lf_model = QStandardItemModel(self)
        self.lf_model.setHorizontalHeaderLabels(
            ["Файл", "Размер", "Безопасность удаления", "Что это и можно ли удалять", "Папка"]
        )
        self.lf_view = QTableView()
        self.lf_view.setModel(self.lf_model)
        self.lf_view.setAlternatingRowColors(True)
        self.lf_view.setShowGrid(False)
        self.lf_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lf_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lf_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lf_view.verticalHeader().setVisible(False)
        self.lf_view.verticalHeader().setDefaultSectionSize(34)
        self.lf_view.horizontalHeader().setStretchLastSection(True)
        self.lf_view.horizontalHeader().setHighlightSections(False)
        self.lf_view.selectionModel().selectionChanged.connect(lambda *_: self._on_large_file_selected())
        self.lf_view.doubleClicked.connect(lambda _: self._reveal_selected_large_file())
        lf_layout.addWidget(self.lf_view, stretch=1)

        layout.addWidget(lf_card, stretch=3)
        return w

    # ---------- Страница «Видео» ----------

    def _build_video_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        table = DataTable(title="Видеоадаптеры")
        self._tables["video"] = table
        layout.addWidget(table, stretch=1)

        self.gpu_box = QFrame()
        self.gpu_box.setObjectName("Card")
        box_outer = QVBoxLayout(self.gpu_box)
        box_outer.setContentsMargins(16, 14, 16, 14)
        box_outer.setSpacing(10)

        hdr = QLabel("Официальные драйверы производителей GPU")
        hdr.setStyleSheet("font-size: 14px; font-weight: 700;")
        box_outer.addWidget(hdr)

        self.gpu_box_layout = QVBoxLayout()
        self.gpu_box_layout.setSpacing(8)
        box_outer.addLayout(self.gpu_box_layout)

        layout.addWidget(self.gpu_box)
        return w

    # ---------- Страница «Обновления» ----------

    def _build_updates_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top_card = QFrame()
        top_card.setObjectName("Card")
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(16, 12, 16, 12)
        top_layout.setSpacing(12)

        self.admin_label = QLabel()
        self.admin_label.setStyleSheet("font-weight: 600;")
        self.btn_elevate = QPushButton("🛡 Перезапустить от администратора")
        self.btn_elevate.setObjectName("ActionBtn")
        self.btn_elevate.clicked.connect(self._relaunch_admin)

        self.search_timer_lbl = QLabel("")
        self.search_timer_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 13px; font-weight: 700;")
        self.search_timer_lbl.hide()

        self.btn_search = QPushButton("🔍 Проверить обновления (Windows Update)")
        self.btn_search.setObjectName("PrimaryBtn")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.clicked.connect(self.start_search_updates)

        self.btn_cancel_search = QPushButton("✖ Отменить поиск")
        self.btn_cancel_search.setObjectName("ActionBtn")
        self.btn_cancel_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel_search.hide()
        self.btn_cancel_search.clicked.connect(self.cancel_search_updates)

        self.btn_install = QPushButton("⬇ Установить выбранные")
        self.btn_install.setObjectName("ActionBtn")
        self.btn_install.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_install.setEnabled(False)
        self.btn_install.clicked.connect(self.start_install)

        top_layout.addWidget(self.admin_label)
        top_layout.addWidget(self.btn_elevate)
        top_layout.addStretch(1)
        top_layout.addWidget(self.search_timer_lbl)
        top_layout.addWidget(self.btn_search)
        top_layout.addWidget(self.btn_cancel_search)
        top_layout.addWidget(self.btn_install)
        layout.addWidget(top_card)

        self.updates_model = QStandardItemModel(self)
        self.updates_model.setHorizontalHeaderLabels(
            ["Обновление", "Класс", "Производитель", "Дата версии", "Размер"]
        )
        self.updates_view = QTableView()
        self.updates_view.setModel(self.updates_model)
        self.updates_view.setAlternatingRowColors(True)
        self.updates_view.setShowGrid(False)
        self.updates_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.updates_view.verticalHeader().setVisible(False)
        self.updates_view.verticalHeader().setDefaultSectionSize(34)
        self.updates_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.updates_view, stretch=2)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Журнал поиска и установки обновлений драйверов…")
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view, stretch=1)
        return w

    def _update_admin_status(self) -> None:
        admin = is_admin()
        if admin:
            self.admin_label.setText("✅ Права администратора активны")
            self.admin_label.setStyleSheet(f"color: {SUCCESS}; font-weight: 600;")
        else:
            self.admin_label.setText("⚠ Нет прав администратора — установка драйверов недоступна")
            self.admin_label.setStyleSheet(f"color: {WARNING}; font-weight: 600;")
        self.btn_elevate.setVisible(not admin)

    def _log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    # ---------- Сканирование оборудования ----------

    def start_scan(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.spinner.start()
        self.status_title.setText("Сканирование системы…")
        self._scan_worker = ScanWorker()
        self._scan_worker.progress.connect(self.status_title.setText)
        self._scan_worker.succeeded.connect(self._on_scan_done)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_finished(self) -> None:
        self.btn_scan.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_scan_done(self, report: SystemReport) -> None:
        self._report = report
        for key, table in self._tables.items():
            table.load(report.section(key))
        self._update_dashboard(report)
        self._fill_gpu_links(report)
        self.btn_html.setEnabled(True)
        self.btn_json.setEnabled(True)

        drivers = report.section("drivers")
        count = len(drivers.rows) if drivers else 0
        self._nav_buttons["drivers"].set_badge(str(count) if count else "")
        self.status_title.setText(
            f"Обновлено в {report.scanned_at:%H:%M:%S} · Устройств и драйверов: {count}"
        )

    def _on_scan_failed(self, message: str) -> None:
        self.status_title.setText("Ошибка сканирования")
        QMessageBox.critical(self, "Ошибка", message)

    def _refresh_live_ram(self) -> None:
        pct, used_gb, total_gb = get_memory_usage()
        if total_gb > 0:
            self.gauge_ram.set_data(
                pct,
                "Оперативная память",
                f"Занято {used_gb:.1f} из {total_gb:.1f} ГБ",
            )

    def _update_dashboard(self, report: SystemReport) -> None:
        ov_map = dict(row[:2] for row in (report.section("overview").rows if report.section("overview") else []) if len(row) >= 2)
        cpu_map = dict(row[:2] for row in (report.section("cpu").rows if report.section("cpu") else []) if len(row) >= 2)

        bat_charge = ov_map.get("Заряд батареи", "")
        os_sub = f"{ov_map.get('Имя компьютера', '')} · Сборка {ov_map.get('Сборка', '—')}"
        if bat_charge:
            os_sub += f" · 🔋 {bat_charge.split(' ')[0]}"
        self.card_os.set_content(ov_map.get("ОС", "Windows"), os_sub)

        freq = cpu_map.get("Базовая частота, МГц") or cpu_map.get("Макс. частота, МГц", "—")
        self.card_cpu.set_content(
            cpu_map.get("Модель", "—"),
            f"Ядер: {cpu_map.get('Ядра', '—')} · Потоков: {cpu_map.get('Потоки', '—')} · {freq} МГц",
        )
        if report.gpus:
            g0 = report.gpus[0]
            self.card_gpu.set_content(g0.name, f"Драйвер {g0.driver_version} ({g0.driver_date})")
        else:
            self.card_gpu.set_content("Не обнаружена", "")

        drv_sec = report.section("drivers")
        drv_rows = drv_sec.rows if drv_sec else []
        signed_count = sum(1 for r in drv_rows if len(r) > 6 and r[6] == "Да")
        self.card_ram.set_content(
            f"ОЗУ: {ov_map.get('ОЗУ всего', '—')}",
            f"Драйверов: {len(drv_rows)} (подписано: {signed_count})",
        )

        disks_sec = report.section("disks")
        logical = [r for r in (disks_sec.rows if disks_sec else []) if len(r) >= 5 and r[2] == "Логический"]
        if logical:
            c_disk = next((r for r in logical if r[0].upper().startswith("C")), logical[0])
            c_total = _parse_size_gb(c_disk[3])
            c_free = _parse_size_gb(c_disk[4])
            c_used_pct = ((c_total - c_free) / c_total * 100.0) if c_total > 0 else 0.0
            self.gauge_sys_disk.set_data(
                c_used_pct,
                f"Диск {c_disk[0]}",
                f"Своб. {c_free:.0f} из {c_total:.0f} ГБ",
            )

            all_total = sum(_parse_size_gb(r[3]) for r in logical)
            all_free = sum(_parse_size_gb(r[4]) for r in logical)
            all_pct = ((all_total - all_free) / all_total * 100.0) if all_total > 0 else 0.0
            self.gauge_all_disks.set_data(
                all_pct,
                f"Все разделы ({len(logical)})",
                f"Занято {all_total - all_free:.0f} из {all_total:.0f} ГБ",
            )
        else:
            self.gauge_sys_disk.set_data(0.0, "Системный диск", "нет данных")
            self.gauge_all_disks.set_data(0.0, "Все разделы", "нет данных")

        signed_pct = (signed_count / len(drv_rows) * 100.0) if drv_rows else 0.0
        self.gauge_signed.set_data(
            signed_pct,
            "Подписаны WHQL",
            f"{signed_count} из {len(drv_rows)} драйверов",
        )

    def _fill_gpu_links(self, report: SystemReport) -> None:
        while self.gpu_box_layout.count():
            item = self.gpu_box_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                inner = item.layout()
                while inner.count():
                    sub = inner.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        if not report.gpus:
            self.gpu_box_layout.addWidget(QLabel("Видеокарты не обнаружены"))
            return
        for gpu in report.gpus:
            row = QHBoxLayout()
            safe_name = html.escape(gpu.name or "Неизвестный адаптер")
            safe_ver = html.escape(gpu.driver_version or "—")
            safe_date = html.escape(gpu.driver_date or "—")
            info = QLabel(
                f"<b>{safe_name}</b>  ·  текущий драйвер: <span style='color:{TEXT_SECONDARY}'>{safe_ver} ({safe_date})</span>"
            )
            info.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(info, 1)
            if gpu.download_url:
                btn = QPushButton(f"🌐 Официальный сайт {gpu.vendor}")
                btn.setObjectName("PrimaryBtn")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                url = gpu.download_url
                btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                row.addWidget(btn)
            else:
                lbl = QLabel("Производитель не определён")
                lbl.setObjectName("MutedLabel")
                row.addWidget(lbl)
            self.gpu_box_layout.addLayout(row)

    # ---------- Логика очистки мусора ----------

    def _populate_initial_cleaner_catalog(self) -> None:
        """Заполняет таблицу категорий очистки сразу при открытии, до запуска анализа."""
        self.cleaner_model.blockSignals(True)
        self.cleaner_model.removeRows(0, self.cleaner_model.rowCount())
        for t in catalog_definitions():
            item_title = QStandardItem(t.title)
            item_title.setCheckable(True)
            item_title.setCheckState(
                Qt.CheckState.Checked if t.checked_by_default else Qt.CheckState.Unchecked
            )
            item_title.setData(t.key, Qt.ItemDataRole.UserRole)
            item_title.setData(0, Qt.ItemDataRole.UserRole + 1)
            item_title.setData(0, Qt.ItemDataRole.UserRole + 2)
            item_title.setData(t.checked_by_default, Qt.ItemDataRole.UserRole + 3)

            status_item = QStandardItem("Нажмите «Анализировать систему»")
            status_item.setForeground(QColor(TEXT_SECONDARY))

            self.cleaner_model.appendRow([
                item_title,
                QStandardItem(t.group),
                QStandardItem(t.description),
                QStandardItem("—"),
                QStandardItem("—"),
                status_item,
            ])
        self.cleaner_model.blockSignals(False)
        self._resize_cleaner_columns()

    def _resize_cleaner_columns(self) -> None:
        self.cleaner_view.setColumnWidth(0, 290)
        self.cleaner_view.setColumnWidth(1, 165)
        self.cleaner_view.setColumnWidth(2, 340)
        self.cleaner_view.setColumnWidth(3, 85)
        self.cleaner_view.setColumnWidth(4, 95)

    def _select_cleaner_preset(self, mode: str) -> None:
        self.cleaner_model.blockSignals(True)
        for row in range(self.cleaner_model.rowCount()):
            it = self.cleaner_model.item(row, 0)
            if not it:
                continue
            sz = int(it.data(Qt.ItemDataRole.UserRole + 1) or 0)
            rec = bool(it.data(Qt.ItemDataRole.UserRole + 3))
            if mode == "all":
                state = Qt.CheckState.Checked if (sz > 0 or not self._clean_targets) else Qt.CheckState.Unchecked
            elif mode == "recommended":
                state = Qt.CheckState.Checked if (rec and (sz > 0 or not self._clean_targets)) else Qt.CheckState.Unchecked
            else:
                state = Qt.CheckState.Unchecked
            it.setCheckState(state)
        self.cleaner_model.blockSignals(False)
        self._recalc_selected_junk()

    def _cleaner_log_append(self, text: str) -> None:
        self.cleaner_log.appendPlainText(text)

    def _set_cleaner_row_status(self, key: str, status_text: str, color: str | None = None) -> None:
        for row in range(self.cleaner_model.rowCount()):
            it = self.cleaner_model.item(row, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == key:
                st_it = self.cleaner_model.item(row, 5)
                if st_it:
                    st_it.setText(status_text)
                    if color:
                        st_it.setForeground(QColor(color))
                self.cleaner_view.scrollTo(self.cleaner_model.index(row, 0))
                break

    def start_analyze_junk(self) -> None:
        if self._analyze_worker and self._analyze_worker.isRunning():
            return
        self.btn_analyze_junk.setEnabled(False)
        self.btn_clean_junk.setEnabled(False)
        self.spinner.start()
        self.cleaner_progress.setRange(0, len(catalog_definitions()))
        self.cleaner_progress.setValue(0)
        self.cleaner_progress.setFormat("Анализ категорий мусора… (%p%)")
        self.cleaner_progress.show()
        self._cleaner_log_append("🔍 Запущен анализ временных файлов и кэша…")
        self.status_title.setText("Анализ временных файлов и кэша…")

        self._analyze_worker = AnalyzeJunkWorker()
        self._analyze_worker.progress.connect(self._on_analyze_progress)
        self._analyze_worker.succeeded.connect(self._on_analyze_done)
        self._analyze_worker.failed.connect(self._on_analyze_failed)
        self._analyze_worker.finished.connect(self._on_analyze_finished)
        self._analyze_worker.start()

    def _on_analyze_progress(self, current: int, total: int, key: str, message: str) -> None:
        self.cleaner_progress.setRange(0, max(total, 1))
        self.cleaner_progress.setValue(current)
        self.cleaner_progress.setFormat(f"{message}  ({current}/{total})")
        self.status_title.setText(message)
        if key:
            self._set_cleaner_row_status(key, "⏳ Сканирование…", ACCENT)

    def _on_analyze_failed(self, msg: str) -> None:
        self.cleaner_progress.hide()
        self._cleaner_log_append(f"✖ Ошибка анализа: {msg}")
        QMessageBox.critical(self, "Анализ", msg)

    def _on_analyze_finished(self) -> None:
        self.btn_analyze_junk.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_analyze_done(self, targets: list[CleanTarget]) -> None:
        self._clean_targets = targets
        self.cleaner_progress.setValue(self.cleaner_progress.maximum())
        self.cleaner_progress.setFormat("✔ Анализ завершён (100%)")

        # Сохраняем текущий выбор пользователя, если он уже переключал чекбоксы
        prev_checked: dict[str, bool] = {}
        if self._clean_targets:
            for row in range(self.cleaner_model.rowCount()):
                it = self.cleaner_model.item(row, 0)
                if it:
                    prev_checked[str(it.data(Qt.ItemDataRole.UserRole))] = (
                        it.checkState() == Qt.CheckState.Checked
                    )

        self.cleaner_model.blockSignals(True)
        self.cleaner_model.removeRows(0, self.cleaner_model.rowCount())
        total_bytes = 0
        total_files = 0
        for t in targets:
            total_bytes += t.size_bytes
            total_files += t.files_count
            item_title = QStandardItem(t.title)
            item_title.setCheckable(True)
            want_check = prev_checked.get(t.key, t.checked_by_default)
            state = Qt.CheckState.Checked if (want_check and t.size_bytes > 0) else Qt.CheckState.Unchecked
            item_title.setCheckState(state)
            item_title.setData(t.key, Qt.ItemDataRole.UserRole)
            item_title.setData(t.size_bytes, Qt.ItemDataRole.UserRole + 1)
            item_title.setData(t.files_count, Qt.ItemDataRole.UserRole + 2)
            item_title.setData(t.checked_by_default, Qt.ItemDataRole.UserRole + 3)

            if t.size_bytes > 0:
                status_txt = (
                    f"Найдено {fmt_bytes(t.size_bytes)} (готово к очистке)"
                    if state == Qt.CheckState.Checked
                    else f"Найдено {fmt_bytes(t.size_bytes)} (отметьте при необходимости)"
                )
                status_color = WARNING if state == Qt.CheckState.Checked else TEXT_SECONDARY
            else:
                status_txt = "✔ Чисто"
                status_color = SUCCESS

            status_it = QStandardItem(status_txt)
            status_it.setForeground(QColor(status_color))

            self.cleaner_model.appendRow([
                item_title,
                QStandardItem(t.group),
                QStandardItem(t.description),
                QStandardItem(str(t.files_count)),
                QStandardItem(fmt_bytes(t.size_bytes) or "0 Б"),
                status_it,
            ])
        self.cleaner_model.blockSignals(False)
        self._resize_cleaner_columns()

        self.card_junk_found.set_content(
            fmt_bytes(total_bytes) or "0 Б",
            f"Всего файлов и объектов: {total_files}",
        )
        self._recalc_selected_junk()
        summary_msg = f"Анализ завершён · Найдено {fmt_bytes(total_bytes) or '0 Б'} ({total_files} объектов)"
        self.status_title.setText(summary_msg)
        self._cleaner_log_append(f"✔ {summary_msg}")

    def _recalc_selected_junk(self) -> None:
        sel_bytes = 0
        sel_files = 0
        sel_keys: list[str] = []
        for row in range(self.cleaner_model.rowCount()):
            it = self.cleaner_model.item(row, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                sel_keys.append(it.data(Qt.ItemDataRole.UserRole))
                sel_bytes += int(it.data(Qt.ItemDataRole.UserRole + 1) or 0)
                sel_files += int(it.data(Qt.ItemDataRole.UserRole + 2) or 0)
        self.card_junk_selected.set_content(
            fmt_bytes(sel_bytes) or "0 Б",
            f"Категорий: {len(sel_keys)} · Файлов: {sel_files}",
        )
        self.btn_clean_junk.setEnabled(bool(sel_keys) and sel_bytes > 0)

    def start_clean_junk(self) -> None:
        if self._clean_worker and self._clean_worker.isRunning():
            return
        keys: list[str] = []
        for row in range(self.cleaner_model.rowCount()):
            it = self.cleaner_model.item(row, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                keys.append(it.data(Qt.ItemDataRole.UserRole))
        if not keys:
            return

        self.btn_analyze_junk.setEnabled(False)
        self.btn_clean_junk.setEnabled(False)
        self.spinner.start()
        self.cleaner_progress.setRange(0, len(keys))
        self.cleaner_progress.setValue(0)
        self.cleaner_progress.setFormat("Подготовка к очистке…")
        self.cleaner_progress.show()
        self._cleaner_log_append(f"🧹 Запуск очистки выбранных категорий ({len(keys)})…")
        self.status_title.setText("Очистка выбранных категорий…")

        self._clean_worker = CleanJunkWorker(keys)
        self._clean_worker.progress.connect(self._on_clean_progress)
        self._clean_worker.log.connect(self._cleaner_log_append)
        self._clean_worker.succeeded.connect(self._on_clean_done)
        self._clean_worker.failed.connect(self._on_clean_failed)
        self._clean_worker.finished.connect(self._on_analyze_finished)
        self._clean_worker.start()

    def _on_clean_progress(self, current: int, total: int, key: str, message: str) -> None:
        self.cleaner_progress.setRange(0, max(total, 1))
        self.cleaner_progress.setValue(current)
        self.cleaner_progress.setFormat(f"{message}  ({current}/{total})")
        self.status_title.setText(message)
        if key:
            self._set_cleaner_row_status(key, "⏳ Очистка…", ACCENT)

    def _on_clean_failed(self, msg: str) -> None:
        self.cleaner_progress.hide()
        self._cleaner_log_append(f"✖ Ошибка очистки: {msg}")
        QMessageBox.critical(self, "Очистка", msg)

    def _on_clean_done(self, summary: CleanSummary) -> None:
        self._session_freed_bytes += summary.freed_bytes
        self.card_junk_freed.set_content(
            fmt_bytes(self._session_freed_bytes) or "0 Б",
            f"За запуск удалено: {summary.deleted_count} ф. (активных пропущено: {summary.skipped_count})",
        )
        self.cleaner_progress.setValue(self.cleaner_progress.maximum())
        self.cleaner_progress.setFormat(
            f"✔ Очистка завершена · Освобождено {fmt_bytes(summary.freed_bytes) or '0 Б'}"
        )

        # Обновляем таблицу на месте без сброса статусов результатов
        self.cleaner_model.blockSignals(True)
        rem_total_bytes = 0
        rem_total_files = 0
        for row in range(self.cleaner_model.rowCount()):
            it = self.cleaner_model.item(row, 0)
            if not it:
                continue
            key = str(it.data(Qt.ItemDataRole.UserRole))
            cat_res = summary.categories.get(key)
            if cat_res is not None:
                it.setData(cat_res.remaining_bytes, Qt.ItemDataRole.UserRole + 1)
                it.setData(cat_res.remaining_files, Qt.ItemDataRole.UserRole + 2)
                if cat_res.remaining_bytes == 0 or cat_res.deleted_count > 0:
                    it.setCheckState(Qt.CheckState.Unchecked)
                cnt_it = self.cleaner_model.item(row, 3)
                sz_it = self.cleaner_model.item(row, 4)
                st_it = self.cleaner_model.item(row, 5)
                if cnt_it:
                    cnt_it.setText(str(cat_res.remaining_files))
                if sz_it:
                    sz_it.setText(fmt_bytes(cat_res.remaining_bytes) or "0 Б")
                if st_it:
                    st_it.setText(cat_res.status)
                    color = SUCCESS if cat_res.status.startswith("✔") else WARNING
                    st_it.setForeground(QColor(color))

            rem_total_bytes += int(it.data(Qt.ItemDataRole.UserRole + 1) or 0)
            rem_total_files += int(it.data(Qt.ItemDataRole.UserRole + 2) or 0)
        self.cleaner_model.blockSignals(False)

        self.card_junk_found.set_content(
            fmt_bytes(rem_total_bytes) or "0 Б",
            f"Осталось невыбранных/занятых объектов: {rem_total_files}",
        )
        self._recalc_selected_junk()
        done_msg = (
            f"Очистка завершена · Освобождено {fmt_bytes(summary.freed_bytes) or '0 Б'} "
            f"(удалено {summary.deleted_count}, занято процессами: {summary.skipped_count})"
        )
        self.status_title.setText(done_msg)
        self._cleaner_log_append(f"🏁 {done_msg}")

    # ---------- Логика реестра, автозагрузки и оптимизации ----------

    @staticmethod
    def _friendly_registry_target(iss: RegistryIssue) -> tuple[str, str]:
        """Возвращает понятное имя программы/файла и простую причину для таблицы реестра."""
        raw = iss.value_data if iss.value_name in ("(По умолчанию)", "") else f"{iss.value_name} {iss.value_data}"
        exe = extract_executable_path(iss.value_name) or extract_executable_path(iss.value_data)
        if exe is not None:
            prog_name = exe.name
            if iss.value_data and not iss.value_data.startswith(("C:", "D:", "E:", '"')) and len(iss.value_data) < 48:
                prog_name = f"{iss.value_data} ({exe.name})"
            reason = f"Файл программы удалён ({exe})"
            return prog_name, reason
        fallback = iss.value_name if iss.value_name != "(По умолчанию)" else iss.subkey.rsplit("\\", 1)[-1]
        return fallback, iss.reason

    def start_scan_registry(self) -> None:
        if self._reg_scan_worker and self._reg_scan_worker.isRunning():
            return
        self.btn_reg_scan.setEnabled(False)
        self.btn_reg_fix.setEnabled(False)
        self.spinner.start()
        self.reg_status_lbl.setText("⏳ Поиск оставшихся следов удалённых программ и нерабочих ярлыков…")
        self.status_title.setText("Поиск следов удалённых программ…")

        self._reg_scan_worker = ScanRegistryWorker()
        self._reg_scan_worker.progress.connect(self.status_title.setText)
        self._reg_scan_worker.succeeded.connect(self._on_reg_scan_done)
        self._reg_scan_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Проверка", msg))
        self._reg_scan_worker.finished.connect(self._on_reg_scan_finished)
        self._reg_scan_worker.start()

    def _on_reg_scan_finished(self) -> None:
        self.btn_reg_scan.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_reg_scan_done(self, issues: list[RegistryIssue]) -> None:
        self._reg_issues = issues
        self._nav_buttons["optimize"].set_badge(str(len(issues)) if issues else "")
        self.reg_model.removeRows(0, self.reg_model.rowCount())
        for iss in issues:
            cat_item = QStandardItem(iss.category)
            cat_item.setCheckable(True)
            cat_item.setCheckState(Qt.CheckState.Checked)
            cat_item.setData(iss.issue_id, Qt.ItemDataRole.UserRole)

            friendly_prog, friendly_reason = self._friendly_registry_target(iss)
            tech_tooltip = f"Раздел: {iss.hive_name}\\{iss.subkey}\nПараметр: {iss.value_name}\nЗначение: {iss.value_data}"
            prog_item = QStandardItem(friendly_prog)
            reason_item = QStandardItem(friendly_reason)
            for cell in (cat_item, prog_item, reason_item):
                cell.setToolTip(tech_tooltip)

            self.reg_model.appendRow([cat_item, prog_item, reason_item])

        self.btn_reg_fix.setEnabled(bool(issues))
        if issues:
            self.reg_status_lbl.setText(
                f"⚠ Найдено ненужных записей от удалённых программ: {len(issues)}. Нажмите «Исправить выбранные»."
            )
            self.reg_status_lbl.setStyleSheet(f"color: {WARNING}; font-size: 12px; font-weight: 600;")
        else:
            self.reg_status_lbl.setText("✔ Всё в порядке! Следов удалённых программ и ошибок не обнаружено.")
            self.reg_status_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 12px; font-weight: 600;")
        self.status_title.setText(f"Проверка завершена · Найдено записей: {len(issues)}")

    def start_fix_registry(self) -> None:
        if self._reg_fix_worker and self._reg_fix_worker.isRunning():
            return
        selected: list[str] = []
        for row in range(self.reg_model.rowCount()):
            it = self.reg_model.item(row, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                selected.append(it.data(Qt.ItemDataRole.UserRole))
        if not selected:
            return

        self.btn_reg_scan.setEnabled(False)
        self.btn_reg_fix.setEnabled(False)
        self.spinner.start()
        self.reg_status_lbl.setText("⏳ Безопасное удаление старых записей и сохранение резервной копии…")

        self._reg_fix_worker = FixRegistryWorker(self._reg_issues, selected)
        self._reg_fix_worker.succeeded.connect(self._on_reg_fix_done)
        self._reg_fix_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Исправление", msg))
        self._reg_fix_worker.finished.connect(self._on_reg_scan_finished)
        self._reg_fix_worker.start()

    def _on_reg_fix_done(self, summary: RegistryFixSummary) -> None:
        self.reg_status_lbl.setText(
            f"✔ Успешно исправлено записей: {summary.fixed_count} (резервная копия сохранена автоматически)"
        )
        self.reg_status_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 12px; font-weight: 600;")
        self.start_scan_registry()

    def refresh_startup_items(self) -> None:
        self._startup_loading = True
        try:
            self._startup_items = list_startup_items()
            self.startup_model.removeRows(0, self.startup_model.rowCount())
            for idx, item in enumerate(self._startup_items):
                name_it = QStandardItem(item.name)
                name_it.setCheckable(True)
                name_it.setCheckState(Qt.CheckState.Checked if item.enabled else Qt.CheckState.Unchecked)
                name_it.setData(idx, Qt.ItemDataRole.UserRole)

                st_it = QStandardItem("Включён" if item.enabled else "Выключен")
                st_it.setForeground(QColor(SUCCESS if item.enabled else TEXT_SECONDARY))

                impact = estimate_startup_impact(item.name, item.command)
                imp_it = QStandardItem(impact)
                imp_color = DANGER if impact == "Высокая" else (WARNING if impact == "Средняя" else SUCCESS)
                imp_it.setForeground(QColor(imp_color))

                src_it = QStandardItem(item.source)
                for cell in (name_it, st_it, imp_it, src_it):
                    cell.setToolTip(f"Команда запуска: {item.command}")

                self.startup_model.appendRow([name_it, st_it, imp_it, src_it])
            self.startup_view.setColumnWidth(0, 175)
            self.startup_view.setColumnWidth(1, 95)
            self.startup_view.setColumnWidth(2, 130)
        finally:
            self._startup_loading = False

    def _on_startup_item_toggled(self, item: QStandardItem) -> None:
        if self._startup_loading or item.column() != 0:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or not (0 <= int(idx) < len(self._startup_items)):
            return
        st_item = self._startup_items[int(idx)]
        new_enabled = item.checkState() == Qt.CheckState.Checked
        try:
            set_startup_item_enabled(st_item, new_enabled)
            st_item.enabled = new_enabled
            st_col = self.startup_model.item(item.row(), 1)
            if st_col:
                st_col.setText("Включён" if new_enabled else "Выключен")
                st_col.setForeground(QColor(SUCCESS if new_enabled else TEXT_SECONDARY))
            state_txt = "будет запускаться с Windows" if new_enabled else "больше не тормозит включение ПК"
            self.status_title.setText(f"Программа «{st_item.name}» — {state_txt}")
        except OSError as exc:
            self._startup_loading = True
            item.setCheckState(Qt.CheckState.Checked if st_item.enabled else Qt.CheckState.Unchecked)
            self._startup_loading = False
            QMessageBox.warning(
                self,
                "Автозапуск программ",
                f"Чтобы изменить автозапуск системной программы «{st_item.name}», перезапустите OptiSys от имени администратора.\n\n({exc})",
            )

    def start_optimize_task(self, task_key: str) -> None:
        if self._opt_worker and self._opt_worker.isRunning():
            return
        for b in self._opt_buttons:
            b.setEnabled(False)
        self.spinner.start()
        self.opt_progress.setRange(0, 0)
        self.opt_progress.setFormat("Выполняется ускорение и настройка системы…")
        self.opt_progress.show()
        self.opt_status_lbl.setText("⏳ Пожалуйста, подождите — идёт настройка…")
        self.status_title.setText("Выполняется оптимизация компьютера…")

        if task_key == "all_fast":
            for k in ("flush_dns", "arp_net_cache", "free_ram", "trim_ssd"):
                if k in self._opt_card_status:
                    self._opt_card_status[k].setText("⏳ Выполняется…")
                    self._opt_card_status[k].setStyleSheet(f"color: {ACCENT}; font-size: 11px; font-weight: 600;")
        elif task_key in self._opt_card_status:
            self._opt_card_status[task_key].setText("⏳ Выполняется…")
            self._opt_card_status[task_key].setStyleSheet(f"color: {ACCENT}; font-size: 11px; font-weight: 600;")

        self._opt_worker = OptimizeTaskWorker(task_key)
        self._opt_worker.succeeded.connect(self._on_optimize_done)
        self._opt_worker.failed.connect(self._on_optimize_failed)
        self._opt_worker.finished.connect(self._on_optimize_finished)
        self._opt_worker.start()

    def _on_optimize_finished(self) -> None:
        for b in self._opt_buttons:
            b.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_optimize_done(self, res: OptimizeResult) -> None:
        self.opt_progress.hide()
        icon = "✔" if res.success else "⚠"
        color = SUCCESS if res.success else WARNING
        self.opt_status_lbl.setText(f"{icon} {res.message}")
        self.opt_status_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
        self.status_title.setText(f"{res.title}: {res.message}")

        self._refresh_live_ram()
        if res.key == "all_fast":
            for k in ("flush_dns", "arp_net_cache", "free_ram", "trim_ssd"):
                if k in self._opt_card_status:
                    self._opt_card_status[k].setText("✔ Выполнено")
                    self._opt_card_status[k].setStyleSheet(f"color: {SUCCESS}; font-size: 11px; font-weight: 600;")
        elif res.key in self._opt_card_status:
            lbl = self._opt_card_status[res.key]
            lbl.setText("✔ Выполнено успешно" if res.success else "⚠ Не удалось выполнить")
            lbl.setToolTip(res.message)
            lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

    def _on_optimize_failed(self, message: str) -> None:
        self.opt_progress.hide()
        self.opt_status_lbl.setText(f"Ошибка: {message}")
        QMessageBox.warning(self, "Оптимизация", message)

    def refresh_system_tweaks(self) -> None:
        self._tweak_loading = True
        try:
            for tw in list_system_tweaks():
                chk = self._tweak_checks.get(tw.key)
                if chk is not None:
                    chk.setChecked(tw.enabled)
        finally:
            self._tweak_loading = False

    def _on_tweak_toggled(self, key: str, enabled: bool) -> None:
        if self._tweak_loading:
            return
        ok, msg = set_system_tweak(key, enabled)
        color = SUCCESS if ok else WARNING
        self.tweak_status_lbl.setText(f"{'✔' if ok else '⚠'} {msg}")
        self.tweak_status_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
        self.status_title.setText(msg)
        if not ok:
            self.refresh_system_tweaks()

    # ---------- Поиск крупных файлов ----------

    def start_scan_large_files(self) -> None:
        if self._large_worker and self._large_worker.isRunning():
            return
        self.btn_lf_scan.setEnabled(False)
        self.btn_lf_open.setEnabled(False)
        self.btn_lf_del.setEnabled(False)
        self.spinner.start()
        self.lf_status_lbl.setText("⏳ Поиск файлов крупнее 100 МБ в папках пользователя…")

        self._large_worker = ScanLargeFilesWorker(100)
        self._large_worker.succeeded.connect(self._on_large_files_done)
        self._large_worker.failed.connect(lambda m: QMessageBox.warning(self, "Крупные файлы", m))
        self._large_worker.finished.connect(self._on_large_files_finished)
        self._large_worker.start()

    def _on_large_files_finished(self) -> None:
        self.btn_lf_scan.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_large_files_done(self, items: list[LargeFileItem]) -> None:
        self._large_files = items
        self.lf_model.removeRows(0, self.lf_model.rowCount())
        total_bytes = sum(it.size_bytes for it in items)
        safe_bytes = sum(it.size_bytes for it in items if it.safety_level == "safe")
        level_colors = {"safe": SUCCESS, "review": WARNING, "danger": DANGER}
        for idx, it in enumerate(items):
            name_item = QStandardItem(it.name)
            name_item.setData(idx, Qt.ItemDataRole.UserRole)
            sz_item = QStandardItem(fmt_bytes(it.size_bytes))
            sz_item.setForeground(QColor(WARNING if it.size_bytes >= 1024**3 else ACCENT))
            badge_item = QStandardItem(it.safety_badge)
            badge_item.setForeground(QColor(level_colors.get(it.safety_level, TEXT_SECONDARY)))
            desc_item = QStandardItem(it.description)
            dir_item = QStandardItem(it.folder)
            tooltip = f"{it.safety_badge}\n{it.description}\n\nПуть: {it.path}\nИзменён: {it.modified_str}"
            for cell in (name_item, sz_item, badge_item, desc_item, dir_item):
                cell.setToolTip(tooltip)
            self.lf_model.appendRow([name_item, sz_item, badge_item, desc_item, dir_item])
        self.lf_view.setColumnWidth(0, 210)
        self.lf_view.setColumnWidth(1, 90)
        self.lf_view.setColumnWidth(2, 250)
        self.lf_view.setColumnWidth(3, 390)
        has_items = bool(items)
        self.btn_lf_open.setEnabled(has_items)
        self.btn_lf_del.setEnabled(has_items)
        if has_items:
            safe_txt = f" (можно безопасно очистить: {fmt_bytes(safe_bytes)})" if safe_bytes > 0 else ""
            self.lf_status_lbl.setText(
                f"Найдено файлов: {len(items)} на сумму {fmt_bytes(total_bytes)}{safe_txt}"
            )
            self.lf_status_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 12px; font-weight: 600;")
        else:
            self.lf_status_lbl.setText("✔ Крупных файлов (> 100 МБ) в пользовательских папках не обнаружено")
            self.lf_status_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 12px; font-weight: 600;")

    def _on_large_file_selected(self) -> None:
        target = self._selected_large_file()
        if not target:
            return
        level_colors = {"safe": SUCCESS, "review": WARNING, "danger": DANGER}
        color = level_colors.get(target.safety_level, TEXT_SECONDARY)
        self.lf_hint_lbl.setText(f"{target.safety_badge}: {target.description}")
        self.lf_hint_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
        self.btn_lf_del.setEnabled(target.safety_level != "danger")
        self.btn_lf_del.setText(
            "🔒 Удаление заблокировано (нужен системе)" if target.safety_level == "danger" else "🗑 Удалить файл"
        )

    def _selected_large_file(self) -> LargeFileItem | None:
        indexes = self.lf_view.selectionModel().selectedRows() if self.lf_view.selectionModel() else []
        if not indexes:
            return None
        it = self.lf_model.item(indexes[0].row(), 0)
        if not it:
            return None
        idx = it.data(Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= int(idx) < len(self._large_files):
            return self._large_files[int(idx)]
        return None

    def _reveal_selected_large_file(self) -> None:
        target = self._selected_large_file()
        if not target:
            return
        subprocess.Popen(["explorer.exe", f"/select,{target.path}"])

    def _delete_selected_large_file(self) -> None:
        target = self._selected_large_file()
        if not target:
            return
        if target.safety_level == "danger":
            QMessageBox.warning(
                self,
                "Удаление запрещено для защиты системы",
                f"{target.safety_badge}\n\n{target.description}\n\n"
                "Этот файл необходим для работы установленной программы или виртуальной среды.",
            )
            return
        box = QMessageBox(self)
        box.setWindowTitle("Подтверждение удаления файла")
        box.setIcon(QMessageBox.Icon.Question)
        badge_color = SUCCESS if target.safety_level == "safe" else WARNING
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<div style='font-size:14px; font-weight:700; color:#f1f5f9; margin-bottom:6px;'>"
            f"Удалить файл «{html.escape(target.name)}» ({fmt_bytes(target.size_bytes)})?"
            f"</div>"
            f"<div style='color:{badge_color}; font-weight:700; margin-bottom:6px;'>"
            f"{html.escape(target.safety_badge)}"
            f"</div>"
            f"<div style='color:#cbd5e1; margin-bottom:8px;'>"
            f"{html.escape(target.description)}"
            f"</div>"
            f"<div style='color:#94a3b8; font-size:11px;'>"
            f"Папка: {html.escape(target.folder)}"
            f"</div>"
        )
        btn_yes = box.addButton("🗑 Удалить файл", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() != btn_yes:
            return
        try:
            os.remove(target.path)
            self.status_title.setText(f"Удалён файл {target.name} (+{fmt_bytes(target.size_bytes)})")
            self.start_scan_large_files()
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось удалить файл", str(exc))

    # ---------- Экспорт ----------

    def _export(self, fmt: str) -> None:
        if not self._report:
            return
        default = f"system_report_{self._report.scanned_at:%Y%m%d_%H%M}.{fmt}"
        filters = "HTML (*.html)" if fmt == "html" else "JSON (*.json)"
        path_str, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", default, filters)
        if not path_str:
            return
        path = Path(path_str)
        try:
            (export_html if fmt == "html" else export_json)(self._report, path)
        except OSError as exc:
            logger.exception("Ошибка экспорта")
            QMessageBox.critical(self, "Ошибка экспорта", str(exc))
            return
        self.status_title.setText(f"Отчёт сохранён: {path.name}")

    # ---------- Обновление драйверов ----------

    def _relaunch_admin(self) -> None:
        if relaunch_as_admin():
            QApplication.quit()
        else:
            QMessageBox.warning(self, "UAC", "Не удалось запросить права администратора")

    def _on_search_timer_tick(self) -> None:
        self._search_elapsed_sec += 1
        mm, ss = divmod(self._search_elapsed_sec, 60)
        time_str = f"{mm:02d}:{ss:02d}"
        self.search_timer_lbl.setText(f"⏱ Время поиска: {time_str}")
        # Плавная пульсация полосы (Qt рисует текст внутри QProgressBar только при maximum > 0)
        pulse_val = 20 + (self._search_elapsed_sec * 12) % 75
        self.progress.setValue(pulse_val)
        self.progress.setFormat(f"Поиск обновлений в каталоге Windows Update… ({time_str})")

    def start_search_updates(self) -> None:
        self.btn_search.setEnabled(False)
        self.btn_cancel_search.setEnabled(True)
        self.btn_cancel_search.show()
        self.btn_install.setEnabled(False)
        self.spinner.start()

        self._search_elapsed_sec = 0
        self.search_timer_lbl.setText("⏱ Время поиска: 00:00")
        self.search_timer_lbl.show()
        self._search_ui_timer.start()

        self.progress.setRange(0, 100)
        self.progress.setValue(20)
        self.progress.setFormat("Поиск обновлений в каталоге Windows Update… (00:00)")
        self.progress.show()
        self._log("Запущен поиск обновлений драйверов через Windows Update…")

        self._search_worker = SearchUpdatesWorker()
        self._search_worker.succeeded.connect(self._on_search_done)
        self._search_worker.cancelled.connect(self._on_search_cancelled)
        self._search_worker.failed.connect(self._on_updates_failed)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def cancel_search_updates(self) -> None:
        if self._search_worker and self._search_worker.isRunning():
            self._search_ui_timer.stop()
            self.btn_cancel_search.setEnabled(False)
            self.search_timer_lbl.setText("⏱ Отмена поиска…")
            self.progress.setFormat("Отмена поиска Windows Update…")
            self._search_worker.cancel()

    def _on_search_cancelled(self) -> None:
        self._search_ui_timer.stop()
        self.search_timer_lbl.hide()
        self.progress.hide()
        self._log(f"Поиск обновлений отменён пользователем (прошло {self._search_elapsed_sec} с).")
        self.status_title.setText("Поиск обновлений отменён")

    def _on_search_finished(self) -> None:
        self._search_ui_timer.stop()
        self.search_timer_lbl.hide()
        self.btn_search.setEnabled(True)
        self.btn_cancel_search.hide()
        self._stop_spinner_if_idle()

    def _on_search_done(self, updates: list[DriverUpdate]) -> None:
        self.progress.hide()
        self._updates = updates
        self._nav_buttons["updates"].set_badge(str(len(updates)) if updates else "")
        self.updates_model.removeRows(0, self.updates_model.rowCount())
        for upd in updates:
            title = QStandardItem(upd.title)
            title.setCheckable(True)
            title.setCheckState(Qt.CheckState.Checked)
            title.setData(upd.update_id, Qt.ItemDataRole.UserRole)
            self.updates_model.appendRow([
                title,
                QStandardItem(upd.driver_class),
                QStandardItem(upd.manufacturer),
                QStandardItem(upd.version_date),
                QStandardItem(fmt_bytes(upd.size_bytes)),
            ])
        self._log(f"Найдено доступных обновлений драйверов: {len(updates)}")
        self.btn_install.setEnabled(bool(updates))

    def _selected_update_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self.updates_model.rowCount()):
            item = self.updates_model.item(row, 0)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def start_install(self) -> None:
        ids = self._selected_update_ids()
        if not ids:
            QMessageBox.information(self, "Установка", "Не выбрано ни одного обновления")
            return
        answer = QMessageBox.question(
            self,
            "Установка драйверов",
            f"Установить выбранные обновления ({len(ids)})?\nРекомендуется закрыть другие программы.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.btn_search.setEnabled(False)
        self.btn_install.setEnabled(False)
        self.spinner.start()
        self.progress.setRange(0, len(ids))
        self.progress.setValue(0)
        self.progress.show()

        self._install_worker = InstallUpdatesWorker(ids)
        self._install_worker.progress.connect(self._on_install_progress)
        self._install_worker.log.connect(self._log)
        self._install_worker.succeeded.connect(self._on_install_done)
        self._install_worker.failed.connect(self._on_updates_failed)
        self._install_worker.finished.connect(self._on_search_finished)
        self._install_worker.start()

    def _on_install_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.progress.setFormat(f"{message}  (%v/%m)")
        self._log(message)

    def _on_install_done(self, summary: InstallSummary) -> None:
        self.progress.hide()
        text = f"Установлено: {len(summary.installed)}\nОшибок: {len(summary.failed)}"
        if summary.failed:
            text += "\n\n" + "\n".join(summary.failed)
        if summary.reboot_required:
            text += "\n\n⚠ Требуется перезагрузка компьютера."
        self._log(text.replace("\n\n", "\n"))
        QMessageBox.information(self, "Установка завершена", text)
        self.start_scan()

    def _on_updates_failed(self, message: str) -> None:
        self.progress.hide()
        self._log(f"Ошибка: {message}")
        self.btn_install.setEnabled(bool(self._updates))
        QMessageBox.critical(self, "Windows Update", message)

    # ---------- Закрытие ----------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if (self._install_worker and self._install_worker.isRunning()) or (
            self._clean_worker and self._clean_worker.isRunning()
        ):
            QMessageBox.warning(
                self,
                "Выполняется операция",
                "Дождитесь завершения текущей установки или очистки.",
            )
            event.ignore()
            return
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.cancel()
        for worker in (
            self._scan_worker,
            self._search_worker,
            self._analyze_worker,
            self._reg_scan_worker,
            self._reg_fix_worker,
            self._opt_worker,
            self._large_worker,
        ):
            if worker and worker.isRunning():
                self.status_title.setText("Завершение фоновых операций…")
                worker.wait()
        event.accept()
