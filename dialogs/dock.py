# -*- coding: utf-8 -*-
"""ParcelFlux dock widget — tabbed UI: Run / Guide / About."""
from __future__ import annotations

import os
from contextlib import suppress

from qgis.PyQt.QtCore import QEvent, Qt, QThread, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

from .theme import apply_adaptive_theme

# Default parameter set — used by the Reset button
DEFAULTS = {
    "lot_width": 16.0,
    "min_area": 300.0,
    "max_area": 2000.0,
    "merge_threshold": 35.0,
    "uniform_corners": True,
    "width_variation": 0.0,
    "fishbone_offset": 0.0,
    "row_width_asymmetry": 0.0,
    "hline_offset": 0.0,
    "min_frontage": 0.0,
    "parcel_depth": 0.0,
    "corner_widening": 0.0,
    "seed": 42,
    "add_id_fields": True,
    "add_facade_fields": True,
    "selected_only": False,
    "output_name": "ParcelFlux Parcels",
}

_ORPHANED_WORKERS = []


class _SliderSpin(QWidget):
    """Slider + DoubleSpinBox pair that stay in sync. Compact, frame-less."""

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum, maximum, default, step=1.0, suffix="", decimals=1,
                 tooltip="", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._scale = 10 ** decimals
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(int(minimum * self._scale))
        self.slider.setMaximum(int(maximum * self._scale))
        self.slider.setSingleStep(int(step * self._scale))
        self.slider.setPageStep(int(step * self._scale * 5))
        self.slider.setValue(int(default * self._scale))
        self.slider.setToolTip(tooltip)

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setValue(default)
        self.spin.setSuffix(f" {suffix}" if suffix else "")
        self.spin.setMinimumWidth(90)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setToolTip(tooltip)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)

        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin, 0)

    def _on_slider(self, raw):
        v = raw / self._scale
        if abs(self.spin.value() - v) > 1e-9:
            self.spin.blockSignals(True)
            self.spin.setValue(v)
            self.spin.blockSignals(False)
        self.valueChanged.emit(v)

    def _on_spin(self, v):
        raw = int(round(v * self._scale))
        if self.slider.value() != raw:
            self.slider.blockSignals(True)
            self.slider.setValue(raw)
            self.slider.blockSignals(False)
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v: float) -> None:
        self.spin.setValue(v)

    def setEnabled(self, enabled: bool) -> None:  # noqa: D401
        self.slider.setEnabled(enabled)
        self.spin.setEnabled(enabled)


