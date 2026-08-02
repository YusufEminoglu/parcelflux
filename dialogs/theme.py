"""Palette-aware styling for the ParcelFlux dock.

The dock deliberately derives every surface and text colour from the active Qt
palette.  That keeps it readable under QGIS light, dark, high-contrast, and
custom themes while retaining the ParcelFlux green accent identity.
"""
from __future__ import annotations

from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication, QWidget


def _hex(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexRgb)


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(first.red() * (1.0 - amount) + second.red() * amount),
        round(first.green() * (1.0 - amount) + second.green() * amount),
        round(first.blue() * (1.0 - amount) + second.blue() * amount),
    )


def _contrast_text(background: QColor) -> QColor:
    luminance = (
        0.2126 * background.red()
        + 0.7152 * background.green()
        + 0.0722 * background.blue()
    )
    return QColor("#102019") if luminance >= 150 else QColor("#FFFFFF")


def _relative_luminance(color: QColor) -> float:
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: QColor, second: QColor) -> float:
    """Return the WCAG relative-luminance contrast ratio."""
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def dock_color_tokens(palette: QPalette | None = None) -> dict[str, str]:
    """Return the palette-derived colours used by the dock stylesheet."""
    active = palette or QApplication.palette()
    window = active.color(QPalette.ColorRole.Window)
    base = active.color(QPalette.ColorRole.Base)
    text = active.color(QPalette.ColorRole.WindowText)
    input_text = active.color(QPalette.ColorRole.Text)
    button = active.color(QPalette.ColorRole.Button)
    button_text = active.color(QPalette.ColorRole.ButtonText)
    highlight = active.color(QPalette.ColorRole.Highlight)
    disabled = active.color(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.WindowText,
    )
    dark = window.lightness() < 128

    white = QColor("#FFFFFF")
    accent = QColor("#4CAF50" if dark else "#2E7D32")
    accent_hover = QColor("#66BB6A" if dark else "#1B5E20")
    accent_text = _contrast_text(accent)
    accent_soft = _mix(accent, window, 0.88 if dark else 0.92)

    surface = (
        _mix(window, white, 0.07)
        if dark
        else _mix(window, base, 0.72)
    )
    card = _mix(window, white, 0.12) if dark else _mix(base, window, 0.96)
    input_surface = _mix(base, white, 0.04) if dark else base

    border = _mix(text, surface, 0.76 if dark else 0.84)
    subtle = _mix(text, surface, 0.40 if dark else 0.37)

    status_bg = _mix(surface, accent, 0.13 if dark else 0.06)
    status_border = _mix(text, surface, 0.76 if dark else 0.84)

    warn = QColor("#FFB74D" if dark else "#EF6C00")
    error = QColor("#EF5350" if dark else "#C62828")
    info = QColor("#64B5F6" if dark else "#1565C0")

    if contrast_ratio(input_text, input_surface) < 4.5:
        input_text = QColor(text)
    if contrast_ratio(button_text, button) < 4.5:
        button_text = QColor(text)
    selection = highlight if highlight.isValid() else accent

    return {
        "surface": _hex(surface),
        "card": _hex(card),
        "input_surface": _hex(input_surface),
        "text": _hex(text),
        "input_text": _hex(input_text),
        "button": _hex(button),
        "button_text": _hex(button_text),
        "border": _hex(border),
        "subtle": _hex(subtle),
        "disabled": _hex(disabled),
        "accent": _hex(accent),
        "accent_hover": _hex(accent_hover),
        "accent_text": _hex(accent_text),
        "accent_soft": _hex(accent_soft),
        "status_bg": _hex(status_bg),
        "status_border": _hex(status_border),
        "warn": _hex(warn),
        "error": _hex(error),
        "info": _hex(info),
        "selection": _hex(selection),
    }


