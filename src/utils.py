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
import threading
from urllib.parse import urlparse


def project_root() -> str:
    """The directory where config.json, tools/ffmpeg, tools/poppler, etc.
    live. Normally the directory containing this src/ folder - but inside a
    PyInstaller-frozen .exe, __file__ resolves into the bundle's temporary
    extraction dir instead, so we anchor to the .exe's own directory there."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative_path: str) -> str:
    """Locate a bundled read-only asset (e.g. assets/logo.png) that ships
    inside the app itself, as opposed to project_root()'s user-writable
    files (config.json, tools/). Inside a frozen .exe built with PyInstaller
    --onedir, data added via --add-data is extracted into the _internal/
    folder next to the executable, exposed at runtime as sys._MEIPASS -
    NOT the executable's own directory (project_root()'s meaning), so this
    needs its own base-path logic rather than reusing project_root()."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)

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


# [AUDIT] Section 5 - MEDIUM: `ffmpeg -version` used to be re-run as a fresh
# subprocess every time this is called - at startup, every time Configurações
# opens, and before every conversion job. Cached here by (path, mtime): the
# same path with an unchanged mtime returns the cached result instead of
# spawning another process; a different mtime (the binary was reinstalled or
# replaced) or a different path (the user picked a new custom path in
# Configurações) naturally misses the cache and re-checks for real.
_binary_check_cache = {}


def binary_is_working(path: str, version_flag: str = "-version") -> bool:
    """Actually try to run `<path> <version_flag>` to confirm it's a real,
    executable binary rather than just a path that happens to exist."""
    if not path:
        return False

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    cached = _binary_check_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        proc = subprocess.run(
            [path, version_flag],
            capture_output=True,
            timeout=5,
            creationflags=no_window_flags(),
        )
        result = proc.returncode == 0
    except Exception:
        result = False

    _binary_check_cache[path] = (mtime, result)
    return result


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

        # [AUDIT] Section 4: a portable ffmpeg placed by hand (or by the
        # distributor) into tools/ffmpeg, next to the app, when it isn't
        # already on PATH - never registered system-wide, so this fixed,
        # predictable location is the only way the app can find it. (No
        # installer script does this automatically; see startup_check.py.)
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
        # Same idea as find_ffmpeg()'s tools/ffmpeg fallback: a portable
        # Poppler placed into tools/poppler when it isn't already on PATH.
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


# [AUDIT] Section 1 - MEDIUM: unique_path() used to only check
# os.path.exists() with no reservation - two conversions racing to pick a
# name at nearly the same moment (e.g. two queued jobs that both source
# from a same-named file, or two tabs converting at once) could both land
# on the same candidate before either had actually created it; whichever
# finished writing second silently overwrote the other's output (most
# writers here, including ffmpeg with -y, happily truncate an existing
# file). Names are now reserved in-process the moment they're handed out,
# under a lock, so a second caller racing the first is guaranteed to see
# the reservation and move on to the next candidate - not just an
# unwritten-but-about-to-collide path.
#
# This is an in-memory, per-application-lifetime reservation, not a
# filesystem one: it doesn't touch disk (several callers check
# os.path.exists(out_path) afterward as their own success signal, which a
# pre-created placeholder file would have broken), and a name whose
# conversion later fails or is cancelled stays reserved for the rest of
# this run - a minor cosmetic cost (a subsequent retry with the same base
# name gets " (1)" instead of reusing the original), traded for actually
# preventing the silent overwrite.
_reserved_paths_lock = threading.Lock()
_reserved_paths = set()


def unique_path(directory: str, base_name: str, ext: str) -> str:
    """Build a path for `<directory>/<base_name>.<ext>`, appending \" (1)\",
    \" (2)\", etc. until it doesn't collide with an existing file or an
    in-process reservation from a concurrent caller. Shared by every module
    that writes converted/exported output (the top-level converter.py,
    documentos/converter.py)."""
    with _reserved_paths_lock:
        candidate = os.path.join(directory, f"{base_name}.{ext}")
        counter = 1
        while os.path.exists(candidate) or candidate in _reserved_paths:
            candidate = os.path.join(directory, f"{base_name} ({counter}).{ext}")
            counter += 1
        _reserved_paths.add(candidate)
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


# ---------------------------------------------------------------------------
# [AUDIT] Section 3 - duplicate code: shared by the Documentos tab's two
# image-handling modules (documentos/scanner_engine.py,
# documentos/converter.py), which used to each carry their own identical
# copy. Not the same list as the top-level converter.py's IMAGE_FORMATS,
# which covers a different, broader set of ffmpeg-convertible formats
# (includes gif, for one) for a different feature - this one is
# specifically "image formats the Documentos scanner/converter pipeline
# accepts", so it stays its own constant rather than being unified with
# that one.
# ---------------------------------------------------------------------------

IMAGE_EXTS = ["jpg", "jpeg", "png", "bmp", "webp", "tiff"]


def fit_to_page(image_size, page_size):
    """Scale an image to fit centered inside a page, preserving aspect
    ratio. Returns (draw_width, draw_height, x, y). Shared by
    documentos/converter.py (image -> PDF) and documentos/scanner_engine.py
    (scan result -> PDF), which used to each carry an identical copy of
    this exact formula."""
    img_w, img_h = image_size
    page_w, page_h = page_size
    scale = min(page_w / img_w, page_h / img_h)
    draw_w, draw_h = img_w * scale, img_h * scale
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2
    return draw_w, draw_h, x, y
