"""
converter.py

Generic local file format converter, powered by ffmpeg/ffprobe (the same
ffmpeg the download side of this app already requires). Mirrors the
architecture of downloader.py: a Qt-signal-emitting manager that runs a
small pool of worker threads, so conversion never blocks the GUI.

Same-category conversions (video->video, audio->audio, image->image) match
"give me other formats of this file". Video->audio is the one deliberate
cross-category exception: extracting just the audio track from a video the
user already has locally is a distinct, common need from the Downloads
tab's "audio only" option, which only applies to a fresh online download.

As in downloader.py, mutating `items`/`order` must always happen under
`_lock` (see remove_item) - the dispatcher thread iterates both.
"""

import itertools
import os
import re
import shutil
import subprocess
import threading
import time

from queue_manager import QueueManager
from settings import Settings
from utils import ffmpeg_is_working, find_ffmpeg, no_window_flags, safe_filename, unique_path

VIDEO_FORMATS = ["mp4", "mkv", "avi", "mov", "webm", "flv"]
AUDIO_FORMATS = ["mp3", "wav", "aac", "flac", "ogg", "m4a", "wma"]
IMAGE_FORMATS = ["png", "jpg", "jpeg", "webp", "bmp", "gif", "tiff"]

CATEGORY_FORMATS = {
    "video": VIDEO_FORMATS,
    "audio": AUDIO_FORMATS,
    "image": IMAGE_FORMATS,
}

CATEGORY_LABELS = {
    "video": "Vídeo",
    "audio": "Áudio",
    "image": "Imagem",
}

EXTENSION_CATEGORY = {}
for _category, _exts in CATEGORY_FORMATS.items():
    for _ext in _exts:
        EXTENSION_CATEGORY[_ext] = _category

VIDEO_CODEC_ARGS = {
    "mp4": ["-c:v", "libx264", "-c:a", "aac", "-b:a", "192k"],
    "mkv": ["-c:v", "libx264", "-c:a", "aac", "-b:a", "192k"],
    "mov": ["-c:v", "libx264", "-c:a", "aac", "-b:a", "192k"],
    "avi": ["-c:v", "mpeg4", "-c:a", "libmp3lame", "-b:a", "192k"],
    "webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"],
    "flv": ["-c:v", "flv", "-c:a", "libmp3lame", "-b:a", "192k"],
}

AUDIO_CODEC_ARGS = {
    "mp3": ["-codec:a", "libmp3lame", "-b:a", "192k"],
    "wav": ["-codec:a", "pcm_s16le"],
    "aac": ["-codec:a", "aac", "-b:a", "192k"],
    "m4a": ["-codec:a", "aac", "-b:a", "192k"],
    "flac": ["-codec:a", "flac"],
    "ogg": ["-codec:a", "libvorbis", "-q:a", "5"],
    "wma": ["-codec:a", "wmav2", "-b:a", "192k"],
}


def detect_format(file_path: str):
    """Return (extension, category). category is None if unsupported."""
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    return ext, EXTENSION_CATEGORY.get(ext)


def available_targets(category, current_ext):
    if not category:
        return []
    targets = [f for f in CATEGORY_FORMATS[category] if f != current_ext]
    if category == "video":
        targets += [f for f in AUDIO_FORMATS if f != current_ext]
    return targets


class _CancelledConversion(Exception):
    pass


class ConversionItem:
    STATUS_WAITING = "Aguardando"
    STATUS_CONVERTING = "Convertendo..."
    STATUS_DONE = "Concluído ✓"
    STATUS_ERROR = "Erro ✗"
    STATUS_UNSUPPORTED = "Formato não suportado"
    STATUS_CANCELLED = "Cancelado"

    _id_counter = itertools.count(1)

    def __init__(self, source_path: str, target_ext: str, category, source_ext: str):
        self.id = next(ConversionItem._id_counter)
        self.source_path = source_path
        self.filename = os.path.basename(source_path)
        self.source_ext = source_ext
        self.category = category
        self.target_ext = target_ext
        self.status = ConversionItem.STATUS_WAITING
        self.progress = 0.0
        self.error_message = ""
        self.output_path = ""
        self.cancel_event = threading.Event()
        self.process = None  # active subprocess.Popen, kept so cancel can terminate it


