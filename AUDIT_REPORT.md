# Allora — Audit & Fix Report

**Performed by:** Claude Code (full cycle: audit + fix)
**Date:** 2026-09-02 · **Version:** 1.3.3 → **1.3.4** (PATCH)
**Scope:** all 16 Python modules, 6.642 lines

> Not to be confused with `audit-source/AUDIT_REPORT.md`, which is Codex's
> read-only report from the previous cycle and was not modified. That cycle's
> implementation is documented in `CLAUDE_IMPLEMENTATION_REPORT.md`; this file
> is an independent audit performed on top of the resulting code.

---

## Codebase Map

| File | Purpose |
|------|---------|
| `src/main.py` | Entry point. QApplication, taskbar icon, `freeze_support()`, deferred startup diagnostics, fatal-error dialog. |
| `src/ui.py` | MainWindow (frameless), sidebar nav, download queue, converter tab, SettingsDialog. Largest module. |
| `src/queue_manager.py` | `QueueManager` base: item registry, pause/start, dispatcher thread, idle detection. |
| `src/downloader.py` | `DownloadManager(QueueManager)` + yt-dlp worker, metadata prefetch, retry/backoff. |
| `src/converter.py` | `ConversionManager(QueueManager)` + ffmpeg worker, format matrix, progress parsing. |
| `src/settings.py` | `config.json`: load, type validation, atomic save, output-dir fallbacks. |
| `src/theme.py` | 12 themes + cached QSS stylesheet generator applied app-wide. |
| `src/icons.py` | Stroke SVG icon set rendered to `QIcon` in the theme color (memoized). |
| `src/utils.py` | ffmpeg/poppler discovery, platform detection, formatting, safe filenames, `unique_path`, `fit_to_page`. |
| `src/startup_check.py` | Dependency verification, returns warnings (never raises). |
| `src/version.py` | `APP_VERSION` + semver policy. |
| `src/documentos/tab_documentos.py` | Documentos tab: draggable corner editor, scanner sub-tab, batch conversion sub-tab. |
| `src/documentos/scanner_engine.py` | Pure OpenCV pipeline (warp + enhance). No Qt. |
| `src/documentos/converter.py` | Local document conversion + PDF merge. |
| `src/documentos/workers.py` | QThreads for the Documentos tab. |
| `src/documentos/__init__.py` | Package docstring. |

---

## Bugs Found and Fixed

### [HIGH] `clear_completed()` breaks the project's own mandatory invariant
**File:** `src/queue_manager.py` line 106
**Trigger:** any state where `order` holds an id `items` no longer has
**Effect:** `KeyError` raised inside a GUI slot. This is the exact failure mode AGENTS.md invariant 1 documents as having historically killed the dispatcher thread and silently frozen the queue for the rest of the session.
**Detail:** every other comprehension in the module that indexes `self.items[i]` while iterating `self.order` carries an `if i in self.items` guard (3 sites). `clear_completed` was the single outlier. Not reachable today because all mutations pair the `items`/`order` writes under `_lock` — but it is one unlocked mutation away from being a live crash, and it is the only place where that guard is missing.
**Fix applied:** added the guard.

### [HIGH] Replacing a running `QThread` reference aborts the process
**File:** `src/documentos/tab_documentos.py` lines 455, 506, 1138
**Trigger:** `self.worker = NewWorker(...)` while the previous worker is still running
**Effect:** the previous worker's Python refcount hits zero → PySide6 destroys a live `QThread` → Qt calls `qFatal("QThread: Destroyed while thread is still running")` → **the whole process dies**.
**Detail:** the same hazard `shutdown()`/`_ORPHANED_WORKERS` was already hardened against, reached from a different direction. Each start path is nominally guarded by disabling its own button, but `ConvertSubTab` makes it genuinely reachable: `all_finished` is emitted as the *last statement* of `run()` and delivered as a queued signal, so a user clicking "Converter" the instant a batch reports done can replace a worker whose thread has not finished unwinding. `ScannerSubTab._load_image` is more striking still — it carries a generation counter whose comment explicitly anticipates "the user selected a different file before this one finished", the very scenario that would crash first.
**Fix applied:** new `_retire_worker()` parks a still-running outgoing worker in `_ORPHANED_WORKERS` (self-removing on `finished`); called at all three replacement sites. `_finish_or_keep_alive()` now delegates to it.

### [HIGH] A cancelled download can hang on "Baixando..." forever
**File:** `src/downloader.py` line 312
**Trigger:** `cancel_event` observed at the top of the retry loop (cancel landing between the dispatcher's `.start()` and the worker's first check, or during a retry backoff)
**Effect:** the loop `break`s with `last_error` still `None`, so **nothing sets a terminal status**. The item keeps the `STATUS_DOWNLOADING` the dispatcher assigned, permanently. Its cancel button is dead too — `cancel_item()` only re-sets an already-set event for that status.
**Fix applied:** track the cancel explicitly (`cancelled_before_attempt`) and set `STATUS_CANCELLED`. Ordered before `last_error` so an explicit cancel wins over a transient error we were about to retry, and deliberately never on the success path (a cancel landing after the final progress hook still leaves a complete file, which must stay Concluído).

