# -*- coding: utf-8 -*-
"""Side-by-side comparison of several seeded subdivision results.

Random variation makes one parameter set produce many plausible layouts. Rather
than re-running and re-reading the numbers each time, the gallery renders each
candidate and shows its quality summary, so the operator picks by eye.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)
from qgis.core import (
    QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsRectangle,
)

THUMB = QSize(260, 200)
ACCENT = "#2E7D32"
MUTED = "#616161"


def render_thumbnail(layer, size: QSize = THUMB) -> QPixmap:
    """Render a layer to a pixmap without adding it to the project."""
    blank = QPixmap(size)
    blank.fill(QColor("#FAFAFA"))
    if layer is None or layer.featureCount() == 0:
        return blank

    extent = QgsRectangle(layer.extent())
    if extent.isEmpty():
        return blank
    # A little air around the block reads better than an edge-to-edge fill.
    extent.grow(max(extent.width(), extent.height()) * 0.05)

    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setBackgroundColor(QColor("#FAFAFA"))
    settings.setOutputSize(size)
    settings.setExtent(extent)
    if layer.crs().isValid():
        settings.setDestinationCrs(layer.crs())

    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image: QImage = job.renderedImage()
    if image.isNull():
        return blank
    return QPixmap.fromImage(image)


class _SeedCard(QFrame):
    """One clickable candidate: thumbnail plus the numbers that matter."""

    def __init__(self, index, seed, layer, report, on_pick, parent=None):
        super().__init__(parent)
        self._index = index
        self._on_pick = on_pick
        self._selected = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title = QLabel(f"<b>Seed {seed}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        thumb = QLabel()
        thumb.setPixmap(render_thumbnail(layer))
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(THUMB)
        layout.addWidget(thumb, alignment=Qt.AlignmentFlag.AlignCenter)

        report = report or {}
        flagged = (report.get("flag_oversize", 0)
                   + report.get("flag_undersize", 0)
                   + report.get("flag_narrow", 0))
        stats = (
            f"<span style='color:{MUTED};'>Parcels</span> "
            f"<b>{report.get('parcels', layer.featureCount() if layer else 0)}</b>"
            f" &nbsp;·&nbsp; <span style='color:{MUTED};'>Coverage</span> "
            f"<b>{report.get('coverage_pct', 0):.1f}%</b><br>"
            f"<span style='color:{MUTED};'>Median area</span> "
            f"<b>{report.get('area_median', 0):,.0f} m²</b>"
            f" &nbsp;·&nbsp; <span style='color:{MUTED};'>Frontage</span> "
            f"<b>{report.get('frontage_mean', 0):,.1f} m</b>"
        )
        if flagged:
            stats += (f"<br><span style='color:#EF6C00;'>{flagged} flagged "
                      f"parcel(s)</span>")
        info = QLabel(stats)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        self.setSelected(False)

    def setSelected(self, selected: bool) -> None:  # noqa: N802 - Qt naming
        self._selected = selected
        border = f"2px solid {ACCENT}" if selected else "1px solid #D0D0D0"
        background = "#F1F8E9" if selected else "transparent"
        self.setStyleSheet(
            f"QFrame {{ border: {border}; border-radius: 6px; "
            f"background: {background}; }}"
        )

    def isSelected(self) -> bool:  # noqa: N802 - Qt naming
        return self._selected

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        self._on_pick(self._index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802 - Qt naming
        self._on_pick(self._index, accept=True)
        super().mouseDoubleClickEvent(event)


class SeedGalleryDialog(QDialog):
    """Pick one of several seeded subdivisions."""

    def __init__(self, candidates, iface=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ParcelFlux — Compare seeds")
        self.setModal(True)
        self._candidates = candidates
        self._selected = 0
        self._cards = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Same parameters, different random seeds. Click a layout to select "
            "it, or double-click to select and close.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setSpacing(10)
        for index, (seed, layer, report) in enumerate(candidates):
            card = _SeedCard(index, seed, layer, report, self._pick, self)
            grid.addWidget(card, index // 2, index % 2)
            self._cards.append(card)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use this layout")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._pick(0)

    def _pick(self, index: int, accept: bool = False) -> None:
        self._selected = index
        for position, card in enumerate(self._cards):
            card.setSelected(position == index)
        if accept:
            self.accept()

    def selected_index(self) -> int:
        return self._selected
