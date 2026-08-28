"""
theme.py

Centralized theme system for Allora: a gallery of complete, independently
designed themes (not just one palette with a light/dark switch) - each one
carries its own colors, fonts, corner radius and border weight, and a couple
of structural flags (tab style, accent-on-accent text color) that change how
the stylesheet itself is built, not just which colors fill it in.

`apply_theme()` is the single entry point the rest of the app calls to push
a theme's stylesheet onto the whole QApplication - never set an inline
stylesheet on a widget just to theme it. `repolish()` is the tiny helper
every widget that recolors itself via a dynamic property (a Pill's
"variant", a Card's "status") must call after changing that property - Qt
caches style results per widget and won't notice the property change on its
own.
"""

from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Theme gallery
# ---------------------------------------------------------------------------
# Every theme carries the same set of keys, so build_stylesheet() never has
# to special-case a theme by name - only by these declared traits:
#
#   font_body / font_display   - font_display is used for the header title
#                                 only, everything else uses font_body. Both
#                                 are Windows-safe system fonts (no bundled
#                                 font files, no internet fetch - the app has
#                                 to look right offline on a bare Windows box).
#   radius_card/button/pill    - corner radius in px. A near-0 radius reads
#                                 as "sharp/technical", 100 on radius_pill
#                                 makes a true pill; a small radius_pill (2-4)
#                                 makes the same badge read as a squared tag
#                                 instead, which several of these themes want.
#   border_width                - 1px for most themes, thicker for the
#                                 poster themes' bold-outline look.
#   tab_style                  - "underline" (accent line under the active
#                                 tab, transparent otherwise) or "blocked"
#                                 (the active tab is a solid filled block,
#                                 like a real tab you'd pull out of a folder).
#   accent_ink                  - text color placed ON TOP of a solid accent
#                                 fill (Primary button, blocked active tab).
#                                 White reads fine on most accents; the
#                                 brighter/lighter ones (amber, yellow, matrix
#                                 green) need dark ink instead.
#   uppercase_tracking          - letter-spacing (px) added to the small
#                                 uppercase section labels ("FILA DE
#                                 DOWNLOADS") - a couple of themes want that
#                                 pushed further for a more "printed" feel.

