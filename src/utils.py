"""
utils.py

Small, dependency-light helpers shared across the app:
- URL validation
- Platform detection (YouTube, Instagram, Twitter/X, TikTok, Reddit, Facebook, generic)
- ffmpeg detection
- human-readable formatting for byte sizes / ETA / speed
- filesystem-safe filename / unique-output-path helpers
- small subprocess helpers shared by every module that shells out to a
  helper binary (ffmpeg, ffprobe, tesseract, soffice/LibreOffice)
"""

import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse


def project_root() -> str:
    """The directory where config.json, tools/ffmpeg, tools/poppler, etc.
    live. Normally the directory containing this src/ folder - but inside a
    PyInstaller-frozen .exe, __file__ resolves into the bundle's temporary
    extraction dir instead, so we anchor to the .exe's own directory there."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

# Ordered so more specific hosts are checked before generic fallbacks.
PLATFORM_PATTERNS = [
    ("YouTube", re.compile(r"(youtube\.com|youtu\.be|music\.youtube\.com)", re.I)),
    ("Instagram", re.compile(r"instagram\.com", re.I)),
    ("Twitter/X", re.compile(r"(twitter\.com|x\.com)", re.I)),
    ("TikTok", re.compile(r"tiktok\.com", re.I)),
    ("Reddit", re.compile(r"reddit\.com", re.I)),
    ("Facebook", re.compile(r"(facebook\.com|fb\.watch)", re.I)),
    ("Vimeo", re.compile(r"vimeo\.com", re.I)),
    ("Twitch", re.compile(r"twitch\.tv", re.I)),
]

PLATFORM_ICONS = {
    "YouTube": "\U0001F534",       # red circle
    "Instagram": "\U0001F4F7",     # camera
    "Twitter/X": "\U0001F426",     # bird
    "TikTok": "\U0001F3B5",        # musical note
    "Reddit": "\U0001F47D",        # alien
    "Facebook": "\U0001F535",      # blue circle
    "Vimeo": "\U0001F3AC",         # clapper
    "Twitch": "\U0001F47E",        # game controller-ish
    "Outro": "\U0001F310",         # globe
}


def detect_platform(url: str) -> str:
    """Return a human-readable platform label detected from the URL host."""
    for label, pattern in PLATFORM_PATTERNS:
        if pattern.search(url):
            return label
    return "Outro"


def platform_icon(label: str) -> str:
    return PLATFORM_ICONS.get(label, PLATFORM_ICONS["Outro"])


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def is_valid_url(url: str) -> bool:
    """Basic structural validation - not a guarantee yt-dlp can extract it,
    but enough to catch empty/garbage input before we hit the network."""
    if not url or not url.strip():
        return False
    url = url.strip()
    try:
        result = urlparse(url)
    except ValueError:
        return False
    if result.scheme not in ("http", "https"):
        return False
    if not result.netloc:
        return False
    return True


def split_urls(text: str) -> list:
    """Split multi-line pasted text into a list of individual, valid URLs."""
    candidates = re.split(r"[\r\n]+", text)
    urls = []
    for c in candidates:
        c = c.strip()
        if c and is_valid_url(c):
            urls.append(c)
    return urls


# ---------------------------------------------------------------------------
# Subprocess helpers shared by every module that shells out to a helper
# binary (ffmpeg/ffprobe in converter.py, soffice in documentos/converter.py)
# ---------------------------------------------------------------------------

def no_window_flags():
    """subprocess creationflags that suppress the console window that would
    otherwise flash briefly on Windows when launching a helper binary from
    a GUI app. No-op (0) on non-Windows platforms."""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def binary_is_working(path: str, version_flag: str = "-version") -> bool:
    """Actually try to run `<path> <version_flag>` to confirm it's a real,
    executable binary rather than just a path that happens to exist."""
    if not path:
        return False
    try:
        proc = subprocess.run(
            [path, version_flag],
            capture_output=True,
            timeout=5,
            creationflags=no_window_flags(),
        )
        return proc.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ffmpeg detection
# ---------------------------------------------------------------------------

def find_ffmpeg(custom_path: str = "") -> str:
    """Return a usable ffmpeg executable path/name, or '' if none found."""
    if custom_path:
        candidate = custom_path
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("ffmpeg")
    if found:
        return found

    if os.name == "nt":
        # winget installs ffmpeg as a "portable" package and exposes it via
        # a shim in this fixed folder. Checking it directly means the app
        # can find ffmpeg right after an installer script runs, even before
        # the user has restarted the PC and the PATH change has fully
        # propagated to every already-running process.
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            winget_shim = os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
            if os.path.isfile(winget_shim):
                return winget_shim

        # MasterApp.bat downloads a portable ffmpeg into tools/ffmpeg on
        # first run when it isn't already on PATH - never registered
        # system-wide, so this fixed, predictable location is the only way
        # the app can find it.
        bundled = os.path.join(project_root(), "tools", "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled

    return ""


def ffmpeg_is_working(ffmpeg_path: str) -> bool:
    """Actually try to run ffmpeg -version to confirm it's a real binary."""
    return binary_is_working(ffmpeg_path, "-version")


# ---------------------------------------------------------------------------
# Poppler detection (needed by pdf2image, used for PDF -> image conversion)
# ---------------------------------------------------------------------------

def find_poppler_bin_dir(custom_path: str = "") -> str:
    """Return a directory containing pdftoppm/pdftocairo, or '' if none
    found. pdf2image's `poppler_path` argument wants a *directory*, not the
    binary path itself, unlike find_ffmpeg()."""
    pdftoppm_name = "pdftoppm.exe" if os.name == "nt" else "pdftoppm"

    if custom_path and os.path.isdir(custom_path):
        if os.path.isfile(os.path.join(custom_path, pdftoppm_name)):
            return custom_path

    found = shutil.which("pdftoppm")
    if found:
        return os.path.dirname(found)

    if os.name == "nt":
        # Same idea as find_ffmpeg()'s tools/ffmpeg fallback: MasterApp.bat
        # downloads a portable Poppler into tools/poppler when it isn't
        # already on PATH.
        bundled = os.path.join(project_root(), "tools", "poppler", pdftoppm_name)
        if os.path.isfile(bundled):
            return os.path.dirname(bundled)

    return ""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_bytes_per_sec(num_bytes) -> str:
    if not num_bytes:
        return "-- MB/s"
    num_bytes = float(num_bytes)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB/s"


def format_eta(seconds) -> str:
    if seconds is None:
        return "--"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_size(num_bytes) -> str:
    if not num_bytes:
        return "--"
    num_bytes = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def safe_filename(name: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip()[:150] or "video"


def unique_path(directory: str, base_name: str, ext: str) -> str:
    """Build a path for `<directory>/<base_name>.<ext>`, appending \" (1)\",
    \" (2)\", etc. until it doesn't collide with an existing file. Shared by
    every module that writes converted/exported output (the top-level
    converter.py, documentos/converter.py)."""
    candidate = os.path.join(directory, f"{base_name}.{ext}")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base_name} ({counter}).{ext}")
        counter += 1
    return candidate


def height_to_label(height) -> str:
    """Map a pixel height back onto one of our quality labels for display."""
    if not height:
        return "desconhecida"
    if height >= 2160:
        return "4K (2160p)"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    if height >= 360:
        return "360p"
    return f"{height}p"
