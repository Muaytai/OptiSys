"""Современная тёмная тема оформления (Slate / Fluent Dark)."""
from __future__ import annotations

BG_MAIN = "#0f131a"
BG_SIDEBAR = "#141923"
BG_CARD = "#181f2c"
BG_CARD_HOVER = "#1e2738"
BG_INPUT = "#111620"
BORDER = "#252f42"
TEXT_PRIMARY = "#f1f5f9"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#64748b"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
SUCCESS = "#10b981"
WARNING = "#f59e0b"
DANGER = "#ef4444"

APP_STYLESHEET = f"""
QMainWindow, QWidget#RootWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}}

QFrame#Sidebar {{
    background-color: {BG_SIDEBAR};
    border-right: 1px solid {BORDER};
}}

QFrame#HeaderBar {{
    background-color: {BG_SIDEBAR};
    border-bottom: 1px solid {BORDER};
}}

QFrame#Card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QFrame#CardHover {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#CardHover:hover {{
    background-color: {BG_CARD_HOVER};
    border-color: #334155;
}}

QLabel {{
    color: {TEXT_PRIMARY};
    background: transparent;
}}

QLabel#MutedLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}

QLabel#SectionTitle {{
    color: {TEXT_PRIMARY};
    font-size: 18px;
    font-weight: 600;
}}

QPushButton#ActionBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1e293b, stop:1 #151d2a);
    color: {TEXT_PRIMARY};
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton#ActionBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2563eb, stop:1 #1d4ed8);
    border-color: #60a5fa;
    color: #ffffff;
}}
QPushButton#ActionBtn:pressed {{
    background-color: #1e40af;
}}
QPushButton#ActionBtn:disabled {{
    color: {TEXT_MUTED};
    background-color: #131822;
    border-color: #1c2433;
}}

QPushButton#PrimaryBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:0.5 #3b82f6, stop:1 #06b6d4);
    color: #ffffff;
    border: 1px solid #60a5fa;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 700;
}}
QPushButton#PrimaryBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1d4ed8, stop:0.5 #2563eb, stop:1 #0891b2);
    border-color: #93c5fd;
}}
QPushButton#PrimaryBtn:pressed {{
    background-color: #1e40af;
}}
QPushButton#PrimaryBtn:disabled {{
    background-color: #1e293b;
    border-color: #252f42;
    color: {TEXT_MUTED};
}}

QPushButton#SuccessBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:0.5 #10b981, stop:1 #0d9488);
    color: #ffffff;
    border: 1px solid #34d399;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 700;
}}
QPushButton#SuccessBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:0.5 #059669, stop:1 #0f766e);
    border-color: #6ee7b7;
}}
QPushButton#SuccessBtn:pressed {{
    background-color: #065f46;
}}
QPushButton#SuccessBtn:disabled {{
    background-color: #1e293b;
    border-color: #252f42;
    color: {TEXT_MUTED};
}}

QPushButton#AccentPurpleBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:0.5 #8b5cf6, stop:1 #ec4899);
    color: #ffffff;
    border: 1px solid #a78bfa;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 700;
}}
QPushButton#AccentPurpleBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:0.5 #7c3aed, stop:1 #db2777);
    border-color: #c4b5fd;
}}
QPushButton#AccentPurpleBtn:disabled {{
    background-color: #1e293b;
    border-color: #252f42;
    color: {TEXT_MUTED};
}}

QLineEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}

QTableView {{
    background-color: {BG_CARD};
    alternate-background-color: #151b26;
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: #1e2636;
    selection-background-color: rgba(59, 130, 246, 0.25);
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QTableView::item {{
    padding: 6px 10px;
    border: none;
}}
QTableView::item:hover {{
    background-color: rgba(255, 255, 255, 0.04);
}}

QHeaderView::section {{
    background-color: #131822;
    color: {TEXT_SECONDARY};
    font-weight: 600;
    font-size: 12px;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid #1c2331;
}}

QPlainTextEdit {{
    background-color: {BG_INPUT};
    color: #cbd5e1;
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}}

QProgressBar {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    text-align: center;
    height: 22px;
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #06b6d4);
    border-radius: 7px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2d3748;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #4a5568;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #2d3748;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QStatusBar {{
    background-color: {BG_SIDEBAR};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BORDER};
}}
"""

