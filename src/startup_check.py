"""
startup_check.py

Verifies at runtime that all required Python packages and external tools
are available, and returns a list of human-readable warnings (never
raises) if anything is missing. Called once from main.py before the GUI
is created.

This is a diagnostic safety net, not an installer - there isn't one.
`requirements.txt` covers the Python side (`pip install -r requirements.txt`);
ffmpeg and Poppler are external tools the user (or `build_exe.ps1`'s own
build/distribution step) drops into `tools/ffmpeg/` and `tools/poppler/` next
to the app, installs system-wide, or gets via `winget`. This module exists so
that if any of that is still missing, the user gets one clear, all-in-one
heads-up instead of a raw traceback deep inside some unrelated feature the
first time they touch it.

# [AUDIT] Section 4 - dead-comment drift: this docstring and the warning
# strings below used to reference "Allora.bat" as the installer that sets
# all of this up - no such script exists anywhere in this repository. The
# actual mechanism is `tools/ffmpeg/` and `tools/poppler/` next to the app
# (see find_ffmpeg()/find_poppler_bin_dir() in utils.py) plus PATH/winget.

Reuses utils.find_ffmpeg()/find_poppler_bin_dir() rather than
re-implementing PATH/tools-folder detection here, so this stays in sync
with wherever those functions actually look.
"""

import importlib

from utils import find_poppler_bin_dir

# (import name, pip package name) - matches requirements.txt exactly.
REQUIRED_PACKAGES = [
    ("PySide6", "PySide6"),
    ("yt_dlp", "yt-dlp"),
    ("requests", "requests"),
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
    ("pdf2image", "pdf2image"),
    ("pdfplumber", "pdfplumber"),
    ("pdf2docx", "pdf2docx"),
    ("docx", "python-docx"),
    ("reportlab", "reportlab"),
    ("docx2pdf", "docx2pdf"),
    ("pypdf", "pypdf"),
]


def verify_environment() -> list:
    """Returns a list of warning strings; an empty list means everything
    looks fine. Missing packages/tools don't block startup - individual
    features already show their own clear error when they actually hit a
    missing dependency (e.g. the ffmpeg-missing dialog on the Downloads
    tab) - this is just an early, all-in-one summary."""
    warnings = []

    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            warnings.append(f"Pacote Python ausente: {pip_name}  ->  pip install {pip_name}")
        except Exception as exc:  # noqa: BLE001
            # [AUDIT] Section 1 - HIGH: a broken install (e.g. a DLL-load
            # failure inside numpy/cv2) used to raise something other than
            # ImportError here, which escaped this loop entirely - and, in
            # main.py, escaped the surrounding try/except too, since this
            # call used to run before that try started. One bad package no
            # longer stops the rest of the check from running.
            warnings.append(f"Pacote Python com problema: {pip_name} ({exc})")

    # [AUDIT] Section 1 - HIGH: no ffmpeg check here anymore. Now that
    # verify_environment()'s findings are actually shown through a Qt
    # dialog (see main.py) instead of a print() nobody in --windowed sees,
    # keeping this one too would pop a second, more generic warning right
    # alongside MainWindow._check_ffmpeg_on_start()'s own dedicated
    # ffmpeg-missing dialog, which already tells the user exactly what to
    # do (including where to fix it in Configurações). Poppler has no
    # equivalent proactive check elsewhere, so it stays here.
    if not find_poppler_bin_dir():
        warnings.append(
            "Poppler não encontrado - necessário para converter PDFs na aba "
            "Documentos. Baixe manualmente em "
            "https://github.com/oschwartz10612/poppler-windows/releases "
            "e coloque em tools\\poppler\\ ao lado do Allora."
        )

    return warnings
