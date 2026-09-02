"""
downloader.py

yt-dlp wrapper, download queue model and background threading.

Design notes
------------
- Each queue item is a `DownloadItem` (plain data holder + a threading.Event
  that signals cancellation, plus a handle on the live yt-dlp process).
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
- yt-dlp is invoked as a SUBPROCESS, not imported as a library. It ships in
  tools/ next to the .exe like ffmpeg and poppler do, so the user can run
  `yt-dlp.exe -U` when a site changes its extractor instead of waiting for a
  new Allora release - see find_yt_dlp() in utils.py. Everything the library
  handed back as Python objects now arrives on stdout: --dump-single-json for
  metadata, --progress-template for progress, --print after_move: for the
  final path and resolved height.
- True mid-stream pause of an active HTTP download is not something yt-dlp
  exposes, so "Pausar" stops the dispatcher from starting new items - any
  downloads already in progress finish naturally. Cancellation kills the
  child process outright, which lands immediately; the old library path could
  only raise KeyboardInterrupt from inside a progress hook and so had to wait
  for the next callback to fire.
- Mutating `items`/`order` must always happen under `_lock` (see
  remove_item) - the dispatcher thread iterates both, and an inconsistent
  pair between them used to kill it with a KeyError.
"""

import itertools
import json
import os
import subprocess
import threading
import time

