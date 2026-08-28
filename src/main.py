"""
main.py

Entry point for the Allora desktop app.

Usage:
    pip install -r requirements.txt
    python src/main.py

This file is intentionally thin: it wires together settings, the download
manager and the GUI, and makes sure any startup failure is shown to the
user in a dialog instead of crashing silently to a console window that
most end users of this app will never see.
"""

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from settings import load_settings
from downloader import DownloadManager
from converter import ConversionManager
from ui import MainWindow
from startup_check import verify_environment


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Allora")

    for warning in verify_environment():
        print(f"[AVISO] {warning}")

    try:
        settings = load_settings()
        manager = DownloadManager(settings)
        conversion_manager = ConversionManager(settings)
        window = MainWindow(manager, conversion_manager, settings)
        window.show()
    except Exception:
        # Anything going wrong during startup still gets a visible dialog
        # rather than a silent crash / invisible console traceback.
        error_text = traceback.format_exc()
        QMessageBox.critical(
            None,
            "Erro ao iniciar o Allora",
            f"Ocorreu um erro inesperado ao iniciar o aplicativo:\n\n{error_text}",
        )
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
