"""Light and dark themes: one palette per theme feeds the stylesheet, waveform and floating pill."""

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

PREFERENCES = ("system", "light", "dark")
PREFERENCE_LABELS = {"system": "Match Windows", "light": "Light", "dark": "Dark"}

# Warm neutrals with the terracotta accent in both themes.
LIGHT = {
    "text": "#292724", "bg": "#faf9f6", "sidebar": "#eeede8", "sidebar_border": "#deddd7",
    "panel": "#f3f2ee", "panel_border": "#e4e2db", "card": "#ffffff", "card_border": "#e4e2db",
    "muted": "#6b675f", "status_fg": "#925137", "status_bg": "#f5e5db",
    "chip_ok_fg": "#35602a", "chip_ok_bg": "#e1eddb", "chip_warn_fg": "#925137", "chip_warn_bg": "#f5e5db",
    "chip_off_fg": "#5f5b53", "chip_off_bg": "#ebeae4", "scroll_handle": "#cfcabd",
    "notice_bg": "#fbf0df", "notice_border": "#efd7b0", "notice_text": "#6b4311",
    "btn_bg": "#ffffff", "btn_border": "#dcd9d1", "btn_hover_bg": "#f1eee7", "btn_hover_border": "#c0baad",
    "btn_pressed": "#e7e1d6", "btn_dis_fg": "#9a968e", "btn_dis_bg": "#f2f0eb", "btn_dis_border": "#e5e2db",
    "primary": "#b6553a", "primary_hover": "#a24a31", "primary_dis": "#dbb5a7", "primary_fg": "#ffffff",
    "primary_dis_fg": "#ffffff", "input_bg": "#ffffff", "input_border": "#dbd8d0", "input_focus": "#b66b4f",
    "selection": "#edcbb7", "tab_fg": "#6b675f", "tab_sel_fg": "#a94e32", "list_sel": "#dfddd5",
    "list_hover": "#e6e3db", "menu_bg": "#faf9f6", "menu_border": "#dad6cd", "menu_sel": "#ece6dc",
    "tip_bg": "#292724", "tip_fg": "#ffffff",
    "wave_idle": "#e4dfd5", "wave_active": "#be5b3d",
}

DARK = {
    "text": "#ece8e1", "bg": "#1b1a18", "sidebar": "#151413", "sidebar_border": "#2a2825",
    "panel": "#22201e", "panel_border": "#302d29", "card": "#242220", "card_border": "#34312c",
    "muted": "#9b958a", "status_fg": "#eaa184", "status_bg": "#3a2a22",
    "chip_ok_fg": "#a8d49a", "chip_ok_bg": "#26351f", "chip_warn_fg": "#eaa184", "chip_warn_bg": "#3a2a22",
    "chip_off_fg": "#a29c91", "chip_off_bg": "#2e2c28", "scroll_handle": "#4a463f",
    "notice_bg": "#3a2e1d", "notice_border": "#5b4524", "notice_text": "#f0d4a2",
    "btn_bg": "#2b2926", "btn_border": "#3d3935", "btn_hover_bg": "#353230", "btn_hover_border": "#55504a",
    "btn_pressed": "#3f3b37", "btn_dis_fg": "#6a655c", "btn_dis_bg": "#1f1e1c", "btn_dis_border": "#2b2926",
    "primary": "#b4532f", "primary_hover": "#a14826", "primary_dis": "#5a3b2e", "primary_fg": "#ffffff",
    "primary_dis_fg": "#b99d90", "input_bg": "#1c1b19", "input_border": "#3d3935", "input_focus": "#d2704f",
    "selection": "#5a3b2c", "tab_fg": "#9b958a", "tab_sel_fg": "#ee9a78", "list_sel": "#34312c",
    "list_hover": "#2b2926", "menu_bg": "#22201e", "menu_border": "#3d3935", "menu_sel": "#34312c",
    "tip_bg": "#ece8e1", "tip_fg": "#1b1a18",
    "wave_idle": "#34312c", "wave_active": "#d2704f",
}

# The floating pill paints itself, so it has its own small palette.
CAPSULE_LIGHT = {"body": "#f8f5ef", "border": "#d7d0c3", "ink": "#292724", "hover": "#ece6dc",
                 "pressed": "#e2dacb", "divider": "#e4ded2", "accent": "#be5b3d", "record": "#d9382c",
                 "shadow": "#292724", "shadow_alpha": 11}
CAPSULE_DARK = {"body": "#262422", "border": "#3b3733", "ink": "#ece8e1", "hover": "#33302c",
                "pressed": "#3d3934", "divider": "#3a3632", "accent": "#d2704f", "record": "#ef5a4d",
                "shadow": "#000000", "shadow_alpha": 38}

