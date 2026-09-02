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

from theme import DEFAULT_THEME, normalize_theme_name
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
    theme: str = DEFAULT_THEME  # key into theme.THEMES
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
                if key not in data:
                    continue
                value = data[key]
                # [FIX] Type-check against the default before accepting a
                # value. Dataclasses don't validate, so a hand-edited or
                # partially-written config.json used to flow straight
                # through: `"output_dir": null` reached os.makedirs(None)
                # and raised TypeError (not the OSError load_settings
                # guards against), so the app refused to start at all; a
                # non-int max_simultaneous instead killed the download
                # dispatcher thread on its first arithmetic. Anything of
                # the wrong type now silently falls back to its default.
                expected = type(merged[key])
                if expected is bool:
                    if not isinstance(value, bool):
                        continue
                elif expected is int:
                    # bool is a subclass of int - reject it explicitly.
                    if not isinstance(value, int) or isinstance(value, bool):
                        continue
                elif expected is str:
                    if not isinstance(value, str):
                        continue
                merged[key] = value
        merged["theme"] = normalize_theme_name(merged["theme"])
        # A zero/negative worker count would stall every queue forever
        # (free_slots never goes above 0); clamp to the range the
        # Configurações spinbox itself allows.
        merged["max_simultaneous"] = max(1, min(3, merged["max_simultaneous"]))
        return Settings(**merged)


def _ensure_dir(path: str, fallback: str) -> str:
    """Return a directory that actually exists, preferring `path` and falling
    back to `fallback`.

    [FIX] The three output folders each repeated the same
    try-makedirs / on-OSError-reset-to-default / makedirs-again block. That
    second makedirs was itself unguarded, so a home directory that can't be
    written to (locked-down profile, full disk) turned a merely-bad saved
    setting into an OSError escaping load_settings() - which main.py can only
    answer with a fatal "erro ao iniciar" dialog, leaving the app unable to
    open at all. Returning the best path we managed lets the app start; every
    feature that actually writes there already surfaces its own OSError to
    the user (see ScannerSubTab.on_save_as, ConvertSubTab.on_convert_clicked,
    SettingsDialog.accept), so nothing fails silently."""
    for candidate in (path, fallback):
        try:
            os.makedirs(candidate, exist_ok=True)
            return candidate
        except OSError:
            continue
    return fallback


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

    # Make sure the three output directories exist / are creatable.
    settings.output_dir = _ensure_dir(settings.output_dir, DEFAULT_OUTPUT_DIR)
    settings.ocr_output_dir = _ensure_dir(settings.ocr_output_dir, DEFAULT_OCR_OUTPUT_DIR)
    settings.doc_convert_output_dir = _ensure_dir(
        settings.doc_convert_output_dir, DEFAULT_DOC_CONVERT_OUTPUT_DIR
    )

    return settings


def save_settings(settings: Settings) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        # [FIX] Write-then-rename instead of writing over config.json in
        # place. Opening the real file with "w" truncated it *before* the
        # new contents were written, so anything interrupting the write
        # (crash, kill, power loss) left a truncated file behind - and
        # load_settings() treats unparseable JSON as "corrupt, reset to
        # defaults", silently wiping every setting the user had. os.replace
        # is atomic on Windows and POSIX alike, so config.json is now
        # always either the old file or the complete new one.
        tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
    except OSError as exc:
        # Config is not writable - non-fatal, the app can keep running with
        # in-memory settings for this session. Encode defensively: a GUI
        # app may have no console, or one using a legacy codepage that
        # can't represent every character in the error message.
        try:
            print(f"[settings] Nao foi possivel salvar config.json: {exc}")
        except Exception:
            pass
