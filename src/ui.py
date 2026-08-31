"""
ui.py

All GUI components, built with PySide6:
- MainWindow: header, tab bar, URL input, quality/folder controls, download
  queue list, queue controls.
- QueueItemWidget: one row in the download queue (thumbnail, title,
  platform/quality, progress bar, action button).
- SettingsDialog: everything in settings.Settings, editable and persisted.

The GUI never touches yt-dlp directly - all of that goes through
`downloader.DownloadManager`, which emits Qt signals that this module
listens to. Because DownloadManager's worker threads emit those signals,
and Qt auto-queues cross-thread signal/slot connections, none of the code
below has to worry about thread safety.

Theming is entirely centralized in `theme.py` - this module never sets an
inline stylesheet for color/appearance purposes. Every widget that needs to
reflect a changing state (a queue row's status, say) does it by setting a
Qt dynamic property (`status`/`variant`) and calling `theme.repolish()`,
which lets the one stylesheet installed by `theme.apply_theme()` pick up
the change. Icons are drawn on demand by `icons.py` instead of using emoji,
since the app's own colors need to reach into them (a baked pixmap can't be
recolored by a stylesheet) - see `_refresh_icon_theme()` for how every
icon-bearing widget gets rebuilt when the user switches theme.
"""

import os

from PySide6.QtCore import Qt, QSize, QPoint
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QComboBox, QListWidget,
    QListWidgetItem, QProgressBar, QFileDialog, QDialog, QSpinBox, QCheckBox,
    QMessageBox, QFrame, QStackedWidget, QTabWidget,
)

from downloader import DownloadItem, DownloadManager
from converter import (
    ConversionItem, ConversionManager, CATEGORY_LABELS, available_targets,
)
from documentos.tab_documentos import DocumentosTab
from icons import make_icon
from settings import Settings, QUALITY_CHOICES, save_settings
from theme import (
    apply_theme as set_app_theme, repolish, theme_colors,
    THEME_VARIANTS, base_theme_names, theme_key_to_base_and_mode, resolve_theme_variant,
)
from utils import split_urls, platform_icon, find_ffmpeg, ffmpeg_is_working, resource_path
from version import APP_VERSION

THUMB_SIZE = QSize(104, 60)
ROW_ICON_SIZE = 32
CATEGORY_ICON_NAMES = {"video": "film", "audio": "music", "image": "image"}


# ---------------------------------------------------------------------------
# Small shared row-building helpers (consolidates boilerplate that used to
# be duplicated verbatim between QueueItemWidget and ConversionItemWidget)
# ---------------------------------------------------------------------------

def make_progress_bar() -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    return bar


def make_pill(text: str = "") -> QLabel:
    """A small rounded status badge - see QLabel#Pill in theme.py. Call
    set_pill() to change its text/color together."""
    pill = QLabel(text)
    pill.setObjectName("Pill")
    pill.setProperty("variant", "neutral")
    return pill


def set_pill(pill: QLabel, text: str, variant: str) -> None:
    """variant is one of 'accent' (in progress / error), 'success'
    (done), 'neutral' (waiting/cancelled) - matches theme.py's Pill
    variants."""
    pill.setText(text)
    pill.setProperty("variant", variant)
    repolish(pill)


def make_row_action_button(on_click, theme_name: str = "classic_dark") -> QPushButton:
    """Small icon-only button used for a queue row's context action
    (cancel while active, retry on error, remove when done). The icon
    itself is swapped per-state by set_action_icon()."""
    btn = QPushButton()
    btn.setObjectName("IconGhost")
    btn.setFixedSize(ROW_ICON_SIZE, ROW_ICON_SIZE)
    btn.setIconSize(QSize(15, 15))
    set_action_icon(btn, "x", theme_name)
    btn.clicked.connect(on_click)
    return btn


def set_action_icon(btn: QPushButton, name: str, theme_name: str = "classic_dark") -> None:
    color = theme_colors(theme_name)["text_secondary"]
    btn.setIcon(make_icon(name, color, size=15))
    btn.setToolTip(
        {"x": "Cancelar", "retry": "Tentar novamente", "trash": "Remover", "copy": "Copiar link"}.get(name, "")
    )


class Header(QFrame):
    """The app's header bar, doubling as a hand-drawn title bar for the
    frameless MainWindow (see MainWindow's window-flag setup in ui.py).
    Once the OS stops drawing its own caption strip, the app has to supply
    the two things that strip used to give for free: dragging it moves the
    window (via Qt's startSystemMove(), so Aero Snap still works), and
    double-clicking it toggles maximize/restore. Clicks on child widgets
    (the Tema/Configurações/window-control buttons) never reach this
    handler in the first place - Qt only bubbles a mouse press up to the
    parent when the widget under the cursor doesn't handle it itself,
    which a QPushButton always does, but the plain QLabels here don't."""

    def __init__(self, on_double_click, parent=None):
        super().__init__(parent)
        self._on_double_click = on_double_click

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_double_click()
        super().mouseDoubleClickEvent(event)


