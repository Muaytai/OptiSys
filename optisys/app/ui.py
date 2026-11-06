"""Главное окно приложения с современным дашбордом, очисткой, оптимизацией и анимацией."""
from __future__ import annotations

import html
import logging
import re
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .models import (
    CleanSummary,
    CleanTarget,
    DriverUpdate,
    InstallSummary,
    OptimizeResult,
    RegistryFixSummary,
    RegistryIssue,
    Section,
    StartupItem,
    SystemReport,
)
from .optimizer import list_startup_items, set_startup_item_enabled
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
    ScanRegistryWorker,
    ScanWorker,
    SearchUpdatesWorker,
)

logger = logging.getLogger(__name__)

APP_TITLE = "OptiSys — диагностика, очистка и оптимизация системы"


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
        self.resize(1280, 800)
        self.setStyleSheet(APP_STYLESHEET)

        self._report: SystemReport | None = None
        self._updates: list[DriverUpdate] = []
        self._clean_targets: list[CleanTarget] = []
        self._reg_issues: list[RegistryIssue] = []
        self._startup_items: list[StartupItem] = []
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

        self._tables: dict[str, DataTable] = {}
        self._nav_buttons: dict[str, SidebarButton] = {}
        self._page_indices: dict[str, int] = {}

        self._build_layout()
        self._update_admin_status()
        self._select_page("overview")
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
        sidebar.setFixedWidth(225)

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
        layout.addWidget(self.status_title)

        layout.addStretch(1)

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
            ("disks", "Накопители и разделы"),
        ):
            self._register_table_page(key, title)

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
        if key == "optimize" and not self._startup_items:
            self.refresh_startup_items()

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
        g_title = QLabel("Визуальная сводка")
        g_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        gauges_layout.addWidget(g_title)

        rings_row = QHBoxLayout()
        rings_row.setSpacing(8)
        self.gauge_sys_disk = AnimatedRingGauge("Системный диск", "ожидание…", ACCENT)
        self.gauge_all_disks = AnimatedRingGauge("Все разделы", "ожидание…", "#06b6d4")
        self.gauge_signed = AnimatedRingGauge("Подписанные драйверы", "ожидание…", SUCCESS)
        rings_row.addWidget(self.gauge_sys_disk)
        rings_row.addWidget(self.gauge_all_disks)
        rings_row.addWidget(self.gauge_signed)
        gauges_layout.addLayout(rings_row)
        gauges_layout.addStretch(1)

        mid_row.addWidget(gauges_card, stretch=5)

        overview_table = DataTable(title="Параметры системы")
        self._tables["overview"] = overview_table
        mid_row.addWidget(overview_table, stretch=6)

        layout.addLayout(mid_row, stretch=1)
        return page

    # ---------- Страница «Очистка» (в стиле CCleaner) ----------

    def _build_cleaner_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Сводные карточки очистки
        cards = QGridLayout()
        cards.setSpacing(12)
        self.card_junk_found = HeroCard("cleaner", "Обнаружено мусора", WARNING)
        self.card_junk_found.set_content("Не проверено", "Нажмите «Анализировать»")
        self.card_junk_selected = HeroCard("disks", "Выбрано к очистке", ACCENT)
        self.card_junk_selected.set_content("0 Б", "Отметьте нужные категории")
        self.card_junk_freed = HeroCard("optimize", "Освобождено за сессию", SUCCESS)
        self.card_junk_freed.set_content("0 Б", "Безопасное удаление кэша и Temp")

        cards.addWidget(self.card_junk_found, 0, 0)
        cards.addWidget(self.card_junk_selected, 0, 1)
        cards.addWidget(self.card_junk_freed, 0, 2)
        for c in range(3):
            cards.setColumnStretch(c, 1)
        layout.addLayout(cards)

        # Панель действий
        bar = QFrame()
        bar.setObjectName("Card")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 12, 16, 12)
        bar_layout.setSpacing(12)

        lbl_hint = QLabel("Пароли, куки, открытые вкладки браузеров и системные библиотеки не затрагиваются.")
        lbl_hint.setObjectName("MutedLabel")

        self.btn_analyze_junk = QPushButton("🔍 Анализировать систему")
        self.btn_analyze_junk.setObjectName("PrimaryBtn")
        self.btn_analyze_junk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze_junk.clicked.connect(self.start_analyze_junk)

        self.btn_clean_junk = QPushButton("🧹 Очистить выбранное")
        self.btn_clean_junk.setObjectName("ActionBtn")
        self.btn_clean_junk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clean_junk.setEnabled(False)
        self.btn_clean_junk.clicked.connect(self.start_clean_junk)

        bar_layout.addWidget(lbl_hint, stretch=1)
        bar_layout.addWidget(self.btn_analyze_junk)
        bar_layout.addWidget(self.btn_clean_junk)
        layout.addWidget(bar)

        # Таблица категорий
        self.cleaner_model = QStandardItemModel(self)
        self.cleaner_model.setHorizontalHeaderLabels(
            ["Категория", "Группа", "Описание", "Файлов", "Размер"]
        )
        self.cleaner_model.itemChanged.connect(self._recalc_selected_junk)

        self.cleaner_view = QTableView()
        self.cleaner_view.setModel(self.cleaner_model)
        self.cleaner_view.setAlternatingRowColors(True)
        self.cleaner_view.setShowGrid(False)
        self.cleaner_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cleaner_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cleaner_view.verticalHeader().setVisible(False)
        self.cleaner_view.verticalHeader().setDefaultSectionSize(34)
        self.cleaner_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.cleaner_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.cleaner_view, stretch=1)
        return page

    # ---------- Страница «Оптимизация» (Реестр + Автозагрузка + Твики) ----------

    def _build_optimize_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Быстрые действия оптимизации (1-клик)
        quick_card = QFrame()
        quick_card.setObjectName("Card")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(16, 12, 16, 12)
        quick_layout.setSpacing(10)

        q_hdr = QHBoxLayout()
        q_title = QLabel("⚡ Быстрая оптимизация системы")
        q_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.opt_status_lbl = QLabel("Выберите задачу для ускорения и обслуживания ОС")
        self.opt_status_lbl.setObjectName("MutedLabel")
        q_hdr.addWidget(q_title)
        q_hdr.addStretch(1)
        q_hdr.addWidget(self.opt_status_lbl)
        quick_layout.addLayout(q_hdr)

        q_btns = QHBoxLayout()
        q_btns.setSpacing(10)
        self._opt_buttons: list[QPushButton] = []
        for task_key, btn_text, tooltip in (
            ("flush_dns", "🌐 Сбросить кэш DNS", "ipconfig /flushdns — очистка кэша DNS-резолвера"),
            ("arp_net_cache", "📡 Сбросить сетевой кэш ARP", "Очистка таблиц маршрутизации ARP и DNS"),
            ("trim_ssd", "🚀 Оптимизация / TRIM диска C:", "defrag C: /O — выполнение TRIM для SSD или оптимизации диска"),
            ("winsxs_clean", "📦 Очистка пакетов WinSxS", "DISM /StartComponentCleanup — очистка устаревших версий компонентов"),
        ):
            btn = QPushButton(btn_text)
            btn.setObjectName("ActionBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _=False, k=task_key: self.start_optimize_task(k))
            q_btns.addWidget(btn)
            self._opt_buttons.append(btn)
        quick_layout.addLayout(q_btns)
        layout.addWidget(quick_card)

        # Нижний сплит: слева Реестр, справа Автозагрузка
        split = QHBoxLayout()
        split.setSpacing(14)

        # --- Левая колонка: Очистка реестра ---
        reg_card = QFrame()
        reg_card.setObjectName("Card")
        reg_layout = QVBoxLayout(reg_card)
        reg_layout.setContentsMargins(16, 14, 16, 14)
        reg_layout.setSpacing(10)

        reg_hdr = QHBoxLayout()
        reg_title = QLabel("🛡 Очистка реестра (авто-бэкап .reg)")
        reg_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        reg_hdr.addWidget(reg_title)
        reg_hdr.addStretch(1)

        self.btn_reg_scan = QPushButton("🔍 Проверить реестр")
        self.btn_reg_scan.setObjectName("PrimaryBtn")
        self.btn_reg_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reg_scan.clicked.connect(self.start_scan_registry)

        self.btn_reg_fix = QPushButton("🛠 Исправить выбранные")
        self.btn_reg_fix.setObjectName("ActionBtn")
        self.btn_reg_fix.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reg_fix.setEnabled(False)
        self.btn_reg_fix.clicked.connect(self.start_fix_registry)

        reg_hdr.addWidget(self.btn_reg_scan)
        reg_hdr.addWidget(self.btn_reg_fix)
        reg_layout.addLayout(reg_hdr)

        self.reg_model = QStandardItemModel(self)
        self.reg_model.setHorizontalHeaderLabels(["Категория", "Раздел / Параметр", "Причина"])
        self.reg_view = QTableView()
        self.reg_view.setModel(self.reg_model)
        self.reg_view.setAlternatingRowColors(True)
        self.reg_view.setShowGrid(False)
        self.reg_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reg_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.reg_view.verticalHeader().setVisible(False)
        self.reg_view.verticalHeader().setDefaultSectionSize(32)
        self.reg_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.reg_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.reg_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        reg_layout.addWidget(self.reg_view, stretch=1)

        split.addWidget(reg_card, stretch=6)

        # --- Правая колонка: Менеджер автозагрузки ---
        st_card = QFrame()
        st_card.setObjectName("Card")
        st_layout = QVBoxLayout(st_card)
        st_layout.setContentsMargins(16, 14, 16, 14)
        st_layout.setSpacing(10)

        st_hdr = QHBoxLayout()
        st_title = QLabel("🚀 Автозагрузка Windows")
        st_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        st_hdr.addWidget(st_title)
        st_hdr.addStretch(1)

        self.btn_st_refresh = QPushButton("🔄 Обновить")
        self.btn_st_refresh.setObjectName("ActionBtn")
        self.btn_st_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_st_refresh.clicked.connect(self.refresh_startup_items)
        st_hdr.addWidget(self.btn_st_refresh)
        st_layout.addLayout(st_hdr)

        self.startup_model = QStandardItemModel(self)
        self.startup_model.setHorizontalHeaderLabels(["Программа", "Источник", "Команда"])
        self.startup_model.itemChanged.connect(self._on_startup_item_toggled)

        self.startup_view = QTableView()
        self.startup_view.setModel(self.startup_model)
        self.startup_view.setAlternatingRowColors(True)
        self.startup_view.setShowGrid(False)
        self.startup_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.startup_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.startup_view.verticalHeader().setVisible(False)
        self.startup_view.verticalHeader().setDefaultSectionSize(32)
        self.startup_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.startup_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.startup_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        st_layout.addWidget(self.startup_view, stretch=1)

        split.addWidget(st_card, stretch=5)
        layout.addLayout(split, stretch=1)
        return page

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

        self.btn_search = QPushButton("🔍 Проверить обновления (Windows Update)")
        self.btn_search.setObjectName("PrimaryBtn")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.clicked.connect(self.start_search_updates)

        self.btn_install = QPushButton("⬇ Установить выбранные")
        self.btn_install.setObjectName("ActionBtn")
        self.btn_install.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_install.setEnabled(False)
        self.btn_install.clicked.connect(self.start_install)

        top_layout.addWidget(self.admin_label)
        top_layout.addWidget(self.btn_elevate)
        top_layout.addStretch(1)
        top_layout.addWidget(self.btn_search)
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

    def _update_dashboard(self, report: SystemReport) -> None:
        ov_map = dict(row[:2] for row in (report.section("overview").rows if report.section("overview") else []) if len(row) >= 2)
        cpu_map = dict(row[:2] for row in (report.section("cpu").rows if report.section("cpu") else []) if len(row) >= 2)

        self.card_os.set_content(
            ov_map.get("ОС", "Windows"),
            f"{ov_map.get('Имя компьютера', '')} · Сборка {ov_map.get('Сборка', '—')}",
        )
        self.card_cpu.set_content(
            cpu_map.get("Модель", "—"),
            f"Ядер: {cpu_map.get('Ядра', '—')} · Потоков: {cpu_map.get('Потоки', '—')} · {cpu_map.get('Макс. частота, МГц', '—')} МГц",
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
                f"Свободно {c_disk[4]} из {c_disk[3]}",
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

    def start_analyze_junk(self) -> None:
        if self._analyze_worker and self._analyze_worker.isRunning():
            return
        self.btn_analyze_junk.setEnabled(False)
        self.btn_clean_junk.setEnabled(False)
        self.spinner.start()
        self.status_title.setText("Анализ временных файлов и кэша…")

        self._analyze_worker = AnalyzeJunkWorker()
        self._analyze_worker.progress.connect(self.status_title.setText)
        self._analyze_worker.succeeded.connect(self._on_analyze_done)
        self._analyze_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Анализ", msg))
        self._analyze_worker.finished.connect(self._on_analyze_finished)
        self._analyze_worker.start()

    def _on_analyze_finished(self) -> None:
        self.btn_analyze_junk.setEnabled(True)
        self._stop_spinner_if_idle()

    def _on_analyze_done(self, targets: list[CleanTarget]) -> None:
        self._clean_targets = targets
        self.cleaner_model.blockSignals(True)
        self.cleaner_model.removeRows(0, self.cleaner_model.rowCount())
        total_bytes = 0
        total_files = 0
        for t in targets:
            total_bytes += t.size_bytes
            total_files += t.files_count
            item_title = QStandardItem(t.title)
            item_title.setCheckable(True)
            state = Qt.CheckState.Checked if (t.checked_by_default and t.size_bytes > 0) else Qt.CheckState.Unchecked
            item_title.setCheckState(state)
            item_title.setData(t.key, Qt.ItemDataRole.UserRole)
            item_title.setData(t.size_bytes, Qt.ItemDataRole.UserRole + 1)
            item_title.setData(t.files_count, Qt.ItemDataRole.UserRole + 2)

            self.cleaner_model.appendRow([
                item_title,
                QStandardItem(t.group),
                QStandardItem(t.description),
                QStandardItem(str(t.files_count)),
                QStandardItem(fmt_bytes(t.size_bytes) or "0 Б"),
            ])
        self.cleaner_model.blockSignals(False)

        self.card_junk_found.set_content(
            fmt_bytes(total_bytes) or "0 Б",
            f"Всего файлов и объектов: {total_files}",
        )
        self._recalc_selected_junk()
        self.status_title.setText(f"Анализ завершён · Найдено {fmt_bytes(total_bytes) or '0 Б'}")

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
        self.btn_clean_junk.setEnabled(bool(sel_keys))

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
        ans = QMessageBox.question(
            self,
            "Подтверждение очистки",
            f"Очистить выбранные категории ({len(keys)})?\nЗанятые открытыми программами файлы будут безопасно пропущены.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.btn_analyze_junk.setEnabled(False)
        self.btn_clean_junk.setEnabled(False)
        self.spinner.start()
        self.status_title.setText("Очистка выбранных категорий…")

        self._clean_worker = CleanJunkWorker(keys)
        self._clean_worker.progress.connect(self.status_title.setText)
        self._clean_worker.succeeded.connect(self._on_clean_done)
        self._clean_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Очистка", msg))
        self._clean_worker.finished.connect(self._on_analyze_finished)
        self._clean_worker.start()

    def _on_clean_done(self, summary: CleanSummary) -> None:
        self._session_freed_bytes += summary.freed_bytes
        self.card_junk_freed.set_content(
            fmt_bytes(self._session_freed_bytes) or "0 Б",
            f"Удалено объектов: {summary.deleted_count} (пропущено активных: {summary.skipped_count})",
        )
        QMessageBox.information(
            self,
            "Очистка завершена",
            f"Освобождено: {fmt_bytes(summary.freed_bytes) or '0 Б'}\n"
            f"Удалено файлов: {summary.deleted_count}\n"
            f"Занято процессами (пропущено): {summary.skipped_count}",
        )
        self.start_analyze_junk()

    # ---------- Логика реестра, автозагрузки и оптимизации ----------

    def start_scan_registry(self) -> None:
        if self._reg_scan_worker and self._reg_scan_worker.isRunning():
            return
        self.btn_reg_scan.setEnabled(False)
        self.btn_reg_fix.setEnabled(False)
        self.spinner.start()
        self.status_title.setText("Проверка реестра на битые ссылки…")

        self._reg_scan_worker = ScanRegistryWorker()
        self._reg_scan_worker.progress.connect(self.status_title.setText)
        self._reg_scan_worker.succeeded.connect(self._on_reg_scan_done)
        self._reg_scan_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Реестр", msg))
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
            key_display = f"{iss.hive_name}\\{iss.subkey} → {iss.value_name}"
            self.reg_model.appendRow([
                cat_item,
                QStandardItem(key_display),
                QStandardItem(iss.reason),
            ])
        self.btn_reg_fix.setEnabled(bool(issues))
        self.status_title.setText(f"Проверка реестра завершена · Найдено записей: {len(issues)}")

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
        ans = QMessageBox.question(
            self,
            "Исправление реестра",
            f"Удалить выбранные битые записи реестра ({len(selected)})?\n"
            "Перед удалением автоматически сохранится .reg-файл резервной копии.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.btn_reg_scan.setEnabled(False)
        self.btn_reg_fix.setEnabled(False)
        self.spinner.start()

        self._reg_fix_worker = FixRegistryWorker(self._reg_issues, selected)
        self._reg_fix_worker.succeeded.connect(self._on_reg_fix_done)
        self._reg_fix_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Реестр", msg))
        self._reg_fix_worker.finished.connect(self._on_reg_scan_finished)
        self._reg_fix_worker.start()

    def _on_reg_fix_done(self, summary: RegistryFixSummary) -> None:
        msg = f"Исправлено записей: {summary.fixed_count}\nРезервная копия сохранена:\n{summary.backup_path}"
        if summary.errors:
            msg += "\n\nНе удалось удалить:\n" + "\n".join(summary.errors[:5])
        QMessageBox.information(self, "Реестр очищен", msg)
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
                self.startup_model.appendRow([
                    name_it,
                    QStandardItem(item.source),
                    QStandardItem(item.command),
                ])
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
            state_txt = "включена" if new_enabled else "отключена"
            self.status_title.setText(f"Автозагрузка «{st_item.name}» {state_txt}")
        except OSError as exc:
            self._startup_loading = True
            item.setCheckState(Qt.CheckState.Checked if st_item.enabled else Qt.CheckState.Unchecked)
            self._startup_loading = False
            QMessageBox.warning(self, "Автозагрузка", f"Не удалось изменить статус (нужны права администратора):\n{exc}")

    def start_optimize_task(self, task_key: str) -> None:
        if self._opt_worker and self._opt_worker.isRunning():
            return
        for b in self._opt_buttons:
            b.setEnabled(False)
        self.spinner.start()
        self.opt_status_lbl.setText("Выполняется задача оптимизации…")
        self.status_title.setText("Выполняется оптимизация системы…")

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
        icon = "✔" if res.success else "⚠"
        self.opt_status_lbl.setText(f"{icon} {res.title}: {res.message}")
        self.status_title.setText(f"{res.title} — {'выполнено' if res.success else 'ошибка'}")

    def _on_optimize_failed(self, message: str) -> None:
        self.opt_status_lbl.setText(f"Ошибка: {message}")
        QMessageBox.warning(self, "Оптимизация", message)

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

    def start_search_updates(self) -> None:
        self.btn_search.setEnabled(False)
        self.btn_install.setEnabled(False)
        self.spinner.start()
        self.progress.setRange(0, 0)
        self.progress.setFormat("Поиск обновлений в каталоге Windows Update…")
        self.progress.show()
        self._log("Запущен поиск обновлений драйверов через Windows Update…")

        self._search_worker = SearchUpdatesWorker()
        self._search_worker.succeeded.connect(self._on_search_done)
        self._search_worker.failed.connect(self._on_updates_failed)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_finished(self) -> None:
        self.btn_search.setEnabled(True)
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
        for worker in (
            self._scan_worker,
            self._search_worker,
            self._analyze_worker,
            self._reg_scan_worker,
            self._reg_fix_worker,
            self._opt_worker,
        ):
            if worker and worker.isRunning():
                self.status_title.setText("Завершение фоновых операций…")
                worker.wait()
        event.accept()