from queue_manager import QueueManager
from settings import Settings
from utils import (
    detect_platform, ffmpeg_is_working, find_ffmpeg, find_yt_dlp,
    format_bytes_per_sec, format_eta, height_to_label, no_window_flags,
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


# [FIX] _QuietLogger removed: it existed only to hand yt-dlp's *library* API a
# no-op logger, so nothing it printed could reach the sys.stdout/sys.stderr
# that are None in the --windowed build. Running yt-dlp as a subprocess makes
# that structurally impossible instead - the child owns its own pipes and this
# process never shares a stream with it.


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


# yt-dlp is driven as a subprocess (see find_yt_dlp in utils.py for why), so
# everything the library used to hand back through Python objects now has to
# come out of stdout. These prefixes tag the lines we care about, keeping them
# unambiguous against anything else yt-dlp prints.
#
# --progress is NOT optional here: with stdout redirected to a pipe (which it
# always is for us) yt-dlp suppresses progress reporting entirely, and the
# progress bar would silently never move. Verified against the real binary.
_P = "@@P@@"      # download progress
_PP = "@@PP@@"    # postprocessor progress (merge / audio extraction)
_F = "@@F@@"      # final file path
_H = "@@H@@"      # resolved video height

PROGRESS_TEMPLATE = (
    "download:" + _P +
    "%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
)
POSTPROCESS_TEMPLATE = "postprocess:" + _PP + "%(progress.status)s|%(progress.postprocessor)s"

YTDLP_MISSING_MESSAGE = (
    "yt-dlp nao encontrado - ele e necessario para baixar videos. Coloque o "
    "yt-dlp.exe em tools\\yt-dlp\\ ao lado do Allora, ou instale-o e "
    "adicione ao PATH do Windows."
)


def _num(text: str):
    """yt-dlp writes the literal string 'NA' for a field it doesn't have, so a
    plain float() would raise on perfectly normal output."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _last_error_line(stderr: str) -> str:
    """Pick the most informative line out of yt-dlp's stderr. It prefixes real
    failures with 'ERROR:'; if none is present we fall back to the last
    non-empty line so nothing is ever reported as an empty message."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR:")]
    if errors:
        return errors[-1]
    return lines[-1] if lines else ""


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
        # Active yt-dlp subprocess, kept so cancel/shutdown can terminate it -
        # the same field ConversionItem carries for its ffmpeg process.
        self.process = None


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

    def _on_shutdown_item(self, item):
        # Now that yt-dlp runs as a child process, closing the app has to kill
        # it: cancel_event alone only stops OUR loop, and the download would
        # otherwise keep running (and keep writing) after the window is gone.
        # Same override ConversionManager already needed for ffmpeg.
        if item.process is not None:
            try:
                item.process.terminate()
            except Exception:
                pass

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
            # Terminating outright makes the cancel immediate. The event alone
            # is only noticed on the next line of yt-dlp output, which during a
            # slow merge or a stalled connection can be a long wait.
            if item.process is not None:
                try:
                    item.process.terminate()
                except Exception:
                    pass
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
            ytdlp_path = find_yt_dlp(self.settings.ytdlp_path)
            if not ytdlp_path:
                raise RuntimeError(YTDLP_MISSING_MESSAGE)

            # --dump-single-json is the CLI equivalent of the library's
            # extract_info(download=False): same info dict, serialized.
            proc = subprocess.run(
                [ytdlp_path, "--encoding", "utf-8", "--no-warnings", "--no-playlist",
                 "--skip-download", "--dump-single-json", "--socket-timeout", "15", item.url],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=no_window_flags(),
            )
            if proc.returncode != 0 or not (proc.stdout or "").strip():
                raise RuntimeError(
                    _last_error_line(proc.stderr) or "Nao foi possivel obter informacoes do video"
                )
            info = json.loads(proc.stdout)

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
        # [FIX] Tracks a cancel observed at the top of the retry loop (rather
        # than mid-transfer, which raises CancelledError through the progress
        # hook and returns early below). Without this the loop just broke out
        # with last_error still None, so NOTHING set a terminal status and the
        # row stayed on "Baixando..." forever - its cancel button dead, since
        # cancel_item() only re-sets an already-set event for that status.
        cancelled_before_attempt = False

        for attempt in range(1, max_attempts + 1):
            if item.cancel_event.is_set():
                cancelled_before_attempt = True
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
            except Exception as exc:  # noqa: BLE001 - surface *everything* to the UI
                # [FIX] Was two branches, one catching yt_dlp.utils.DownloadError
                # from the library. Driving yt-dlp as a subprocess means every
                # failure arrives the same way - a RuntimeError carrying its
                # stderr - so the unavailable/ffmpeg checks collapse into this
                # single handler instead of being duplicated across two.
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
                    # cancel_event.wait() rather than sleep(): a cancel during
                    # the backoff is noticed at once instead of after up to
                    # three seconds of dead waiting.
                    if item.cancel_event.wait(1.5 * attempt):
                        cancelled_before_attempt = True
                        break
                    continue

        # [FIX] An explicit cancel is reported as Cancelado even when a
        # previous attempt had already failed - the user's action is the more
        # accurate outcome than a transient error we were about to retry.
        # Deliberately checked before last_error, and never on the success
        # path (a cancel landing after the final progress hook still leaves a
        # fully downloaded file, which must stay Concluído).
        if cancelled_before_attempt:
            item.status = DownloadItem.STATUS_CANCELLED
            item.error_message = ""
            self.item_updated.emit(item.id)
        elif last_error:
            item.status = DownloadItem.STATUS_ERROR
            item.error_message = last_error
            self.item_updated.emit(item.id)

        self._finish_item(item)

    def _run_ytdlp(self, item: DownloadItem):
        settings = self.settings
        os.makedirs(settings.output_dir, exist_ok=True)

        ytdlp_path = find_yt_dlp(settings.ytdlp_path)
        if not ytdlp_path:
            raise RuntimeError(YTDLP_MISSING_MESSAGE)

        ffmpeg_path = find_ffmpeg(settings.ffmpeg_path)
        needs_ffmpeg = settings.use_ffmpeg_merge or item.quality == AUDIO_ONLY_LABEL
        if needs_ffmpeg and not ffmpeg_is_working(ffmpeg_path):
            self.ffmpeg_missing.emit()

        outtmpl = os.path.join(settings.output_dir, "%(title).150s [%(id)s].%(ext)s")

        cmd = [
            ytdlp_path,
            # --encoding is what makes the utf-8 decoding below correct. By
            # default yt-dlp writes stdout in the Windows ANSI codepage, so an
            # accented title came back as mojibake and item.output_path pointed
            # at a name that did not match the (correctly named) file actually
            # on disk - "Abrir pasta" would have opened onto nothing. Verified
            # against the real binary: default is cp1252, PYTHONIOENCODING is
            # ignored by the frozen build, this flag works.
            "--encoding", "utf-8",
            "--newline", "--progress", "--no-warnings", "--no-playlist",
            "--retries", "3", "--fragment-retries", "3",
            "--socket-timeout", "30",
            "--progress-template", PROGRESS_TEMPLATE,
            "--progress-template", POSTPROCESS_TEMPLATE,
            "--print", "after_move:" + _F + "%(filepath)s",
            "--print", "after_move:" + _H + "%(height)s",
            "-o", outtmpl,
        ]
        if ffmpeg_path:
            cmd += ["--ffmpeg-location", ffmpeg_path]
        if settings.save_thumbnail:
            cmd.append("--write-thumbnail")
        if settings.save_metadata:
            cmd.append("--write-info-json")

        if item.quality == AUDIO_ONLY_LABEL:
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "192K"]
        else:
            cmd += ["-f", FORMAT_MAP.get(item.quality, FORMAT_MAP["Melhor qualidade disponível"])]
            if settings.use_ffmpeg_merge:
                cmd += ["--merge-output-format", "mp4"]

        cmd.append(item.url)

        # encoding pinned to utf-8 instead of left to the locale: yt-dlp emits
        # UTF-8, but text=True on Windows decodes with the ANSI codepage, which
        # would mangle every accented title and path it prints back to us.
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=no_window_flags(),
        )
        item.process = process

        stderr_lines = []

        def _drain_stderr():
            for line in process.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        last_emit = 0.0
        cancelled = False
        height = None
        try:
            for raw in process.stdout:
                if item.cancel_event.is_set():
                    # A real process kill. The library path could only raise
                    # KeyboardInterrupt from inside a progress hook, so it had
                    # to wait for the next callback before anything happened.
                    process.terminate()
                    cancelled = True
                    break

                line = raw.strip()
                if line.startswith(_P):
                    parts = line[len(_P):].split("|")
                    if len(parts) < 6:
                        continue
                    status = parts[0]
                    if status == "downloading":
                        downloaded = _num(parts[1]) or 0.0
                        total = _num(parts[2]) or _num(parts[3])
                        if total:
                            item.progress = min(99.0, downloaded / total * 100.0)
                        item.speed_text = format_bytes_per_sec(_num(parts[4]))
                        item.eta_text = format_eta(_num(parts[5]))
                        item.status = DownloadItem.STATUS_DOWNLOADING
                        now = time.monotonic()
                        if now - last_emit > 0.25:
                            last_emit = now
                            self.item_updated.emit(item.id)
                    elif status == "finished":
                        item.progress = 99.0
                        self.item_updated.emit(item.id)
                elif line.startswith(_PP):
                    # Merging streams or extracting audio - the phase the UI
                    # labels "Mesclando audio/video...".
                    item.status = DownloadItem.STATUS_MERGING
                    item.progress = 99.0
                    self.item_updated.emit(item.id)
                elif line.startswith(_F):
                    item.output_path = line[len(_F):].strip()
                elif line.startswith(_H):
                    raw_height = line[len(_H):].strip()
                    height = int(raw_height) if raw_height.isdigit() else None
        finally:
            process.wait()
            stderr_thread.join(timeout=2)
            item.process = None

        if cancelled or item.cancel_event.is_set():
            raise CancelledError()

        if process.returncode != 0:
            raise RuntimeError(
                _last_error_line("".join(stderr_lines)) or "Falha desconhecida do yt-dlp"
            )

        if item.quality == AUDIO_ONLY_LABEL:
            item.actual_quality = "MP3 192kbps"
        else:
            item.actual_quality = height_to_label(height)

        item.progress = 100.0
        item.status = DownloadItem.STATUS_DONE
        item.error_message = ""
        self.item_updated.emit(item.id)


def _looks_unavailable(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in UNAVAILABLE_MARKERS)