class NavItem(QFrame):
    """One row in the sidebar (Downloads / Converter Arquivos / Documentos).
    A plain QFrame rather than a QPushButton because it needs three
    independent children (icon, label, optional count badge) laid out in a
    row - Qt buttons can't host a child layout. Selection state lives in
    the "active" dynamic property (same repolish() dance as everything
    else theme.py drives), and MainWindow is responsible for making sure
    only one NavItem is active at a time."""

    def __init__(self, icon_name: str, label: str, on_click, theme_name: str = "classic_dark", parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.theme_name = theme_name
        self._on_click = on_click
        self.setObjectName("NavItem")
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        layout.addWidget(self.icon_label)

        self.text_label = QLabel(label)
        self.text_label.setObjectName("NavItemText")
        layout.addWidget(self.text_label, stretch=1)

        self.badge = make_pill("")
        self.badge.setVisible(False)
        layout.addWidget(self.badge)

        self._refresh_icon()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(event)

    def set_active(self, active: bool):
        self.setProperty("active", active)
        repolish(self)
        self.text_label.setProperty("active", active)
        repolish(self.text_label)
        self._refresh_icon()

    def set_count(self, count: int):
        if count > 0:
            set_pill(self.badge, str(count), "neutral")
            self.badge.setVisible(True)
        else:
            self.badge.setVisible(False)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self._refresh_icon()

    def _refresh_icon(self):
        colors = theme_colors(self.theme_name)
        active = bool(self.property("active"))
        color = colors["accent"] if active else colors["text_secondary"]
        self.icon_label.setPixmap(make_icon(self.icon_name, color, size=17).pixmap(17, 17))


class QueueItemWidget(QFrame):
    """One row inside the download queue list."""

    def __init__(self, item_id: int, manager: DownloadManager, theme_name: str = "classic_dark", parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.manager = manager
        self.theme_name = theme_name
        self.setObjectName("Card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self.thumb_label = QLabel()
        self.thumb_label.setObjectName("Thumb")
        self.thumb_label.setFixedSize(THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.thumb_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self.title_label = QLabel("...")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.title_label.setWordWrap(True)
        header_row.addWidget(self.title_label, stretch=1)
        self.pill = make_pill(DownloadItem.STATUS_WAITING)
        header_row.addWidget(self.pill, alignment=Qt.AlignTop)
        text_col.addLayout(header_row)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Dim")
        text_col.addWidget(self.meta_label)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(10)
        self.progress_bar = make_progress_bar()
        detail_row.addWidget(self.progress_bar, stretch=1)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("Dim")
        detail_row.addWidget(self.detail_label)
        text_col.addLayout(detail_row)

        top_row.addLayout(text_col, stretch=1)

        self.action_btn = make_row_action_button(self._on_action_clicked, theme_name)
        top_row.addWidget(self.action_btn, alignment=Qt.AlignTop)

        # Only shown on Erro/Indisponível (see refresh()) - error is the one
        # state where a single action button isn't enough: the user may want
        # to copy the link before it's lost (no way back to it once removed,
        # since the source tab it came from is long gone) and/or remove the
        # item, on top of the existing retry.
        self.copy_btn = make_row_action_button(self._on_copy_link_clicked, theme_name)
        set_action_icon(self.copy_btn, "copy", theme_name)
        self.copy_btn.setVisible(False)
        top_row.addWidget(self.copy_btn, alignment=Qt.AlignTop)

        self.remove_btn = make_row_action_button(self._on_remove_clicked, theme_name)
        set_action_icon(self.remove_btn, "trash", theme_name)
        self.remove_btn.setVisible(False)
        top_row.addWidget(self.remove_btn, alignment=Qt.AlignTop)

        outer.addLayout(top_row)
        self._thumb_loaded = False
        self._set_thumb_icon("film")

    def _set_thumb_icon(self, name: str):
        colors = theme_colors(self.theme_name)
        icon = make_icon(name, colors["text_tertiary"], size=20)
        self.thumb_label.setPixmap(icon.pixmap(20, 20))

    def refresh(self, item: DownloadItem):
        self.title_label.setText(item.title)
        icon = platform_icon(item.platform)
        quality_text = item.actual_quality or item.quality
        self.meta_label.setText(f"{icon} {item.platform}   ·   {quality_text}")

        if item.thumbnail_bytes and not self._thumb_loaded:
            pixmap = QPixmap()
            if pixmap.loadFromData(item.thumbnail_bytes):
                scaled = pixmap.scaled(THUMB_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.thumb_label.setPixmap(scaled)
                self._thumb_loaded = True

        self.progress_bar.setValue(int(item.progress))

        # Reset per-status extras before the branches below re-show what's
        # relevant - only the Erro/Indisponível branch turns these back on.
        self.copy_btn.setVisible(False)
        self.remove_btn.setVisible(False)

        status = item.status
        if status == DownloadItem.STATUS_DOWNLOADING:
            detail = f"{int(item.progress)}%"
            if item.speed_text:
                detail += f" · {item.speed_text}"
            if item.eta_text:
                detail += f" · {item.eta_text}"
            self.detail_label.setText(detail)
            set_pill(self.pill, "Baixando", "accent")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(True)
        elif status == DownloadItem.STATUS_MERGING:
            self.detail_label.setText("Mesclando áudio/vídeo...")
            set_pill(self.pill, "Mesclando", "accent")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(True)
        elif status == DownloadItem.STATUS_DONE:
            self.detail_label.setText("Salvo")
            set_pill(self.pill, "Concluído", "success")
            set_action_icon(self.action_btn, "trash", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        elif status in (DownloadItem.STATUS_ERROR, DownloadItem.STATUS_UNAVAILABLE):
            text = "Vídeo indisponível" if status == DownloadItem.STATUS_UNAVAILABLE else "Erro"
            if item.error_message:
                text += f" — {item.error_message[:80]}"
            self.detail_label.setText(text)
            set_pill(self.pill, "Erro", "accent")
            set_action_icon(self.action_btn, "retry", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.copy_btn.setVisible(True)
            self.remove_btn.setVisible(True)
        elif status == DownloadItem.STATUS_CANCELLED:
            self.detail_label.setText("Cancelado")
            set_pill(self.pill, "Cancelado", "neutral")
            set_action_icon(self.action_btn, "trash", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        elif status == DownloadItem.STATUS_FETCHING:
            self.detail_label.setText("Buscando informações...")
            set_pill(self.pill, "Buscando", "accent")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        else:  # WAITING
            self.detail_label.setText("")
            set_pill(self.pill, "Na fila", "neutral")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

        is_error = status in (DownloadItem.STATUS_ERROR, DownloadItem.STATUS_UNAVAILABLE)
        self.detail_label.setObjectName("ErrorLabel" if is_error else "Dim")
        repolish(self.detail_label)

    def _on_action_clicked(self):
        item = self.manager.get_item(self.item_id)
        if item is None:
            return
        if item.status in (DownloadItem.STATUS_ERROR, DownloadItem.STATUS_UNAVAILABLE):
            self.manager.retry_item(self.item_id)
        elif item.status in (DownloadItem.STATUS_DONE, DownloadItem.STATUS_CANCELLED):
            self._remove_self()
        else:
            self.manager.cancel_item(self.item_id)

    def _on_copy_link_clicked(self):
        item = self.manager.get_item(self.item_id)
        if item is not None and item.url:
            QApplication.clipboard().setText(item.url)

    def _on_remove_clicked(self):
        self._remove_self()

    def _remove_self(self):
        self.manager.items.pop(self.item_id, None)
        if self.item_id in self.manager.order:
            self.manager.order.remove(self.item_id)
        self.manager.item_removed.emit(self.item_id)


class DropZone(QFrame):
    """A drag-and-drop target for the converter tab. Accepts one or more
    local files dropped from Windows Explorer and forwards their paths."""

    def __init__(self, on_files_dropped, theme_name: str = "classic_dark", parent=None):
        super().__init__(parent)
        self._on_files_dropped = on_files_dropped
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        layout.addWidget(self.icon_label)

        label = QLabel("Arraste arquivos aqui, ou clique em \"Selecionar arquivo(s)\"")
        label.setObjectName("Dim")
        layout.addWidget(label)

        self.set_theme(theme_name)

    def set_theme(self, theme_name: str):
        colors = theme_colors(theme_name)
        icon = make_icon("upload-cloud", colors["text_secondary"], size=18)
        self.icon_label.setPixmap(icon.pixmap(18, 18))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._on_files_dropped(paths)


class ConversionItemWidget(QFrame):
    """One row inside the file-conversion queue list."""

    def __init__(self, item_id: int, manager: ConversionManager, theme_name: str = "classic_dark", parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.manager = manager
        self.theme_name = theme_name
        self.setObjectName("Card")

        item = manager.get_item(item_id)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("Thumb")
        self.icon_label.setFixedSize(44, 44)
        self.icon_label.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.icon_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self.title_label = QLabel(item.filename)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.title_label.setWordWrap(True)
        header_row.addWidget(self.title_label, stretch=1)
        self.pill = make_pill(ConversionItem.STATUS_WAITING)
        header_row.addWidget(self.pill, alignment=Qt.AlignTop)
        text_col.addLayout(header_row)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Dim")
        text_col.addWidget(self.meta_label)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(10)
        self.progress_bar = make_progress_bar()
        detail_row.addWidget(self.progress_bar, stretch=1)
        text_col.addLayout(detail_row)

        top_row.addLayout(text_col, stretch=1)

        self.format_combo = QComboBox()
        self.format_combo.setFixedWidth(100)
        if item.category:
            self.format_combo.addItems([f.upper() for f in available_targets(item.category, item.source_ext)])
            idx = self.format_combo.findText(item.target_ext.upper())
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
        else:
            self.format_combo.addItem("--")
            self.format_combo.setEnabled(False)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        top_row.addWidget(self.format_combo, alignment=Qt.AlignTop)

        self.action_btn = make_row_action_button(self._on_action_clicked, theme_name)
        top_row.addWidget(self.action_btn, alignment=Qt.AlignTop)

        outer.addLayout(top_row)

        self._set_category_icon(item.category)
        self.refresh(item)

    def _set_category_icon(self, category: str):
        colors = theme_colors(self.theme_name)
        icon_name = CATEGORY_ICON_NAMES.get(category, "document")
        icon = make_icon(icon_name, colors["text_tertiary"], size=18)
        self.icon_label.setPixmap(icon.pixmap(18, 18))

    def _on_format_changed(self, text):
        if not text or text == "--":
            return
        self.manager.set_target_format(self.item_id, text.lower())

    def refresh(self, item: ConversionItem):
        category_label = CATEGORY_LABELS.get(item.category, "")
        if item.category:
            self.meta_label.setText(f"{category_label}   ·   .{item.source_ext} → .{item.target_ext}")
        else:
            self.meta_label.setText(f".{item.source_ext}")

        self.progress_bar.setValue(int(item.progress))
        self.format_combo.setEnabled(item.status == ConversionItem.STATUS_WAITING and item.category is not None)

        status = item.status
        if status == ConversionItem.STATUS_CONVERTING:
            set_pill(self.pill, f"Convertendo {int(item.progress)}%", "accent")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(True)
        elif status == ConversionItem.STATUS_DONE:
            set_pill(self.pill, "Concluído", "success")
            set_action_icon(self.action_btn, "trash", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        elif status in (ConversionItem.STATUS_ERROR, ConversionItem.STATUS_UNSUPPORTED):
            label = "Erro" if status == ConversionItem.STATUS_ERROR else "Não suportado"
            if item.error_message:
                label += f" — {item.error_message[:80]}"
            set_pill(self.pill, label, "accent" if status == ConversionItem.STATUS_ERROR else "neutral")
            set_action_icon(
                self.action_btn,
                "retry" if status == ConversionItem.STATUS_ERROR else "trash",
                self.theme_name,
            )
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        elif status == ConversionItem.STATUS_CANCELLED:
            set_pill(self.pill, "Cancelado", "neutral")
            set_action_icon(self.action_btn, "trash", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
        else:  # WAITING
            set_pill(self.pill, "Aguardando", "neutral")
            set_action_icon(self.action_btn, "x", self.theme_name)
            self.action_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

    def _on_action_clicked(self):
        item = self.manager.get_item(self.item_id)
        if item is None:
            return
        if item.status == ConversionItem.STATUS_ERROR:
            self.manager.retry_item(self.item_id)
        elif item.status in (ConversionItem.STATUS_DONE, ConversionItem.STATUS_CANCELLED,
                              ConversionItem.STATUS_UNSUPPORTED):
            self.manager.items.pop(self.item_id, None)
            if self.item_id in self.manager.order:
                self.manager.order.remove(self.item_id)
            self.manager.item_removed.emit(self.item_id)
        else:
            self.manager.cancel_item(self.item_id)


class SettingsDialog(QDialog):
    """Frameless to match MainWindow (see Header) - its own title bar reuses
    the Header widget/objectNames so it repaints for free whenever the
    global stylesheet changes, which matters here specifically because
    changing the theme picker below live-previews the theme app-wide
    (see _apply_theme_preview()) while this dialog is still open."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._main_window = parent
        self.settings = settings
        self._original_theme = settings.theme
        self._resolved_theme = settings.theme

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_title_bar())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(16)

        tabs = QTabWidget()
        tabs.addTab(self._build_downloads_tab(settings), "Downloads")
        tabs.addTab(self._build_appearance_tab(settings), "Aparência")
        body_layout.addWidget(tabs)

        btn_row = QHBoxLayout()
        version_label = QLabel(f"Allora {APP_VERSION}")
        version_label.setObjectName("Dim")
        btn_row.addWidget(version_label)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("Ghost")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Salvar")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        body_layout.addLayout(btn_row)

        outer.addWidget(body)

    # ------------------------------------------------------------------
    # Title bar - a slimmed-down twin of MainWindow's Header: draggable,
    # themed, but close-only (a settings dialog has no minimize/maximize).
    # ------------------------------------------------------------------

    def _build_title_bar(self) -> QFrame:
        bar = Header(lambda: None)
        bar.setObjectName("Header")
        bar.setFixedHeight(60)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 16, 0)
        layout.setSpacing(12)

        title = QLabel("Configurações")
        title.setObjectName("HeaderTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        self._close_btn = QPushButton()
        self._close_btn.setObjectName("WinBtn")
        self._close_btn.setProperty("kind", "close")
        self._close_btn.setFixedSize(32, 28)
        self._close_btn.setIconSize(QSize(12, 12))
        self._close_btn.setToolTip("Fechar")
        self._close_btn.clicked.connect(self.reject)
        layout.addWidget(self._close_btn)

        return bar

    # ------------------------------------------------------------------
    # Tab pages
    # ------------------------------------------------------------------

    def _build_downloads_tab(self, settings: Settings) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(16)

        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(16)
        row = 0

        grid.addWidget(QLabel("Pasta de destino:"), row, 0)
        self.folder_edit = QLineEdit(settings.output_dir)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setMinimumHeight(34)
        grid.addWidget(self.folder_edit, row, 1)
        self._browse_btn = QPushButton(" Escolher")
        self._browse_btn.setObjectName("Secondary")
        self._browse_btn.setMinimumHeight(34)
        self._browse_btn.clicked.connect(self._choose_folder)
        grid.addWidget(self._browse_btn, row, 2)
        row += 1

        grid.addWidget(QLabel("Qualidade padrão:"), row, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_CHOICES)
        self.quality_combo.setCurrentText(settings.default_quality)
        self.quality_combo.setMinimumHeight(34)
        grid.addWidget(self.quality_combo, row, 1, 1, 2)
        row += 1

        grid.addWidget(QLabel("Downloads simultâneos:"), row, 0)
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 3)
        self.max_spin.setValue(settings.max_simultaneous)
        self.max_spin.setMinimumHeight(34)
        grid.addWidget(self.max_spin, row, 1, 1, 2)
        row += 1

        self.ffmpeg_check = QCheckBox("Usar ffmpeg para mesclar áudio/vídeo")
        self.ffmpeg_check.setChecked(settings.use_ffmpeg_merge)
        grid.addWidget(self.ffmpeg_check, row, 0, 1, 3)
        row += 1

        self.thumb_check = QCheckBox("Salvar thumbnail junto com o vídeo")
        self.thumb_check.setChecked(settings.save_thumbnail)
        grid.addWidget(self.thumb_check, row, 0, 1, 3)
        row += 1

        self.meta_check = QCheckBox("Salvar metadados do vídeo (.info.json)")
        self.meta_check.setChecked(settings.save_metadata)
        grid.addWidget(self.meta_check, row, 0, 1, 3)
        row += 1

        grid.addWidget(QLabel("Caminho customizado do ffmpeg:"), row, 0)
        self.ffmpeg_path_edit = QLineEdit(settings.ffmpeg_path)
        self.ffmpeg_path_edit.setPlaceholderText("Deixe em branco para usar o PATH do sistema")
        self.ffmpeg_path_edit.setMinimumHeight(34)
        grid.addWidget(self.ffmpeg_path_edit, row, 1)
        self._ffmpeg_browse_btn = QPushButton()
        self._ffmpeg_browse_btn.setObjectName("Secondary")
        self._ffmpeg_browse_btn.setFixedWidth(40)
        self._ffmpeg_browse_btn.setMinimumHeight(34)
        self._ffmpeg_browse_btn.clicked.connect(self._choose_ffmpeg)
        grid.addWidget(self._ffmpeg_browse_btn, row, 2)
        row += 1

        outer.addLayout(grid)

        self.ffmpeg_status_label = QLabel()
        outer.addWidget(self.ffmpeg_status_label)
        self._refresh_ffmpeg_status()

        outer.addStretch(1)
        self._refresh_field_icons()
        return page

    def _build_appearance_tab(self, settings: Settings) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 16, 4, 4)
        outer.setSpacing(16)

        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(16)

        grid.addWidget(QLabel("Tema:"), 0, 0)
        self.theme_base_combo = QComboBox()
        self.theme_base_combo.addItems(base_theme_names())
        base_label, mode = theme_key_to_base_and_mode(settings.theme)
        self._theme_mode = mode
        idx = self.theme_base_combo.findText(base_label)
        if idx >= 0:
            self.theme_base_combo.setCurrentIndex(idx)
        self.theme_base_combo.setMinimumHeight(34)
        self.theme_base_combo.currentTextChanged.connect(self._on_base_theme_changed)
        grid.addWidget(self.theme_base_combo, 0, 1, 1, 2)

        self.theme_mode_btn = QPushButton()
        self.theme_mode_btn.setObjectName("Secondary")
        self.theme_mode_btn.setMinimumHeight(34)
        self.theme_mode_btn.clicked.connect(self._toggle_theme_mode)
        grid.addWidget(self.theme_mode_btn, 1, 1, 1, 2)

        outer.addLayout(grid)
        outer.addStretch(1)
        self._refresh_theme_mode_button()
        return page

    # ------------------------------------------------------------------
    # Theme live preview - selecting a base theme or toggling light/dark
    # applies it to the whole running app immediately (via the parent
    # MainWindow's own apply_theme(), the same one theme changes always
    # went through), but nothing is written to config.json until Salvar;
    # Cancelar/closing reverts back to whatever theme was active when the
    # dialog opened. See reject() below.
    # ------------------------------------------------------------------

    def _on_base_theme_changed(self, base_label: str):
        variants = THEME_VARIANTS.get(base_label, {})
        if not variants.get(self._theme_mode):
            self._theme_mode = "dark"
        self._refresh_theme_mode_button()
        self._apply_theme_preview()

    def _toggle_theme_mode(self):
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        self._refresh_theme_mode_button()
        self._apply_theme_preview()

    def _refresh_theme_mode_button(self):
        base_label = self.theme_base_combo.currentText()
        variants = THEME_VARIANTS.get(base_label, {})
        has_both = variants.get("dark") and variants.get("light")
        if not has_both:
            self.theme_mode_btn.setText("Apenas um modo disponível")
            self.theme_mode_btn.setEnabled(False)
        else:
            self.theme_mode_btn.setEnabled(True)
            if self._theme_mode == "dark":
                self.theme_mode_btn.setText("☀ Mudar para Claro")
            else:
                self.theme_mode_btn.setText("🌙 Mudar para Escuro")

    def _apply_theme_preview(self):
        base_label = self.theme_base_combo.currentText()
        self._resolved_theme = resolve_theme_variant(base_label, self._theme_mode)
        if self._main_window is not None and hasattr(self._main_window, "apply_theme"):
            self._main_window.apply_theme(self._resolved_theme)
        self._refresh_field_icons()

    def _refresh_field_icons(self):
        """Icons living inside this dialog (browse buttons, close button)
        are baked bitmaps like everywhere else in the app (see icons.py) -
        they need their own refresh on every theme change, previewed or
        not, the same way MainWindow._refresh_icon_theme() does for the
        rest of the UI."""
        colors = theme_colors(self._resolved_theme)
        self._browse_btn.setIcon(make_icon("folder", colors["text_primary"], size=15))
        self._ffmpeg_browse_btn.setIcon(make_icon("folder", colors["text_primary"], size=15))
        self._close_btn.setIcon(make_icon("win-close", colors["text_secondary"], size=12))

    # ------------------------------------------------------------------

    def _refresh_ffmpeg_status(self):
        path = find_ffmpeg(self.ffmpeg_path_edit.text().strip())
        if ffmpeg_is_working(path):
            self.ffmpeg_status_label.setText(f"ffmpeg encontrado: {path}")
            self.ffmpeg_status_label.setObjectName("StatusDone")
        else:
            self.ffmpeg_status_label.setText(
                "ffmpeg não encontrado. Instale-o e adicione ao PATH, ou informe o caminho acima."
            )
            self.ffmpeg_status_label.setObjectName("StatusError")
        repolish(self.ffmpeg_status_label)

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _choose_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar executável do ffmpeg")
        if path:
            self.ffmpeg_path_edit.setText(path)
            self._refresh_ffmpeg_status()

    def reject(self):
        # Cancelar, the titlebar close button, and Esc all funnel through
        # here - undo the live preview so an aborted theme change never
        # sticks after the dialog closes without Salvar.
        if self._resolved_theme != self._original_theme:
            if self._main_window is not None and hasattr(self._main_window, "apply_theme"):
                self._main_window.apply_theme(self._original_theme)
        super().reject()

    def apply_to(self, settings: Settings):
        settings.output_dir = self.folder_edit.text().strip() or settings.output_dir
        settings.default_quality = self.quality_combo.currentText()
        settings.max_simultaneous = self.max_spin.value()
        settings.theme = self._resolved_theme
        settings.use_ffmpeg_merge = self.ffmpeg_check.isChecked()
        settings.save_thumbnail = self.thumb_check.isChecked()
        settings.save_metadata = self.meta_check.isChecked()
        settings.ffmpeg_path = self.ffmpeg_path_edit.text().strip()


class UrlInput(QPlainTextEdit):
    """A QPlainTextEdit dressed up to look/behave like a single-line field,
    but that still accepts multi-line paste (one URL per line) and submits
    on Enter."""

    def __init__(self, on_submit, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self.setPlaceholderText("Cole o link aqui... (um por linha para vários vídeos)")
        self.setFixedHeight(40)
        # AlwaysOff, not AsNeeded: what looked like a mystery icon docked to
        # the field's right edge (reported as two small "monitor" glyphs)
        # turned out to be Qt's own native scrollbar up/down arrow buttons,
        # showing up whenever pasted text needed more than the field's
        # fixed height - not a Windows IME/touch-keyboard overlay at all,
        # which is why the _detach_windows_ime() workaround below never
        # touched it. This field is meant to look like a single line (see
        # class docstring), so it should never show a scrollbar - overflow
        # from a multi-URL paste just isn't visible before Enter is
        # pressed, which is fine since on_add_clicked() reads and clears
        # the whole text at once rather than displaying it for editing.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        # Qt's own WA_InputMethodEnabled only turns off Qt's IME candidate
        # window - it does nothing to the native Win32 IME context that
        # Windows' shell actually keys the emoji/touch-keyboard flyout icon
        # off of. Detaching that context directly (below, once the widget
        # has a real native handle) is the documented workaround other
        # frameworks use for this exact icon; disabling WA_InputMethodEnabled
        # too doesn't hurt as a secondary signal.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self._detach_windows_ime()

    def _detach_windows_ime(self) -> None:
        """Removes the native IME context Windows associates with this
        control's HWND, which is what the shell actually checks before
        drawing its emoji/touch-keyboard flyout icon over a focused text
        field - Qt's own input-method flag (above) doesn't touch this.
        Uses ImmAssociateContextEx with IACE_IGNORENOCONTEXT rather than
        plain ImmAssociateContext(hwnd, None): the plain call only detaches
        once and Windows can silently reattach a fresh default IME context
        the next time the field regains focus, so the icon comes back;
        IACE_IGNORENOCONTEXT tells the system to remember "no IME" for this
        HWND persistently. Falls back to the older call on Windows builds
        where ImmAssociateContextEx isn't available. Trade-off: this also
        turns off IME composition for this field, so typing Chinese/
        Japanese/Korean directly into it won't work (pasting already-typed
        text is unaffected). Windows-only and best-effort - wrapped so any
        failure just leaves the field working normally."""
        if os.name != "nt":
            return
        try:
            import ctypes

            self.setAttribute(Qt.WA_NativeWindow, True)
            hwnd = int(self.winId())
            IACE_IGNORENOCONTEXT = 0x0004
            try:
                ok = ctypes.windll.imm32.ImmAssociateContextEx(hwnd, None, IACE_IGNORENOCONTEXT)
                if not ok:
                    raise OSError("ImmAssociateContextEx returned FALSE")
            except (AttributeError, OSError):
                ctypes.windll.imm32.ImmAssociateContext(hwnd, None)

            # The visible icon itself (the little keyboard glyph docked to
            # the field's right edge) isn't drawn by IME at all - it's the
            # separate "touch keyboard invocation" affordance TextInputHost
            # overlays on any focusable/editable control system-wide since
            # Windows 10 1903. The IME detach above doesn't touch it. The
            # Microsoft-documented opt-out (also how Chromium suppresses it
            # on every editable HWND - see ui/base/win/internal_constants.cc)
            # is tagging the window with this exact property name.
            ctypes.windll.user32.SetPropW(hwnd, "MicrosoftTabletPenServiceProperty", ctypes.c_void_p(1))
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self._on_submit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        # Belt-and-suspenders: re-detach on every focus in case
        # IACE_IGNORENOCONTEXT isn't fully honored on this Windows build
        # and the shell reattaches a default IME context anyway.
        self._detach_windows_ime()
        super().focusInEvent(event)

    def showEvent(self, event):
        # The __init__-time call happens before this widget is parented
        # into its layout (see input_row.addWidget() in
        # MainWindow._build_downloads_tab()) - Qt can recreate the native
        # HWND on reparenting, silently discarding whatever winId() pointed
        # at during __init__. By the time showEvent fires the widget is
        # fully parented and its HWND is final, so this is the call that
        # actually sticks.
        super().showEvent(event)
        self._detach_windows_ime()


class MainWindow(QMainWindow):
    def __init__(self, manager: DownloadManager, conversion_manager: ConversionManager, settings: Settings):
        super().__init__()
        self.manager = manager
        self.conversion_manager = conversion_manager
        self.settings = settings
        self._widgets: dict[int, QueueItemWidget] = {}
        self._list_items: dict[int, QListWidgetItem] = {}
        self._conv_widgets: dict[int, ConversionItemWidget] = {}
        self._conv_list_items: dict[int, QListWidgetItem] = {}
        self._ffmpeg_warned = False
        # (button, icon name, palette key) - recolored on every theme
        # change by _refresh_icon_theme(), since QPushButton icons are
        # baked pixmaps that QSS can't recolor on its own.
        self._icon_buttons: list[tuple[QPushButton, str, str]] = []
        # Manual "fake maximize" state (see _toggle_maximize) - resizing to
        # the screen's available geometry instead of calling the native
        # showMaximized(), which on a frameless Windows window can end up
        # covering the taskbar since Windows normally uses the presence of
        # a native caption/frame to decide the maximized window shouldn't
        # cover it.
        self._is_pseudo_maximized = False
        self._restore_geometry = None

        self.setWindowTitle("Allora")
        self.resize(900, 650)
        self.setMinimumSize(700, 500)
        # No native title bar/border: the Header widget below draws its own
        # (see the Header class, _toggle_maximize, and nativeEvent for how
        # moving, maximize/restore, and edge resizing are reimplemented
        # without it).
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

        self._build_ui()
        self._connect_manager_signals()
        self.apply_theme(settings.theme)
        self._check_ffmpeg_on_start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_downloads_tab())
        self.stack.addWidget(self._build_converter_tab())
        self.documentos_tab = DocumentosTab(self.settings)
        self.stack.addWidget(self.documentos_tab)
        body_layout.addWidget(self.stack, stretch=1)

        root.addWidget(body, stretch=1)
        self._select_nav(0)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 16, 10, 16)
        layout.setSpacing(3)

        self.nav_items: list[NavItem] = []

        downloads_item = NavItem("download", "Downloads", lambda: self._select_nav(0), self.settings.theme)
        converter_item = NavItem("convert", "Converter Arquivos", lambda: self._select_nav(1), self.settings.theme)
        documentos_item = NavItem("document", "Documentos", lambda: self._select_nav(2), self.settings.theme)
        for item in (downloads_item, converter_item, documentos_item):
            layout.addWidget(item)
            self.nav_items.append(item)
        self.nav_downloads_item = downloads_item
        self.nav_converter_item = converter_item

        layout.addStretch(1)
        return sidebar

    def _select_nav(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, item in enumerate(self.nav_items):
            item.set_active(i == index)

    def _build_header(self) -> QFrame:
        header = Header(self._toggle_maximize)
        header.setObjectName("Header")
        header.setFixedHeight(60)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 16, 0)
        layout.setSpacing(12)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(34, 34)
        layout.addWidget(self.logo_label)
        title = QLabel("Allora")
        title.setObjectName("HeaderTitle")
        layout.addWidget(title)
        layout.addStretch(1)

        self.settings_btn = QPushButton(" Configurações")
        self.settings_btn.setObjectName("Secondary")
        self.settings_btn.setIconSize(QSize(15, 15))
        self.settings_btn.clicked.connect(self.open_settings)
        self._icon_buttons.append((self.settings_btn, "gear", "text_primary"))
        layout.addWidget(self.settings_btn)

        about_btn = QPushButton()
        about_btn.setObjectName("Ghost")
        about_btn.setFixedWidth(36)
        about_btn.setIconSize(QSize(16, 16))
        about_btn.setToolTip("Sobre")
        about_btn.clicked.connect(self.show_about)
        self._icon_buttons.append((about_btn, "help-circle", "text_secondary"))
        layout.addWidget(about_btn)

        layout.addSpacing(8)

        # --- window controls (replace the OS-drawn min/max/close now that
        # the window is frameless) --------------------------------------
        # Not run through self._icon_buttons: that loop always renders at
        # size=15 to match the toolbar buttons' 15px icon size, but these
        # sit in a slimmer 32x28 slot and want a crisper 12px render - so
        # they're refreshed alongside maximize_btn in _refresh_icon_theme().
        self.min_btn = QPushButton()
        self.min_btn.setObjectName("WinBtn")
        self.min_btn.setFixedSize(32, 28)
        self.min_btn.setIconSize(QSize(12, 12))
        self.min_btn.setToolTip("Minimizar")
        self.min_btn.clicked.connect(self.showMinimized)
        layout.addWidget(self.min_btn)

        # Not registered in self._icon_buttons: its icon toggles between
        # maximize/restore depending on window state, which a theme
        # refresh must preserve rather than resetting - see
        # _refresh_icon_theme() and _refresh_maximize_icon().
        self.maximize_btn = QPushButton()
        self.maximize_btn.setObjectName("WinBtn")
        self.maximize_btn.setFixedSize(32, 28)
        self.maximize_btn.setIconSize(QSize(12, 12))
        self.maximize_btn.setToolTip("Maximizar")
        self.maximize_btn.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.maximize_btn)

        self.close_btn = QPushButton()
        self.close_btn.setObjectName("WinBtn")
        self.close_btn.setProperty("kind", "close")
        self.close_btn.setFixedSize(32, 28)
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setToolTip("Fechar")
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)

        return header

    def _toggle_maximize(self):
        """Manual stand-in for showMaximized()/showNormal() - see the
        comment on self._is_pseudo_maximized in __init__ for why a
        frameless window on Windows can't just use the native call."""
        if self._is_pseudo_maximized:
            if self._restore_geometry is not None:
                self.setGeometry(self._restore_geometry)
            self._is_pseudo_maximized = False
        else:
            self._restore_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry())
            self._is_pseudo_maximized = True
        self._refresh_maximize_icon()

    def _refresh_maximize_icon(self):
        name = "win-restore" if self._is_pseudo_maximized else "win-maximize"
        color = theme_colors(self.settings.theme)["text_secondary"]
        self.maximize_btn.setIcon(make_icon(name, color, size=12))
        self.maximize_btn.setToolTip("Restaurar" if self._is_pseudo_maximized else "Maximizar")

    def _build_queue_footer(self, start_text, start_icon, on_start, on_pause, on_clear):
        """Shared skeleton for the Downloads/Converter tabs' bottom control
        row: a Primary start button, a Ghost pause button, a Ghost clear
        button, and a status label pinned to the right. Returns the layout
        plus the three widgets callers need to keep mutating."""
        row = QHBoxLayout()
        start_btn = QPushButton(f" {start_text}")
        start_btn.setObjectName("Primary")
        start_btn.setIconSize(QSize(14, 14))
        start_btn.clicked.connect(on_start)
        self._icon_buttons.append((start_btn, start_icon, "accent_ink"))
        row.addWidget(start_btn)

        # Not registered in self._icon_buttons: its icon toggles between
        # pause/play depending on paused state, which a theme refresh
        # must preserve rather than resetting to a fixed icon - see
        # _refresh_icon_theme().
        pause_btn = QPushButton(" Pausar")
        pause_btn.setObjectName("Ghost")
        pause_btn.setIconSize(QSize(14, 14))
        pause_btn.clicked.connect(on_pause)
        row.addWidget(pause_btn)

        clear_btn = QPushButton(" Limpar concluídos")
        clear_btn.setObjectName("Ghost")
        clear_btn.setIconSize(QSize(14, 14))
        clear_btn.clicked.connect(on_clear)
        self._icon_buttons.append((clear_btn, "trash", "text_secondary"))
        row.addWidget(clear_btn)

        row.addStretch(1)
        status_label = QLabel("")
        status_label.setObjectName("Dim")
        row.addWidget(status_label)

        return row, pause_btn, status_label

    def _build_downloads_tab(self) -> QWidget:
        tab = QWidget()
        body = QVBoxLayout(tab)
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(10)

        # --- URL input row -------------------------------------------------
        input_row = QHBoxLayout()
        self.url_input = UrlInput(self.on_add_clicked)
        input_row.addWidget(self.url_input, stretch=1)
        add_btn = QPushButton(" Adicionar")
        add_btn.setObjectName("Primary")
        add_btn.setFixedWidth(120)
        add_btn.setIconSize(QSize(14, 14))
        add_btn.clicked.connect(self.on_add_clicked)
        self._icon_buttons.append((add_btn, "plus", "accent_ink"))
        input_row.addWidget(add_btn)
        body.addLayout(input_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setVisible(False)
        body.addWidget(self.error_label)

        # --- quality / folder row ------------------------------------------
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Qualidade:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_CHOICES)
        self.quality_combo.setCurrentText(self.settings.default_quality)
        options_row.addWidget(self.quality_combo)

        options_row.addSpacing(20)
        options_row.addWidget(QLabel("Pasta:"))
        self.folder_label = QLabel(self.settings.output_dir)
        self.folder_label.setObjectName("Dim")
        options_row.addWidget(self.folder_label, stretch=1)
        folder_btn = QPushButton(" Escolher")
        folder_btn.setObjectName("Secondary")
        folder_btn.clicked.connect(self.choose_output_folder)
        self._icon_buttons.append((folder_btn, "folder", "text_primary"))
        options_row.addWidget(folder_btn)
        body.addLayout(options_row)

        # --- queue section ---------------------------------------------------
        queue_label = QLabel("FILA DE DOWNLOADS")
        queue_label.setObjectName("SectionLabel")
        body.addWidget(queue_label)

        self.queue_list = QListWidget()
        self.queue_list.setSpacing(8)
        self.queue_list.setSelectionMode(QListWidget.NoSelection)
        self.queue_list.setFocusPolicy(Qt.NoFocus)
        body.addWidget(self.queue_list, stretch=1)

        # --- bottom controls ---------------------------------------------------
        footer, self.pause_btn, self.status_bar_label = self._build_queue_footer(
            "Iniciar tudo", "play", self.on_start_all, self.on_pause, self.manager.clear_completed,
        )
        body.addLayout(footer)
        return tab

    def _build_converter_tab(self) -> QWidget:
        tab = QWidget()
        body = QVBoxLayout(tab)
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(10)

        # --- file picker / drop zone row ------------------------------------
        input_row = QHBoxLayout()
        select_btn = QPushButton(" Selecionar arquivo(s)")
        select_btn.setObjectName("Primary")
        select_btn.setIconSize(QSize(15, 15))
        select_btn.clicked.connect(self.on_select_conversion_files)
        self._icon_buttons.append((select_btn, "upload-cloud", "accent_ink"))
        input_row.addWidget(select_btn)
        self.drop_zone = DropZone(self.on_files_dropped, self.settings.theme)
        input_row.addWidget(self.drop_zone, stretch=1)
        body.addLayout(input_row)

        self.conv_error_label = QLabel("")
        self.conv_error_label.setObjectName("ErrorLabel")
        self.conv_error_label.setVisible(False)
        body.addWidget(self.conv_error_label)

        # --- folder row ------------------------------------------------------
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Pasta de destino:"))
        self.conv_folder_label = QLabel(self.settings.output_dir)
        self.conv_folder_label.setObjectName("Dim")
        folder_row.addWidget(self.conv_folder_label, stretch=1)
        conv_folder_btn = QPushButton(" Escolher")
        conv_folder_btn.setObjectName("Secondary")
        conv_folder_btn.clicked.connect(self.choose_output_folder)
        self._icon_buttons.append((conv_folder_btn, "folder", "text_primary"))
        folder_row.addWidget(conv_folder_btn)
        body.addLayout(folder_row)

        # --- queue section -----------------------------------------------------
        queue_label = QLabel("ARQUIVOS PARA CONVERTER")
        queue_label.setObjectName("SectionLabel")
        body.addWidget(queue_label)

        self.conv_queue_list = QListWidget()
        self.conv_queue_list.setSpacing(8)
        self.conv_queue_list.setSelectionMode(QListWidget.NoSelection)
        self.conv_queue_list.setFocusPolicy(Qt.NoFocus)
        body.addWidget(self.conv_queue_list, stretch=1)

        # --- bottom controls -----------------------------------------------------
        footer, self.conv_pause_btn, self.conv_status_label = self._build_queue_footer(
            "Converter tudo", "play", self.on_start_all_conversions, self.on_pause_conversions,
            self.conversion_manager.clear_completed,
        )
        body.addLayout(footer)
        return tab

    def _connect_manager_signals(self):
        self.manager.item_added.connect(self._on_item_added)
        self.manager.item_updated.connect(self._on_item_updated)
        self.manager.item_removed.connect(self._on_item_removed)
        self.manager.queue_idle.connect(self._on_queue_idle)
        self.manager.ffmpeg_missing.connect(self._on_ffmpeg_missing)

        self.conversion_manager.item_added.connect(self._on_conv_item_added)
        self.conversion_manager.item_updated.connect(self._on_conv_item_updated)
        self.conversion_manager.item_removed.connect(self._on_conv_item_removed)
        self.conversion_manager.queue_idle.connect(self._on_conv_queue_idle)
        self.conversion_manager.ffmpeg_missing.connect(self._on_ffmpeg_missing)

    # ------------------------------------------------------------------
    # URL input handling
    # ------------------------------------------------------------------

    def on_add_clicked(self):
        text = self.url_input.toPlainText().strip()
        if not text:
            self._show_error("Cole um link antes de adicionar.")
            return

        urls = split_urls(text)
        if not urls:
            self._show_error("URL inválida. Verifique o link e tente novamente.")
            return

        quality = self.quality_combo.currentText()
        for url in urls:
            self.manager.add_url(url, quality)

        self.url_input.clear()
        self.error_label.setVisible(False)

    def _show_error(self, message: str):
        self.error_label.setText(message)
        self.error_label.setVisible(True)

    # ------------------------------------------------------------------
    # Queue signal handlers
    # ------------------------------------------------------------------

    def _on_item_added(self, item_id: int):
        item = self.manager.get_item(item_id)
        if item is None:
            return
        widget = QueueItemWidget(item_id, self.manager, self.settings.theme)
        widget.refresh(item)

        list_item = QListWidgetItem()
        list_item.setSizeHint(QSize(0, 100))
        self.queue_list.addItem(list_item)
        self.queue_list.setItemWidget(list_item, widget)

        self._widgets[item_id] = widget
        self._list_items[item_id] = list_item
        self.nav_downloads_item.set_count(len(self._widgets))

    def _on_item_updated(self, item_id: int):
        item = self.manager.get_item(item_id)
        widget = self._widgets.get(item_id)
        if item is None or widget is None:
            return
        widget.refresh(item)

    def _on_item_removed(self, item_id: int):
        list_item = self._list_items.pop(item_id, None)
        self._widgets.pop(item_id, None)
        if list_item is not None:
            row = self.queue_list.row(list_item)
            if row >= 0:
                self.queue_list.takeItem(row)
        self.nav_downloads_item.set_count(len(self._widgets))

    def _on_queue_idle(self):
        self.status_bar_label.setText("Fila concluída.")

    # ------------------------------------------------------------------
    # Converter tab handling
    # ------------------------------------------------------------------

    def on_select_conversion_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Selecionar arquivo(s) para converter")
        if paths:
            self.on_files_dropped(paths)

    def on_files_dropped(self, paths):
        self.conv_error_label.setVisible(False)
        added = 0
        for path in paths:
            if os.path.isfile(path):
                self.conversion_manager.add_file(path)
                added += 1
        if added == 0:
            self.conv_error_label.setText("Nenhum arquivo válido foi selecionado.")
            self.conv_error_label.setVisible(True)

    def on_start_all_conversions(self):
        self.conversion_manager.start_all()
        self.conv_status_label.setText("Convertendo...")

    def on_pause_conversions(self):
        if self.conversion_manager.paused:
            self.conversion_manager.paused = False
            self.conv_pause_btn.setText(" Pausar")
            set_action_icon(self.conv_pause_btn, "pause", self.settings.theme)
            self.conv_status_label.setText("Convertendo...")
        else:
            self.conversion_manager.pause()
            self.conv_pause_btn.setText(" Retomar")
            set_action_icon(self.conv_pause_btn, "play", self.settings.theme)
            self.conv_status_label.setText("Pausado (conversões em andamento serão concluídas).")

    def _on_conv_item_added(self, item_id: int):
        item = self.conversion_manager.get_item(item_id)
        if item is None:
            return
        widget = ConversionItemWidget(item_id, self.conversion_manager, self.settings.theme)

        list_item = QListWidgetItem()
        list_item.setSizeHint(QSize(0, 100))
        self.conv_queue_list.addItem(list_item)
        self.conv_queue_list.setItemWidget(list_item, widget)

        self._conv_widgets[item_id] = widget
        self._conv_list_items[item_id] = list_item
        self.nav_converter_item.set_count(len(self._conv_widgets))

    def _on_conv_item_updated(self, item_id: int):
        item = self.conversion_manager.get_item(item_id)
        widget = self._conv_widgets.get(item_id)
        if item is None or widget is None:
            return
        widget.refresh(item)

    def _on_conv_item_removed(self, item_id: int):
        list_item = self._conv_list_items.pop(item_id, None)
        self._conv_widgets.pop(item_id, None)
        if list_item is not None:
            row = self.conv_queue_list.row(list_item)
            if row >= 0:
                self.conv_queue_list.takeItem(row)
        self.nav_converter_item.set_count(len(self._conv_widgets))

    def _on_conv_queue_idle(self):
        self.conv_status_label.setText("Conversões concluídas.")

    def _on_ffmpeg_missing(self):
        if self._ffmpeg_warned:
            return
        self._ffmpeg_warned = True
        QMessageBox.warning(
            self,
            "ffmpeg não encontrado",
            "O ffmpeg não foi encontrado no PATH do sistema.\n\n"
            "Ele é necessário para mesclar vídeo+áudio em qualidades acima de "
            "360p e para extrair áudio em MP3.\n\n"
            "Baixe em https://ffmpeg.org/download.html, adicione ao PATH do "
            "Windows, ou informe o caminho do executável em "
            "Configurações → Caminho customizado do ffmpeg.",
        )

    # ------------------------------------------------------------------
    # Toolbar / bottom actions
    # ------------------------------------------------------------------

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", self.settings.output_dir)
        if folder:
            self.settings.output_dir = folder
            self.folder_label.setText(folder)
            self.conv_folder_label.setText(folder)
            save_settings(self.settings)

    def on_start_all(self):
        self.manager.start_all()
        self.status_bar_label.setText("Baixando...")

    def on_pause(self):
        if self.manager.paused:
            self.manager.paused = False
            self.pause_btn.setText(" Pausar")
            set_action_icon(self.pause_btn, "pause", self.settings.theme)
            self.status_bar_label.setText("Baixando...")
        else:
            self.manager.pause()
            self.pause_btn.setText(" Retomar")
            set_action_icon(self.pause_btn, "play", self.settings.theme)
            self.status_bar_label.setText("Pausado (downloads em andamento serão concluídos).")

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply_to(self.settings)
            save_settings(self.settings)
            self.folder_label.setText(self.settings.output_dir)
            self.conv_folder_label.setText(self.settings.output_dir)
            # Already showing live (see SettingsDialog._apply_theme_preview) -
            # re-applying here is just cheap insurance that the persisted
            # settings.theme and the on-screen theme can never drift apart.
            self.apply_theme(self.settings.theme)

    def show_about(self):
        QMessageBox.information(
            self,
            "Sobre",
            f"Allora {APP_VERSION}\n\n"
            "Baixe vídeos do YouTube, Instagram, Twitter/X, TikTok e mais, "
            "usando yt-dlp.\n\n"
            "Cole um link, escolha a qualidade e clique em Adicionar. "
            "Depois, clique em 'Iniciar tudo' para começar a fila.\n\n"
            "Na aba 'Converter Arquivos', envie um arquivo de vídeo, áudio "
            "ou imagem já salvo no seu PC e escolha para qual formato "
            "convertê-lo.",
        )

    def _check_ffmpeg_on_start(self):
        path = find_ffmpeg(self.settings.ffmpeg_path)
        if not ffmpeg_is_working(path):
            self._on_ffmpeg_missing()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def set_theme(self, theme_name: str):
        self.settings.theme = theme_name
        save_settings(self.settings)
        self.apply_theme(theme_name)

    def apply_theme(self, theme_name: str):
        set_app_theme(QApplication.instance(), theme_name)
        self._refresh_icon_theme(theme_name)

    def _load_logo_pixmap(self, size: int, invert: bool = False) -> QPixmap:
        """The Allora mark, bundled as a fixed PNG asset (assets/logo.png)
        rather than drawn from icons.py's theme-recolorable SVG set - this
        one is a fixed piece of brand art, not a stroke icon, so it can't
        be recolored by the QSS stylesheet the way the rest of the UI's
        icons are. Instead, on a light theme (see THEMES[...]["is_light"]
        in theme.py) the whole image's RGB is inverted - the dark tile
        becomes light and the light mark becomes dark - so the badge still
        reads correctly against a light header instead of looking like a
        leftover dark-mode asset. Rendered once at 2x and given a device
        pixel ratio of 2 to stay crisp on high-DPI displays, matching
        make_icon()'s convention."""
        scale = 2
        side = size * scale
        image = QImage(resource_path("assets/logo.png"))
        if image.isNull():
            # Fallback so a missing/misplaced asset never crashes the app -
            # just leaves the header logo blank instead.
            pixmap = QPixmap(side, side)
            pixmap.fill(Qt.transparent)
            pixmap.setDevicePixelRatio(float(scale))
            return pixmap
        if invert:
            image = image.convertToFormat(QImage.Format_ARGB32)
            image.invertPixels(QImage.InvertRgb)
        pixmap = QPixmap.fromImage(image).scaled(
            side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        pixmap.setDevicePixelRatio(float(scale))
        return pixmap

    def _refresh_icon_theme(self, theme_name: str):
        """QPushButton icons and QLabel pixmaps are baked bitmaps that QSS
        can't recolor - every icon-bearing widget gets rebuilt here so a
        theme change recolors the whole UI, not just backgrounds/text."""
        colors = theme_colors(theme_name)

        self.logo_label.setPixmap(self._load_logo_pixmap(34, invert=colors.get("is_light", False)))

        for item in self.nav_items:
            item.set_theme(theme_name)

        for button, icon_name, color_key in self._icon_buttons:
            button.setIcon(make_icon(icon_name, colors[color_key], size=15))

        self.min_btn.setIcon(make_icon("win-minimize", colors["text_secondary"], size=12))
        self.close_btn.setIcon(make_icon("win-close", colors["text_secondary"], size=12))
        self._refresh_maximize_icon()

        self.documentos_tab.set_theme(theme_name)

        set_action_icon(self.pause_btn, "play" if self.manager.paused else "pause", theme_name)
        set_action_icon(self.conv_pause_btn, "play" if self.conversion_manager.paused else "pause", theme_name)

        self.drop_zone.set_theme(theme_name)

        for widget in self._widgets.values():
            widget.theme_name = theme_name
            if not widget._thumb_loaded:
                widget._set_thumb_icon("film")
            item = self.manager.get_item(widget.item_id)
            if item is not None:
                widget.refresh(item)

        for widget in self._conv_widgets.values():
            widget.theme_name = theme_name
            item = self.conversion_manager.get_item(widget.item_id)
            if item is not None:
                widget._set_category_icon(item.category)
                widget.refresh(item)

    # ------------------------------------------------------------------
    # Frameless-window plumbing (Windows only)
    # ------------------------------------------------------------------
    #
    # Going frameless (see the Qt.FramelessWindowHint flag set in
    # __init__) drops the OS's own edge/corner resize handling along with
    # its title bar - Header replaces the title bar (dragging it moves the
    # window, double-clicking toggles maximize/restore), and this restores
    # just the resize-by-dragging-the-edge part by answering Windows'
    # WM_NCHITTEST message for a thin border around the window. Returning
    # one of the HTLEFT/HTRIGHT/HTTOP/HTBOTTOM(-corner) codes there is the
    # same protocol a normal bordered window uses to tell Windows "the
    # user grabbed an edge, start an interactive resize" - Windows handles
    # the actual drag, cursor shape, and screen-edge snapping from there.
    # Deliberately skipped while pseudo-maximized (see _toggle_maximize):
    # a maximized window shouldn't be edge-resizable until it's restored.
    _RESIZE_BORDER = 6

    def nativeEvent(self, eventType, message):
        if os.name == "nt" and eventType == b"windows_generic_MSG" and not self._is_pseudo_maximized:
            try:
                import ctypes
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                WM_NCHITTEST = 0x0084
                if msg.message == WM_NCHITTEST:
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    pos = self.mapFromGlobal(QPoint(x, y))
                    bw = self._RESIZE_BORDER
                    w, h = self.width(), self.height()

                    left = pos.x() < bw
                    right = pos.x() >= w - bw
                    top = pos.y() < bw
                    bottom = pos.y() >= h - bw

                    if top and left:
                        return True, 13      # HTTOPLEFT
                    if top and right:
                        return True, 14      # HTTOPRIGHT
                    if bottom and left:
                        return True, 16      # HTBOTTOMLEFT
                    if bottom and right:
                        return True, 17      # HTBOTTOMRIGHT
                    if left:
                        return True, 10      # HTLEFT
                    if right:
                        return True, 11      # HTRIGHT
                    if top:
                        return True, 12      # HTTOP
                    if bottom:
                        return True, 15      # HTBOTTOM
            except Exception:
                # Best-effort - never let a malformed/unexpected native
                # message break the window; just fall through to Qt's
                # own handling below.
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        self.manager.shutdown()
        self.conversion_manager.shutdown()
        super().closeEvent(event)
