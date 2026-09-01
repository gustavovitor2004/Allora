"""
documentos/tab_documentos.py

The "Documentos" tab widget: registers two sub-tabs, "Digitalizar"
(document scanner: perspective correction + image enhancement) and
"Converter Formato" (local document conversion). Reuses the same visual
system established in ui.py/theme.py - QFrame#Card (with the dynamic
"status" property that colors its left border), the four QPushButton
variants (#Primary/#Secondary/#Ghost/#Danger), and
QLabel#Dim/#ErrorLabel/#StatusDone/#StatusError - so it inherits the app's
theme (dark/light) automatically through the single app-wide stylesheet
theme.apply_theme() sets, with no Documentos-specific styling needed.
"""

import itertools
import os

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QProgressBar, QFileDialog, QCheckBox,
    QMessageBox, QFrame, QTabWidget, QInputDialog, QLineEdit,
    QRadioButton, QButtonGroup,
)

from documentos import scanner_engine
from documentos import converter as doc_converter
from documentos.workers import ScannerWorker, ConversionWorker
from icons import make_icon
from settings import save_settings
from theme import repolish, theme_colors
from utils import format_size


def _bgr_to_qpixmap(image_bgr: np.ndarray) -> QPixmap:
    """Convert an OpenCV BGR numpy array into a QPixmap for display. Copies
    the QImage explicitly so the pixmap doesn't end up depending on the
    numpy array's buffer staying alive."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    height, width, channels = rgb.shape
    qimage = QImage(rgb.data, width, height, channels * width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class CornerEditor(QWidget):
    """Shows an image with 4 draggable corner handles the user can
    reposition to fine-tune automatic document-edge detection.

    All state (`self.corners`) is kept in ORIGINAL IMAGE pixel coordinates
    - the only thing that depends on this widget's current (scaled,
    letterboxed) size is the paint/hit-test math, computed fresh every time
    so the handles stay correctly aligned across window resizes."""

    HANDLE_RADIUS = 8
    HANDLE_HIT_RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 320)
        self.setMouseTracking(True)
        self.pixmap = None
        self.image_size = (0, 0)   # (w, h) of the ORIGINAL image
        self.corners = []          # 4 (x, y) points in ORIGINAL image space
        self._dragging_index = None

    def set_image(self, path: str, corners=None):
        self.pixmap = QPixmap(path)
        self.image_size = (self.pixmap.width(), self.pixmap.height())
        if corners is not None and len(corners) == 4:
            self.corners = [(float(x), float(y)) for x, y in corners]
        else:
            w, h = self.image_size
            self.corners = [(0, 0), (w, 0), (w, h), (0, h)]
        self._dragging_index = None
        self.update()

    def clear(self):
        self.pixmap = None
        self.image_size = (0, 0)
        self.corners = []
        self._dragging_index = None
        self.update()

    def has_image(self) -> bool:
        return self.pixmap is not None and not self.pixmap.isNull()

    def get_corners(self):
        return list(self.corners)

    # --- coordinate mapping between image space and this widget's space -----

    def _fit_rect(self):
        if not self.has_image():
            return 1.0, 0.0, 0.0
        img_w, img_h = self.image_size
        if img_w <= 0 or img_h <= 0:
            return 1.0, 0.0, 0.0
        widget_w, widget_h = max(self.width(), 1), max(self.height(), 1)
        scale = min(widget_w / img_w, widget_h / img_h)
        offset_x = (widget_w - img_w * scale) / 2
        offset_y = (widget_h - img_h * scale) / 2
        return scale, offset_x, offset_y

    def _to_widget(self, point):
        scale, offset_x, offset_y = self._fit_rect()
        x, y = point
        return offset_x + x * scale, offset_y + y * scale

    def _to_image(self, x, y):
        scale, offset_x, offset_y = self._fit_rect()
        if scale <= 0:
            return 0.0, 0.0
        img_w, img_h = self.image_size
        ix = max(0.0, min((x - offset_x) / scale, img_w))
        iy = max(0.0, min((y - offset_y) / scale, img_h))
        return ix, iy

    # --- painting -------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.has_image():
            painter.setPen(QPen(Qt.gray))
            painter.drawText(self.rect(), Qt.AlignCenter, "Nenhuma imagem selecionada")
            return

        scale, offset_x, offset_y = self._fit_rect()
        img_w, img_h = self.image_size
        target = QRectF(offset_x, offset_y, img_w * scale, img_h * scale)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))

        if len(self.corners) == 4:
            widget_points = [self._to_widget(p) for p in self.corners]
            polygon = QPolygonF([QPointF(x, y) for x, y in widget_points])

            painter.setPen(QPen(Qt.green, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(polygon)

            painter.setBrush(Qt.green)
            painter.setPen(QPen(Qt.darkGreen, 1))
            for x, y in widget_points:
                painter.drawEllipse(QRectF(x - self.HANDLE_RADIUS, y - self.HANDLE_RADIUS,
                                            self.HANDLE_RADIUS * 2, self.HANDLE_RADIUS * 2))

    # --- mouse interaction -----------------------------------------------------

    def _hit_test(self, x, y):
        widget_points = [self._to_widget(p) for p in self.corners]
        for index, (px, py) in enumerate(widget_points):
            if ((px - x) ** 2 + (py - y) ** 2) ** 0.5 <= self.HANDLE_HIT_RADIUS:
                return index
        return None

    def mousePressEvent(self, event):
        if not self.has_image() or len(self.corners) != 4:
            return
        pos = event.position()
        self._dragging_index = self._hit_test(pos.x(), pos.y())

    def mouseMoveEvent(self, event):
        if self._dragging_index is None:
            return
        pos = event.position()
        self.corners[self._dragging_index] = self._to_image(pos.x(), pos.y())
        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging_index = None


class ScannerSubTab(QWidget):
    """Feature 1 - Digitalizar: turn a raw phone-camera photo of a document
    into a clean, perspective-corrected, enhanced scan (color, grayscale,
    or classic black & white "scanner" look) - 100% local via OpenCV, no
    text extraction involved."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.theme_name = settings.theme
        self.current_path = None
        self.original_pixmap = None
        self.result_image = None      # processed BGR numpy array
        self.result_pixmap = None
        self.showing_original = False
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        panels_row = QHBoxLayout()
        panels_row.setSpacing(12)

        # --- left: original photo + draggable corner handles -----------------
        left_card = QFrame()
        left_card.setObjectName("Card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)
        left_layout.addWidget(self._section_label("IMAGEM ORIGINAL"))
        self.corner_editor = CornerEditor()
        left_layout.addWidget(self.corner_editor, stretch=1)
        self.detect_note_label = QLabel("")
        self.detect_note_label.setObjectName("Dim")
        self.detect_note_label.setWordWrap(True)
        left_layout.addWidget(self.detect_note_label)
        panels_row.addWidget(left_card, stretch=1)

        # --- right: processed result ------------------------------------------
        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._section_label("RESULTADO"))
        self.result_label = QLabel("Nenhum resultado ainda")
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setWordWrap(True)
        self.result_label.setMinimumSize(240, 240)
        self.result_label.setStyleSheet("background-color: rgba(255,255,255,15); border-radius: 6px;")
        right_layout.addWidget(self.result_label, stretch=1)

        result_footer = QHBoxLayout()
        self.toggle_view_btn = QPushButton(" Ver original")
        self.toggle_view_btn.setObjectName("Ghost")
        self.toggle_view_btn.setIconSize(QSize(15, 15))
        self.toggle_view_btn.setEnabled(False)
        self.toggle_view_btn.clicked.connect(self.on_toggle_view)
        result_footer.addWidget(self.toggle_view_btn)
        result_footer.addStretch(1)
        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("Dim")
        result_footer.addWidget(self.elapsed_label)
        right_layout.addLayout(result_footer)

        panels_row.addWidget(right_card, stretch=1)
        layout.addLayout(panels_row, stretch=1)

        # --- output mode ------------------------------------------------------
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Modo:"))
        self.mode_group = QButtonGroup(self)
        self.color_radio = QRadioButton("Colorido")
        self.color_radio.setChecked(True)
        self.grayscale_radio = QRadioButton("Escala de cinza")
        self.bw_radio = QRadioButton("Preto e branco (Scanner)")
        for radio in (self.color_radio, self.grayscale_radio, self.bw_radio):
            self.mode_group.addButton(radio)
            mode_row.addWidget(radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # --- select / scan ------------------------------------------------------
        action_row = QHBoxLayout()
        self.select_btn = QPushButton(" Selecionar Imagem")
        self.select_btn.setObjectName("Secondary")
        self.select_btn.setIconSize(QSize(15, 15))
        self.select_btn.clicked.connect(self.on_select_file)
        action_row.addWidget(self.select_btn)

        self.scan_btn = QPushButton(" Digitalizar")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.setIconSize(QSize(15, 15))
        self.scan_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self.on_scan_clicked)
        action_row.addWidget(self.scan_btn)

        action_row.addStretch(1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        action_row.addWidget(self.progress_bar)
        layout.addLayout(action_row)

        # --- save row --------------------------------------------------------
        save_row = QHBoxLayout()
        self.save_jpeg_btn = QPushButton(" JPEG")
        self.save_jpeg_btn.setObjectName("Secondary")
        self.save_jpeg_btn.setIconSize(QSize(14, 14))
        self.save_jpeg_btn.setEnabled(False)
        self.save_jpeg_btn.clicked.connect(lambda: self.on_save_as("jpeg"))
        save_row.addWidget(self.save_jpeg_btn)

        self.save_png_btn = QPushButton(" PNG")
        self.save_png_btn.setObjectName("Secondary")
        self.save_png_btn.setIconSize(QSize(14, 14))
        self.save_png_btn.setEnabled(False)
        self.save_png_btn.clicked.connect(lambda: self.on_save_as("png"))
        save_row.addWidget(self.save_png_btn)

        self.save_pdf_btn = QPushButton(" PDF")
        self.save_pdf_btn.setObjectName("Secondary")
        self.save_pdf_btn.setIconSize(QSize(14, 14))
        self.save_pdf_btn.setEnabled(False)
        self.save_pdf_btn.clicked.connect(lambda: self.on_save_as("pdf"))
        save_row.addWidget(self.save_pdf_btn)

        self.reset_btn = QPushButton(" Processar outra imagem")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.setIconSize(QSize(14, 14))
        self.reset_btn.clicked.connect(self.on_reset)
        save_row.addWidget(self.reset_btn)

        save_row.addStretch(1)
        layout.addLayout(save_row)

        self.set_theme(self.theme_name)

    def set_theme(self, theme_name: str):
        """Recolors every icon-bearing button in this sub-tab - called on
        construction and again whenever MainWindow switches theme (see
        DocumentosTab.set_theme), the same pattern ui.py uses for its own
        icon buttons since QPushButton icons are baked pixmaps QSS can't
        recolor on its own."""
        self.theme_name = theme_name
        colors = theme_colors(theme_name)
        self.toggle_view_btn.setIcon(make_icon("eye", colors["text_secondary"], size=15))
        self.select_btn.setIcon(make_icon("folder", colors["text_primary"], size=15))
        self.scan_btn.setIcon(make_icon("sparkle", colors["accent_ink"], size=15))
        for btn in (self.save_jpeg_btn, self.save_png_btn, self.save_pdf_btn):
            btn.setIcon(make_icon("download", colors["text_primary"], size=14))
        self.reset_btn.setIcon(make_icon("retry", colors["text_secondary"], size=14))

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("SectionLabel")
        return label

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_result_display()

    # ------------------------------------------------------------------
    # File selection + automatic edge detection
    # ------------------------------------------------------------------

    def on_select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar imagem do documento",
            "",
            "Imagens (*.jpg *.jpeg *.png *.bmp *.webp *.tiff)",
        )
        if not path:
            return
        self._load_image(path)

    def _load_image(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.critical(self, "Erro ao abrir imagem", f"Não foi possível abrir:\n{path}")
            return

        self.current_path = path
        self.original_pixmap = pixmap

        corners = None
        try:
            image = scanner_engine.load_image(path)
            corners = scanner_engine.detect_document_corners(image)
        except Exception:
            corners = None

        self.corner_editor.set_image(path, corners)
        if corners is None:
            self.detect_note_label.setText(
                "Bordas não detectadas automaticamente. Ajuste manualmente arrastando os cantos."
            )
        else:
            self.detect_note_label.setText(
                "Bordas detectadas automaticamente. Arraste os pontos para ajustar."
            )

        self.scan_btn.setEnabled(True)
        self.result_image = None
        self.result_pixmap = None
        self.showing_original = False
        self.result_label.setPixmap(QPixmap())
        self.result_label.setText("Nenhum resultado ainda")
        self.toggle_view_btn.setEnabled(False)
        self.toggle_view_btn.setText(" Ver original")
        self.elapsed_label.setText("")
        for btn in (self.save_jpeg_btn, self.save_png_btn, self.save_pdf_btn):
            btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Scan run
    # ------------------------------------------------------------------

    def on_scan_clicked(self):
        if not self.current_path:
            return
        corners = self.corner_editor.get_corners()
        if len(corners) != 4:
            return

        self.scan_btn.setEnabled(False)
        self.select_btn.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.worker = ScannerWorker(self.current_path, corners, self._selected_mode())
        self.worker.finished_ok.connect(self._on_scan_finished)
        self.worker.failed.connect(self._on_scan_failed)
        self.worker.start()

    def _selected_mode(self) -> str:
        if self.grayscale_radio.isChecked():
            return scanner_engine.MODE_GRAYSCALE
        if self.bw_radio.isChecked():
            return scanner_engine.MODE_BW
        return scanner_engine.MODE_COLOR

    def _on_scan_finished(self, image_bgr, elapsed):
        self.result_image = image_bgr
        self.result_pixmap = _bgr_to_qpixmap(image_bgr)
        self.showing_original = False
        self._refresh_result_display()

        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.select_btn.setEnabled(True)
        self.toggle_view_btn.setEnabled(True)
        self.toggle_view_btn.setText(" Ver original")
        for btn in (self.save_jpeg_btn, self.save_png_btn, self.save_pdf_btn):
            btn.setEnabled(True)
        self.elapsed_label.setText(f"Processado em {elapsed:.1f}s")

    def _on_scan_failed(self, message):
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.select_btn.setEnabled(True)
        QMessageBox.critical(self, "Erro ao digitalizar", message)

    # ------------------------------------------------------------------
    # Result preview (before/after toggle)
    # ------------------------------------------------------------------

    def on_toggle_view(self):
        self.showing_original = not self.showing_original
        self.toggle_view_btn.setText(
            " Ver digitalizado" if self.showing_original else " Ver original"
        )
        self._refresh_result_display()

    def _refresh_result_display(self):
        pixmap = self.original_pixmap if self.showing_original else self.result_pixmap
        if pixmap is None or pixmap.isNull():
            return
        scaled = pixmap.scaled(
            self.result_label.width(), self.result_label.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.result_label.setText("")
        self.result_label.setPixmap(scaled)

    # ------------------------------------------------------------------
    # Save / reset
    # ------------------------------------------------------------------

    def on_save_as(self, fmt):
        if self.result_image is None:
            return
        output_dir = self.settings.ocr_output_dir
        # [FIX] Was unguarded: if the configured folder lives on a drive
        # that's since been unplugged or a directory that was deleted, this
        # raised OSError straight onto the GUI thread (unhandled - the
        # button just died). It now fails the same way the rest of this
        # method already reports failures.
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Pasta indisponível",
                f"Não foi possível acessar a pasta de saída:\n{output_dir}\n\n{exc}",
            )
            return

        filters = {
            "jpeg": ("Imagem JPEG (*.jpg *.jpeg)", "jpg"),
            "png": ("Imagem PNG (*.png)", "png"),
            "pdf": ("Documento PDF (*.pdf)", "pdf"),
        }
        filter_str, ext = filters[fmt]
        default_path = os.path.join(output_dir, f"documento_digitalizado.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Salvar como", default_path, filter_str)
        if not path:
            return

        try:
            if fmt == "jpeg":
                scanner_engine.save_as_jpeg(self.result_image, path)
            elif fmt == "png":
                scanner_engine.save_as_png(self.result_image, path)
            else:
                scanner_engine.save_as_pdf(self.result_image, path)
            QMessageBox.information(self, "Salvo", f"Arquivo salvo em:\n{path}")
        except Exception as exc:  # noqa: BLE001 - never crash silently
            QMessageBox.critical(self, "Erro ao salvar", str(exc))

    # [FIX] Added: nothing used to stop this QThread when the app closed.
    # A scan still running at exit meant Qt tearing down a live QThread
    # ("QThread: Destroyed while thread is still running" -> abort), which
    # is a crash on close, not a clean shutdown. Called from
    # DocumentosTab.shutdown() <- MainWindow.closeEvent().
    def shutdown(self):
        worker = self.worker
        if worker is not None and worker.isRunning():
            # Bounded wait: the scan pipeline has no cancellation point, so
            # we give it a moment to land rather than blocking the close
            # forever if it's mid-denoise on a huge photo.
            worker.wait(3000)

    def on_reset(self):
        self.current_path = None
        self.original_pixmap = None
        self.result_image = None
        self.result_pixmap = None
        self.showing_original = False
        self.corner_editor.clear()
        self.detect_note_label.setText("")
        self.result_label.setPixmap(QPixmap())
        self.result_label.setText("Nenhum resultado ainda")
        self.scan_btn.setEnabled(False)
        self.toggle_view_btn.setEnabled(False)
        self.toggle_view_btn.setText(" Ver original")
        self.elapsed_label.setText("")
        for btn in (self.save_jpeg_btn, self.save_png_btn, self.save_pdf_btn):
            btn.setEnabled(False)
        self.color_radio.setChecked(True)


class DocConversionItemWidget(QFrame):
    """One row inside the document-conversion file list."""

    STATUS_WAITING = "waiting"
    STATUS_CONVERTING = "converting"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"
    STATUS_UNSUPPORTED = "unsupported"

    # status_kind -> (display text, QSS objectName for the status label)
    _STATUS_DISPLAY = {
        STATUS_WAITING: ("Aguardando", ""),
        STATUS_CONVERTING: ("Convertendo...", ""),
        STATUS_DONE: ("Concluído ✓", "StatusDone"),
        STATUS_ERROR: ("Erro ✗", "StatusError"),
        STATUS_UNSUPPORTED: ("Não suportado", "Dim"),
    }

    # status_kind -> Card left-border color bucket (see theme.py)
    _CARD_STATUS = {
        STATUS_WAITING: "waiting",
        STATUS_CONVERTING: "active",
        STATUS_DONE: "done",
        STATUS_ERROR: "error",
        STATUS_UNSUPPORTED: "waiting",
    }

    def __init__(self, on_delete=None, theme_name: str = "classic_dark", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.status_kind = self.STATUS_WAITING
        self.theme_name = theme_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # Drag handle (⣿), visible only when merge mode is active - lets
        # the user reorder the pages of the final merged PDF.
        self.handle_label = QLabel("⣿")
        self.handle_label.setObjectName("Dim")
        self.handle_label.setFixedWidth(18)
        self.handle_label.setVisible(False)
        layout.addWidget(self.handle_label)

        self.name_label = QLabel("")
        self.name_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.name_label, stretch=1)

        self.format_label = QLabel("")
        self.format_label.setObjectName("Dim")
        self.format_label.setFixedWidth(60)
        layout.addWidget(self.format_label)

        self.size_label = QLabel("")
        self.size_label.setObjectName("Dim")
        self.size_label.setFixedWidth(90)
        layout.addWidget(self.size_label)

        self.status_label = QLabel("Aguardando")
        self.status_label.setFixedWidth(160)
        layout.addWidget(self.status_label)

        # Compact per-row delete button - reuses the #Danger variant (red,
        # transparent until hover) shared with the rest of the app.
        self.delete_btn = QPushButton()
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setFixedWidth(28)
        self.delete_btn.setIconSize(QSize(13, 13))
        self.delete_btn.setToolTip("Remover da lista")
        if on_delete:
            self.delete_btn.clicked.connect(on_delete)
        layout.addWidget(self.delete_btn)

        self.set_theme(theme_name)

    def set_file(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.name_label.setText(os.path.basename(path))
        self.format_label.setText(os.path.splitext(path)[1].lstrip(".").upper())
        self.size_label.setText(format_size(size))

    def set_status(self, status_kind, detail=""):
        self.status_kind = status_kind
        text, qss_kind = self._STATUS_DISPLAY.get(status_kind, (status_kind, ""))
        if detail:
            text = f"{text} — {detail[:60]}"
        self.status_label.setText(text)
        self.status_label.setObjectName(qss_kind)
        repolish(self.status_label)
        self.setProperty("status", self._CARD_STATUS.get(status_kind, "waiting"))
        repolish(self)
        # Never deletable while actively converting.
        self.delete_btn.setEnabled(status_kind != self.STATUS_CONVERTING)

    def set_merge_mode(self, active: bool):
        self.handle_label.setVisible(active)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        colors = theme_colors(theme_name)
        self.delete_btn.setIcon(make_icon("x", colors["error"], size=13))


class ConvertSubTab(QWidget):
    """Feature 2 - Conversão de Formato: batch-convert images, PDFs, DOCX
    and TXT files locally, in a background QThread."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.theme_name = settings.theme
        self.jobs = []              # list of {"id": int, "path": str}, in display order
        self._item_widgets = {}     # job id -> DocConversionItemWidget
        self._next_job_id = itertools.count(1)
        self._completed = 0
        self._merge_active = False
        self._converting = False
        self._skip_ids = set()      # ids removed from the list during an active run
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.add_btn = QPushButton(" Adicionar arquivos")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setIconSize(QSize(14, 14))
        self.add_btn.clicked.connect(self.on_add_files)
        top_row.addWidget(self.add_btn)

        self.remove_all_btn = QPushButton(" Remover todos")
        self.remove_all_btn.setObjectName("Secondary")
        self.remove_all_btn.setIconSize(QSize(14, 14))
        self.remove_all_btn.clicked.connect(self.on_remove_all)
        top_row.addWidget(self.remove_all_btn)

        top_row.addWidget(QLabel("Converter para:"))
        self.target_combo = QComboBox()
        self.target_combo.setEnabled(False)
        self.target_combo.currentTextChanged.connect(self._on_target_changed)
        top_row.addWidget(self.target_combo)

        top_row.addStretch(1)
        self.convert_btn = QPushButton(" Converter")
        self.convert_btn.setObjectName("Primary")
        self.convert_btn.setIconSize(QSize(14, 14))
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self.on_convert_clicked)
        top_row.addWidget(self.convert_btn)
        layout.addLayout(top_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Salvar em:"))
        self.folder_label = QLabel(self.settings.doc_convert_output_dir)
        self.folder_label.setObjectName("Dim")
        folder_row.addWidget(self.folder_label, stretch=1)
        self.folder_btn = QPushButton(" Escolher")
        self.folder_btn.setObjectName("Secondary")
        self.folder_btn.setIconSize(QSize(14, 14))
        self.folder_btn.clicked.connect(self.on_choose_folder)
        folder_row.addWidget(self.folder_btn)
        layout.addLayout(folder_row)

        # "Mesclar tudo em um único PDF" - appears whenever the destination
        # is PDF, for any mix of supported formats (not just images).
        self.merge_checkbox = QCheckBox("Mesclar tudo em um único PDF")
        self.merge_checkbox.setVisible(False)
        self.merge_checkbox.toggled.connect(self._on_merge_toggled)
        layout.addWidget(self.merge_checkbox)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.file_list = QListWidget()
        self.file_list.setSpacing(4)
        self.file_list.setSelectionMode(QListWidget.NoSelection)
        self.file_list.setFocusPolicy(Qt.NoFocus)
        self.file_list.setDragDropMode(QListWidget.NoDragDrop)
        # Drag-and-drop reordering is only enabled in merge mode.
        self.file_list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.file_list, stretch=1)

        bottom_row = QHBoxLayout()
        self.overall_progress = QProgressBar()
        self.overall_progress.setTextVisible(False)
        bottom_row.addWidget(self.overall_progress, stretch=1)
        self.overall_status_label = QLabel("")
        self.overall_status_label.setObjectName("Dim")
        bottom_row.addWidget(self.overall_status_label)
        layout.addLayout(bottom_row)

        self.open_folder_btn = QPushButton(" Abrir pasta de saída")
        self.open_folder_btn.setObjectName("Secondary")
        self.open_folder_btn.setIconSize(QSize(14, 14))
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        layout.addWidget(self.open_folder_btn)

        self.set_theme(self.theme_name)

    def set_theme(self, theme_name: str):
        """Recolors this sub-tab's icon buttons and cascades to every
        already-added file row - see ScannerSubTab.set_theme for why this
        has to happen explicitly rather than through QSS."""
        self.theme_name = theme_name
        colors = theme_colors(theme_name)
        self.add_btn.setIcon(make_icon("plus", colors["accent_ink"], size=14))
        self.remove_all_btn.setIcon(make_icon("trash", colors["text_primary"], size=14))
        self.convert_btn.setIcon(make_icon("play", colors["accent_ink"], size=14))
        self.folder_btn.setIcon(make_icon("folder", colors["text_primary"], size=14))
        self.open_folder_btn.setIcon(make_icon("folder", colors["text_primary"], size=14))
        for widget in self._item_widgets.values():
            widget.set_theme(theme_name)

    # ------------------------------------------------------------------
    # File selection
    # ------------------------------------------------------------------

    def on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar arquivo(s)",
            "",
            "Documentos (*.jpg *.jpeg *.png *.bmp *.webp *.tiff *.pdf *.docx *.txt)",
        )
        if not paths:
            return
        self.error_label.setVisible(False)
        existing_paths = {job["path"] for job in self.jobs}
        for path in paths:
            if path in existing_paths:
                continue
            _, category = doc_converter.detect_format(path)
            if category is None:
                continue
            self._add_job(path)
            existing_paths.add(path)
        self._refresh_target_options()
        self._refresh_progress_display()

    def _add_job(self, path):
        # Each row has a stable id (not indexed by list position), so
        # deleting/reordering never desyncs progress tracking for other rows.
        job_id = next(self._next_job_id)
        self.jobs.append({"id": job_id, "path": path})

        widget = DocConversionItemWidget(
            on_delete=lambda _checked=False, jid=job_id: self._on_delete_clicked(jid),
            theme_name=self.theme_name,
        )
        widget.set_file(path)
        widget.set_merge_mode(self.merge_checkbox.isChecked())

        list_item = QListWidgetItem()
        list_item.setSizeHint(QSize(0, 50))
        list_item.setData(Qt.UserRole, job_id)
        self.file_list.addItem(list_item)
        self.file_list.setItemWidget(list_item, widget)
        self._item_widgets[job_id] = widget
        return job_id

    def on_remove_all(self):
        if self._converting:
            return
        self.jobs = []
        self._item_widgets = {}
        self.file_list.clear()
        self._refresh_target_options()
        self._refresh_progress_display()

    def _on_delete_clicked(self, job_id):
        widget = self._item_widgets.get(job_id)
        if widget is None:
            return
        if widget.status_kind == DocConversionItemWidget.STATUS_CONVERTING:
            return  # defensive - the button is already disabled in this state
        if self._converting:
            # File not yet reached by the worker: flag it to be skipped.
            self._skip_ids.add(job_id)
        self._remove_job_row(job_id)
        if not self._converting:
            self._refresh_target_options()
        self._refresh_progress_display()

    def _remove_job_row(self, job_id):
        self.jobs = [job for job in self.jobs if job["id"] != job_id]
        self._item_widgets.pop(job_id, None)
        for row in range(self.file_list.count()):
            list_item = self.file_list.item(row)
            if list_item.data(Qt.UserRole) == job_id:
                self.file_list.takeItem(row)
                break

    def _on_rows_moved(self, *_args):
        # Resyncs self.jobs' order after a drag-and-drop reorder.
        by_id = {job["id"]: job for job in self.jobs}
        ordered_ids = [self.file_list.item(row).data(Qt.UserRole) for row in range(self.file_list.count())]
        self.jobs = [by_id[jid] for jid in ordered_ids if jid in by_id]

    def _refresh_target_options(self):
        self.target_combo.blockSignals(True)
        previous_target = self.target_combo.currentText()
        self.target_combo.clear()

        if not self.jobs:
            self.target_combo.setEnabled(False)
            self.convert_btn.setEnabled(False)
            self.target_combo.blockSignals(False)
            self._on_target_or_jobs_changed()
            return

        # The dropdown shows any format valid for AT LEAST ONE file in the
        # list (a union, not an intersection). Files that don't support the
        # chosen target are marked "Não suportado" and skipped automatically
        # during conversion instead of blocking the whole batch.
        union = set()
        for job in self.jobs:
            ext, _ = doc_converter.detect_format(job["path"])
            union |= set(doc_converter.available_targets(ext))

        # "PDF" is always a valid destination, even when the whole list is
        # already PDFs (a same-format list wouldn't otherwise contribute
        # "pdf" to the union above, since a PDF never lists itself as a
        # target) - this is also what makes "Mesclar tudo em um único PDF"
        # available in that scenario, since it's only shown when the
        # selected target is PDF.
        union.add("pdf")
        options = sorted(union)
        if "pdf" in options:
            # PDF gets priority: always the first option, and therefore the
            # one selected by default when files are first added.
            options.remove("pdf")
            options.insert(0, "pdf")

        if options:
            self.target_combo.addItems([f.upper() for f in options])
            self.target_combo.setEnabled(True)
            self.convert_btn.setEnabled(True)
            self.error_label.setVisible(False)
            idx = self.target_combo.findText(previous_target)
            self.target_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.target_combo.setEnabled(False)
            self.convert_btn.setEnabled(False)
            self.error_label.setText("Nenhum formato de destino disponível para os arquivos selecionados.")
            self.error_label.setVisible(True)

        self.target_combo.blockSignals(False)
        self._on_target_or_jobs_changed()

    def _on_target_changed(self, _text):
        self._on_target_or_jobs_changed()

    def _on_target_or_jobs_changed(self):
        self._update_merge_visibility()
        self._update_supported_status()

    def _update_merge_visibility(self):
        # Merging accepts any mix of supported formats, so it only depends
        # on the chosen target being PDF.
        target_is_pdf = self.target_combo.currentText().upper() == "PDF"
        self.merge_checkbox.setVisible(target_is_pdf)
        if not target_is_pdf:
            self.merge_checkbox.setChecked(False)

    def _update_supported_status(self):
        # Marks "Não suportado" on rows whose file can't reach the
        # currently selected target format.
        if self._converting:
            return  # don't touch statuses while a run is in progress
        target_ext = self.target_combo.currentText().lower()
        if not target_ext:
            return
        for job in self.jobs:
            widget = self._item_widgets.get(job["id"])
            if not widget:
                continue
            ext, _ = doc_converter.detect_format(job["path"])
            if doc_converter.can_convert(ext, target_ext):
                widget.set_status(DocConversionItemWidget.STATUS_WAITING)
            else:
                widget.set_status(DocConversionItemWidget.STATUS_UNSUPPORTED)

    def _on_merge_toggled(self, checked):
        # Drag-and-drop reordering is only active in merge mode.
        self.file_list.setDragDropMode(QListWidget.InternalMove if checked else QListWidget.NoDragDrop)
        for widget in self._item_widgets.values():
            widget.set_merge_mode(checked)

    def on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Escolher pasta de destino", self.settings.doc_convert_output_dir
        )
        if folder:
            self.settings.doc_convert_output_dir = folder
            self.folder_label.setText(folder)
            save_settings(self.settings)

    # ------------------------------------------------------------------
    # Conversion run
    # ------------------------------------------------------------------

    def on_convert_clicked(self):
        if not self.jobs or not self.target_combo.currentText():
            return
        target_ext = self.target_combo.currentText().lower()
        output_dir = self.settings.doc_convert_output_dir
        # [FIX] Same unguarded os.makedirs as ScannerSubTab.on_save_as - an
        # output folder on a removed drive raised OSError on the GUI thread
        # and the Converter button silently did nothing. Reported through
        # this tab's existing error label instead.
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            self.error_label.setText(f"Não foi possível acessar a pasta de saída: {exc}")
            self.error_label.setVisible(True)
            return

        self._merge_active = self.merge_checkbox.isVisible() and self.merge_checkbox.isChecked()

        # Password checks for encrypted PDFs happen inside the background
        # worker (see _on_password_requested) - not here, since opening a
        # real-world PDF to check its encryption status can take a while
        # for large/unusual files, and doing that on the GUI thread would
        # freeze the window.

        output_name = None
        if self._merge_active:
            name, ok = QInputDialog.getText(
                self, "Nome do arquivo mesclado", "Salvar PDF mesclado como:",
                text="documento_mesclado.pdf",
            )
            if not ok:
                return
            output_name = name.strip() or "documento_mesclado.pdf"
            if not output_name.lower().endswith(".pdf"):
                output_name += ".pdf"

        self._converting = True
        self._completed = 0
        self._skip_ids = set()
        self.convert_btn.setEnabled(False)
        self.remove_all_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.open_folder_btn.setEnabled(False)

        if self._merge_active:
            # The whole list locks during merging (it's one atomic
            # operation over the entire list, not per-file) - set_status
            # (CONVERTING) already disables each row's delete button.
            for job in self.jobs:
                widget = self._item_widgets.get(job["id"])
                if widget:
                    widget.set_status(DocConversionItemWidget.STATUS_CONVERTING)
            self.overall_progress.setRange(0, 1)
            self.overall_progress.setValue(0)
            self.overall_status_label.setText("Mesclando...")
        else:
            for job in self.jobs:
                widget = self._item_widgets.get(job["id"])
                if widget:
                    widget.set_status(DocConversionItemWidget.STATUS_WAITING)
            self._refresh_progress_display()

        job_snapshot = [(job["id"], job["path"]) for job in self.jobs]
        self.worker = ConversionWorker(
            job_snapshot, output_dir, target_ext,
            merge=self._merge_active, skip_ids=self._skip_ids, output_name=output_name,
        )
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.file_skipped.connect(self._on_file_skipped)
        self.worker.merge_finished.connect(self._on_merge_finished)
        self.worker.all_finished.connect(self._on_all_finished)
        # Blocking connection: when the worker hits a password-protected
        # PDF, this (GUI) thread shows the password dialog and only then
        # hands control back to the worker - the window never freezes
        # waiting, since it's the worker that waits, never the GUI.
        self.worker.password_requested.connect(
            self._on_password_requested, Qt.ConnectionType.BlockingQueuedConnection
        )
        self.worker.start()

    def _on_password_requested(self, path):
        # Runs on the GUI thread, called (via BlockingQueuedConnection) by
        # the worker when it hits, DURING the merge, a password-protected
        # PDF. self.worker.password_response is read by the worker right
        # after this method returns.
        password, ok = QInputDialog.getText(
            self, "Senha necessária",
            f"O arquivo \"{os.path.basename(path)}\" está protegido por senha.\n"
            "Digite a senha para incluí-lo na mesclagem:",
            QLineEdit.EchoMode.Password,
        )
        self.worker.password_response = password if ok else None

    def _on_file_started(self, job_id):
        widget = self._item_widgets.get(job_id)
        if widget:
            widget.set_status(DocConversionItemWidget.STATUS_CONVERTING)

    def _on_file_skipped(self, job_id, _reason):
        widget = self._item_widgets.get(job_id)
        if widget:
            widget.set_status(DocConversionItemWidget.STATUS_UNSUPPORTED)
        self._bump_progress()

    def _on_file_finished(self, job_id, success, message):
        widget = self._item_widgets.get(job_id)
        if widget:
            if success:
                widget.set_status(DocConversionItemWidget.STATUS_DONE)
            else:
                widget.set_status(DocConversionItemWidget.STATUS_ERROR, message)
        self._bump_progress()

    def _on_merge_finished(self, success, message):
        # On success, the whole list collapses into a single row
        # representing the final generated PDF.
        if success:
            self.file_list.clear()
            self._item_widgets = {}
            self.jobs = []
            job_id = self._add_job(message)
            self._item_widgets[job_id].set_status(DocConversionItemWidget.STATUS_DONE)
            self.overall_progress.setRange(0, 1)
            self.overall_progress.setValue(1)
            self.overall_status_label.setText(f"PDF mesclado gerado: {os.path.basename(message)}")
        else:
            for job in self.jobs:
                widget = self._item_widgets.get(job["id"])
                if widget:
                    widget.set_status(DocConversionItemWidget.STATUS_ERROR, message)
            self.overall_status_label.setText(f"Erro ao mesclar: {message[:120]}")

    def _bump_progress(self):
        self._completed += 1
        self._refresh_progress_display()

    def _refresh_progress_display(self):
        # Centralizes the overall-progress calculation, including the case
        # where the user deletes a file (the bar reflects the remaining
        # count both before and during an active run).
        total = len(self.jobs)
        self.overall_progress.setMaximum(max(total, 1))
        if self._converting and not self._merge_active:
            shown = min(self._completed, total)
            self.overall_progress.setValue(shown)
            self.overall_status_label.setText(f"{shown} de {total} arquivos convertidos")
        elif not self._converting:
            self.overall_progress.setValue(0)
            self.overall_status_label.setText("")

    def _on_all_finished(self):
        self._converting = False
        self.convert_btn.setEnabled(True)
        self.remove_all_btn.setEnabled(True)
        self.add_btn.setEnabled(True)
        self.target_combo.setEnabled(True)
        self.open_folder_btn.setEnabled(True)

    def on_open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.doc_convert_output_dir))

    # [FIX] Added - see ScannerSubTab.shutdown() for why. Extra wrinkle
    # here: this worker can be blocked inside a BlockingQueuedConnection
    # waiting on the GUI thread for a PDF password, so an unbounded wait()
    # from the GUI thread would deadlock outright. The bounded wait can't.
    def shutdown(self):
        worker = self.worker
        if worker is not None and worker.isRunning():
            try:
                worker.password_requested.disconnect(self._on_password_requested)
            except (RuntimeError, TypeError):
                pass  # never connected, or already torn down
            worker.wait(3000)


class DocumentosTab(QWidget):
    """Top-level widget registered as the "Documentos" tab in MainWindow."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.theme_name = settings.theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.sub_tabs = QTabWidget()
        self.scanner_tab = ScannerSubTab(settings)
        self.convert_tab = ConvertSubTab(settings)
        self.sub_tabs.addTab(self.scanner_tab, "Digitalizar")
        self.sub_tabs.addTab(self.convert_tab, "Converter Formato")
        layout.addWidget(self.sub_tabs)

        self.set_theme(self.theme_name)

    def set_theme(self, theme_name: str):
        """Cascades a theme change into both sub-tabs and recolors the
        sub-tab bar's own icons - called from MainWindow._refresh_icon_theme
        alongside every other icon-bearing part of the app."""
        self.theme_name = theme_name
        colors = theme_colors(theme_name)
        self.scanner_tab.set_theme(theme_name)
        self.convert_tab.set_theme(theme_name)
        self.sub_tabs.setTabIcon(0, make_icon("search", colors["text_secondary"], size=15))
        self.sub_tabs.setTabIcon(1, make_icon("convert", colors["text_secondary"], size=15))

    # [FIX] Added: MainWindow.closeEvent() shut down the download and
    # conversion managers but knew nothing about this tab's QThreads, so a
    # scan or document conversion running at exit was torn down live.
    def shutdown(self):
        self.scanner_tab.shutdown()
        self.convert_tab.shutdown()
