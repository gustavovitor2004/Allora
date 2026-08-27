"""
updater.py

Best-effort startup check against GitHub Releases: is a newer version of
MasterApp available? Runs on a background QThread so a slow or unreachable
network never delays the window from opening, and any failure (offline,
GitHub down, rate-limited) is swallowed silently - this is a convenience
nag, not a feature the app depends on, so it must never surface an error
dialog or block anything.
"""

import json
import urllib.request
from urllib.error import URLError

from PySide6.QtCore import QThread, Signal

from version import APP_VERSION, GITHUB_OWNER, GITHUB_REPO

_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def _parse_version(text: str) -> tuple:
    """'v1.2.0' / '1.2.0' -> (1, 2, 0). Non-numeric parts are dropped so a
    stray suffix like '1.2.0-beta' still compares sanely against '1.2.0'
    instead of raising."""
    text = text.strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _parse_version(remote) > _parse_version(local)


class UpdateCheckWorker(QThread):
    """Emits update_available(tag_name, html_url) once, only if GitHub's
    latest release is newer than APP_VERSION. Emits nothing at all on any
    failure - callers should simply never hear from a worker that found no
    update (or couldn't check)."""

    update_available = Signal(str, str)

    def run(self):
        try:
            request = urllib.request.Request(
                _API_URL,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "MasterApp"},
            )
            with urllib.request.urlopen(request, timeout=6) as response:
                data = json.load(response)
        except (URLError, TimeoutError, ValueError, OSError):
            return

        tag_name = data.get("tag_name", "")
        html_url = data.get("html_url", "")
        if tag_name and html_url and is_newer(tag_name):
            self.update_available.emit(tag_name, html_url)
