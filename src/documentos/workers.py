"""
documentos/workers.py

QThread-based background workers for the Documentos tab: one for document
scanning (perspective correction + enhancement), one for batch format
conversion.

Note on threading style: the rest of the app (downloader.py, the top-level
converter.py) uses a persistent QObject "manager" running plain
threading.Thread workers behind a small dispatcher loop, because those
features manage an ongoing, pause/resume-able download or conversion
*queue*. The Documentos tab's scanning and conversion actions are one-shot,
started-by-a-button operations with a simple start -> progress -> finished
lifecycle, so a QThread per action (started fresh each click) is the
simpler, equally thread-safe fit here - QThread's signals are marshaled to
the GUI thread exactly like the QObject signals used elsewhere in the app.
"""

import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from documentos import scanner_engine
from documentos import converter as doc_converter


def bgr_to_qimage(image_bgr: np.ndarray) -> QImage:
    """Convert an OpenCV BGR numpy array into a detached QImage. QImage
    (unlike QPixmap) is safe to construct off the GUI thread, which is why
    ImagePrepWorker below builds one instead of a QPixmap - the caller
    converts it to a QPixmap once back on the GUI thread. Copies the buffer
    explicitly so the QImage doesn't end up depending on the numpy array's
    memory staying alive."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    height, width, channels = rgb.shape
    qimage = QImage(rgb.data, width, height, channels * width, QImage.Format_RGB888)
    return qimage.copy()


class ImagePrepWorker(QThread):
    """[AUDIT] Section 1/2 - HIGH: loads a source photo and finds its
    document corners off the GUI thread. Selecting a full-resolution phone
    photo used to do all of this synchronously in
    ScannerSubTab._load_image() - a QPixmap decode, a second independent
    OpenCV decode of the same file, and a full Canny/contour analysis -
    which visibly froze the frameless window (no repaint) for a noticeable
    moment. This worker does the OpenCV decode once and derives both the
    preview image and the corner detection from that single array, off the
    GUI thread; ScannerSubTab only touches the QImage it hands back."""

    finished_ok = Signal(str, object, object)   # path, QImage preview, corners-or-None
    failed = Signal(str, str)                   # path, error message

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path

    def run(self):
        try:
            image_bgr = scanner_engine.load_image(self.image_path)
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI, never crash silently
            self.failed.emit(self.image_path, str(exc))
            return

        try:
            corners = scanner_engine.detect_document_corners(image_bgr)
        except Exception:
            # [AUDIT] Section 1 - LOW: previously this same broad except
            # around both the decode AND the detection meant a genuine
            # decode failure (Qt could open the file, OpenCV couldn't) was
            # indistinguishable from "no four-sided contour found" - the UI
            # just said borders weren't detected. Decode failures now go
            # through the `failed` signal above with the real error message;
            # this narrower except is only around detect_document_corners,
            # where "no contour found" is an expected, non-fatal outcome.
            corners = None

        qimage = bgr_to_qimage(image_bgr)
        self.finished_ok.emit(self.image_path, qimage, corners)


class ScannerWorker(QThread):
    """Runs the document-scanning pipeline (warp + enhance) off the GUI
    thread - a real photo can take a noticeable fraction of a second to
    process, which would otherwise stall the interface."""

    finished_ok = Signal(object, float)   # processed BGR numpy image, elapsed seconds
    failed = Signal(str)

    def __init__(self, image_path: str, corners, mode: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.corners = corners
        self.mode = mode
        # [AUDIT] Section 1 - HIGH: cooperative cancellation flag for
        # shutdown(). scanner_engine.process_document() is a single tight
        # sequence of OpenCV calls with no yield points of its own, so this
        # can only stop the run before it starts (checked below) - it can't
        # interrupt an enhancement pass already in progress. A run already
        # past this point is handled by ScannerSubTab.shutdown() keeping the
        # worker alive until it actually finishes, instead of destroying a
        # live QThread.
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    def run(self):
        if self.cancel_event.is_set():
            return
        start = time.monotonic()
        try:
            result = scanner_engine.process_document(self.image_path, self.corners, self.mode)
            elapsed = time.monotonic() - start
            self.finished_ok.emit(result, elapsed)
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI, never crash silently
            self.failed.emit(str(exc))


class ConversionWorker(QThread):
    """Batch document conversion. Jobs are identified by a stable integer
    id (not list position) so the UI can delete queued-but-not-yet-reached
    items mid-run without any index-shifting bugs - deleted ids just get
    added to `skip_ids` (a set shared by reference with the UI) and this
    worker skips them when it gets to them."""

    file_started = Signal(int)                # job_id (individual mode only)
    file_finished = Signal(int, bool, str)     # job_id, success, message (individual mode only)
    file_skipped = Signal(int, str)            # job_id, reason - "Não suportado" (individual mode only)
    merge_finished = Signal(bool, str)         # success, output_path or error message (merge mode only)
    all_finished = Signal()
    # Emitted (with Qt.ConnectionType.BlockingQueuedConnection) when
    # merge_to_pdf() running on THIS thread hits an encrypted PDF with no
    # known password - the slot connected to this, on the GUI thread, must
    # store the password in self.password_response before returning. Since
    # the connection is blocking, this thread waits for the user to type
    # the password, but the app window keeps responding normally (only
    # this worker thread would ever be blocked, never the GUI).
    password_requested = Signal(str)

    def __init__(self, jobs, output_dir: str, target_ext: str, merge: bool = False,
                 skip_ids=None, output_name: str = None, passwords: dict = None, parent=None):
        """`jobs` is a list of (job_id, path) tuples, in display order.
        `merge=True` converts every job to PDF and merges them into a
        single output file named `output_name` instead of converting them
        individually. `passwords` is an optional {path: password} map with
        passwords already known upfront - any encrypted PDF not covered by
        it triggers `password_requested` instead."""
        super().__init__(parent)
        self.jobs = jobs
        self.output_dir = output_dir
        self.target_ext = target_ext
        self.merge = merge
        self.skip_ids = skip_ids if skip_ids is not None else set()
        # [AUDIT] Section 1 - MEDIUM: `skip_ids` is a plain set mutated by
        # the GUI thread (ConvertSubTab._on_delete_clicked) while this
        # thread reads it - CPython's GIL happens to make individual
        # add()/`in` calls atomic today, but there was no synchronization
        # contract backing that, so it was implementation-detail-dependent
        # rather than guaranteed. A real lock makes the contract explicit
        # and correct regardless of interpreter internals.
        self._skip_ids_lock = threading.Lock()
        self.output_name = output_name
        self.passwords = passwords or {}
        self.password_response = None
        # [AUDIT] Section 1 - HIGH: cooperative cancellation for shutdown().
        # Checked between files (individual mode) and before the merge
        # starts (merge mode) - merge_to_pdf() itself is one call with no
        # internal cancellation point, so a cancel requested mid-merge can't
        # interrupt it; ConvertSubTab.shutdown() covers that remaining case
        # the same way ScannerSubTab.shutdown() does, by not destroying a
        # still-running worker.
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    def is_skipped(self, job_id: int) -> bool:
        with self._skip_ids_lock:
            return job_id in self.skip_ids

    def add_skip(self, job_id: int) -> None:
        with self._skip_ids_lock:
            self.skip_ids.add(job_id)

    def _ask_password(self, path: str):
        # Runs on THIS thread (the conversion one) - the emit() below only
        # returns after the slot connected on the GUI thread has already
        # run and filled in self.password_response, thanks to the blocking
        # connection.
        self.password_response = None
        self.password_requested.emit(path)
        return self.password_response

    def run(self):
        if self.merge:
            self._run_merge()
        else:
            self._run_individual()

    def _run_individual(self):
        for job_id, path in self.jobs:
            if self.cancel_event.is_set():
                break
            if self.is_skipped(job_id):
                # Item removed from the list by the user while the
                # conversion was running - skip silently, no signal (the
                # corresponding widget has already been removed from the UI).
                continue

            ext, _ = doc_converter.detect_format(path)
            # The dropdown shows any format valid for AT LEAST ONE file in
            # the list, so each individual file still needs to be checked
            # here - ones that don't support the chosen target are marked
            # "Não suportado" and skipped, without counting as an error.
            if not doc_converter.can_convert(ext, self.target_ext):
                self.file_skipped.emit(job_id, "Não suportado")
                continue

            self.file_started.emit(job_id)
            try:
                out_paths = doc_converter.convert_file(path, self.target_ext, self.output_dir)
                message = out_paths[0] if len(out_paths) == 1 else f"{len(out_paths)} arquivos gerados"
                self.file_finished.emit(job_id, True, message)
            except Exception as exc:  # noqa: BLE001 - surface everything to the UI, never crash silently
                self.file_finished.emit(job_id, False, str(exc))
        self.all_finished.emit()

    def _run_merge(self):
        # Merge mode: converts everything to PDF and joins it into a single
        # file, in the order received (which reflects the list's order,
        # including any drag-and-drop reordering).
        if self.cancel_event.is_set():
            self.all_finished.emit()
            return
        with self._skip_ids_lock:
            remaining_paths = [path for job_id, path in self.jobs if job_id not in self.skip_ids]
        try:
            out_path = doc_converter.merge_to_pdf(
                remaining_paths, self.output_dir, self.output_name,
                passwords=self.passwords, password_callback=self._ask_password,
            )
            self.merge_finished.emit(True, out_path)
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI, never crash silently
            self.merge_finished.emit(False, str(exc))
        self.all_finished.emit()
