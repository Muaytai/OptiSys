"""Точка входа optisys.exe."""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from app.ui import MainWindow
from app.utils import log_dir


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir() / "optisys.log", encoding="utf-8")],
    )


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("optisys")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