class _Card(QFrame):
    """Soft-edged card container for a parameter group."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("PFCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(6)
        head = QLabel(f"<b>{title}</b>")
        head.setObjectName("PFCardTitle")
        outer.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("PFCardSubtitle")
            sub.setWordWrap(True)
            outer.addWidget(sub)
        self._content = QWidget()
        self._lyt = QFormLayout(self._content)
        self._lyt.setContentsMargins(0, 4, 0, 0)
        self._lyt.setSpacing(8)
        self._lyt.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self._content)

    def addRow(self, label, widget):
        self._lyt.addRow(label, widget)

    def addWidget(self, widget):
        self._lyt.addRow(widget)


_CORE_PARAM_KEYS = (
    'lot_width', 'min_area', 'max_area', 'merge_threshold', 'uniform_corners',
    'width_variation', 'fishbone_offset', 'row_width_asymmetry', 'hline_offset',
    'min_frontage', 'parcel_depth', 'corner_widening',
    'add_id_fields', 'add_facade_fields', 'selected_only',
)


class _WorkerThread(QThread):
    progress = pyqtSignal(int, str)
    # result_layer(s), error, report(s). Lists when running a seed comparison.
    completed = pyqtSignal(object, object, object)
    cancelled = pyqtSignal()

    def __init__(self, runner, params, seeds=None, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.params = params
        # A single seed produces one layer; a seed list produces one per seed
        # so the operator can compare alternatives before committing.
        self.seeds = list(seeds) if seeds else [params['seed']]
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self.requestInterruption()

    def run(self):
        layers = []
        reports = []
        try:
            for index, seed in enumerate(self.seeds):
                if self._is_cancel_requested():
                    self.cancelled.emit()
                    return
                self._seed_index = index
                call = {key: self.params[key] for key in _CORE_PARAM_KEYS}
                result, error = self.runner.run(
                    input_layer=self.params['input_layer'],
                    seed=seed,
                    progress_callback=self._emit_progress,
                    cancel_callback=self._is_cancel_requested,
                    **call,
                )
                if error:
                    self.completed.emit(None, error, None)
                    return
                layers.append(result)
                reports.append(dict(self.runner.report))
        except Exception as exc:  # last-line safety net
            import traceback
            if self._cancel_requested or self.isInterruptionRequested():
                self.cancelled.emit()
                return
            self.completed.emit(None, f"{exc}\n{traceback.format_exc()}", None)
            return
        if self._cancel_requested or self.isInterruptionRequested():
            self.cancelled.emit()
            return
        if len(self.seeds) == 1:
            self.completed.emit(layers[0], None, reports[0])
        else:
            self.completed.emit(layers, None, reports)

    def _emit_progress(self, pct: int, msg: str) -> None:
        if self._cancel_requested or self.isInterruptionRequested():
            return
        count = len(self.seeds)
        if count > 1:
            # Spread each run's 0-100 across its slice of the overall bar.
            index = getattr(self, "_seed_index", 0)
            pct = int((index * 100 + pct) / count)
            msg = f"Seed {self.seeds[index]} ({index + 1}/{count}) — {msg}"
        self.progress.emit(pct, msg)

    def _is_cancel_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()


class ParcelFluxDock(QDockWidget):
    def __init__(self, iface, icon_dir: str, parent=None):
        super().__init__("ParcelFlux", parent)
        self.iface = iface
        self.icon_dir = icon_dir
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._worker: _WorkerThread | None = None
        self._cancel_requested = False
        self._preview_layer = None
        self._compare_seeds = None
        self._build_ui()
        self._apply_theme()

    # =================================================================
    # UI BUILD
    # =================================================================

    def _build_ui(self) -> None:
        root_container = QWidget()
        root_container.setObjectName("ParcelFluxRoot")
        root_lyt = QVBoxLayout(root_container)
        root_lyt.setContentsMargins(8, 8, 8, 8)
        root_lyt.setSpacing(6)

        # Header bar
        root_lyt.addWidget(self._build_header())

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setObjectName("PFTabs")
        self.tabs.addTab(self._build_run_tab(), "  Run  ")
        self.tabs.addTab(self._build_guide_tab(), "  Guide  ")
        self.tabs.addTab(self._build_about_tab(), "  About  ")
        root_lyt.addWidget(self.tabs, 1)

        # Footer status banner
        self.status_lbl = QLabel("Ready. Pick a polygon layer and press RUN.")
        self.status_lbl.setObjectName("PFStatus")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setMinimumHeight(32)
        root_lyt.addWidget(self.status_lbl)

        self.setWidget(root_container)

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PFHeader")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 8, 10, 8)

        icon_path = os.path.join(self.icon_dir, "parcelflux.png")
        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(icon_path).pixmap(28, 28))
            h.addWidget(icon_lbl)

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel("<b>ParcelFlux</b>")
        title.setObjectName("PFHeaderTitle")
        subtitle = QLabel("Block → Parcel Subdivision Engine")
        subtitle.setObjectName("PFHeaderSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        h.addLayout(title_block)
        h.addStretch()

        # Mini help button → switches to Guide tab
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setObjectName("PFHelpBtn")
        help_btn.setToolTip("Open the in-panel Guide")
        help_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        h.addWidget(help_btn)

        return bar

    # ----- RUN TAB -----

    def _build_run_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 8, 2, 8)
        lay.setSpacing(10)

        # --- Input card ---
        input_card = _Card("1. Input",
                           "The polygon layer that contains your zoning blocks (imar adaları).")
        self.input_layer_cb = QgsMapLayerComboBox()
        # FIX: previous setFilters(3) selected raster+no-geometry only, blocking polygon layers.
        # Scoped enum form: required by Qt6/QGIS 4 and valid in QGIS 3 as well.
        self.input_layer_cb.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.input_layer_cb.setCurrentIndex(-1)
        self.input_layer_cb.setToolTip("Pick a polygon layer (only polygon layers are listed).")
        self.input_layer_cb.layerChanged.connect(self._refresh_layer_meta)
        input_card.addRow("Block layer:", self.input_layer_cb)

        self.layer_meta_lbl = QLabel("No layer selected.")
        self.layer_meta_lbl.setObjectName("PFLayerMeta")
        self.layer_meta_lbl.setWordWrap(True)
        input_card.addWidget(self.layer_meta_lbl)

        self.selected_only_cb = QCheckBox("Use selected features only")
        self.selected_only_cb.setToolTip(
            "If enabled, only the currently selected features in the chosen layer are processed.")
        input_card.addWidget(self.selected_only_cb)
        lay.addWidget(input_card)

        # --- Basic params card ---
        basic = _Card("2. Basic Parameters",
                      "Core controls — these alone produce a uniform grid of parcels.")
        self.lot_width = _SliderSpin(
            5.0, 100.0, DEFAULTS["lot_width"], step=0.5, suffix="m", decimals=1,
            tooltip="Target parcel frontage (14–20 m is typical for residential).")
        basic.addRow("Lot width:", self.lot_width)

        self.min_area = _SliderSpin(
            0.0, 5000.0, DEFAULTS["min_area"], step=10.0, suffix="m²", decimals=0,
            tooltip="Parcels smaller than this are merged into the longest-shared-edge neighbour. "
                    "0 = disable.")
        basic.addRow("Min. area:", self.min_area)

        self.max_area = _SliderSpin(
            0.0, 20000.0, DEFAULTS["max_area"], step=50.0, suffix="m²", decimals=0,
            tooltip="Parcels exceeding this area are split again across their long axis "
                    "until they fit. Anything that still cannot fit is kept and flagged, "
                    "never deleted. 0 = no upper limit.")
        basic.addRow("Max. area:", self.max_area)

        self.merge_threshold = _SliderSpin(
            0.0, 100.0, DEFAULTS["merge_threshold"], step=1.0, suffix="%", decimals=0,
            tooltip="Residual-merge threshold as % of mean parcel area. 35% is a good default.")
        basic.addRow("Merge threshold:", self.merge_threshold)

        lay.addWidget(basic)

        # --- Planning constraints card ---
        rules = _Card("3. Planning Constraints",
                      "Zoning rules applied during subdivision. 0 = not enforced.")
        self.min_frontage = _SliderSpin(
            0.0, 60.0, DEFAULTS["min_frontage"], step=0.5, suffix="m", decimals=1,
            tooltip="Parcels whose street frontage falls below this are merged into "
                    "the neighbour sharing the longest edge. Must stay below the lot "
                    "width. 0 = disable.")
        rules.addRow("Min. frontage:", self.min_frontage)

        self.parcel_depth = _SliderSpin(
            0.0, 120.0, DEFAULTS["parcel_depth"], step=1.0, suffix="m", decimals=1,
            tooltip="Target depth of the front row, in metres. Places the centreline "
                    "at this distance from the front edge instead of the block midpoint. "
                    "0 = automatic (midpoint) and lets H-line shift apply.")
        rules.addRow("Parcel depth:", self.parcel_depth)

        self.corner_widening = _SliderSpin(
            0.0, 100.0, DEFAULTS["corner_widening"], step=5.0, suffix="%", decimals=0,
            tooltip="Extra width for the first and last parcel of each row, as many "
                    "zoning codes require for corner plots. Interior parcels absorb "
                    "the difference. 0 = disable.")
        rules.addRow("Corner widening:", self.corner_widening)
        lay.addWidget(rules)

        # --- Advanced params card ---
        adv = _Card("4. Advanced — Variation & Geometry",
                    "Inject organic variation. Leave at 0 for clean uniform grids.")
        self.uniform_corners = QCheckBox("Distribute leftover space evenly to both ends")
        self.uniform_corners.setChecked(DEFAULTS["uniform_corners"])
        self.uniform_corners.setToolTip(
            "When on, corner parcels are similar in width to interior parcels.")
        adv.addWidget(self.uniform_corners)

        self.width_variation = _SliderSpin(
            0.0, 25.0, DEFAULTS["width_variation"], step=1.0, suffix="%", decimals=0,
            tooltip="Per-parcel random width variation. 0 = identical widths in a row.")
        adv.addRow("Width variation:", self.width_variation)

        self.fishbone_offset = _SliderSpin(
            0.0, 15.0, DEFAULTS["fishbone_offset"], step=1.0, suffix="%", decimals=0,
            tooltip="Herringbone zigzag offset on division-line endpoints.")
        adv.addRow("Fishbone offset:", self.fishbone_offset)

        self.row_width_asymmetry = _SliderSpin(
            0.0, 25.0, DEFAULTS["row_width_asymmetry"], step=1.0, suffix="%", decimals=0,
            tooltip="Different parcel widths on opposite sides of the centerline (double-row only).")
        adv.addRow("Row width asymmetry:", self.row_width_asymmetry)

        self.hline_offset = _SliderSpin(
            0.0, 25.0, DEFAULTS["hline_offset"], step=1.0, suffix="%", decimals=0,
            tooltip="Shift centerline away from geometric midpoint, producing asymmetric "
                    "front/rear depths. Ignored when Parcel depth is set.")
        adv.addRow("H-line shift:", self.hline_offset)

        seed_row = QHBoxLayout()
        seed_row.setContentsMargins(0, 0, 0, 0)
        seed_row.setSpacing(6)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 99999)
        self.seed_spin.setValue(DEFAULTS["seed"])
        self.seed_spin.setToolTip(
            "Random seed for variation. Same seed → same output. Change to re-roll.")
        seed_row.addWidget(self.seed_spin, 1)
        reroll_btn = QToolButton()
        reroll_btn.setText("🎲")
        reroll_btn.setToolTip("Roll a new random seed")
        reroll_btn.clicked.connect(self._reroll_seed)
        seed_row.addWidget(reroll_btn)
        seed_wrap = QWidget()
        seed_wrap.setLayout(seed_row)
        adv.addRow("Random seed:", seed_wrap)
        lay.addWidget(adv)

        # --- Output card ---
        out = _Card("5. Output",
                    "Choose what extra columns to attach and what to name the result.")
        self.output_name = QLineEdit(DEFAULTS["output_name"])
        self.output_name.setToolTip("Name of the new layer in the QGIS layer panel.")
        out.addRow("Layer name:", self.output_name)

        self.add_id_fields = QCheckBox("Add block_id and parcel_id columns")
        self.add_id_fields.setChecked(DEFAULTS["add_id_fields"])
        out.addWidget(self.add_id_fields)

        self.add_facade_fields = QCheckBox(
            "Add computed facade metrics (front length, corner flag, direction, ...)")
        self.add_facade_fields.setChecked(DEFAULTS["add_facade_fields"])
        out.addWidget(self.add_facade_fields)

        self.preview_first = QCheckBox("Preview before adding (Apply / Discard)")
        self.preview_first.setChecked(True)
        self.preview_first.setToolTip(
            "Draw the result as a temporary preview layer. Keep it with Apply, or "
            "throw it away with Discard and adjust the parameters.")
        out.addWidget(self.preview_first)
        lay.addWidget(out)

        # --- Report card (filled after a run) ---
        self.report_card = _Card("Result", "Quality summary of the last run.")
        self.report_lbl = QLabel("No run yet.")
        self.report_lbl.setWordWrap(True)
        self.report_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.report_card.addWidget(self.report_lbl)
        self.report_card.setVisible(False)
        lay.addWidget(self.report_card)

        # --- Progress + buttons ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        lay.addWidget(self.progress_bar)

        preview_row = QHBoxLayout()
        self.apply_btn = QPushButton("✔  Apply preview")
        self.apply_btn.setObjectName("PFApplyBtn")
        self.apply_btn.setToolTip("Keep the preview layer as the final result.")
        self.apply_btn.clicked.connect(self._on_apply_preview)
        self.discard_btn = QPushButton("✖  Discard")
        self.discard_btn.setToolTip("Remove the preview layer from the project.")
        self.discard_btn.clicked.connect(self._on_discard_preview)
        preview_row.addWidget(self.apply_btn)
        preview_row.addWidget(self.discard_btn)
        self.preview_row_widget = QWidget()
        self.preview_row_widget.setLayout(preview_row)
        self.preview_row_widget.setVisible(False)
        lay.addWidget(self.preview_row_widget)

        btn_row = QHBoxLayout()
        self.reset_btn = QPushButton("Reset defaults")
        self.reset_btn.setObjectName("PFResetBtn")
        self.reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(self.reset_btn)

        self.compare_btn = QPushButton("Compare seeds")
        self.compare_btn.setObjectName("PFCompareBtn")
        self.compare_btn.setToolTip(
            "Run the same parameters with four different seeds and pick the layout "
            "you like best.")
        self.compare_btn.clicked.connect(self._on_compare_seeds)
        btn_row.addWidget(self.compare_btn)
        btn_row.addStretch()

        self.run_btn = QPushButton("  ▶  RUN")
        self.run_btn.setObjectName("PFRunBtn")
        self.run_btn.setMinimumHeight(40)
        self.run_btn.setMinimumWidth(170)
        self.run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self.run_btn)
        lay.addLayout(btn_row)

        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ----- GUIDE TAB -----

    def _build_guide_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        nav = QHBoxLayout()
        nav.setContentsMargins(8, 8, 8, 0)
        nav.setSpacing(6)
        open_ext_btn = QPushButton("Open full HTML in browser")
        open_ext_btn.setObjectName("PFGhostBtn")
        open_ext_btn.clicked.connect(self._open_external_docs)
        nav.addWidget(open_ext_btn)
        nav.addStretch()
        lay.addLayout(nav)

        self.guide_view = QTextBrowser()
        self.guide_view.setOpenExternalLinks(True)
        self.guide_view.setHtml(self._guide_html())
        lay.addWidget(self.guide_view, 1)
        return wrap

    # ----- ABOUT TAB -----

    def _build_about_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        icon_path = os.path.join(self.icon_dir, "parcelflux.png")
        if os.path.exists(icon_path):
            big = QLabel()
            big.setAlignment(Qt.AlignmentFlag.AlignCenter)
            big.setPixmap(QIcon(icon_path).pixmap(72, 72))
            lay.addWidget(big)

        title = QLabel("<h2 style='margin:0;'>ParcelFlux</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        sub = QLabel("Block-to-Parcel Subdivision Engine for QGIS")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setObjectName("PFSubtitle")
        lay.addWidget(sub)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setObjectName("PFAboutBody")
        body.setHtml(self._about_html())
        lay.addWidget(body, 1)
        return wrap

    # =================================================================
    # ABOUT HTML
    # =================================================================

    def _about_html(self) -> str:
        from .theme import dock_color_tokens
        c = dock_color_tokens()
        return f"""
        <div style='font-family: Segoe UI, sans-serif; color:{c["text"]};'>
          <p><b>Author:</b> Yusuf Eminoğlu<br>
             <b>Affiliation:</b> Dokuz Eylül University, Department of City and Regional Planning<br>
             <b>License:</b> GPL-3.0<br>
             <b>QGIS support:</b> 3.28 → 4.x</p>

          <p>ParcelFlux automates the subdivision of zoning blocks into individual parcels with
             tunable geometric variation. It is part of the
             <a href='https://github.com/YusufEminoglu'>PlanX</a> ecosystem of urban-planning
             QGIS plugins.</p>

          <p style='color:{c["accent"]};'><b>Highlights</b></p>
          <ul>
            <li>Single/double-row auto-detection based on block geometry</li>
            <li>Row-width asymmetry, fishbone offset, H-line shift, width variation</li>
            <li>3-pass residual-fragment merging with longest-shared-edge neighbour rule</li>
            <li>Native Processing pipeline — no external dependencies</li>
            <li>Reproducible randomness via configurable seed</li>
          </ul>

          <p style='color:{c["accent"]};'><b>Links</b></p>
          <ul>
            <li><a href='https://github.com/YusufEminoglu/parcelflux'>Source code (GitHub)</a></li>
            <li><a href='https://github.com/YusufEminoglu/parcelflux/issues'>Issue tracker</a></li>
          </ul>

          <p style='color:{c["subtle"]}; font-size:9pt;'>
            If ParcelFlux helps your research or practice, please cite the GitHub repository.
          </p>
        </div>
        """

    # =================================================================
    # GUIDE HTML
    # =================================================================

    def _guide_html(self) -> str:
        """Long-form, in-panel guide. Self-contained — no external assets."""
        from .theme import dock_color_tokens
        c = dock_color_tokens()
        return f"""
