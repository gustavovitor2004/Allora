"""
startup_check.py

Verifies at runtime that all required Python packages and external tools
are available, and returns a list of human-readable warnings (never
raises) if anything is missing. Called once from main.py before the GUI
is created.

This is a diagnostic safety net, not the primary install mechanism -
Allora.bat is what actually installs everything on first run. This
module exists for the case where the app gets launched some other way
(e.g. `python src/main.py` directly, skipping Allora.bat) and something
is still missing, so the user gets one clear, all-in-one heads-up printed
to the console instead of a raw traceback deep inside some unrelated
feature the first time they touch it.

Reuses utils.find_ffmpeg()/find_poppler_bin_dir() rather than
re-implementing PATH/tools-folder detection here, so this stays in sync
with wherever Allora.bat actually puts things.
"""

import importlib

from utils import ffmpeg_is_working, find_ffmpeg, find_poppler_bin_dir

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

    if not ffmpeg_is_working(find_ffmpeg()):
        warnings.append(
            "ffmpeg não encontrado - necessário para baixar/converter vídeo. "
            "Rode Allora.bat novamente, ou baixe manualmente em "
            "https://ffmpeg.org/download.html"
        )

    if not find_poppler_bin_dir():
        warnings.append(
            "Poppler não encontrado - necessário para converter PDFs na aba "
            "Documentos. Rode Allora.bat novamente, ou baixe manualmente em "
            "https://github.com/oschwartz10612/poppler-windows/releases"
        )

    return warnings
