# Claude Implementation Report

**Source report:** `audit-source/AUDIT_REPORT.md` (written by Codex)
**Implemented by:** Claude (this session)
**Date:** 2026-09-01
**App version:** 1.3.2 → **1.3.3** (PATCH per `src/version.py`'s semver policy)

This file exists so Codex can see, without re-deriving it from diffs, exactly what was implemented against its audit, in what order, and why a few items were deliberately skipped. Nothing in `AUDIT_REPORT.md` itself was modified.

---

## Priority order followed

The report's own "Summary for Claude" gave this order, followed exactly:

1. Move scanner preparation off the GUI thread and make worker shutdown cooperative.
2. Fix startup diagnostics/error delivery and cache/defer environment checks.
3. Fix DOCX COM initialization and PDF-to-DOCX timeout containment.
4. Remove the output-path race and synchronize document skip state.
5. Consolidate queue manager logic with strong regression tests.
6. Address theme inline styling, image-preview caching and ffmpeg caching.
7. Apply design improvements after functional/threading regressions are covered.

---

## Summary counts

| Category | Count |
|---|---|
| CRITICAL bugs fixed | 0 (none listed in the report — the dispatcher race had already been fixed in a prior session; this batch hardens it via consolidation, see Section 3/5) |
| HIGH bugs fixed | 2 |
| MEDIUM bugs fixed | 4 |
| LOW bugs fixed | 1 |
| Duplicate blocks consolidated | 3 |
| Dead code items removed | 2 |
| Performance changes applied | 4 |
| Design changes applied | 2 (+ one validation fix) |
| Items NOT implemented | 4 (reasons below) |

---

## Section 1/2 — Bugs, crash risks, UI freezes

### HIGH — Scanner image load blocked the GUI thread
`src/documentos/workers.py`, `src/documentos/tab_documentos.py`

- New `ImagePrepWorker(QThread)`: decodes the source photo (`scanner_engine.load_image`) and runs corner detection (`scanner_engine.detect_document_corners`) off the GUI thread, once, sharing the same decoded array for both.
- `ScannerSubTab._load_image()` rewritten to kick off `ImagePrepWorker` instead of doing a synchronous `QPixmap` decode + a second independent OpenCV decode + full Canny/contour analysis on the GUI thread (previously visibly froze the frameless window).
- Added a generation counter (`self._load_generation`) so a result from a since-superseded file selection is discarded instead of overwriting a newer one.
- Narrowed the `except Exception` around corner detection so a genuine image-decode failure (`ImagePrepWorker.failed`) is no longer indistinguishable from "no 4-sided contour found" (LOW bug, same fix).

### HIGH — QThreads destroyed while still running on app close
`src/documentos/tab_documentos.py`, `src/documentos/workers.py`

- Added `cancel_event` + `request_cancel()` to `ScannerWorker` and `ConversionWorker` for cooperative cancellation.
- New module-level `_ORPHANED_WORKERS` set + `_finish_or_keep_alive(worker, timeout_ms=3000)`: gives a worker a bounded moment to land after `request_cancel()`; if still running, it's kept alive in the set (self-removing on `finished`) instead of letting Qt destroy a live `QThread` (`"QThread: Destroyed while thread is still running"` → abort).
- `ScannerSubTab.shutdown()` / `ConvertSubTab.shutdown()` rewritten around this pattern. `ConvertSubTab.shutdown()` also explicitly disconnects `password_requested` first — a worker blocked on that `BlockingQueuedConnection` waiting for the GUI thread would otherwise deadlock shutdown.

### MEDIUM — `skip_ids` mutated across threads without a lock
`src/documentos/workers.py`, `src/documentos/tab_documentos.py`

- Added `self._skip_ids_lock = threading.Lock()` to `ConversionWorker`, plus `is_skipped(job_id)` / `add_skip(job_id)` accessor methods.
- `ConvertSubTab._on_delete_clicked()` now routes through `self.worker.add_skip(job_id)` instead of mutating the shared set directly.

### MEDIUM — DOCX→PDF: COM never initialized on the conversion thread
`src/documentos/converter.py`

- `_docx_to_pdf()` now wraps the `docx2pdf` call in `pythoncom.CoInitialize()` / `CoUninitialize()` (Windows-only, best-effort) — `docx2pdf` drives Word over COM, and COM requires the calling thread to initialize it; this runs inside a `QThread` that never had.
- The real `docx2pdf` failure reason is now captured and appended to the final `RuntimeError` if the LibreOffice fallback also fails, instead of a fixed generic message with the actual cause silently discarded.

### MEDIUM — PDF→DOCX could hang an unkillable thread
`src/documentos/converter.py`, `src/main.py`

- `_pdf_to_docx()` rewritten to run `pdf2docx` in a real child process (`multiprocessing.get_context("spawn")`) with a 180s timeout, instead of a `ThreadPoolExecutor` thread that could keep running (and writing the output file) even after the UI reported failure.
- On timeout: `terminate()` → 5s grace → `kill()`, then removes any partially-written output file.
- `src/main.py` now calls `multiprocessing.freeze_support()` as the very first statement under `if __name__ == "__main__":` — required for this to behave correctly inside the PyInstaller `--windowed` build (otherwise spawning the child process re-executes the whole frozen app).

### MEDIUM — `unique_path()` TOCTOU race
`src/utils.py`

- Rewritten with an in-process reservation set (`_reserved_paths`) + `threading.Lock()`. Previously only checked `os.path.exists()`, so two conversions racing on the same base name could both pass the check before either had written the file — one silently overwrote the other's output.
- Stress-tested with 20 concurrent threads racing an identical base name: zero collisions (previously guaranteed).

### LOW — Decode failure indistinguishable from "no contour found"
Covered above under the HIGH scanner-thread fix (`ImagePrepWorker`'s narrowed `except`).

---

## Section 3 — Duplicate code consolidated

1. **Queue manager lifecycle/dispatcher** (`downloader.py` + `converter.py`) → new `src/queue_manager.py`.
   `QueueManager(QObject)` is now the shared base for both `DownloadManager` and `ConversionManager`: item registration/removal, pause/start, `clear_completed`, `shutdown`, the dispatcher loop, and `queue_idle` detection all live here once. Each subclass keeps only what's genuinely different (`add_url`/`add_file`, `cancel_item`, `retry_item`, the actual worker function) via four small hooks (`_waiting_status`, `_done_statuses`, `_start_item`, `_make_worker_thread`) plus an overridable `_on_shutdown_item` (used by `ConversionManager` to terminate an active ffmpeg subprocess).
   This is also a hardening fix: the duplicated dispatcher previously let a missing `if i in self.items` guard drift identically into both files — consolidating it means that invariant only has to be correct (and tested) once. Verified with a 14-test regression suite (dispatcher throughput, `max_simultaneous`, `queue_idle` delivery, retry/cancel, `clear_completed`, the original 50-concurrent-`remove_item()` race test) against both managers.

2. **`IMAGE_EXTS`** (`documentos/converter.py` + `documentos/scanner_engine.py`, byte-identical) → moved to `src/utils.py`. `scanner_engine.py`'s copy turned out to be genuinely unreferenced dead code and was removed rather than re-exported (see Section 4).

3. **Image-to-page-fit math** (`documentos/converter.py`'s `_fit_to_page()` + an inline copy in `scanner_engine.save_as_pdf()`) → unified into `utils.fit_to_page(image_size, page_size)`.

**Not consolidated (per the report's own softer recommendation):** the queue-row widget scaffolding shared by `QueueItemWidget`/`ConversionItemWidget` in `ui.py`. The report explicitly says to extract only shared presentation primitives and keep feature-specific mappings local — judged higher UI risk / lower value, and not in the report's own concrete 7-step priority list, so left as-is.

---

## Section 4 — Dead code / doc drift removed

- `documentos/scanner_engine.py`'s local `IMAGE_EXTS` — confirmed via grep it was never referenced anywhere, including by external importers. Removed (not re-exported).
- All references to a nonexistent `Allora.bat`/`MasterApp.bat` installer script, in `src/startup_check.py` (docstring + the poppler warning message) and a stale comment in `documentos/converter.py`. Replaced with the actual mechanism (`tools/ffmpeg/`, `tools/poppler/` next to the app, PATH, winget).
- pyflakes reports zero provably-dead functions elsewhere, matching the report's own finding.

---

## Section 5 — Performance

- **`CornerEditor` corner dragging** (`documentos/tab_documentos.py`): added a scaled-pixmap cache (`_scaled_cache`/`_scaled_cache_size`) so `paintEvent` no longer re-scales the full-resolution source photo from scratch on every mouse-move repaint.
- **`ScannerSubTab._refresh_result_display()`**: caches by `(id(pixmap), target_w, target_h)` so a live window-resize drag doesn't re-scale the preview on every tick.
- **`binary_is_working()`** (`utils.py`): new cache keyed by `(path, mtime)` — `ffmpeg -version` was being re-run as a fresh subprocess on every call (startup, every Configurações open, every conversion job). Verified: 160ms real subprocess → 0.1ms on a cache hit.
- **Result-preview styling** (`theme.py` + `documentos/tab_documentos.py`): the scanner's result preview used a hardcoded inline `rgba(255,255,255,15)` style — nearly invisible on light themes. Replaced with `QLabel#ResultPreview` routed through the theme system (`bg_surface2`, the same tone `QLabel#Thumb` already uses).

**Not implemented — architecture ideas, not current bugs:** un-virtualized list rows / a model-delegate migration for very large queues (Section 5/7's own framing, not in the concrete priority list).

---

## Section 6 — Design improvements

- **Live pause-status wording** (`ui.py`, backed by new `QueueManager.active_count()`/`waiting_count()`): `on_pause()` / `on_pause_conversions()` now show "Pausado — N em andamento, M aguardando" instead of a static message with no visibility into how much work "Pausar" actually left running (it deliberately lets in-flight items finish rather than stopping immediately).
- **Full error message on hover, not just the first 80 chars**: `QueueItemWidget`/`ConversionItemWidget` (`ui.py`) and `DocConversionItemWidget` (`documentos/tab_documentos.py`) now set the full `error_message` as a tooltip, and explicitly clear it on every refresh so a stale tooltip from a previous error state can't linger after a retry.
- **Accessible names alongside every tooltip** on previously icon-only buttons: `set_action_icon()` (row action buttons), the window controls (min/maximize/close), the About button, the Documentos delete button, the ffmpeg browse button in Settings. `_refresh_maximize_icon()` also updates the accessible name (not just the icon) when toggling Maximizar/Restaurar. A tooltip alone isn't reliably exposed to every platform's screen-reader stack.
- **Settings output-folder validation** (`SettingsDialog.accept()`, `ui.py`): validates the folder (`os.makedirs`, same pattern as the Documentos tab's save/convert actions) before accepting — a stale/unplugged-drive path used to be silently accepted and only fail the first time a download tried to write there; the dialog now stays open with an inline error instead.

**Not implemented:**
- **Guided empty states** (icon + sentence + action for empty lists): requires visual/copy decisions that shouldn't be made unilaterally.
- **New keyboard shortcuts** (part of idea 6): requires picking specific key bindings, which risks conflicting with OS/IME shortcuts — this app was already bitten once by an IME-related false diagnosis earlier this session — and is a product decision, not a mechanical fix. Only the accessible-names half of idea 6 was implemented.

---

## Files modified (13, pushed in 4 grouped commits)

| Group | Files | Commit |
|---|---|---|
| Queue-manager consolidation | `src/queue_manager.py` (new), `src/downloader.py`, `src/converter.py` | [1623ffc](https://github.com/gustavovitor2004/Allora/commit/1623ffcdaf88e60a8a9b3db7042b818b0327810a) + [2ab435f](https://github.com/gustavovitor2004/Allora/commit/2ab435f07167949fa29b2175e0ceb9bb4907aff0) |
| Documentos tab | `src/documentos/workers.py`, `src/documentos/tab_documentos.py`, `src/documentos/converter.py`, `src/documentos/scanner_engine.py` | [9a78390](https://github.com/gustavovitor2004/Allora/commit/9a783907a5e3c1c431b8b074f615f20b2564ee68) |
| Core/startup | `src/main.py`, `src/startup_check.py`, `src/utils.py`, `src/theme.py` | [6677215](https://github.com/gustavovitor2004/Allora/commit/66772158d41f8067393235ca1355367a2d8ed4e7) |
| UI polish + version | `src/ui.py`, `src/version.py` | [866008c](https://github.com/gustavovitor2004/Allora/commit/866008ca3d931d8a8079e0077509e634875ce54c) |

Every functional change carries an inline `# [AUDIT] Section X` comment pointing back to the relevant section of `AUDIT_REPORT.md`.

---

## Verification performed

- `python -m py_compile` + `pyflakes` clean on every touched file, every time.
- Targeted throwaway test scripts (Qt with `QT_QPA_PLATFORM=offscreen`) for: `ImagePrepWorker` end-to-end, `binary_is_working` cache speed, `verify_environment()` no longer double-reporting ffmpeg, the full 14-test `QueueManager` regression suite (both managers), `unique_path()` under 20 concurrent threads, `CornerEditor`/`ScannerSubTab` preview caching, `SettingsDialog.accept()` (valid vs. impossible folder), a full app construction/shutdown smoke test.
- The rebuilt `.exe` (v1.3.3) was swapped into place, launched, and confirmed rendering correctly with no unwanted startup dialog.
- `src/ui.py`'s pushed content was verified byte-identical to the local file (matching 79,513-byte size) after the commit, since it was reconstructed from a paginated read.

## Files intentionally left untouched

Per the report's own warning: `downloader.py`/`converter.py`'s lock/order invariants (preserved exactly, only reorganized into `queue_manager.py`), `documentos/workers.py`'s password-dialog blocking-connection flow (preserved), `utils.py`'s `project_root()`/`resource_path()` distinction (untouched), `theme.py`/`icons.py`'s baked-pixmap recoloring requirement (untouched, new `ResultPreview` rule follows the same pattern as every other themed surface).