<html><head><style>
  body {{ font-family: 'Segoe UI', sans-serif; color: {c['text']}; line-height: 1.5; }}
  h1 {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; padding-bottom: 4px; }}
  h2 {{ color: {c['accent']}; margin-top: 22px; border-bottom: 1px solid {c['border']}; padding-bottom: 2px; }}
  h3 {{ margin-top: 18px; color: {c['text']}; }}
  code {{ background:{c['accent_soft']}; padding:1px 5px; border-radius:3px; color:{c['text']}; }}
  .formula {{ background: {c['accent_soft']}; border-left: 4px solid {c['accent']};
              padding: 8px 12px; margin: 8px 0; font-family: Consolas, monospace; }}
  .note {{ background: {c['status_bg']}; border-left: 4px solid {c['warn']};
           padding: 8px 12px; margin: 8px 0; }}
  .tip {{ background: {c['status_bg']}; border-left: 4px solid {c['info']};
          padding: 8px 12px; margin: 8px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  th, td {{ border: 1px solid {c['border']}; padding: 5px 8px; text-align: left; vertical-align: top; }}
  th {{ background: {c['accent_soft']}; color: {c['accent']}; }}
  .diagram {{ background: {c['card']}; border: 1px solid {c['border']}; padding: 8px;
              font-family: Consolas, monospace; white-space: pre; }}
  hr {{ border: none; border-top: 1px solid {c['border']}; margin: 16px 0; }}
  a {{ color: {c['accent']}; }}
</style></head><body>

<h1>ParcelFlux — Elite User Guide</h1>
<p>A complete walkthrough of the algorithm, every parameter, and recommended recipes.
This panel is your in-app reference; the same content (and more diagrams) is also
shipped as an external HTML guide.</p>

<h2>1. What ParcelFlux does</h2>
<p>ParcelFlux takes a polygon layer of zoning blocks (<i>imar adaları</i>) and produces
a new polygon layer of <b>parcels</b>. The algorithm:</p>
<ol>
  <li>Normalises geometry. Irregular polygons (≥5 vertices) are repaired and kept as
      the real subdivision footprint. Their <b>oriented minimum bounding box</b> (OBB)
      is used only as a layout guide for axis detection.</li>
  <li>Identifies the two <b>shortest edges</b> of each block as its "ends". The line
      joining their midpoints is the <b>centerline (h-line)</b>; the perpendicular
      direction is the <b>division axis</b>.</li>
  <li>Decides between <b>single-row</b> and <b>double-row</b> layout based on whether
      the short-edge length is ≥ <code>1.8 × lot_width</code>.</li>
  <li>Generates perpendicular <b>division lines</b> at spacing equal to
      <code>lot_width</code>, with optional width variation, fishbone offset,
      row-width asymmetry and h-line shift.</li>
  <li>Splits the block using <code>native:splitwithlines</code>.</li>
  <li>Cleans up: up to 3 passes of <b>residual merging</b> fold slivers into the
      neighbour with the longest shared edge.</li>
  <li>Filters by min/max area and writes the output.</li>
</ol>

<h2>2. Requirements & inputs</h2>
<ul>
  <li><b>Layer type:</b> polygon (single or multi-part).</li>
  <li><b>CRS:</b> must be <b>projected</b> (units of metres). Geographic CRS
      (EPSG:4326) is rejected because parcel widths are in metres.</li>
  <li><b>Geometry hygiene:</b> use <code>Fix geometries</code> beforehand if the layer
      has self-intersections or duplicate vertices.</li>
</ul>
<div class="tip"><b>Tip.</b> Pre-process with <i>Vector → Geometry Tools → Fix
geometries</i> if you get unexpected slivers or missing parcels.</div>

<h2>3. Parameter reference</h2>

<h3>3.1 Lot width</h3>
<p><b>Default:</b> 16 m · <b>Range:</b> 5–100 m · The primary control over parcel count.</p>
<div class="formula">n_parcels_per_row ≈ L_centerline / lot_width</div>

<h3>3.2 Min. area</h3>
<p><b>Default:</b> 300 m². Parcels smaller than this are merged into the neighbour with
the longest shared edge. 0 disables the rule.</p>

<h3>3.3 Max. area</h3>
<p><b>Default:</b> 2000 m². A parcel above this limit is <b>bisected across its long
axis</b> and the halves are re-checked, up to five levels deep. A parcel that still
cannot fit — because every cut would produce a sliver — is written out and marked
<code>oversize</code> in the <code>pf_flag</code> column. Set to 0 to disable the
upper limit.</p>
<div class="tip"><b>Changed in 0.3.0.</b> Oversized parcels used to be dropped from the
output, which punched holes into the block and could make an entire block disappear
without a message. Nothing is deleted any more.</div>

<h3>3.4 Merge threshold</h3>
<p><b>Default:</b> 35%. Residual-merge threshold expressed as % of the mean parcel
area for the current pass.</p>
<div class="formula">T_pass = (merge_threshold/100) × (Σ area_i / n_parcels)</div>

<h3>3.5 Uniform corners</h3>
<p>When enabled (default), the leftover length along the centerline is split equally on
both ends so corner parcels are similar to interior ones:</p>
<div class="formula">offset_per_side = (L_centerline − n × lot_width) / 2</div>

<h3>3.6 Width variation</h3>
<p>Per-parcel random width factor in [-var, +var]%. After randomisation, widths are
re-normalised to the target row span; leftover length is handled by the corner mode.</p>
<div class="formula">w_i = lot_width × (1 + U(−var, +var)/100)</div>

<h3>3.7 Fishbone offset</h3>
<p>Random offset of division-line endpoints along the centerline direction. Both
endpoints of a single division line are offset independently — lines can slant.</p>
<div class="formula">offset_max = lot_width × (fishbone/100)</div>

<h3>3.8 Row width asymmetry</h3>
<p>Different parcel widths on opposite sides of the centerline. Only applies in
<b>double-row</b> mode.</p>
<div class="formula">width_A = lot_width × (1 + shift)<br>
width_B = lot_width × (1 − shift)</div>

<h3>3.9 H-line shift</h3>
<p>Moves the centerline away from the geometric midpoint, producing asymmetric
front/rear depths:</p>
<div class="formula">h_ratio = 0.5 + U(−off, +off)/100</div>
<p>Ignored when <b>Parcel depth</b> is set, since that pins the centreline exactly.</p>

<h2>4. Planning constraints</h2>

<h3>4.1 Min. frontage</h3>
<p><b>Default:</b> 0 (off). A parcel whose street frontage — the part of its boundary
that lies on the parent block boundary — falls below this value is merged into the
neighbour sharing the longest edge, in the same passes that handle small areas. Must
stay below the lot width, otherwise every parcel would qualify and the run is rejected
up front. Parcels that still fall short after merging are marked <code>narrow</code>.</p>

<h3>4.2 Parcel depth</h3>
<p><b>Default:</b> 0 (automatic). Sets the depth of the front row in metres by placing
the centreline at that distance from the front edge, instead of at the block midpoint.
Clamped to 15–85% of the block's cross dimension so a row can never collapse.</p>
<div class="formula">h_ratio = clamp(parcel_depth / cross_width, 0.15, 0.85)</div>

<h3>4.3 Corner widening</h3>
<p><b>Default:</b> 0 (off). Many zoning codes require wider plots at block corners.
The first and last parcel of each row grow by this percentage and the interior parcels
absorb the difference, so the row still spans exactly the same length.</p>
<div class="formula">w_corner = w × (1 + c) &ensp;|&ensp;
w_interior ×= (Σw_interior − extra) / Σw_interior</div>

<h2>5. Reading the result</h2>
<p>Every run fills the <b>Result</b> card: parcel and block counts, coverage, the
area min/median/max, mean frontage, and how many parcels were merged or re-split.
<b>Coverage</b> is the share of the input block area present in the output — it should
read 100%. Anything less means geometry was lost and is worth investigating.</p>
<p>The <code>pf_flag</code> column marks parcels outside the requested envelope:
<code>oversize</code>, <code>undersize</code>, or <code>narrow</code>. They are kept in
the output on purpose, so a plan is never quietly incomplete.</p>
<p><b>Preview before adding</b> (on by default) draws the result as a dashed temporary
layer; <i>Apply</i> keeps it, <i>Discard</i> removes it. <b>Compare seeds</b> runs the
same parameters against four seeds and shows the layouts side by side so you can pick
one by eye.</p>

<h3>3.10 Random seed</h3>
<p>All randomness uses a fixed seed for reproducibility. Same parameters + same seed =
identical output. Press the 🎲 button or change the seed to re-roll variation.</p>

<h2>4. Single-row vs double-row</h2>
<div class="formula">is_single_row = max(short_edge_1, short_edge_2) &lt; 1.8 × lot_width</div>
<ul>
  <li><b>Single-row:</b> division lines cut all the way across the block, no centerline split.</li>
  <li><b>Double-row:</b> the h-line halves the block; each strip is subdivided independently.</li>
</ul>
<div class="diagram">Double-row example (top view, plan view)

   ┌──────┬──────┬──────┬──────┐
   │  P1  │  P2  │  P3  │  P4  │    ← Row A (north-facing)
   ├──────┴──────┴──────┴──────┤    ← h-line (centerline)
   │  P5  │  P6  │  P7  │  P8  │    ← Row B (south-facing)
   └──────┴──────┴──────┴──────┘
</div>

<h2>5. Residual merging</h2>
<ol>
  <li>Compute mean area; flag any parcel below
      <code>max(threshold_pct × mean, min_area)</code>.</li>
  <li>For each flagged parcel, find the spatial neighbour with the
      <b>longest shared boundary</b>.</li>
  <li>Merge via <code>geometry.combine()</code>.</li>
  <li>Recompute mean and repeat — up to 3 passes total, stopping early if no
      merges happen.</li>
</ol>

<h2>6. Output schema</h2>
<table>
  <tr><th>Column</th><th>Type</th><th>Description</th></tr>
  <tr><td><i>(copied from input)</i></td><td>—</td><td>All input attributes are preserved.</td></tr>
  <tr><td><code>block_id</code> *</td><td>String</td><td>Source feature ID of the parent block.</td></tr>
  <tr><td><code>parcel_id</code> *</td><td>Integer</td><td>Sequential parcel number.</td></tr>
  <tr><td><code>area_m2</code></td><td>Double</td><td>Parcel area in m².</td></tr>
  <tr><td><code>facade_front</code> *</td><td>String</td><td>Longest exterior frontage touching the parent block boundary.</td></tr>
  <tr><td><code>facade_side</code> *</td><td>String</td><td>Additional exterior frontage after the main frontage.</td></tr>
  <tr><td><code>facade_back</code> *</td><td>String</td><td>Longest non-exterior parcel edge, useful as a rear-edge proxy.</td></tr>
  <tr><td><code>facade_count</code> *</td><td>Integer</td><td>Count of exterior frontage segments.</td></tr>
  <tr><td><code>is_corner</code> *</td><td>Boolean</td><td>True when the parcel touches more than one exterior frontage segment.</td></tr>
  <tr><td><code>front_direction</code> *</td><td>String</td><td>Cardinal direction of the longest exterior frontage.</td></tr>
</table>
<p><i>* optional — controlled by the "Add … columns" checkboxes.</i></p>

<h2>7. Recommended recipes</h2>

<h3>7.1 Uniform residential grid</h3>
<ul>
  <li>Lot width: <b>16 m</b></li>
  <li>Min / Max area: <b>300 / 2000 m²</b></li>
  <li>Merge: <b>35%</b></li>
  <li>All advanced settings: <b>0</b></li>
</ul>

<h3>7.2 Natural-looking historical fabric</h3>
<ul>
  <li>Lot width: <b>14 m</b></li>
  <li>Width variation: <b>10%</b></li>
  <li>Fishbone offset: <b>5%</b></li>
  <li>H-line shift: <b>8%</b></li>
</ul>

<h3>7.3 Solar-optimised differentiated rows</h3>
<ul>
  <li>Lot width: <b>20 m</b></li>
  <li>Row width asymmetry: <b>12%</b></li>
  <li>Uniform corners: <b>on</b></li>
</ul>

<h2>8. Troubleshooting</h2>
<table>
  <tr><th>Symptom</th><th>Likely cause</th><th>Fix</th></tr>
  <tr><td>Layer combo is empty</td><td>No polygon layer loaded, or layer in
      lat/lon CRS marked as non-polygon</td><td>Load a projected polygon layer.</td></tr>
  <tr><td>"Layer CRS must be projected"</td><td>Input CRS is geographic
      (degrees)</td><td>Reproject to a metric CRS (e.g. EPSG:5254 / 32636 for Turkey).</td></tr>
  <tr><td>Output has 0 parcels</td><td>min/max area filtered everything</td>
      <td>Widen the area window or reduce <code>merge_threshold</code>.</td></tr>
  <tr><td>Tiny slivers persist</td><td>3 passes weren't enough</td>
      <td>Increase <code>merge_threshold</code> or <code>min_area</code>.</td></tr>
  <tr><td>Lines crossing wrongly</td><td>Block has reflex vertices</td>
      <td>Run <i>Fix geometries</i> first; OBB is used only to guide line orientation.</td></tr>
</table>

<h2>9. Reproducibility</h2>
<p>ParcelFlux is fully deterministic given the same input, parameters and random seed.
This makes it suitable for academic publications: cite the version
(<code>0.2.10</code>) and the seed used.</p>

<hr>
<p style='color:{c["subtle"]}; font-size: 9pt;'>
ParcelFlux — Yusuf Eminoğlu · Dokuz Eylül University · GPL-3.0
</p>
</body></html>
        """

    # =================================================================
    # STYLE
    # =================================================================

    def _apply_theme(self) -> None:
        root = self.widget()
        if root is None or getattr(self, "_theme_refreshing", False):
            return
        self._theme_refreshing = True
        try:
            apply_adaptive_theme(root)
        finally:
            self._theme_refreshing = False
        self._set_status("Ready. Pick a polygon layer and press RUN.", "info")

    # =================================================================
    # HELPERS
    # =================================================================

    def _set_status(self, text: str, severity: str = "info") -> None:
        self.status_lbl.setText(text)
        self.status_lbl.setProperty("severity", severity)
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)

    def _refresh_layer_meta(self, layer) -> None:
        if layer is None:
            self.layer_meta_lbl.setText("No layer selected.")
            return
        crs = layer.crs()
        feat_count = layer.featureCount()
        is_geographic = crs.isGeographic()
        from .theme import dock_color_tokens
        error_color = dock_color_tokens()["error"]
        crs_warn = (" — <span style='color:%s;'><b>WARNING:</b> geographic CRS, "
                    "please reproject to a metric CRS first.</span>" % error_color
                    ) if is_geographic else ""
        self.layer_meta_lbl.setText(
            f"<b>{layer.name()}</b> · {feat_count} features · "
            f"CRS: {crs.authid() or 'unknown'} ({crs.description() or '-'}){crs_warn}")

    def _reset_defaults(self) -> None:
        self.lot_width.setValue(DEFAULTS["lot_width"])
        self.min_area.setValue(DEFAULTS["min_area"])
        self.max_area.setValue(DEFAULTS["max_area"])
        self.merge_threshold.setValue(DEFAULTS["merge_threshold"])
        self.uniform_corners.setChecked(DEFAULTS["uniform_corners"])
        self.width_variation.setValue(DEFAULTS["width_variation"])
        self.fishbone_offset.setValue(DEFAULTS["fishbone_offset"])
        self.row_width_asymmetry.setValue(DEFAULTS["row_width_asymmetry"])
        self.hline_offset.setValue(DEFAULTS["hline_offset"])
        self.min_frontage.setValue(DEFAULTS["min_frontage"])
        self.parcel_depth.setValue(DEFAULTS["parcel_depth"])
        self.corner_widening.setValue(DEFAULTS["corner_widening"])
        self.seed_spin.setValue(DEFAULTS["seed"])
        self.add_id_fields.setChecked(DEFAULTS["add_id_fields"])
        self.add_facade_fields.setChecked(DEFAULTS["add_facade_fields"])
        self.selected_only_cb.setChecked(DEFAULTS["selected_only"])
        self.output_name.setText(DEFAULTS["output_name"])
        self._set_status("Parameters reset to defaults.", "ok")

    def _reroll_seed(self) -> None:
        import time
        t_seed = int((time.time() * 1000000)) % 99999 + 1
        self.seed_spin.setValue(t_seed)

    def _open_external_docs(self) -> None:
        import webbrowser
        docs_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "docs", "guide.html")
        if os.path.exists(docs_path):
            webbrowser.open(f"file:///{docs_path.replace(os.sep, '/')}")
        else:
            QMessageBox.information(
                self, "Documentation",
                "Bundled HTML guide not found. See the GitHub repository for documentation.")

    # =================================================================
    # RUN LOGIC
    # =================================================================

    def _on_run(self) -> None:
        if self._worker and self._worker.isRunning():
            if not self._cancel_requested:
                self._on_cancel()
            return

        input_layer = self.input_layer_cb.currentLayer()
        if input_layer is None:
            QMessageBox.warning(self, "ParcelFlux", "Please select a polygon block layer.")
            self._set_status("No layer selected.", "warn")
            return

        if not input_layer.isValid():
            QMessageBox.warning(self, "ParcelFlux", "Selected layer is not valid.")
            self._set_status("Selected layer is invalid.", "error")
            return

        if input_layer.crs().isGeographic():
            QMessageBox.warning(
                self, "ParcelFlux",
                "The selected layer is in a geographic CRS (degrees).\n\n"
                "ParcelFlux requires a projected CRS (metres) because lot widths and "
                "areas are measured in metres. Please reproject your layer first "
                "(e.g. to a Turkish national grid: EPSG:5254, or UTM: EPSG:32636).")
            self._set_status("Layer CRS must be projected (metric), not geographic.", "error")
            return

        if self.selected_only_cb.isChecked() and input_layer.selectedFeatureCount() == 0:
            QMessageBox.warning(
                self, "ParcelFlux",
                "'Use selected features only' is enabled but no features are selected.")
            self._set_status("No features selected.", "warn")
            return

        self._start_job(self._collect_params(input_layer))

    def _collect_params(self, input_layer) -> dict:
        return {
            'input_layer': input_layer,
            'lot_width': self.lot_width.value(),
            'min_area': self.min_area.value(),
            'max_area': self.max_area.value(),
            'merge_threshold': self.merge_threshold.value(),
            'uniform_corners': self.uniform_corners.isChecked(),
            'width_variation': self.width_variation.value(),
            'fishbone_offset': self.fishbone_offset.value(),
            'row_width_asymmetry': self.row_width_asymmetry.value(),
            'hline_offset': self.hline_offset.value(),
            'min_frontage': self.min_frontage.value(),
            'parcel_depth': self.parcel_depth.value(),
            'corner_widening': self.corner_widening.value(),
            'seed': self.seed_spin.value(),
            'add_id_fields': self.add_id_fields.isChecked(),
            'add_facade_fields': self.add_facade_fields.isChecked(),
            'selected_only': self.selected_only_cb.isChecked(),
        }

    def _start_job(self, params, seeds=None) -> None:
        self._discard_preview_layer(silent=True)
        self._cancel_requested = False
        self._compare_seeds = list(seeds) if seeds else None
        self._set_running(True)
        self._set_status(
            "Comparing seeds …" if seeds else "Running …", "info")

        from ..algorithms.parcel_flux_core import ParcelFluxCore
        self._worker = _WorkerThread(ParcelFluxCore(), params, seeds=seeds)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_compare_seeds(self) -> None:
        """Run the same parameters against four seeds and let the user choose."""
        if self._worker and self._worker.isRunning():
            return
        input_layer = self.input_layer_cb.currentLayer()
        if input_layer is None:
            QMessageBox.warning(self, "ParcelFlux", "Please select a polygon block layer.")
            return
        if input_layer.crs().isGeographic():
            QMessageBox.warning(
                self, "ParcelFlux",
                "The selected layer is in a geographic CRS (degrees). Reproject to a "
                "projected metric CRS first.")
            return

        base = self.seed_spin.value()
        seeds = [base, base + 101, base + 727, base + 4242]
        self._start_job(self._collect_params(input_layer), seeds=seeds)

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._cancel_requested = True
            self._worker.request_cancel()
            self.run_btn.setEnabled(False)
            self.run_btn.setText("  CANCELLING")
            self._set_status(
                "Cancelling. The current QGIS processing step will finish safely.", "warn")
            return
        self._on_cancelled()

    def _on_cancelled(self) -> None:
        self._cancel_requested = False
        self._worker = None
        self._compare_seeds = None
        self._set_running(False)
        self.preview_row_widget.setVisible(False)
        self._set_status("Cancelled. No output layer was added.", "warn")

    def _on_progress(self, pct: int, msg: str) -> None:
        if self._cancel_requested:
            return
        self.progress_bar.setValue(pct)
        self._set_status(msg, "info")

    def _on_finished(self, result_layer, error, report=None) -> None:
        if self._cancel_requested:
            self._on_cancelled()
            return
        self._worker = None
        self._set_running(False)
        if error:
            QMessageBox.critical(self, "ParcelFlux", str(error))
            short = str(error).splitlines()[0] if error else "Unknown error"
            self._set_status(f"Error: {short}", "error")
            return

        if isinstance(result_layer, list):
            self._on_seed_comparison(result_layer, report or [])
            return

        if result_layer is None or result_layer.featureCount() == 0:
            self._set_status(
                "Finished, but 0 parcels produced. Try widening the area window "
                "or lowering the merge threshold.", "warn")
            return

        self.progress_bar.setValue(100)
        self._show_report(report)
        self._deliver(result_layer, report)

    def _deliver(self, layer, report) -> None:
        """Add the result as a preview, or straight as the final layer."""
        from qgis.core import QgsProject

        name = self.output_name.text().strip() or DEFAULTS["output_name"]
        if self.preview_first.isChecked():
            layer.setName(f"{name} (preview)")
            self._style_preview(layer)
            QgsProject.instance().addMapLayer(layer)
            self._preview_layer = layer
            self.preview_row_widget.setVisible(True)
            self._set_status(
                f"Preview — {layer.featureCount()} parcels. "
                "Apply to keep, Discard to try other settings.", "info")
        else:
            layer.setName(name)
            QgsProject.instance().addMapLayer(layer)
            self._preview_layer = None
            self.preview_row_widget.setVisible(False)
            self._set_status(
                f"Done — {layer.featureCount()} parcels added as '{name}'.", "ok")

    @staticmethod
    def _style_preview(layer) -> None:
        """Dashed outline so a preview never reads as a finished layer."""
        with suppress(Exception):
            from qgis.core import QgsFillSymbol

            symbol = QgsFillSymbol.createSimple({
                'color': '46,125,50,40',
                'outline_color': '46,125,50,255',
                'outline_width': '0.5',
                'outline_style': 'dash',
            })
            layer.renderer().setSymbol(symbol)

    def _on_apply_preview(self) -> None:
        layer = getattr(self, "_preview_layer", None)
        if layer is None:
            return
        name = self.output_name.text().strip() or DEFAULTS["output_name"]
        layer.setName(name)
        with suppress(Exception):
            layer.renderer().symbol().setOpacity(1.0)
        self._preview_layer = None
        self.preview_row_widget.setVisible(False)
        self._set_status(
            f"Applied — {layer.featureCount()} parcels kept as '{name}'.", "ok")

    def _on_discard_preview(self) -> None:
        self._discard_preview_layer()
        self._set_status("Preview discarded. Adjust the settings and run again.", "info")

    def _discard_preview_layer(self, silent: bool = False) -> None:
        layer = getattr(self, "_preview_layer", None)
        self._preview_layer = None
        self.preview_row_widget.setVisible(False)
        if layer is None:
            return
        try:
            from qgis.core import QgsProject

            QgsProject.instance().removeMapLayer(layer.id())
        except Exception:
            if not silent:
                raise

    def _show_report(self, report) -> None:
        """Render the run's quality summary into the Result card."""
        if not report:
            self.report_card.setVisible(False)
            return

        from .theme import dock_color_tokens
        t = dock_color_tokens()
        accent = t["accent"]
        warn = t["warn"]
        subtle = t["subtle"]

        coverage = report.get("coverage_pct", 0.0)
        coverage_colour = accent if coverage >= 99.5 else warn
        flagged = (report.get("flag_oversize", 0)
                   + report.get("flag_undersize", 0)
                   + report.get("flag_narrow", 0))

        rows = [
            ("Parcels", f"{report.get('parcels', 0)}"),
            ("Blocks", f"{report.get('blocks_in', 0)}"
                       + (f" ({report['blocks_skipped']} skipped)"
                          if report.get("blocks_skipped") else "")),
            ("Coverage",
             f"<span style='color:{coverage_colour};'><b>{coverage:.1f}%</b></span> "
             f"({report.get('output_area', 0):,.0f} / "
             f"{report.get('input_area', 0):,.0f} m²)"),
            ("Area min / median / max",
             f"{report.get('area_min', 0):,.0f} / "
             f"{report.get('area_median', 0):,.0f} / "
             f"{report.get('area_max', 0):,.0f} m²"),
            ("Mean area", f"{report.get('area_mean', 0):,.0f} m²"),
            ("Mean frontage", f"{report.get('frontage_mean', 0):,.1f} m"),
            ("Merged / re-split",
             f"{report.get('merged', 0)} / {report.get('resplit', 0)}"),
        ]
        if flagged:
            rows.append((
                "Flagged",
                f"<span style='color:{warn};'>"
                f"{report.get('flag_oversize', 0)} oversize · "
                f"{report.get('flag_undersize', 0)} undersize · "
                f"{report.get('flag_narrow', 0)} narrow</span>"))

        body = "".join(
            f"<tr><td style='padding-right:10px; color:{subtle};'>{key}</td>"
            f"<td><b>{value}</b></td></tr>"
            for key, value in rows
        )
        note = ""
        if flagged:
            note = (f"<p style='color:{subtle}; margin-top:6px;'>Flagged parcels are "
                    f"kept in the output and marked in the <code>pf_flag</code> "
                    f"column — nothing is deleted.</p>")
        self.report_lbl.setText(f"<table>{body}</table>{note}")
        self.report_card.setVisible(True)

    def _on_seed_comparison(self, layers, reports) -> None:
        """Offer the four seed results and add the one the operator picks."""
        from .seed_gallery import SeedGalleryDialog

        seeds = self._compare_seeds or list(range(len(layers)))
        # None of the candidates are in the project yet, so the ones that are
        # not chosen simply fall out of scope.
        dialog = SeedGalleryDialog(
            list(zip(seeds, layers, reports)), self.iface, self)
        chosen = dialog.exec()
        index = dialog.selected_index() if chosen else -1

        if index < 0:
            self._set_status("Seed comparison closed. Nothing was added.", "info")
            return

        self.seed_spin.setValue(seeds[index])
        self._show_report(reports[index] if index < len(reports) else None)
        self._deliver(layers[index], reports[index] if index < len(reports) else None)

    def detach_worker_for_shutdown(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if not worker.isRunning():
            self._worker = None
            return

        self._cancel_requested = True
        worker.request_cancel()
        for signal, slot in (
            (worker.progress, self._on_progress),
            (worker.completed, self._on_finished),
            (worker.cancelled, self._on_cancelled),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass

        _ORPHANED_WORKERS.append(worker)

        def _cleanup_orphan() -> None:
            try:
                _ORPHANED_WORKERS.remove(worker)
            except ValueError:
                pass

        worker.finished.connect(_cleanup_orphan)
        self._worker = None

    def _set_running(self, running: bool) -> None:
        widgets = [
            self.input_layer_cb, self.selected_only_cb,
            self.lot_width, self.min_area, self.max_area, self.merge_threshold,
            self.uniform_corners, self.width_variation, self.fishbone_offset,
            self.row_width_asymmetry, self.hline_offset, self.seed_spin,
            self.min_frontage, self.parcel_depth, self.corner_widening,
            self.output_name, self.add_id_fields, self.add_facade_fields,
            self.preview_first, self.reset_btn, self.compare_btn,
        ]
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.setValue(0)
            self.run_btn.setText("  ■  CANCEL")
            self.run_btn.setProperty("busy", "true")
        else:
            self.run_btn.setText("  ▶  RUN")
            self.run_btn.setProperty("busy", "false")
        self.run_btn.style().unpolish(self.run_btn)
        self.run_btn.style().polish(self.run_btn)
        for w in widgets:
            w.setEnabled(not running)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._apply_theme()

    def closeEvent(self, event) -> None:  # noqa: D401
        if self._worker and self._worker.isRunning():
            self._on_cancel()
        super().closeEvent(event)
