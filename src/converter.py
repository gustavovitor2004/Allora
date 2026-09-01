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

from PySide6.QtCore import QObject, Signal

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


class ConversionManager(QObject):
    item_added = Signal(int)
    item_updated = Signal(int)
    item_removed = Signal(int)
    queue_idle = Signal()
    ffmpeg_missing = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.items: dict[int, ConversionItem] = {}
        self.order: list[int] = []
        self._lock = threading.RLock()
        self.active_threads: dict[int, threading.Thread] = {}
        self.running = False
        self.paused = False
        self._shutdown = False

        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

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

    def get_item(self, item_id: int):
        return self.items.get(item_id)

    # [FIX] See DownloadManager.remove_item - identical race, identical fix:
    # the UI used to pop from items/order directly, unlocked, from the GUI
    # thread while _dispatch_loop iterated them, which could raise KeyError
    # in the dispatcher and kill it.
    def remove_item(self, item_id: int) -> None:
        with self._lock:
            existed = self.items.pop(item_id, None) is not None
            if item_id in self.order:
                self.order.remove(item_id)
        if existed:
            self.item_removed.emit(item_id)

    def start_all(self):
        self.running = True
        self.paused = False

    def pause(self):
        self.paused = True

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

    def clear_completed(self):
        with self._lock:
            to_remove = [
                i for i in self.order
                if self.items[i].status in (ConversionItem.STATUS_DONE, ConversionItem.STATUS_CANCELLED)
            ]
            for i in to_remove:
                del self.items[i]
                self.order.remove(i)
        for i in to_remove:
            self.item_removed.emit(i)

    def shutdown(self):
        self._shutdown = True
        with self._lock:
            for item in self.items.values():
                item.cancel_event.set()
                if item.process is not None:
                    try:
                        item.process.terminate()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _dispatch_loop(self):
        while not self._shutdown:
            time.sleep(0.4)
            if not self.running or self.paused:
                continue
            with self._lock:
                free_slots = max(0, self.settings.max_simultaneous - len(self.active_threads))
                if free_slots <= 0:
                    continue
                # [FIX] `if i in self.items` guard added - see the identical
                # fix in downloader.py's dispatcher.
                candidates = [
                    self.items[i] for i in self.order
                    if i in self.items
                    and self.items[i].id not in self.active_threads
                    and self.items[i].status == ConversionItem.STATUS_WAITING
                ]
                to_start = candidates[:free_slots]
                for item in to_start:
                    item.status = ConversionItem.STATUS_CONVERTING
                    t = threading.Thread(target=self._convert_worker, args=(item,), daemon=True)
                    self.active_threads[item.id] = t

            for item in to_start:
                self.item_updated.emit(item.id)
                self.active_threads[item.id].start()

            if not to_start:
                with self._lock:
                    any_active = bool(self.active_threads)
                    # [FIX] Same missing `if i in self.items` guard as above.
                    any_waiting = any(
                        self.items[i].status == ConversionItem.STATUS_WAITING for i in self.order
                        if i in self.items
                    )
                if not any_active and not any_waiting and self.running:
                    self.queue_idle.emit()

    def _finish_item(self, item: ConversionItem):
        with self._lock:
            self.active_threads.pop(item.id, None)
            any_active = bool(self.active_threads)
            any_waiting = any(
                self.items[i].status == ConversionItem.STATUS_WAITING for i in self.order
                if i in self.items
            )
        if not any_active and not any_waiting:
            self.queue_idle.emit()

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
