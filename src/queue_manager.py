"""
queue_manager.py

[AUDIT] Section 3 - duplicate code: shared lifecycle base for
DownloadManager (downloader.py) and ConversionManager (converter.py).

Both managers used to independently carry ~90 lines of byte-identical
infrastructure: item registration/removal, pause/start state, locked list
traversal, the dispatcher loop, active-thread bookkeeping, queue-idle
detection and shutdown. That duplication is exactly what let a real
CRITICAL bug hide for a while: an unguarded `self.items[i]` in the
dispatcher's candidate comprehension was missing the same `if i in
self.items` guard in *both* files identically, because both files had to
carry the same logic by hand and it drifted the same way in both places.
Consolidating it here means that invariant - "never mutate `items`/`order`
outside `_lock`, and the dispatcher must always guard against `order`
briefly outliving `items`" - only has to be gotten right, and tested, once.

What stays here: everything that doesn't care whether an "item" is a
video download or a file conversion - registration, removal, pause/start,
`clear_completed`, `shutdown`, and the dispatcher loop that starts queued
items up to `settings.max_simultaneous`.

What stays in each subclass: everything that's genuinely different -
`add_url`/`add_file` (different construction entirely), `cancel_item`
(download has extra in-flight statuses; conversion also terminates an
ffmpeg subprocess), `retry_item` (different fields to reset), and of
course the actual worker functions that do the downloading/converting.
Every public method keeps its exact original name and signature, so
callers (ui.py) needed zero changes.
"""

import threading
import time

from PySide6.QtCore import QObject, Signal


class QueueManager(QObject):
    item_added = Signal(int)
    item_updated = Signal(int)
    item_removed = Signal(int)
    queue_idle = Signal()          # emitted whenever nothing is active/waiting
    ffmpeg_missing = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items: dict = {}
        self.order: list = []
        self._lock = threading.RLock()
        self.active_threads: dict = {}
        self.running = False   # True once the user has pressed "Iniciar tudo"/"Converter tudo"
        self.paused = False
        self._shutdown = False

        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    # ------------------------------------------------------------------
    # Queue management (identical across both managers)
    # ------------------------------------------------------------------

    def get_item(self, item_id: int):
        return self.items.get(item_id)

    # [AUDIT] Section 6 (design) - idea 2: the queue footer's "Pausado"
    # message used to just say pausing stops new items, with no visibility
    # into how many are actually still running vs. queued behind them -
    # surprising, since "Pausar" deliberately lets in-flight work finish
    # rather than stopping it immediately. These back the more specific
    # "Pausado — N em andamento, M aguardando" wording in ui.py.
    def active_count(self) -> int:
        with self._lock:
            return len(self.active_threads)

    def waiting_count(self) -> int:
        with self._lock:
            waiting_status = self._waiting_status()
            return sum(1 for i in self.items if self.items[i].status == waiting_status)

    # Removing an item must go through here, under _lock, rather than the
    # caller (the GUI thread) reaching into self.items/self.order directly
    # - the dispatcher thread iterates both under this same lock, and an
    # inconsistent pair between them (order holding an id items no longer
    # has) is exactly what used to raise KeyError there and kill the
    # dispatcher outright, silently freezing the queue for the rest of the
    # session. Doing the pop+remove pair here, atomically, is what makes
    # that impossible.
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

    def clear_completed(self):
        with self._lock:
            to_remove = [
                i for i in self.order
                if self.items[i].status in self._done_statuses()
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
                self._on_shutdown_item(item)

    # ------------------------------------------------------------------
    # Hooks - implemented by DownloadManager / ConversionManager
    # ------------------------------------------------------------------

    def _waiting_status(self):
        """The item class's STATUS_WAITING constant - the only status the
        dispatcher ever picks up to start."""
        raise NotImplementedError

    def _done_statuses(self):
        """Iterable of statuses clear_completed() treats as removable
        (normally STATUS_DONE and STATUS_CANCELLED)."""
        raise NotImplementedError

    def _start_item(self, item):
        """Called under `_lock`, right before a worker thread is spawned
        for `item` - set item.status to whatever "now active" constant the
        subclass uses (STATUS_DOWNLOADING / STATUS_CONVERTING)."""
        raise NotImplementedError

    def _make_worker_thread(self, item) -> threading.Thread:
        """Return a not-yet-started threading.Thread that will process
        `item`. Its target is responsible for eventually calling
        self._finish_item(item) exactly once, on every exit path."""
        raise NotImplementedError

    def _on_shutdown_item(self, item):
        """Extra per-item cleanup during shutdown(), beyond setting
        cancel_event. Default: nothing. ConversionManager overrides this to
        also terminate an active ffmpeg subprocess."""

    # ------------------------------------------------------------------
    # Dispatcher (identical across both managers)
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
                waiting_status = self._waiting_status()
                # `if i in self.items` guard: self.order can briefly outlive
                # an entry in self.items (see remove_item above) - without
                # this, self.items[i] raises KeyError here and kills this
                # dispatcher thread. This is the exact invariant this module
                # exists to get right in one place instead of two.
                candidates = [
                    self.items[i] for i in self.order
                    if i in self.items
                    and self.items[i].id not in self.active_threads
                    and self.items[i].status == waiting_status
                ]
                to_start = candidates[:free_slots]
                for item in to_start:
                    self._start_item(item)
                    self.active_threads[item.id] = self._make_worker_thread(item)

            for item in to_start:
                self.item_updated.emit(item.id)
                self.active_threads[item.id].start()

            if not to_start:
                with self._lock:
                    any_active = bool(self.active_threads)
                    any_waiting = any(
                        self.items[i].status == waiting_status for i in self.order
                        if i in self.items
                    )
                if not any_active and not any_waiting and self.running:
                    self.queue_idle.emit()

    def _finish_item(self, item):
        with self._lock:
            self.active_threads.pop(item.id, None)
            any_active = bool(self.active_threads)
            any_waiting = any(
                self.items[i].status == self._waiting_status() for i in self.order
                if i in self.items
            )
        if not any_active and not any_waiting:
            self.queue_idle.emit()