def dock_stylesheet(palette: QPalette | None = None) -> str:
    """Build the full dock stylesheet from the current application palette."""
    t = dock_color_tokens(palette)
    return """
QWidget#ParcelFluxRoot {
    background: %(surface)s;
    color: %(text)s;
}
QLabel#PFSubtitle {
    color: %(subtle)s;
    font-size: 9pt;
}
QLabel#PFLayerMeta {
    color: %(subtle)s;
    font-size: 9pt;
}
QLabel#PFCardTitle {
    color: %(accent)s;
    font-size: 11pt;
    font-weight: bold;
}
QLabel#PFCardSubtitle {
    color: %(subtle)s;
    font-size: 9pt;
}
QFrame#PFHeader {
    background-color: %(accent)s;
    border-radius: 6px;
}
QLabel#PFHeaderTitle {
    color: %(accent_text)s;
    font-size: 13pt;
    font-weight: bold;
}
QLabel#PFHeaderSubtitle {
    color: %(accent_text)s;
    font-size: 9pt;
}
QToolButton#PFHelpBtn {
    background: rgba(255,255,255,0.18);
    color: white;
    font-weight: bold;
    border: 1px solid rgba(255,255,255,0.4);
    border-radius: 12px;
    padding: 2px 9px;
}
QToolButton#PFHelpBtn:hover {
    background: rgba(255,255,255,0.30);
}
QTabWidget#PFTabs::pane {
    border: 1px solid %(border)s;
    border-radius: 4px;
    top: -1px;
    background: %(surface)s;
}
QTabBar::tab {
    padding: 6px 16px;
    background: %(card)s;
    border: 1px solid %(border)s;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    font-size: 10pt;
    color: %(subtle)s;
}
QTabBar::tab:selected {
    background: %(surface)s;
    color: %(accent)s;
    font-weight: bold;
}
QTabBar::tab:hover {
    color: %(accent)s;
}
QFrame#PFCard {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
}
QGroupBox {
    background: %(card)s;
    color: %(text)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 9px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 9px;
    padding: 0 5px;
    font-weight: 600;
}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: %(input_surface)s;
    color: %(input_text)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: %(selection)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid %(accent)s;
}
QPushButton {
    background: %(button)s;
    color: %(button_text)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    padding: 7px 11px;
}
QPushButton:hover { border-color: %(accent)s; color: %(accent)s; }
QPushButton:focus { border: 2px solid %(accent)s; }
QPushButton:disabled { color: %(disabled)s; }
QPushButton#PFRunBtn {
    background-color: %(accent)s;
    color: %(accent_text)s;
    font-weight: bold;
    border: none;
    border-radius: 4px;
    padding: 8px 18px;
    font-size: 11pt;
}
QPushButton#PFRunBtn:hover { background-color: %(accent_hover)s; }
QPushButton#PFRunBtn:disabled { background-color: %(border)s; color: %(disabled)s; }
QPushButton#PFRunBtn[busy="true"] {
    background-color: %(error)s;
}
QPushButton#PFResetBtn, QPushButton#PFGhostBtn {
    background: transparent;
    border: 1px solid %(accent)s;
    color: %(accent)s;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton#PFResetBtn:hover, QPushButton#PFGhostBtn:hover {
    background: %(accent_soft)s;
}
QPushButton#PFApplyBtn {
    background-color: %(accent)s;
    color: %(accent_text)s;
    font-weight: bold;
    border: none;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton#PFApplyBtn:hover { background-color: %(accent_hover)s; }
QPushButton#PFDiscardBtn {
    background: transparent;
    border: 1px solid %(border)s;
    color: %(subtle)s;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton#PFDiscardBtn:hover { border-color: %(error)s; color: %(error)s; }
QLabel#PFStatus {
    background: %(status_bg)s;
    border: 1px solid %(status_border)s;
    border-radius: 4px;
    padding: 6px 10px;
    color: %(text)s;
    font-size: 9.5pt;
}
QLabel#PFStatus[severity="info"]  { border-left: 4px solid %(info)s; }
QLabel#PFStatus[severity="ok"]    { border-left: 4px solid %(accent)s; }
QLabel#PFStatus[severity="warn"]  { border-left: 4px solid %(warn)s; }
QLabel#PFStatus[severity="error"] { border-left: 4px solid %(error)s;
                                     background: %(status_bg)s; }
QTextBrowser#PFAboutBody {
    background: transparent;
    border: none;
}
QProgressBar {
    border: 1px solid %(border)s;
    border-radius: 4px;
    background: %(input_surface)s;
    text-align: center;
    height: 16px;
    color: %(text)s;
}
QProgressBar::chunk {
    background-color: %(accent)s;
    border-radius: 3px;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: %(border)s;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QToolTip {
    background: %(card)s;
    color: %(text)s;
    border: 1px solid %(border)s;
    padding: 4px;
}
""" % t


def apply_adaptive_theme(widget: QWidget) -> None:
    """Apply the active QGIS/Qt palette without overriding font preferences."""
    widget.setStyleSheet(dock_stylesheet(widget.palette()))