class ConversionManager(QueueManager):
    """[AUDIT] Section 3 - duplicate code: see QueueManager's docstring
    (queue_manager.py) - item registration/removal, pause/start,
    clear_completed, shutdown, the dispatcher loop and _finish_item are now
    inherited. What's left here is genuinely conversion-specific:
    add_file, set_target_format, cancel_item (also terminates an active
    ffmpeg subprocess), retry_item, and the actual ffmpeg worker."""

    def __init__(self, settings: Settings):
        super().__init__(settings)

    # ------------------------------------------------------------------
    # QueueManager hooks
    # ------------------------------------------------------------------

    def _waiting_status(self):
        return ConversionItem.STATUS_WAITING

    def _done_statuses(self):
        return (ConversionItem.STATUS_DONE, ConversionItem.STATUS_CANCELLED)

    def _start_item(self, item):
        item.status = ConversionItem.STATUS_CONVERTING

    def _make_worker_thread(self, item):
        return threading.Thread(target=self._convert_worker, args=(item,), daemon=True)

    def _on_shutdown_item(self, item):
        # Conversion items (unlike downloads) can have a live ffmpeg
        # subprocess attached - cancel_event alone wouldn't stop it.
        if item.process is not None:
            try:
                item.process.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def add_file(self, path: str) -> ConversionItem:
        ext, category = detect_format(path)
        if category is None:
            item = ConversionItem(path, "", None, ext)
            item.status = ConversionItem.STATUS_UNSUPPORTED
            item.error_message = f"A extensão .{ext or '?'} não é suportada pelo conversor."
        else:
            targets = available_targets(category, ext)
            default_target = targets[0] if targets else ext
            item = ConversionItem(path, default_target, category, ext)

        with self._lock:
            self.items[item.id] = item
            self.order.append(item.id)
        self.item_added.emit(item.id)
        return item

    def set_target_format(self, item_id: int, target_ext: str):
        item = self.items.get(item_id)
        if item and item.status == ConversionItem.STATUS_WAITING:
            item.target_ext = target_ext
            self.item_updated.emit(item.id)

    def cancel_item(self, item_id: int):
        item = self.items.get(item_id)
        if not item:
            return
        if item.status == ConversionItem.STATUS_CONVERTING:
            item.cancel_event.set()
            if item.process is not None:
                try:
                    item.process.terminate()
                except Exception:
                    pass
        else:
            item.status = ConversionItem.STATUS_CANCELLED
            self.item_updated.emit(item.id)

    def retry_item(self, item_id: int):
        item = self.items.get(item_id)
        if not item or item.category is None:
            return
        item.status = ConversionItem.STATUS_WAITING
        item.error_message = ""
        item.progress = 0.0
        item.cancel_event = threading.Event()
        self.item_updated.emit(item.id)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _convert_worker(self, item: ConversionItem):
        try:
            self._run_ffmpeg(item)
            item.status = ConversionItem.STATUS_DONE
            item.progress = 100.0
        except _CancelledConversion:
            item.status = ConversionItem.STATUS_CANCELLED
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI
            item.status = ConversionItem.STATUS_ERROR
            item.error_message = str(exc)
        self.item_updated.emit(item.id)
        self._finish_item(item)

    def _run_ffmpeg(self, item: ConversionItem):
        ffmpeg_path = find_ffmpeg(self.settings.ffmpeg_path)
        if not ffmpeg_is_working(ffmpeg_path):
            self.ffmpeg_missing.emit()
            raise RuntimeError("ffmpeg não encontrado - instale-o para poder converter arquivos.")

        os.makedirs(self.settings.output_dir, exist_ok=True)
        base_name = safe_filename(os.path.splitext(item.filename)[0])
        output_path = unique_path(self.settings.output_dir, base_name, item.target_ext)

        cmd = [ffmpeg_path, "-y", "-i", item.source_path]
        if item.category == "video" and item.target_ext in AUDIO_FORMATS:
            # Audio extraction from a video source: drop the video stream
            # entirely (-vn) rather than transcoding it, and use the same
            # audio codec args as a normal audio->audio conversion.
            cmd += ["-vn"] + AUDIO_CODEC_ARGS.get(item.target_ext, [])
        elif item.category == "video":
            cmd += VIDEO_CODEC_ARGS.get(item.target_ext, [])
        elif item.category == "audio":
            cmd += AUDIO_CODEC_ARGS.get(item.target_ext, [])
        # image category: let ffmpeg infer the right encoder from the extension

        duration = _probe_duration(ffmpeg_path, item.source_path)
        cmd += ["-progress", "pipe:1", "-nostats", "-loglevel", "error", output_path]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
        try:
            for line in process.stdout:
                if item.cancel_event.is_set():
                    process.terminate()
                    cancelled = True
                    break
                match = re.match(r"out_time_(?:ms|us)=(\d+)", line.strip())
                if match and duration:
                    out_seconds = int(match.group(1)) / 1_000_000
                    item.progress = max(0.0, min(99.0, out_seconds / duration * 100.0))
                    now = time.monotonic()
                    if now - last_emit > 0.25:
                        last_emit = now
                        self.item_updated.emit(item.id)
        finally:
            process.wait()
            stderr_thread.join(timeout=2)
            item.process = None

        if cancelled or item.cancel_event.is_set():
            raise _CancelledConversion()

        if process.returncode != 0:
            raise RuntimeError("".join(stderr_lines).strip()[-400:] or "Falha desconhecida do ffmpeg")

        item.output_path = output_path


def _probe_duration(ffmpeg_path: str, file_path: str):
    ffprobe_path = _sibling_ffprobe(ffmpeg_path)
    if not ffprobe_path:
        return None
    try:
        proc = subprocess.run(
            [ffprobe_path, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", file_path],
            capture_output=True,
            timeout=20,
            text=True,
            creationflags=no_window_flags(),
        )
        value = proc.stdout.strip()
        return float(value) if value else None
    except Exception:
        return None


def _sibling_ffprobe(ffmpeg_path: str) -> str:
    if ffmpeg_path:
        directory = os.path.dirname(ffmpeg_path)
        name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe") or ""
