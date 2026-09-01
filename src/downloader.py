"""
downloader.py

yt-dlp wrapper, download queue model and background threading.

Design notes
------------
- Each queue item is a `DownloadItem` (plain data holder + a threading.Event
  used to signal cancellation into the yt-dlp progress hook).
- `DownloadManager` is a QObject so it can emit Qt signals from worker
  threads. PySide6 automatically marshals signal emissions from a non-GUI
  thread to the receiving (GUI) thread's event loop (queued connection),
  so the UI never needs to poll and is never blocked.
- Metadata (title/thumbnail) is fetched in its own short-lived thread as
  soon as a URL is queued, independent from the actual download, so the
  queue list can show a title/thumbnail before the download starts.
- A single lightweight dispatcher thread decides when to start the next
  queued item, respecting the "max simultaneous downloads" setting and the
  paused/running flags.
- True mid-stream pause of an active HTTP download is not something yt-dlp
  exposes, so "Pausar" stops the dispatcher from starting new items - any
  downloads already in progress finish naturally. Cancellation *is* fully
  supported: we raise KeyboardInterrupt from inside the progress hook,
  which is the standard, reliable way to make yt-dlp abort a download
  cleanly mid-transfer.
- Mutating `items`/`order` must always happen under `_lock` (see
  remove_item) - the dispatcher thread iterates both, and an inconsistent
  pair between them used to kill it with a KeyError.
"""

import itertools
import os
import threading
import time

import yt_dlp

from queue_manager import QueueManager
from settings import Settings
from utils import (
    detect_platform, ffmpeg_is_working, find_ffmpeg, format_bytes_per_sec,
    format_eta, height_to_label,
)

try:
    import requests
except ImportError:  # pragma: no cover - requests is a declared dependency
    requests = None


FORMAT_MAP = {
    "4K (2160p)": (
        "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
    ),
    "1080p Full HD": (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    ),
    "720p HD": (
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    ),
    "480p": (
        "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    ),
    "360p": (
        "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
    ),
    "Melhor qualidade disponível": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
}

AUDIO_ONLY_LABEL = "Apenas áudio (MP3)"

UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "account is private",
    "requested content is not available",
    "removed by the uploader",
    "content isn't available",
    "no video formats found",
    "unable to extract",
    "login required",
)