THEMES = {
    "classic_dark": {
        "label": "Clássico — Escuro",
        "is_light": False,
        "bg_primary": "#0b0b0d", "bg_secondary": "#111113", "bg_surface": "#17171a",
        "bg_surface2": "#1d1d21", "bg_hover": "#202024", "bg_active": "#26262b",
        "accent": "#e63946", "accent_hover": "#c1121f", "accent_soft": "rgba(230, 57, 70, 0.14)",
        "accent_ink": "#ffffff",
        "success": "#2dc653", "success_soft": "rgba(45, 198, 83, 0.14)",
        "error": "#e63946", "error_soft": "rgba(230, 57, 70, 0.14)",
        "text_primary": "#f5f5f4", "text_secondary": "#9c9ca4", "text_tertiary": "#68686f",
        "text_disabled": "#4d4d52",
        "border": "rgba(255, 255, 255, 0.07)", "border_strong": "rgba(255, 255, 255, 0.14)",
        "font_body": '"Segoe UI"', "font_display": '"Segoe UI"',
        "radius_card": 12, "radius_button": 8, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "classic_light": {
        "label": "Clássico — Claro",
        "is_light": True,
        "bg_primary": "#faf8f6", "bg_secondary": "#ffffff", "bg_surface": "#f1eeeb",
        "bg_surface2": "#e7e2dd", "bg_hover": "#ece7e2", "bg_active": "#e2dcd6",
        "accent": "#d62828", "accent_hover": "#a31621", "accent_soft": "rgba(214, 40, 40, 0.10)",
        "accent_ink": "#ffffff",
        "success": "#2d6a4f", "success_soft": "rgba(45, 106, 79, 0.12)",
        "error": "#d62828", "error_soft": "rgba(214, 40, 40, 0.10)",
        "text_primary": "#211d1a", "text_secondary": "#6b625a", "text_tertiary": "#948a80",
        "text_disabled": "#b9b0a7",
        "border": "rgba(30, 20, 10, 0.09)", "border_strong": "rgba(30, 20, 10, 0.16)",
        "font_body": '"Segoe UI"', "font_display": '"Segoe UI"',
        "radius_card": 12, "radius_button": 8, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "terminal": {
        "label": "Terminal Utility",
        "is_light": False,
        "bg_primary": "#0d0d0c", "bg_secondary": "#131311", "bg_surface": "#161614",
        "bg_surface2": "#1c1c19", "bg_hover": "#232320", "bg_active": "#2a2a26",
        "accent": "#ffb000", "accent_hover": "#e09e00", "accent_soft": "rgba(255, 176, 0, 0.14)",
        "accent_ink": "#0d0d0c",
        "success": "#8fd14f", "success_soft": "rgba(143, 209, 79, 0.14)",
        "error": "#ff6b4a", "error_soft": "rgba(255, 107, 74, 0.14)",
        "text_primary": "#eae6dc", "text_secondary": "#8f897c", "text_tertiary": "#5c574c",
        "text_disabled": "#403c34",
        "border": "rgba(255, 255, 255, 0.08)", "border_strong": "rgba(255, 255, 255, 0.16)",
        "font_body": "Consolas", "font_display": "Consolas",
        "radius_card": 0, "radius_button": 0, "radius_pill": 2, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "paper_light": {
        "label": "Paper & Ink",
        "is_light": True,
        "bg_primary": "#eef0e9", "bg_secondary": "#f6f7f1", "bg_surface": "#f6f7f1",
        "bg_surface2": "#e2e5d9", "bg_hover": "#e8ebe0", "bg_active": "#dde1d3",
        "accent": "#2f6f62", "accent_hover": "#255a4f", "accent_soft": "rgba(47, 111, 98, 0.12)",
        "accent_ink": "#f6f7f1",
        "success": "#b8862e", "success_soft": "rgba(184, 134, 46, 0.14)",
        "error": "#b2452f", "error_soft": "rgba(178, 69, 47, 0.12)",
        "text_primary": "#242220", "text_secondary": "#6b6a5f", "text_tertiary": "#8c8a7c",
        "text_disabled": "#b3b1a3",
        "border": "#d9dccf", "border_strong": "#c7cabb",
        "font_body": "Constantia", "font_display": "Georgia",
        "radius_card": 8, "radius_button": 8, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 0,
    },
    "paper_dark": {
        "label": "Paper & Ink — Noite",
        "is_light": False,
        "bg_primary": "#161d19", "bg_secondary": "#1a221d", "bg_surface": "#1e2620",
        "bg_surface2": "#232c25", "bg_hover": "#283128", "bg_active": "#2d372e",
        "accent": "#4fa393", "accent_hover": "#3f8a7c", "accent_soft": "rgba(79, 163, 147, 0.16)",
        "accent_ink": "#0f1512",
        "success": "#e0a94a", "success_soft": "rgba(224, 169, 74, 0.16)",
        "error": "#e0755a", "error_soft": "rgba(224, 117, 90, 0.14)",
        "text_primary": "#eef0e9", "text_secondary": "#9ba299", "text_tertiary": "#7a8079",
        "text_disabled": "#4a5049",
        "border": "#2a332c", "border_strong": "#384038",
        "font_body": "Constantia", "font_display": "Georgia",
        "radius_card": 8, "radius_button": 8, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 0,
    },
    "poster_light": {
        "label": "Poster Maximalista",
        "is_light": True,
        "bg_primary": "#ffffff", "bg_secondary": "#ffffff", "bg_surface": "#ffffff",
        "bg_surface2": "#f0f0f0", "bg_hover": "#f4f4f4", "bg_active": "#e8e8e8",
        "accent": "#2f4bff", "accent_hover": "#2038d1", "accent_soft": "#f4e409",
        "accent_ink": "#ffffff",
        "success": "#111111", "success_soft": "#f4e409",
        "error": "#d1272c", "error_soft": "rgba(209, 39, 44, 0.12)",
        "text_primary": "#111111", "text_secondary": "#555555", "text_tertiary": "#888888",
        "text_disabled": "#bbbbbb",
        "border": "#111111", "border_strong": "#111111",
        "font_body": "Segoe UI", "font_display": "Impact",
        "radius_card": 0, "radius_button": 0, "radius_pill": 0, "border_width": 3,
        "tab_style": "blocked", "uppercase_tracking": 1,
    },
    "poster_dark": {
        "label": "Poster Maximalista — Noite",
        "is_light": False,
        "bg_primary": "#0d0d0d", "bg_secondary": "#0d0d0d", "bg_surface": "#0d0d0d",
        "bg_surface2": "#1c1c1c", "bg_hover": "#181818", "bg_active": "#242424",
        "accent": "#4a63ff", "accent_hover": "#3349e6", "accent_soft": "#f4e409",
        "accent_ink": "#0d0d0d",
        "success": "#f5f5f5", "success_soft": "#f4e409",
        "error": "#ff5a5f", "error_soft": "rgba(255, 90, 95, 0.16)",
        "text_primary": "#f5f5f5", "text_secondary": "#9a9a9a", "text_tertiary": "#6f6f6f",
        "text_disabled": "#3f3f3f",
        "border": "#f5f5f5", "border_strong": "#f5f5f5",
        "font_body": "Segoe UI", "font_display": "Impact",
        "radius_card": 0, "radius_button": 0, "radius_pill": 0, "border_width": 3,
        "tab_style": "blocked", "uppercase_tracking": 1,
    },
    "glass": {
        "label": "Console de Vidro",
        "is_light": False,
        "bg_primary": "#0b0d14", "bg_secondary": "#0e1119", "bg_surface": "rgba(255, 255, 255, 0.025)",
        "bg_surface2": "rgba(255, 255, 255, 0.05)", "bg_hover": "rgba(255, 255, 255, 0.06)",
        "bg_active": "rgba(255, 255, 255, 0.09)",
        "accent": "#8b5cf6", "accent_hover": "#7c4de0", "accent_soft": "rgba(139, 92, 246, 0.14)",
        "accent_ink": "#0b0d14",
        "success": "#5ee6d0", "success_soft": "rgba(94, 230, 208, 0.14)",
        "error": "#ff6b81", "error_soft": "rgba(255, 107, 129, 0.14)",
        "text_primary": "#e7e9f2", "text_secondary": "#8188a1", "text_tertiary": "#565a70",
        "text_disabled": "#33364a",
        "border": "rgba(255, 255, 255, 0.08)", "border_strong": "rgba(255, 255, 255, 0.14)",
        "font_body": "Segoe UI", "font_display": "Segoe UI Semibold",
        "radius_card": 12, "radius_button": 10, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "sunset": {
        "label": "Pôr do Sol",
        "is_light": False,
        "bg_primary": "#1c1512", "bg_secondary": "#211915", "bg_surface": "#241a15",
        "bg_surface2": "#33261f", "bg_hover": "#2b2019", "bg_active": "#382a22",
        "accent": "#ff7a59", "accent_hover": "#e6663f", "accent_soft": "rgba(255, 122, 89, 0.14)",
        "accent_ink": "#1c1512",
        "success": "#e8b04b", "success_soft": "rgba(232, 176, 75, 0.14)",
        "error": "#ff6161", "error_soft": "rgba(255, 97, 97, 0.14)",
        "text_primary": "#f3e9e2", "text_secondary": "#c9b8ac", "text_tertiary": "#8a7c72",
        "text_disabled": "#544940",
        "border": "#33261f", "border_strong": "#43332a",
        "font_body": "Calibri", "font_display": "Calibri",
        "radius_card": 16, "radius_button": 100, "radius_pill": 100, "border_width": 1,
        "tab_style": "pill", "uppercase_tracking": 0,
    },
    "ice": {
        "label": "Precisão Gelo",
        "is_light": False,
        "bg_primary": "#10151c", "bg_secondary": "#141a22", "bg_surface": "#141a22",
        "bg_surface2": "#1a2129", "bg_hover": "#1e2630", "bg_active": "#232c37",
        "accent": "#3ecfd6", "accent_hover": "#2fb3ba", "accent_soft": "rgba(62, 207, 214, 0.14)",
        "accent_ink": "#0a1014",
        "success": "#5fd68a", "success_soft": "rgba(95, 214, 138, 0.14)",
        "error": "#ff6b6b", "error_soft": "rgba(255, 107, 107, 0.14)",
        "text_primary": "#dbe4ec", "text_secondary": "#7f8ea1", "text_tertiary": "#4d5b6e",
        "text_disabled": "#2c3743",
        "border": "#1e2733", "border_strong": "#263241",
        "font_body": "Segoe UI", "font_display": "Segoe UI Semibold",
        "radius_card": 6, "radius_button": 5, "radius_pill": 4, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "matrix": {
        "label": "Matrix",
        "is_light": False,
        "bg_primary": "#000000", "bg_secondary": "#020402", "bg_surface": "#050a05",
        "bg_surface2": "#0a140a", "bg_hover": "#0f1c0f", "bg_active": "#142614",
        "accent": "#00ff41", "accent_hover": "#00cc34", "accent_soft": "rgba(0, 255, 65, 0.12)",
        "accent_ink": "#000000",
        "success": "#39ff6a", "success_soft": "rgba(57, 255, 106, 0.12)",
        "error": "#ff3b3b", "error_soft": "rgba(255, 59, 59, 0.14)",
        "text_primary": "#39ff6a", "text_secondary": "#1fae4a", "text_tertiary": "#146b30",
        "text_disabled": "#0b3d1a",
        "border": "rgba(0, 255, 65, 0.18)", "border_strong": "rgba(0, 255, 65, 0.32)",
        "font_body": "Consolas", "font_display": "Consolas",
        "radius_card": 0, "radius_button": 0, "radius_pill": 2, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 1,
    },
    "conductor": {
        "label": "Mesa do Maestro",
        "is_light": False,
        "bg_primary": "#0c0c0c", "bg_secondary": "#111111", "bg_surface": "#141414",
        "bg_surface2": "#1e1e1e", "bg_hover": "#1e1e1e", "bg_active": "#242424",
        "accent": "#c9a84c", "accent_hover": "#b8944a", "accent_soft": "rgba(201, 168, 76, 0.14)",
        "accent_ink": "#0c0c0c",
        "success": "#5d8a5d", "success_soft": "rgba(93, 138, 93, 0.16)",
        "error": "#b85c4a", "error_soft": "rgba(184, 92, 74, 0.16)",
        "text_primary": "#e8e4df", "text_secondary": "#948f88", "text_tertiary": "#6b6b6b",
        "text_disabled": "#3a3a3a",
        "border": "rgba(255, 255, 255, 0.06)", "border_strong": "rgba(255, 255, 255, 0.12)",
        "font_body": "Segoe UI", "font_display": "Cambria",
        "radius_card": 10, "radius_button": 8, "radius_pill": 100, "border_width": 1,
        "tab_style": "underline", "uppercase_tracking": 2,
    },
}

DEFAULT_THEME = "classic_dark"

# config.json written before this multi-theme picker existed only ever
# stored "dark"/"light" - map those forward so nobody's saved preference
# silently resets to the default on first launch after the update.
_LEGACY_ALIASES = {"dark": "classic_dark", "light": "classic_light"}


def normalize_theme_name(name: str) -> str:
    name = _LEGACY_ALIASES.get(name, name)
    return name if name in THEMES else DEFAULT_THEME

# Status values understood by the QLabel#Pill[status=...] selectors below.
# Every queue/file-list row widget sets one of these via
# `widget.setProperty("status", ...)` + `repolish(widget)`.
STATUS_WAITING = "waiting"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def theme_names() -> list:
    """Ordered (name, label) pairs for populating a theme picker."""
    return [(name, t["label"]) for name, t in THEMES.items()]


def theme_colors(theme_name: str) -> dict:
    """Expose the raw theme dict so non-stylesheet code (icons drawn via
    QPainter/QSvgRenderer, which don't go through QSS at all) can pick
    matching colors instead of hardcoding hex values of their own."""
    return THEMES[normalize_theme_name(theme_name)]


def build_stylesheet(theme_name: str) -> str:
    c = THEMES[normalize_theme_name(theme_name)]
    bw = c["border_width"]
    r_card = c["radius_card"]
    r_btn = c["radius_button"]
    r_pill = c["radius_pill"]
    tracking = c["uppercase_tracking"]

    if c["tab_style"] == "blocked":
        tab_css = f"""
    QTabBar::tab {{
        background-color: {c['bg_surface2']};
        color: {c['text_primary']};
        padding: 12px 18px;
        margin-right: 0px;
        border: {bw}px solid {c['border']};
        border-bottom: none;
        font-size: 10pt;
        font-weight: 700;
    }}
    QTabBar::tab:selected {{
        background-color: {c['accent_soft']};
        color: {c['text_primary']};
    }}
    QTabBar::tab:hover {{
        background-color: {c['bg_hover']};
    }}"""
    elif c["tab_style"] == "pill":
        tab_css = f"""
    QTabBar::tab {{
        background-color: transparent;
        color: {c['text_secondary']};
        padding: 10px 18px;
        margin-right: 6px;
        border: none;
        border-radius: 100px;
        font-size: 10pt;
        font-weight: 700;
    }}
    QTabBar::tab:selected {{
        background-color: {c['accent']};
        color: {c['accent_ink']};
    }}
    QTabBar::tab:hover {{
        color: {c['text_primary']};
    }}"""
    else:  # underline
        tab_css = f"""
    QTabBar::tab {{
        background-color: transparent;
        color: {c['text_secondary']};
        padding: 12px 16px;
        margin-right: 4px;
        border: none;
        border-bottom: 3px solid transparent;
        font-size: 10pt;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {c['text_primary']};
        border-bottom: 3px solid {c['accent']};
    }}
    QTabBar::tab:hover {{
        color: {c['text_primary']};
    }}"""

    return f"""
    QWidget {{
        background-color: {c['bg_primary']};
        color: {c['text_primary']};
        font-family: {c['font_body']};
        font-size: 10pt;
    }}
    QMainWindow {{
        background-color: {c['bg_primary']};
    }}

    /* --- Header ------------------------------------------------------- */
    QFrame#Header {{
        background-color: {c['bg_secondary']};
        border-bottom: {bw}px solid {c['border']};
    }}
    QLabel#HeaderTitle {{
        font-family: {c['font_display']};
        font-size: 15pt;
        font-weight: 700;
        color: {c['text_primary']};
    }}
    QLabel#SectionLabel {{
        font-size: 9pt;
        font-weight: 700;
        letter-spacing: {tracking}px;
        color: {c['text_secondary']};
    }}

    /* --- Custom title-bar window controls (minimize/maximize/close) ------ */
    /* The Header doubles as the window's title bar on a frameless
       MainWindow (see ui.py) - these three buttons replace the OS-drawn
       ones, so they're styled to sit flush against the header instead of
       looking like a regular toolbar button. */
    QPushButton#WinBtn {{
        background-color: transparent;
        border: none;
        border-radius: 4px;
        padding: 0px;
    }}
    QPushButton#WinBtn:hover {{
        background-color: {c['bg_hover']};
    }}
    QPushButton#WinBtn:pressed {{
        background-color: {c['bg_active']};
    }}
    QPushButton#WinBtn[kind="close"]:hover {{
        background-color: #e81123;
    }}
    QPushButton#WinBtn[kind="close"]:pressed {{
        background-color: #f1707a;
    }}

    /* --- Sidebar navigation ---------------------------------------------- */
    QFrame#Sidebar {{
        background-color: {c['bg_secondary']};
        border-right: {bw}px solid {c['border']};
    }}
    QFrame#NavItem {{
        background-color: transparent;
        border-radius: {r_btn}px;
    }}
    QFrame#NavItem:hover {{
        background-color: {c['bg_hover']};
    }}
    QFrame#NavItem[active="true"] {{
        background-color: {c['accent_soft']};
    }}
    QLabel#NavItemText {{
        font-size: 10pt;
        font-weight: 600;
        color: {c['text_secondary']};
    }}
    QLabel#NavItemText[active="true"] {{
        color: {c['text_primary']};
    }}

    /* --- Cards / queue rows -------------------------------------------- */
    QFrame#Card {{
        background-color: {c['bg_surface']};
        border-radius: {r_card}px;
        border: {bw}px solid {c['border']};
    }}
    QFrame#Card[status="waiting"] {{ border-left: 3px solid {c['border']}; }}
    QFrame#Card[status="active"]  {{ border-left: 3px solid {c['accent']}; }}
    QFrame#Card[status="done"]    {{ border-left: 3px solid {c['success']}; }}
    QFrame#Card[status="error"]   {{ border-left: 3px solid {c['error']}; }}

    /* --- Inputs --------------------------------------------------------- */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
        background-color: {c['bg_surface']};
        border: {bw}px solid {c['border_strong']};
        border-radius: {r_btn}px;
        padding: 7px 10px;
        color: {c['text_primary']};
        selection-background-color: {c['accent']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border: {bw}px solid {c['accent']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QCheckBox {{
        color: {c['text_primary']};
    }}

    /* --- Buttons: 4 distinct variants ------------------------------------ */
    QPushButton {{
        background-color: {c['bg_surface']};
        color: {c['text_primary']};
        border: {bw}px solid {c['border_strong']};
        border-radius: {r_btn}px;
        padding: 8px 18px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {c['bg_hover']};
    }}
    QPushButton:pressed {{
        background-color: {c['bg_active']};
    }}
    QPushButton:disabled {{
        background-color: {c['bg_surface']};
        color: {c['text_disabled']};
        border-color: {c['border']};
    }}

    QPushButton#Primary {{
        background-color: {c['accent']};
        color: {c['accent_ink']};
        font-weight: 700;
        border: {bw}px solid {c['accent']};
    }}
    QPushButton#Primary:hover {{
        background-color: {c['accent_hover']};
    }}
    QPushButton#Primary:disabled {{
        background-color: {c['bg_surface']};
        color: {c['text_disabled']};
    }}

    QPushButton#Secondary {{
        background-color: {c['bg_surface2']};
        color: {c['text_primary']};
        border: {bw}px solid {c['border_strong']};
    }}
    QPushButton#Secondary:hover {{
        background-color: {c['bg_hover']};
    }}

    QPushButton#Ghost {{
        background-color: transparent;
        color: {c['text_secondary']};
        border: {bw}px solid {c['border']};
        font-weight: 600;
    }}
    QPushButton#Ghost:hover {{
        background-color: {c['bg_surface']};
        color: {c['text_primary']};
    }}

    QPushButton#Danger {{
        background-color: transparent;
        color: {c['error']};
        border: {bw}px solid transparent;
        border-radius: 6px;
    }}
    QPushButton#Danger:hover {{
        background-color: {c['error_soft']};
        border: {bw}px solid {c['error']};
    }}

    QPushButton#IconGhost {{
        background-color: transparent;
        border: none;
        border-radius: 8px;
        padding: 0px;
    }}
    QPushButton#IconGhost:hover {{
        background-color: {c['bg_surface2']};
    }}
    QPushButton#IconGhost:pressed {{
        background-color: {c['bg_active']};
    }}

    /* --- Status pills: small colored badges (queue/file row state) -------- */
    QLabel#Pill {{
        border-radius: {r_pill}px;
        padding: 4px 11px;
        font-size: 8.5pt;
        font-weight: 700;
    }}
    QLabel#Pill[variant="accent"] {{
        background-color: {c['accent_soft']};
        color: {c['accent']};
    }}
    QLabel#Pill[variant="success"] {{
        background-color: {c['success_soft']};
        color: {c['success']};
    }}
    QLabel#Pill[variant="neutral"] {{
        background-color: {c['bg_surface2']};
        color: {c['text_secondary']};
    }}

    /* --- Lists / scroll areas --------------------------------------------- */
    QListWidget {{
        background-color: transparent;
        border: none;
    }}
    QScrollArea {{
        border: none;
    }}

    /* --- Progress bar ------------------------------------------------------- */
    QProgressBar {{
        background-color: {c['bg_surface2']};
        border-radius: 3px;
        text-align: center;
        color: {c['text_primary']};
        height: 6px;
    }}
    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 3px;
    }}
    QProgressBar[status="done"]::chunk {{
        background-color: {c['success']};
    }}

    /* --- Status/caption labels --------------------------------------------- */
    QLabel#ErrorLabel {{ color: {c['error']}; }}
    QLabel#StatusDone {{ color: {c['success']}; }}
    QLabel#StatusError {{ color: {c['error']}; }}
    QLabel#Dim {{ color: {c['text_secondary']}; font-size: 8pt; }}
    QLabel#Faint {{ color: {c['text_tertiary']}; font-size: 8pt; }}

    /* --- Tab bar ------------------------------------------------------------ */
    QTabWidget::pane {{
        border: none;
        background-color: {c['bg_primary']};
    }}
    QTabBar {{
        background-color: {c['bg_primary']};
    }}
    {tab_css}

    /* --- Drag-and-drop zone ------------------------------------------------- */
    QFrame#DropZone {{
        background-color: {c['bg_surface']};
        border: 2px dashed {c['border_strong']};
        border-radius: {r_card}px;
    }}
    QFrame#DropZone:hover {{
        border: 2px dashed {c['accent']};
    }}

    /* --- Logo / thumbnail badges ------------------------------------------- */
    QLabel#Thumb {{
        background-color: {c['bg_surface2']};
        border-radius: {max(r_btn - 2, 0)}px;
    }}
    """


def apply_theme(app: QApplication, theme_name: str) -> None:
    """The single place that pushes a stylesheet onto the whole
    application. Call this once (on startup, and again whenever the user
    changes theme) - never set an inline stylesheet on a widget just to
    theme it."""
    app.setStyleSheet(build_stylesheet(theme_name))


def repolish(widget) -> None:
    """Force a widget to re-evaluate its stylesheet after a dynamic
    property (e.g. Pill's "variant") changed - Qt caches style results per
    widget and won't notice the property change on its own."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