_TEMPLATE = Template("""
QWidget { color: $text; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, #canvas, QMessageBox, QDialog { background: $bg; }
QMessageBox QLabel { color: $text; }
#sidebar { background: $sidebar; border-right: 1px solid $sidebar_border; }
#settings { background: $panel; border: 1px solid $panel_border; border-radius: 14px; }
#card { background: $card; border: 1px solid $card_border; border-radius: 14px; }
#brand { font-size: 19px; font-weight: 700; letter-spacing: -0.5px; }
#heading { font-size: 29px; font-weight: 650; letter-spacing: -0.7px; }
#eyebrow { color: $muted; font-size: 11px; font-weight: 650; }
#muted { color: $muted; }
#status { color: $status_fg; background: $status_bg; border-radius: 11px; padding: 5px 12px; font-weight: 600; }
#time { font-size: 43px; font-weight: 600; letter-spacing: -1px; }
#chip { border-radius: 11px; padding: 5px 12px; font-size: 11px; font-weight: 650; }
#chip[tone="ok"] { color: $chip_ok_fg; background: $chip_ok_bg; }
#chip[tone="warn"] { color: $chip_warn_fg; background: $chip_warn_bg; }
#chip[tone="off"] { color: $chip_off_fg; background: $chip_off_bg; }
#settingsScroll, #settingsInner { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 1px; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 3px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
#notice { background: $notice_bg; border: 1px solid $notice_border; border-radius: 12px; }
#noticeText { color: $notice_text; font-weight: 600; }
QPushButton { background: $btn_bg; border: 1px solid $btn_border; border-radius: 8px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: $btn_hover_bg; border-color: $btn_hover_border; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton:disabled { color: $btn_dis_fg; background: $btn_dis_bg; border-color: $btn_dis_border; }
QPushButton#primary { background: $primary; border-color: $primary; color: $primary_fg; }
QPushButton#primary:hover { background: $primary_hover; }
QPushButton#primary:disabled { background: $primary_dis; border-color: $primary_dis; color: $primary_dis_fg; }
QLineEdit, QComboBox { background: $input_bg; border: 1px solid $input_border; border-radius: 7px; padding: 8px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid $input_focus; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $menu_bg; border: 1px solid $menu_border; selection-background-color: $menu_sel; selection-color: $text; outline: none; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QTextBrowser { background: $card; border: none; padding: 12px; font-size: 14px; selection-background-color: $selection; selection-color: $text; }
QTabWidget::pane { border: none; background: $card; }
QTabBar::tab { background: transparent; color: $tab_fg; padding: 13px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $tab_sel_fg; border-bottom: 2px solid $primary; }
QListWidget { border: none; background: transparent; outline: none; }
QListWidget::item { padding: 12px 8px; border-radius: 7px; margin-bottom: 3px; }
QListWidget::item:selected { background: $list_sel; color: $text; }
QListWidget::item:hover { background: $list_hover; }
QMenu { background: $menu_bg; padding: 6px; border: 1px solid $menu_border; }
QMenu::item { padding: 8px 20px; border-radius: 4px; }
QMenu::item:selected { background: $menu_sel; }
QToolTip { background: $tip_bg; color: $tip_fg; border: none; padding: 5px; }
""")


def palette(theme: str) -> dict:
    return DARK if theme == "dark" else LIGHT


def style_sheet(theme: str) -> str:
    return _TEMPLATE.substitute(palette(theme))


def capsule_colors(theme: str) -> dict:
    """Floating-pill colours as QColor objects (it paints itself instead of using the stylesheet)."""
    source = CAPSULE_DARK if theme == "dark" else CAPSULE_LIGHT
    colors = {name: QColor(value) for name, value in source.items() if name != "shadow_alpha"}
    colors["shadow"].setAlpha(source["shadow_alpha"])
    return colors


def normalize(preference) -> str:
    return preference if preference in PREFERENCES else "system"


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark


def resolve(preference: str) -> str:
    """The theme to draw: an explicit choice, or whatever Windows is currently using."""
    preference = normalize(preference)
    if preference == "system":
        return "dark" if system_is_dark() else "light"
    return preference


def apply_color_scheme(preference: str) -> None:
    """Make Qt itself (title bar, native dialogs, default palette) follow the choice."""
    hints = QGuiApplication.styleHints()
    if hints is None:
        return
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(normalize(preference),
                                                                               Qt.ColorScheme.Unknown)
    hints.setColorScheme(scheme)
