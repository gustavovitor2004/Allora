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

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from settings import load_settings
from downloader import DownloadManager
from converter import ConversionManager
from ui import MainWindow
from startup_check import verify_environment
from utils import resource_path


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Allora")
    # Frameless windows (see MainWindow's Qt.FramelessWindowHint in ui.py)
    # don't reliably inherit the .exe's own embedded icon for the taskbar
    # button - Qt/Windows only picks that up automatically for windows with
    # a native frame. Setting it explicitly here is what actually makes the
    # taskbar (and Alt+Tab) show the real Allora icon instead of a generic
    # placeholder.
    app.setWindowIcon(QIcon(resource_path("assets/icon.ico")))

    try:
        settings = load_settings()
        manager = DownloadManager(settings)
        conversion_manager = ConversionManager(settings)
        window = MainWindow(manager, conversion_manager, settings)
        window.show()

        # [AUDIT] Section 1/2 - HIGH: verify_environment() used to run
        # BEFORE this try block even started, importing 13 packages and
        # shelling out to `ffmpeg -version` before the window was created -
        # delaying first paint with nothing shown for it, and printing its
        # findings via print(), which is invisible in the --windowed build.
        # Worse, since it ran outside this try, any exception it raised
        # other than the ImportError it expects (a DLL-load failure, say)
        # skipped the dialog below entirely and could kill the app with no
        # visible error at all. It's deferred to right after the window
        # is shown (so the window paints first) and wrapped in its own
        # try/except, since a QTimer callback runs after main()'s own try
        # block has already returned and wouldn't be caught by it.
        def _run_startup_diagnostics():
            try:
                warnings = verify_environment()
            except Exception:
                warnings = [f"Falha ao verificar o ambiente:\n{traceback.format_exc()}"]
            if warnings:
                QMessageBox.warning(
                    window,
                    "Verificação de ambiente",
                    "Alguns componentes podem estar ausentes ou com problema:\n\n"
                    + "\n\n".join(warnings),
                )

        QTimer.singleShot(0, _run_startup_diagnostics)
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
    # [AUDIT] Section 1 - MEDIUM: required for documentos/converter.py's
    # PDF-to-DOCX conversion, which now runs pdf2docx in a real child
    # process (via multiprocessing) so a timeout can actually terminate a
    # hung conversion instead of just abandoning an unkillable thread. On
    # Windows (the only platform this ships for, and the only one where
    # multiprocessing's spawn start method matters), spawning that child
    # process from inside the frozen --windowed .exe would otherwise
    # re-execute this whole entry point from scratch instead of just the
    # target function - freeze_support() is what tells the multiprocessing
    # bootstrap it's being re-invoked as a worker, not a fresh app launch.
    # Must be the very first thing that runs, before anything else.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
