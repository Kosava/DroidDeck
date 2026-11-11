"""Teme za DroidDeck — tamna (podrazumevana) i svetla."""

# boje statusa su iste u obe teme
GREEN = "#3ecf6e"
RED = "#e5534b"
YELLOW = "#e3b341"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9eff"

DARK = {
    "BG": "#1b1d22",
    "PANEL": "#25282f",
    "BORDER": "#33363f",
    "TEXT": "#e6e6e6",
    "MUTED": "#8b8f99",
    "LOG_BG": "#121418",
}

LIGHT = {
    "BG": "#f3f4f7",
    "PANEL": "#ffffff",
    "BORDER": "#d6d9e0",
    "TEXT": "#1d2026",
    "MUTED": "#6b7080",
    "LOG_BG": "#eceef2",
}


def build_qss(p: dict) -> str:
    BG, PANEL, BORDER = p["BG"], p["PANEL"], p["BORDER"]
    TEXT, MUTED, LOG_BG = p["TEXT"], p["MUTED"], p["LOG_BG"]
    return f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 14px;
}}
QFrame#bar {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#bar QLabel {{
    background: transparent;
}}
QLabel#placeholder {{
    color: {MUTED};
    font-size: 15px;
}}
QLabel#muted {{
    color: {MUTED};
    padding: 0 4px;
}}
QLineEdit {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QListWidget#grid {{
    background: {BG};
    border: none;
    outline: none;
}}
QListWidget#grid::item {{
    border-radius: 10px;
    padding: 6px;
    color: {TEXT};
}}
QListWidget#grid::item:hover {{
    background: {PANEL};
}}
QListWidget#grid::item:selected {{
    background: {BORDER};
    color: {TEXT};
}}
QCheckBox {{
    color: {MUTED};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {MUTED};
    border-radius: 4px;
    background: {PANEL};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QPlainTextEdit#log {{
    background: {LOG_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: monospace;
    font-size: 12px;
    color: {MUTED};
    padding: 6px;
}}
QDialog {{
    background: {BG};
}}
QProgressBar {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    font-size: 11px;
    font-weight: bold;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}
QStatusBar {{
    color: {MUTED};
}}
QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 10px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {ACCENT};
    color: white;
}}
QMenu::item:disabled {{
    color: {MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 6px;
}}
QToolTip {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QPushButton, QToolButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
}}
QToolButton::menu-indicator {{
    image: none;
}}
QToolButton#nav {{
    padding: 6px 12px;
    font-size: 15px;
    min-width: 22px;
}}
QToolButton:disabled {{
    color: {MUTED};
}}
QPushButton:hover, QToolButton:hover {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {MUTED};
    border-color: {BORDER};
}}
QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: white;
    font-weight: bold;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background: {BORDER};
    color: {MUTED};
}}
"""


DARK_QSS = build_qss(DARK)
LIGHT_QSS = build_qss(LIGHT)


def qss_for(dark: bool) -> str:
    return DARK_QSS if dark else LIGHT_QSS