### [MEDIUM] `queue_idle` re-emitted every 0.4s forever
**File:** `src/queue_manager.py` lines 189 and 199
**Trigger:** the queue drains while `running` is `True`
**Effect:** `queue_idle` is an *edge* event, but the dispatcher re-evaluated it as a *level* condition on every tick — a cross-thread queued signal plus a GUI-thread slot, 2,5×/second, for the remainder of the session. Also double-announced, since `_finish_item` emits the same edge.
**Fix applied:** `_idle_emitted` latch, cleared whenever work actually starts or the user presses start. Measured: 5 emissions → 1 over 2,2s.

### [MEDIUM] ffmpeg warning shown before the window exists
**File:** `src/ui.py` line 1084 (`MainWindow.__init__`)
**Trigger:** launching without a working ffmpeg
**Effect:** `_check_ffmpeg_on_start()` ran inline in `__init__`, i.e. before `main.py` reached `window.show()`. The user's first sight of the app was a lone modal warning over an empty desktop, and the main window only appeared after dismissing it — `__init__` could not return until then.
**Fix applied:** `QTimer.singleShot(0, ...)`, matching how `main.py` already defers its own startup diagnostics.

### [LOW] Preview cache keyed on a recyclable address
**File:** `src/documentos/tab_documentos.py` line 566
**Trigger:** repeated scans at an unchanged window size
**Effect:** the cache key used `id(pixmap)`. CPython recycles freed addresses, and the pixmap the key describes is exactly the one released when a new scan replaces it — leaving a stale integer a later pixmap could legitimately match and so be served the previous scan's rendering. Current assignment ordering happens to make a collision unreachable; the key is one refactor away from silently showing the wrong image.
**Fix applied:** keyed on `QPixmap.cacheKey()`, Qt's own never-reused identifier.

---

## Duplicate Code Consolidated

### Pause/resume handling
**Was in:** `ui.py` `on_pause()` and `on_pause_conversions()` — the same twelve lines twice, differing only in manager/button/label and the resumed wording.
**Fix:** extracted `MainWindow._toggle_pause(manager, btn, label, running_text)`. Both are now 3-line delegations.
**Bonus:** resuming was done by writing `manager.paused = False` straight into the manager — the only place outside `QueueManager` reaching into its state, exactly the habit invariant 1 exists to stop. Added `QueueManager.resume()` to pair with `pause()`.

### BGR→QPixmap conversion
**Was in:** `tab_documentos._bgr_to_qpixmap()` — a byte-for-byte copy of `workers.bgr_to_qimage()` with a `QPixmap.fromImage()` bolted on.
**Fix:** delegates to `bgr_to_qimage()`. The detached-copy rule now lives in one place. Dropped the then-unused `cv2` and `QImage` imports.

### Output-directory creation with fallback
**Was in:** `settings.load_settings()` — the same try-makedirs / reset-to-default / makedirs-again block three times.
**Fix:** extracted `_ensure_dir(path, fallback)`. Also hardened: the fallback `makedirs` was itself unguarded, so an unwritable home directory turned a merely-bad saved setting into an `OSError` escaping `load_settings()` — which `main.py` can only answer with a fatal "erro ao iniciar" dialog. The app now starts; each feature already surfaces its own write error.

---

## Dead Code Removed

