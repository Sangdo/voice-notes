"""Themes: every colour, font and radius lives in one Theme; the Qt stylesheet is generated from it.

To add a theme, add a Theme(...) below and list it in THEMES. It appears in Settings automatically.
App code reads colours from `theme.current()` and never hard-codes them, so switching is live.
"""
from dataclasses import dataclass, fields
from string import Template


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    # surfaces, darkest to lightest
    bg: str            # window
    panel: str         # cards: player bar, empty state, table
    panel_alt: str     # alternate table rows
    raised: str        # buttons, menus, tooltips
    hover: str
    pressed: str
    field: str         # text inputs, combo boxes
    line: str          # borders
    line_strong: str   # hovered borders
    dialog_top: str    # dialog background gradient
    dialog_bottom: str
    # text
    text: str
    text_soft: str     # secondary text, model notes
    muted: str         # times, hints, headers
    faint: str         # disabled
    title: str         # headings
    # accent
    accent: str
    accent_hover: str
    accent_pressed: str
    on_accent: str     # text and icons on accent fills
    accent_line: str   # outline of primary buttons
    # states
    current_row: str   # line being played
    selection: str
    match: str         # search hit
    ok: str            # "offline", "ready"
    star_off: str
    # type and shape
    font: str
    transcript_font: str  # the notes table; the court's own typeface by default
    numbers: str       # clock and line times (system font, fixed-width digits applied in app)
    font_size: int
    transcript_size: int
    transcript_spacing: float  # letter spacing in px, like the translator captions
    radius: int        # cards and dialogs
    control_radius: int
    button_radius: int  # 999 → pill
    button_padding: str
    # one colour per speaker label (S1, S2, …), cycled
    speaker_colors: tuple = ('#7cc4fa', '#f5a97f', '#a6da95', '#c6a0f6', '#f5bde6', '#8bd5ca', '#eed49f',
                             '#91d7e3', '#ee99a0', '#b7bdf8', '#e5c890', '#81c8be')


# Source Sans, the typeface of supremecourt.gov (bundled in voice_notes/fonts, SIL Open Font License).
COURT_FONT = "'Source Sans 3', 'Source Sans Pro', 'Apple SD Gothic Neo', 'Segoe UI', 'Malgun Gothic'"

# Matches the translator app (Still): system font, warm yellow accent, pill buttons, soft greys.
STILL = Theme(
    key='still', label='Still (yellow)',
    bg='#161618', panel='#1c1c1e', panel_alt='#1f1f21', raised='#262628', hover='#2f2f32', pressed='#38383b',
    field='#1b1b1d', line='#2e2e31', line_strong='#45454a', dialog_top='#303033', dialog_bottom='#242426',
    text='#f4f4f5', text_soft='#c7c7cb', muted='#a1a1a6', faint='#5c5c61', title='#eeeeef',
    accent='#ffd34b', accent_hover='#ffe17b', accent_pressed='#eac046', on_accent='#28230e', accent_line='#eac046',
    current_row='#2e2914', selection='#3a3420', match='#ffd34b', ok='#30d158', star_off='#4a4a4f',
    font="'.AppleSystemUIFont', 'Apple SD Gothic Neo', 'Segoe UI', 'Malgun Gothic', 'Noto Sans CJK KR'",
    transcript_font=COURT_FONT,
    numbers="'.AppleSystemUIFont', 'Segoe UI'", font_size=13, transcript_size=17, transcript_spacing=0,
    radius=18, control_radius=10, button_radius=17, button_padding='8px 16px',
)

# The original look shared with the video app (Local Subtitle): orange accent, square-ish controls.
CLASSIC = Theme(
    key='classic', label='Classic (orange)',
    bg='#0e0e10', panel='#161618', panel_alt='#151517', raised='#1f1f22', hover='#2a2a2e', pressed='#333338',
    field='#161618', line='#2c2c2f', line_strong='#3a3a3e', dialog_top='#161618', dialog_bottom='#161618',
    text='#ececf0', text_soft='#c7c7cc', muted='#8e8e93', faint='#5a5a5f', title='#ffffff',
    accent='#ff9f0a', accent_hover='#ffb340', accent_pressed='#e08a00', on_accent='#1a1000', accent_line='#ff9f0a',
    current_row='#2a1d05', selection='#3a2a10', match='#ffd60a', ok='#30d158', star_off='#48484d',
    font="'Helvetica Neue', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans CJK KR'",
    transcript_font=COURT_FONT,
    numbers="'Menlo', 'Consolas'", font_size=13, transcript_size=15, transcript_spacing=0,
    radius=10, control_radius=8, button_radius=8, button_padding='7px 14px',
)

THEMES = {t.key: t for t in (STILL, CLASSIC)}
DEFAULT_THEME = STILL.key
_current = THEMES[DEFAULT_THEME]


def current():
    return _current


def use(key):
    """Make `key` the current theme (unknown keys fall back to the default) and return it."""
    global _current
    _current = THEMES.get(key, THEMES[DEFAULT_THEME])
    return _current