class _QuietLogger:
    """A no-op yt-dlp logger. "quiet"/"no_warnings" suppress most output,
    but not every code path in yt-dlp (or a library it calls into, e.g.
    urllib3) checks those flags before logging - some just call straight
    into whatever `logger` was configured. Passing this instead of leaving
    yt-dlp's default logger installed means those calls always land on a
    method that does nothing, rather than one that ends up writing to
    sys.stdout/sys.stderr - which are None in this app's --windowed
    PyInstaller build (no console attached), so an unguarded write there
    would raise instead of just printing."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _lower_thread_priority():
    """Best-effort, Windows-only: drop this background thread (metadata
    fetch or an actual download) to below-normal OS priority.

    yt-dlp's extraction step for a long video is genuinely CPU-heavy on a
    single thread with very few natural yield points - most visibly the
    JS "nsig" decipherer it runs in pure Python to work around YouTube's
    throttling, which for a long/complex video can burn a solid chunk of
    CPU time with the GIL held almost continuously. At default (equal)
    thread priority, Windows can end up favoring that busy background
    thread over the GUI thread's own event loop for the duration of that
    burst - which shows up exactly as reported: the window looks frozen
    (no repaint), but clicks still land once you force a repaint by
    minimizing/restoring, because Windows was still queuing that input the
    whole time, just not letting the GUI thread get scheduled to process
    it. Running background work at BELOW_NORMAL priority tells the
    scheduler to always prefer the GUI thread when both want the CPU,
    without slowing the download itself in any way that matters (it's
    still network-bound almost all the time). Best-effort and silently
    skipped on any failure or non-Windows platform - never worth breaking
    a download over."""
    if os.name != "nt":
        return
    try:
        import ctypes

        THREAD_PRIORITY_BELOW_NORMAL = -1
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        pass


class CancelledError(Exception):
    """Raised internally when the user cancels an in-progress download."""


class DownloadItem:
    STATUS_WAITING = "Aguardando"
    STATUS_FETCHING = "Buscando informações..."
    STATUS_DOWNLOADING = "Baixando..."
    STATUS_MERGING = "Mesclando áudio/vídeo..."
    STATUS_DONE = "Concluído ✓"
    STATUS_ERROR = "Erro ✗"
    STATUS_UNAVAILABLE = "Indisponível"
    STATUS_CANCELLED = "Cancelado"

    _id_counter = itertools.count(1)

    def __init__(self, url: str, quality: str):
        self.id = next(DownloadItem._id_counter)
        self.url = url
        self.quality = quality
        self.platform = detect_platform(url)
        self.title = url
        self.thumbnail_url = None
        self.thumbnail_bytes = None
        self.status = DownloadItem.STATUS_WAITING
        self.progress = 0.0
        self.speed_text = ""
        self.eta_text = ""
        self.error_message = ""
        self.actual_quality = ""
        self.output_path = ""
        self.cancel_event = threading.Event()


class DownloadManager(QueueManager):
    """[AUDIT] Section 3 - duplicate code: item registration/removal,
    pause/start, clear_completed, shutdown, the dispatcher loop and
    _finish_item are now inherited from QueueManager (queue_manager.py) -
    see that module's docstring for why. Everything below is what's
    genuinely specific to downloading: add_url, cancel_item (extra
    in-flight statuses beyond a single "active" one), retry_item, metadata
    prefetching, and the actual yt-dlp worker."""

    def __init__(self, settings: Settings):
        super().__init__(settings)

    # ------------------------------------------------------------------
    # QueueManager hooks
    # ------------------------------------------------------------------

    def _waiting_status(self):
        return DownloadItem.STATUS_WAITING

    def _done_statuses(self):
        return (DownloadItem.STATUS_DONE, DownloadItem.STATUS_CANCELLED)

    def _start_item(self, item):
        item.status = DownloadItem.STATUS_DOWNLOADING

    def _make_worker_thread(self, item):
        return threading.Thread(target=self._download_worker, args=(item,), daemon=True)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def add_url(self, url: str, quality: str) -> DownloadItem:
        item = DownloadItem(url, quality)
        with self._lock:
            self.items[item.id] = item
            self.order.append(item.id)
        self.item_added.emit(item.id)
        threading.Thread(target=self._fetch_metadata, args=(item,), daemon=True).start()
        return item

    def cancel_item(self, item_id: int):
        item = self.items.get(item_id)
        if not item:
            return
        if item.status in (DownloadItem.STATUS_DOWNLOADING, DownloadItem.STATUS_MERGING,
                            DownloadItem.STATUS_FETCHING):
            item.cancel_event.set()
        else:
            item.status = DownloadItem.STATUS_CANCELLED
            self.item_updated.emit(item.id)

    def retry_item(self, item_id: int):
        item = self.items.get(item_id)
        if not item:
            return
        item.status = DownloadItem.STATUS_WAITING
        item.error_message = ""
        item.progress = 0.0
        item.speed_text = ""
        item.eta_text = ""
        item.cancel_event = threading.Event()
        self.item_updated.emit(item.id)

    # ------------------------------------------------------------------
    # Metadata pre-fetch (title + thumbnail), runs off the GUI thread
    # ------------------------------------------------------------------

    def _fetch_metadata(self, item: DownloadItem):
        _lower_thread_priority()
        item.status = DownloadItem.STATUS_FETCHING
        self.item_updated.emit(item.id)
        try:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "logger": _QuietLogger(),
                "noplaylist": True,
                "skip_download": True,
                "socket_timeout": 15,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(item.url, download=False)
            if info is None:
                raise yt_dlp.utils.DownloadError("Não foi possível obter informações do vídeo")

            item.title = info.get("title") or item.url
            thumb_url = info.get("thumbnail")
            if not thumb_url:
                thumbs = info.get("thumbnails") or []
                if thumbs:
                    thumb_url = thumbs[-1].get("url")
            item.thumbnail_url = thumb_url

            if thumb_url and requests is not None:
                try:
                    resp = requests.get(thumb_url, timeout=10)
                    if resp.status_code == 200:
                        item.thumbnail_bytes = resp.content
                except Exception:
                    pass  # thumbnail is a nice-to-have, never fatal

            item.status = DownloadItem.STATUS_WAITING
        except Exception as exc:
            message = str(exc)
            if _looks_unavailable(message):
                item.status = DownloadItem.STATUS_UNAVAILABLE
            else:
                # Metadata fetch failing doesn't necessarily mean the
                # download will fail (e.g. flaky network) - still queue it
                # and let the real download attempt surface the error.
                item.status = DownloadItem.STATUS_WAITING
            item.error_message = message
        finally:
            # The user may have clicked "Cancelar" while the metadata fetch
            # (which has no cancellation checks of its own) was still in
            # flight - honor that now instead of silently resuming the item.
            if item.cancel_event.is_set():
                item.status = DownloadItem.STATUS_CANCELLED
            self.item_updated.emit(item.id)

    # ------------------------------------------------------------------
    # Actual download
    # ------------------------------------------------------------------

    def _download_worker(self, item: DownloadItem):
        _lower_thread_priority()
        max_attempts = 3
        last_error = None

        for attempt in range(1, max_attempts + 1):
            if item.cancel_event.is_set():
                break
            try:
                self._run_ytdlp(item)
                last_error = None
                break
            except CancelledError:
                last_error = None
                item.status = DownloadItem.STATUS_CANCELLED
                self.item_updated.emit(item.id)
                self._finish_item(item)
                return
            except yt_dlp.utils.DownloadError as exc:
                message = str(exc)
                last_error = message
                if _looks_unavailable(message):
                    item.status = DownloadItem.STATUS_UNAVAILABLE
                    item.error_message = message
                    self.item_updated.emit(item.id)
                    self._finish_item(item)
                    return
                if "ffmpeg" in message.lower():
                    self.ffmpeg_missing.emit()
                if attempt < max_attempts:
                    time.sleep(1.5 * attempt)
                    continue
            except Exception as exc:  # noqa: BLE001 - surface *everything* to the UI
                last_error = str(exc)
                if attempt < max_attempts:
                    time.sleep(1.5 * attempt)
                    continue

        if last_error:
            item.status = DownloadItem.STATUS_ERROR
            item.error_message = last_error
            self.item_updated.emit(item.id)

        self._finish_item(item)

    def _run_ytdlp(self, item: DownloadItem):
        settings = self.settings
        os.makedirs(settings.output_dir, exist_ok=True)

        ffmpeg_path = find_ffmpeg(settings.ffmpeg_path)
        needs_ffmpeg = settings.use_ffmpeg_merge or item.quality == AUDIO_ONLY_LABEL
        if needs_ffmpeg and not ffmpeg_is_working(ffmpeg_path):
            self.ffmpeg_missing.emit()

        outtmpl = os.path.join(settings.output_dir, "%(title).150s [%(id)s].%(ext)s")

        last_progress_emit = [0.0]

        def progress_hook(d):
            if item.cancel_event.is_set():
                raise KeyboardInterrupt("cancelado pelo usuário")

            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes") or 0
                if total:
                    item.progress = min(99.0, downloaded / total * 100.0)
                speed = d.get("speed")
                eta = d.get("eta")
                item.speed_text = format_bytes_per_sec(speed)
                item.eta_text = format_eta(eta)
                item.status = DownloadItem.STATUS_DOWNLOADING

                now = time.monotonic()
                if now - last_progress_emit[0] > 0.25:
                    last_progress_emit[0] = now
                    self.item_updated.emit(item.id)
            elif d.get("status") == "finished":
                item.status = DownloadItem.STATUS_MERGING
                item.progress = 99.0
                self.item_updated.emit(item.id)

        def postprocessor_hook(d):
            if item.cancel_event.is_set():
                raise KeyboardInterrupt("cancelado pelo usuário")
            if d.get("status") == "finished":
                info = d.get("info_dict") or {}
                filepath = info.get("filepath") or info.get("_filename")
                if filepath:
                    item.output_path = filepath

        ydl_opts = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            "retries": 3,
            "fragment_retries": 3,
            "writethumbnail": settings.save_thumbnail,
            "writeinfojson": settings.save_metadata,
            "socket_timeout": 30,
        }
        if ffmpeg_path:
            ydl_opts["ffmpeg_location"] = ffmpeg_path

        if item.quality == AUDIO_ONLY_LABEL:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            ydl_opts["format"] = FORMAT_MAP.get(item.quality, FORMAT_MAP["Melhor qualidade disponível"])
            if settings.use_ffmpeg_merge:
                ydl_opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(item.url, download=True)
        except KeyboardInterrupt:
            raise CancelledError()

        if item.quality == AUDIO_ONLY_LABEL:
            item.actual_quality = "MP3 192kbps"
        else:
            height = None
            if info:
                height = info.get("height")
                if not height:
                    requested = info.get("requested_downloads") or []
                    if requested:
                        height = requested[0].get("height")
            item.actual_quality = height_to_label(height)

        item.progress = 100.0
        item.status = DownloadItem.STATUS_DONE
        item.error_message = ""
        self.item_updated.emit(item.id)


def _looks_unavailable(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in UNAVAILABLE_MARKERS)