- `full_image_corners()` in `src/documentos/scanner_engine.py` — defined, never called anywhere. Its stated job is actually done by `CornerEditor.set_image()`, which builds the same four corners when it receives `corners=None`.
- Five unused entries in `icons.PATHS`: `moon`, `sun`, `check-circle`, `alert-circle`, `palette` — confirmed unreferenced app-wide.
- `UrlInput._detach_windows_ime()` in `src/ui.py`, plus the `WA_InputMethodEnabled`/`WA_NativeWindow` calls that only fed it and the `focusInEvent`/`showEvent` overrides whose entire body was re-calling it (~60 lines). Written to kill a "ghost icon" on the theory it was the Windows IME/touch-keyboard flyout; that diagnosis was wrong (the glyphs were Qt's own scrollbar arrows, fixed by `ScrollBarAlwaysOff`, which remains). It never solved anything while still forcing a native HWND and disabling CJK composition in the one field the user types into.
- `cv2` and `QImage` imports in `tab_documentos.py` — unused after the `_bgr_to_qpixmap` consolidation.

---

## Performance Fixes Applied

- `queue_idle` no longer fires a cross-thread signal every 400 ms for the whole session once a queue drains (see MEDIUM bug above) — the only measurable ongoing cost found.
- Everything else in this category was already addressed in the 1.3.3 batch (icon memoization, stylesheet `lru_cache`, `ffmpeg -version` caching, preview-scaling caches, single image decode). Re-verified as still in place.

---

## Improvement Ideas

### Quick wins (< 2h)
- **Live ffmpeg status while typing** — `SettingsDialog._refresh_ffmpeg_status()` only runs on open and on Browse, so typing a valid path still reads "ffmpeg não encontrado" until reopening. Connect `ffmpeg_path_edit.editingFinished`. *(ui.py)*
- **Cancel during retry backoff** — `_download_worker`'s `time.sleep(1.5 * attempt)` ignores `cancel_event`, so a cancel waits up to 3s. Use `cancel_event.wait(delay)` instead. *(downloader.py)*
- **Reset during image prep** — `ScannerSubTab.on_reset()` doesn't bump `_load_generation`, so an in-flight prep result lands after the reset and undoes it. One line. *(tab_documentos.py)*
- **Named dispatcher constants** — `0.4` tick and `0.25` progress throttle are bare magic numbers in hot paths. *(queue_manager.py, downloader.py, converter.py)*

### Medium improvements (half day)
- **Bounded metadata prefetch** — the one real remaining architecture gap: `_fetch_metadata` spawns one unbounded thread per URL, outside `active_threads` and ignoring `max_simultaneous`. 50 pasted links = 50 concurrent `extract_info` calls. Wants a small fixed pool. *(downloader.py)*
- **Unify the three status vocabularies** — PT strings on `DownloadItem`/`ConversionItem` vs EN keys in `DocConversionItemWidget._CARD_STATUS`. An enum plus a display-mapping would remove a whole class of mismatch. *(downloader.py, converter.py, tab_documentos.py)*
- **Per-page conversion progress** — `convert_file(progress_cb=...)` already exists and is wired through to `_pdf_to_images`/`_pdf_to_txt`; nothing passes it. Wiring it to the row's progress bar makes long PDF jobs legible. *(workers.py, tab_documentos.py)*

### Larger ideas (worth planning)
- **Persistent download history** — queue state dies with the process. A small SQLite/JSON log would enable re-download, "open containing folder" after restart, and stats. *(new module + ui.py)*
- **Drag-and-drop URL onto the window** — the converter tab already has a `DropZone`; the downloads tab accepts text only via paste. *(ui.py)*
- **Keyboard shortcuts** — deliberately skipped in the previous cycle for lack of a binding decision. Worth doing as a designed set (Ctrl+V add, Space pause, Del remove) rather than ad hoc. *(ui.py)*
- **System tray + completion notification** — long queues are currently invisible unless the window is focused.
- **Auto-update check** — there is a GitHub release feed and `APP_VERSION` already; a background check could surface "1.3.5 disponível".

---

## Summary

| Category | Found | Fixed | Skipped |
|----------|-------|-------|---------|
| CRITICAL bugs | 0 | 0 | 0 |
| HIGH bugs | 3 | 3 | 0 |
| MEDIUM bugs | 2 | 2 | 0 |
| LOW bugs | 1 | 1 | 0 |
| Duplicate blocks | 3 | 3 | 0 |
| Dead code items | 5 | 4 | 1 |
| Performance issues | 2 | 1 | 1 |

### Items skipped (with reason)
- **`convert_file(progress_cb=...)` dead extension point** — technically unreachable code, but a deliberate, zero-cost hook already threaded through two functions. Removing it would delete the cheapest path to per-page progress. Documented in AGENTS.md instead.
- **Unbounded `_fetch_metadata` threads** — a real resource gap, but the fix is a thread-pool redesign of the prefetch path, not a contained correction. Outside what an automatic pass should change; listed as the top medium improvement.
- **Possible `QListWidget` item-widget leak on `takeItem()`** — Qt's ownership of widgets set via `setItemWidget` is version-dependent and I could not confirm a leak. Calling `deleteLater()` on a widget Qt also destroys is worse than the suspected problem, so this needs measurement first, not a speculative fix.
- **Emoji in the light/dark toggle** (`☀`/`🌙` as button text, contradicting the whole reason `icons.py` exists) — replacing it with `make_icon()` is a visual design change, out of scope for a fix pass.

### Files modified
- `src/queue_manager.py` — 4 fixes (invariant guard, idle latch ×2, `resume()`)
- `src/documentos/tab_documentos.py` — 6 fixes (`_retire_worker` + 3 call sites, conversion dedup, imports, `cacheKey`)
- `src/ui.py` — 4 fixes (pause dedup + `resume()`, deferred ffmpeg check, IME removal)
- `src/downloader.py` — 1 fix (terminal status on cancel)
- `src/settings.py` — 1 fix (dir dedup + fallback hardening)
- `src/documentos/scanner_engine.py` — 1 removal
- `src/icons.py` — 1 removal (5 entries)
- `src/version.py` — 1.3.3 → 1.3.4
- `AGENTS.md` — stale "problemas em aberto" list (10 of 11 entries already fixed) rewritten; 4 new pitfalls documented

### Verification
`py_compile` + `pyflakes` clean across all 16 modules. 11 targeted regression tests written and passing, one per fix: invariant guard under a forced `order`/`items` mismatch; `queue_idle` emission count over 5 dispatcher ticks; `pause()`/`resume()`; cancelled-item terminal status; live-`QThread` retirement *and* self-removal; `_bgr_to_qpixmap` dimensions and channel order; `_ensure_dir` both paths; full app construct/shutdown; confirmation the IME plumbing is gone.