_QSS = Template('''
QMainWindow, QWidget { background: $bg; color: $text; font-size: ${font_size}px; font-family: $font; }
QToolTip { background: $raised; color: $text; border: 1px solid $line; padding: 6px 8px; border-radius: 6px; }
QLabel#fileTitle { font-size: 16px; font-weight: 600; color: $title; }
QLabel#title { font-size: 23px; font-weight: 600; color: $title; margin-bottom: 10px; }
QLabel#meta { font-size: 12px; color: $muted; }
QLabel#terminalStatus { color: $muted; font-size: 12px; padding: 4px 2px; }
QLabel#offline { color: $ok; font-size: 11px; }
QLabel#time { color: $muted; font-family: $numbers; font-size: 12px; }

QPushButton { background: $raised; color: $text; border: 1px solid $line; border-radius: ${button_radius}px;
    padding: $button_padding; min-height: 18px; }
QPushButton:hover { background: $hover; border-color: $line_strong; }
QPushButton:pressed { background: $pressed; }
QPushButton:focus { border-color: $accent; }
QPushButton:disabled { color: $faint; background: $panel; border-color: $line; }
QPushButton#ghost, QPushButton#iconButton { background: transparent; border: 1px solid transparent; }
QPushButton#ghost:hover, QPushButton#iconButton:hover { background: $raised; border-color: $line; }
QPushButton#iconButton { padding: 8px; min-width: 20px; border-radius: ${button_radius}px; }
QPushButton#primary { background: $accent; color: $on_accent; border: 1px solid $accent_line; font-weight: 600;
    padding: $button_padding; }
QPushButton#primary:hover { background: $accent_hover; }
QPushButton#primary:pressed { background: $accent_pressed; }
QPushButton#primary:disabled { background: $raised; color: $faint; border-color: $line; }

QComboBox, QLineEdit, QSpinBox { padding: 7px 10px; background: $field; border: 1px solid $line;
    border-radius: ${control_radius}px; selection-background-color: $accent; selection-color: $on_accent; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color: $line_strong; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: $accent; }
QComboBox { combobox-popup: 0; }   /* drop the list below the box instead of the macOS overlay */
QComboBox::drop-down { border: 0; width: 22px; }
QComboBoxPrivateContainer { background: $raised; border: 1px solid $line; border-radius: ${control_radius}px; }
QComboBox QAbstractItemView { background: $raised; color: $text; border: 0; padding: 4px; outline: 0;
    selection-background-color: $accent; selection-color: $on_accent; }
QComboBox QAbstractItemView::item { min-height: 30px; padding: 4px 10px; border-radius: 6px; }
QComboBox QAbstractItemView::item:selected { background: $accent; color: $on_accent; }
QLineEdit#search { padding: 9px 14px; font-size: 14px; border-radius: ${button_radius}px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid $star_off; background: $field; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

QTableWidget { background: $panel; alternate-background-color: $panel_alt; border: 1px solid $line;
    border-radius: ${radius}px; gridline-color: transparent; selection-background-color: $selection;
    selection-color: $text; outline: 0; font-size: ${transcript_size}px; font-family: $transcript_font; }
QTableWidget QLineEdit { background: $raised; border: 1px solid $accent; border-radius: 4px; padding: 2px 4px; }
QHeaderView { background: transparent; }
QHeaderView::section { background: $panel; color: $muted; font-size: 11px; font-weight: 600; padding: 8px;
    border: 0; border-bottom: 1px solid $line; }
QHeaderView::section:vertical { padding: 0; border: 0; font-size: 1px; }  /* hidden, but its height floors every row */
QTableCornerButton::section { background: $panel; border: 0; }

QProgressBar { border: 0; border-radius: 3px; background: $line; max-height: 6px; min-height: 6px;
    text-align: center; color: transparent; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QSlider { background: transparent; }
QSlider::groove:horizontal { height: 4px; background: $line; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $title; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }

QMenuBar { background: $bg; color: $text_soft; }
QMenuBar::item:selected { background: $raised; border-radius: 4px; }
QMenu { background: $raised; border: 1px solid $line; border-radius: ${control_radius}px; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: $accent; color: $on_accent; }
QMenu::separator { height: 1px; background: $line; margin: 4px 8px; }
QDialog { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $dialog_top, stop:1 $dialog_bottom); }
QDialog QLabel, QDialog QCheckBox { background: transparent; }
QMessageBox { background: $dialog_bottom; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $line_strong; min-height: 28px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $line_strong; min-width: 28px; border-radius: 3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QListWidget#models { background: $panel; border: 1px solid $line; border-radius: ${control_radius}px; outline: 0;
    padding: 4px; }
QListWidget#models::item { padding: 8px 10px; border-radius: 8px; }
QListWidget#models::item:selected { background: $selection; color: $text; }
QFrame#playerBar, QFrame#emptyState { background: $panel; border: 1px solid $line; border-radius: ${radius}px; }
QFrame#playerBar QLabel, QFrame#emptyState QLabel, QFrame#playerBar QWidget { background: transparent; }
QWidget#queueRow, QWidget#queueRow QLabel { background: transparent; }
QTextBrowser#summary { background: $panel; border: 1px solid $line; border-radius: ${radius}px; padding: 14px 18px;
    font-family: $transcript_font; font-size: ${transcript_size}px; color: $text; }
''')


def stylesheet(t=None):
    t = t or _current
    return _QSS.substitute({f.name: getattr(t, f.name) for f in fields(t)})


__all__ = ['Theme', 'THEMES', 'DEFAULT_THEME', 'STILL', 'CLASSIC', 'current', 'use', 'stylesheet']
