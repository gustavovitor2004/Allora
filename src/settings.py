"""
settings.py

Loads/saves application configuration to a local config.json file that
lives at the project root (one level above this file's own `src/` folder)
so it survives future code updates without being nested inside the source
tree, while all actual *data* (downloads) defaults to a user-writable path
under the user's home directory - never Program Files.
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from utils import project_root

# project_root() resolves to the project folder normally, or to the
# .exe's own directory when running as a PyInstaller-frozen build.
APP_DIR = Path(project_root())
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_OUTPUT_DIR = str(Path.home() / "Videos" / "Downloads")
DEFAULT_OCR_OUTPUT_DIR = str(Path.home() / "Documents" / "Digitalizados")
DEFAULT_DOC_CONVERT_OUTPUT_DIR = str(Path.home() / "Documents" / "Convertidos")

QUALITY_CHOICES = [
    "4K (2160p)",
    "1080p Full HD",
    "720p HD",
    "480p",
    "360p",
    "Melhor qualidade disponível",
    "Apenas áudio (MP3)",
]


@dataclass
class Settings:
    output_dir: str = DEFAULT_OUTPUT_DIR
    default_quality: str = "Melhor qualidade disponível"
    max_simultaneous: int = 2
    use_ffmpeg_merge: bool = True
    save_thumbnail: bool = False
    save_metadata: bool = False
    ffmpeg_path: str = ""
    theme: str = "dark"  # "dark" or "light"
    ocr_output_dir: str = DEFAULT_OCR_OUTPUT_DIR
    doc_convert_output_dir: str = DEFAULT_DOC_CONVERT_OUTPUT_DIR

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Settings":
        defaults = Settings()
        merged = defaults.to_dict()
        if isinstance(data, dict):
            for key in merged:
                if key in data:
                    merged[key] = data[key]
        return Settings(**merged)


def load_settings() -> Settings:
    """Load settings from config.json, creating the file with defaults on
    first run (or if the existing file is corrupted)."""
    if not CONFIG_PATH.exists():
        settings = Settings()
        save_settings(settings)
        return settings

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = Settings.from_dict(data)
    except (json.JSONDecodeError, OSError):
        settings = Settings()
        save_settings(settings)

    # Make sure the output directory actually exists / is creatable.
    try:
        os.makedirs(settings.output_dir, exist_ok=True)
    except OSError:
        settings.output_dir = DEFAULT_OUTPUT_DIR
        os.makedirs(settings.output_dir, exist_ok=True)

    # Same for the Documentos tab's OCR / conversion output folders.
    try:
        os.makedirs(settings.ocr_output_dir, exist_ok=True)
    except OSError:
        settings.ocr_output_dir = DEFAULT_OCR_OUTPUT_DIR
        os.makedirs(settings.ocr_output_dir, exist_ok=True)

    try:
        os.makedirs(settings.doc_convert_output_dir, exist_ok=True)
    except OSError:
        settings.doc_convert_output_dir = DEFAULT_DOC_CONVERT_OUTPUT_DIR
        os.makedirs(settings.doc_convert_output_dir, exist_ok=True)

    return settings


def save_settings(settings: Settings) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2, ensure_ascii=False)
    except OSError as exc:
        # Config is not writable - non-fatal, the app can keep running with
        # in-memory settings for this session. Encode defensively: a GUI
        # app may have no console, or one using a legacy codepage that
        # can't represent every character in the error message.
        try:
            print(f"[settings] Nao foi possivel salvar config.json: {exc}")
        except Exception:
            pass
