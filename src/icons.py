"""
icons.py

Small stroke-style SVG icon set, rendered to QIcon at request time. Replaces
the emoji glyphs the UI used to lean on (\U0001f7e5 \U0001f3ac \U0001f4c1 ▶ etc.) - emoji render
inconsistently across Windows font configurations and read as placeholder
art rather than a designed interface.

Each entry in PATHS is raw SVG path/shape markup on a 24x24 viewBox, drawn
stroke-only (fill="none") unless FILLED_ICONS says otherwise (solid shapes
like the play triangle). Colors are baked into the SVG string at render
time rather than left as CSS "currentColor" - QSvgRenderer only implements
static SVG, it doesn't resolve CSS custom properties or inherited paint
context, so each call renders its own fully-colored copy.
"""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "download": '<path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/><path d="M12 4v10"/><path d="M8 11l4 4 4-4"/>',
    "convert": '<path d="M4 4v5h5"/><path d="M20 20v-5h-5"/><path d="M5.2 9A7.5 7.5 0 0 1 18.5 7"/><path d="M18.8 15A7.5 7.5 0 0 1 5.5 17"/>',
    "document": '<path d="M7 3.2h6.2L18 8v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4.2a1 1 0 0 1 1-1z"/><path d="M13.2 3.2V8H18"/><path d="M9 13h6M9 16.4h6"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M4.6 4.6l1.7 1.7M17.7 17.7l1.7 1.7M2.5 12h2.4M19.1 12h2.4M4.6 19.4l1.7-1.7M17.7 6.3l1.7-1.7"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M2.5 12h3M18.5 12h3M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7.3a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v7.4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.3z"/>',
    "play": '<path d="M8 5v14l11-7z"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    "trash": '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>',
    "retry": '<path d="M4 4v5h5"/><path d="M20 20v-5h-5"/><path d="M5.5 9A7 7 0 0 1 18 7.5"/><path d="M18.5 15A7 7 0 0 1 6 16.5"/>',
    "upload-cloud": '<path d="M20.5 9a3.5 3.5 0 0 0-3.4-3.5A5.5 5.5 0 0 0 7 7.5a4.3 4.3 0 0 0 .6 8.4"/><path d="M12 11.5v8M9.2 14.3l2.8-2.8 2.8 2.8"/>',
    "film": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h9A1.5 1.5 0 0 1 15 6.5v11A1.5 1.5 0 0 1 13.5 19h-9A1.5 1.5 0 0 1 3 17.5v-11z"/><path d="M15 9.5l6-3v11l-6-3"/>',
    "music": '<path d="M9 18V6l9-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="15" cy="16" r="3"/>',
    "image": '<rect x="4" y="4" width="16" height="16" rx="2"/><circle cx="9.5" cy="9.5" r="1.5"/><path d="M5 17l4.5-4.5L12 15l3-3 4 5"/>',
    "palette": (
        '<path d="M12 3a9 9 0 1 0 9 9c0-1.1-.9-1.9-2-1.9h-1.8a2.7 2.7 0 0 1-2.7-2.7c0-.7.3-1.4.8-1.9.5-.6.2-1.6-.6-1.8A9 9 0 0 0 12 3z"/>'
        '<circle cx="7.3" cy="10.8" r="0.9"/><circle cx="9.6" cy="7" r="0.9"/><circle cx="14.6" cy="7" r="0.9"/>'
    ),
    # Custom title-bar window controls (see MainWindow's frameless-window
    # setup in ui.py) - drawn thin/small since they sit in a 32px-tall strip.
    "win-minimize": '<path d="M5 12h14"/>',
    "win-maximize": '<rect x="5.5" y="5.5" width="13" height="13" rx="1"/>',
    "win-restore": '<rect x="7.5" y="7.5" width="11" height="11" rx="1"/><path d="M7.5 5.5h9a2 2 0 0 1 2 2v9"/>',
    "win-close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "help-circle": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M9.1 9.3a2.9 2.9 0 0 1 5.6 1c0 1.9-2.5 2-2.6 3.7"/><path d="M12 17.3h.01"/>'
    ),
    # Documentos tab (scanner + converter) - replaces the emoji glyphs
    # ("\U0001f50d \U0001f504 ✨ \U0001f441 \U0001f4be") that used to stand in for these actions.
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L20 20"/>',
    "eye": (
        '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "sparkle": (
        '<path d="M12 3l1.6 4.6L18 9l-4.4 1.4L12 15l-1.6-4.6L6 9l4.4-1.4L12 3z"/>'
        '<path d="M19 14l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1z"/>'
    ),
    # App mark: the "Allora A" - two bold triangular legs sharing an apex,
    # same silhouette as the app icon baked into the .exe (see build_exe.ps1
    # --icon and assets/icon.ico) so the in-app header badge and the
    # taskbar/window icon read as the same logo.
    "logo-a": '<path d="M12 4L7.2 20L10.3 20z M12 4L16.8 20L13.7 20z"/>',
}

FILLED_ICONS = {"play", "logo-a"}


def make_icon(name: str, color: str, size: int = 18, stroke_width: float = 1.8) -> QIcon:
    """Render one of PATHS as a QIcon, stroked (or filled, for FILLED_ICONS)
    in the given CSS color. Rendered at 2x and given a device pixel ratio of
    2 so it stays crisp on the high-DPI displays most Windows laptops run."""
    inner = PATHS[name]
    if name in FILLED_ICONS:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}" stroke="none">{inner}</svg>'
    else:
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
            f'stroke-linejoin="round">{inner}</svg>'
        )

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


def make_badge(name: str, bg_color: str, icon_color: str, diameter: int = 34, icon_size: int = 17) -> QIcon:
    """A filled rounded-square badge with a centered icon - used for the app
    logo mark and per-category thumbnails (file-type icon in a tinted box).
    Renders the background and the icon's SVG into the same physical-pixel
    canvas in one pass (rather than compositing two separately-scaled
    QIcon pixmaps), so there's no risk of the icon looking soft from a
    mismatched device pixel ratio between the two."""
    scale = 2
    side = diameter * scale
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(bg_color)))
    radius = side * 0.28
    painter.drawRoundedRect(QRectF(0, 0, side, side), radius, radius)

    inner = PATHS[name]
    if name in FILLED_ICONS:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{icon_color}" stroke="none">{inner}</svg>'
    else:
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{icon_color}" stroke-width="1.9" stroke-linecap="round" '
            f'stroke-linejoin="round">{inner}</svg>'
        )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    icon_side = icon_size * scale
    offset = (side - icon_side) / 2
    renderer.render(painter, QRectF(offset, offset, icon_side, icon_side))
    painter.end()

    pixmap.setDevicePixelRatio(float(scale))
    return QIcon(pixmap)
