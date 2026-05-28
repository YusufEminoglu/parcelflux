# -*- coding: utf-8 -*-
"""ParcelFlux dock widget — parameter form + run button + progress."""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)
from qgis.gui import QgsMapLayerComboBox


class _CollapsibleGroup(QGroupBox):
    """Group box whose contents can be shown/hidden."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(False)
        self._toggle_btn.setStyleSheet("QToolButton { border: none; padding: 0; }")
        self._toggle_btn.clicked.connect(self._on_toggle)

        self._content = QWidget()
        self._content.setVisible(False)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.addWidget(self._toggle_btn)
        hl.addStretch()
        self._outer.addWidget(header)
        self._outer.addWidget(self._content)

    def content_layout(self):
        return self._content_layout

    def _on_toggle(self):
        expanded = self._toggle_btn.isChecked()
        self._content.setVisible(expanded)
        self._toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class _WorkerThread(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)  # result_layer, error

    def __init__(self, runner, params, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.params = params

    def run(self):
        result, error = self.runner.run(
            input_layer=self.params['input_layer'],
            lot_width=self.params['lot_width'],
            min_area=self.params['min_area'],
            max_area=self.params['max_area'],
            merge_threshold=self.params['merge_threshold'],
            uniform_corners=self.params['uniform_corners'],
            width_variation=self.params['width_variation'],
            fishbone_offset=self.params['fishbone_offset'],
            row_width_asymmetry=self.params['row_width_asymmetry'],
            hline_offset=self.params['hline_offset'],
            add_id_fields=self.params['add_id_fields'],
            add_facade_fields=self.params['add_facade_fields'],
            progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
        )
        self.finished.emit(result, error)


class ParcelFluxDock(QDockWidget):
    def __init__(self, iface, icon_dir: str, parent=None):
        super().__init__("ParcelFlux", parent)
        self.iface = iface
        self.icon_dir = icon_dir
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._worker: _WorkerThread | None = None
        self._build_ui()

    # -- UI build --

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # header
        header = QHBoxLayout()
        icon_path = os.path.join(self.icon_dir, "parcelflux.png")
        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(icon_path).pixmap(24, 24))
            header.addWidget(icon_lbl)
        title = QLabel("<b>ParcelFlux</b>  —  Block to Parcel Subdivision")
        header.addWidget(title)
        header.addStretch()

        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Open documentation (formulas, methodology, examples)")
        help_btn.setStyleSheet("QToolButton { font-weight: bold; border: 1px solid #999; border-radius: 3px; padding: 2px 6px; }")
        help_btn.clicked.connect(self._open_docs)
        header.addWidget(help_btn)
        root.addLayout(header)

        # input layer
        layer_grp = QGroupBox("Input")
        layer_lyt = QFormLayout(layer_grp)
        self.input_layer_cb = QgsMapLayerComboBox()
        self.input_layer_cb.setFilters(3)  # polygon layers only
        self.input_layer_cb.setCurrentIndex(-1)
        self.input_layer_cb.setToolTip("Select a polygon layer containing zoning blocks (im adas)")
        layer_lyt.addRow("Block layer:", self.input_layer_cb)
        root.addWidget(layer_grp)

        # basic parameters
        basic_grp = QGroupBox("Basic Parameters")
        basic_lyt = QFormLayout(basic_grp)

        self.lot_width_sb = QDoubleSpinBox()
        self.lot_width_sb.setRange(5.0, 100.0)
        self.lot_width_sb.setValue(16.0)
        self.lot_width_sb.setSuffix(" m")
        self.lot_width_sb.setToolTip(
            "Target parcel width (frontage). 14-20 m is typical for residential areas.")
        basic_lyt.addRow("Lot width:", self.lot_width_sb)

        self.min_area_sb = QDoubleSpinBox()
        self.min_area_sb.setRange(0.0, 999999.0)
        self.min_area_sb.setValue(300.0)
        self.min_area_sb.setSuffix(" m2")
        self.min_area_sb.setToolTip(
            "Minimum parcel area. Parcels smaller than this are merged into the neighbor with "
            "the longest shared edge. Set to 0 to disable.")
        basic_lyt.addRow("Min. area:", self.min_area_sb)

        self.max_area_sb = QDoubleSpinBox()
        self.max_area_sb.setRange(0.0, 999999.0)
        self.max_area_sb.setValue(2000.0)
        self.max_area_sb.setSuffix(" m2")
        self.max_area_sb.setToolTip(
            "Maximum parcel area. Parcels exceeding this are excluded from output. "
            "Use a high value to keep all parcels.")
        basic_lyt.addRow("Max. area:", self.max_area_sb)

        self.merge_sb = QDoubleSpinBox()
        self.merge_sb.setRange(0.0, 100.0)
        self.merge_sb.setValue(35.0)
        self.merge_sb.setSuffix(" %")
        self.merge_sb.setToolTip(
            "Residual merge threshold as a percentage of the average parcel area. "
            "Higher values = more aggressive merging. 0 = no merging by threshold "
            "(only min-area rule applies). 35% is a sensible default.")
        basic_lyt.addRow("Merge threshold:", self.merge_sb)

        root.addWidget(basic_grp)

        # advanced parameters (collapsible)
        adv_grp = _CollapsibleGroup("Advanced Parameters")
        adv_lyt = QFormLayout()
        adv_lyt.setContentsMargins(8, 4, 8, 4)

        self.uniform_cb = QCheckBox("Enabled")
        self.uniform_cb.setChecked(True)
        self.uniform_cb.setToolTip(
            "When enabled, leftover space is distributed equally on both ends of each row, "
            "so corner parcels are similar in size to interior parcels. "
            "When disabled, parcels stack from one edge, leaving any remainder at the far end.")
        adv_lyt.addRow("Uniform corners:", self.uniform_cb)

        self.width_var_sb = QDoubleSpinBox()
        self.width_var_sb.setRange(0.0, 25.0)
        self.width_var_sb.setValue(0.0)
        self.width_var_sb.setSuffix(" %")
        self.width_var_sb.setToolTip(
            "Per-parcel width variation. 0 = all parcels in a row have identical width. "
            "15 = each parcel varies randomly by up to +/-15% from the target, "
            "producing natural-looking diversity (e.g. 18-23 m range for a 20 m target).")
        adv_lyt.addRow("Width variation:", self.width_var_sb)

        self.fishbone_sb = QDoubleSpinBox()
        self.fishbone_sb.setRange(0.0, 15.0)
        self.fishbone_sb.setValue(0.0)
        self.fishbone_sb.setSuffix(" %")
        self.fishbone_sb.setToolTip(
            "Fishbone offset: organic zigzag shift along the division lines. "
            "At 0%, division lines are straight. At 5-10%, endpoints are randomly offset "
            "along the centerline, creating a herringbone (fishbone) pattern that mimics "
            "organic settlement morphology. The offset is bounded by target_width * offset_pct.")
        adv_lyt.addRow("Fishbone offset:", self.fishbone_sb)

        self.row_asym_sb = QDoubleSpinBox()
        self.row_asym_sb.setRange(0.0, 25.0)
        self.row_asym_sb.setValue(0.0)
        self.row_asym_sb.setSuffix(" %")
        self.row_asym_sb.setToolTip(
            "Row width asymmetry: creates DIFFERENT parcel widths for the two rows on "
            "opposite sides of the centerline (h-line). At 10% and target=20m: "
            "one row gets ~22m-wide parcels, the opposite row gets ~18m-wide parcels. "
            "This allows wider frontage on the north-facing side and narrower on the south. "
            "Only applies in double-row mode (when the block's short edge >= 1.8 * lot_width).")
        adv_lyt.addRow("Row width asymmetry:", self.row_asym_sb)

        self.hline_sb = QDoubleSpinBox()
        self.hline_sb.setRange(0.0, 25.0)
        self.hline_sb.setValue(0.0)
        self.hline_sb.setSuffix(" %")
        self.hline_sb.setToolTip(
            "H-line shift: moves the center division line away from the geometric midpoint. "
            "At 0%, the block is split exactly in half (equal depth). "
            "At 10%, the line shifts by up to +/-10% of the short-edge length, "
            "producing asymmetric front/rear depths. Useful for differentiating "
            "front-garden and back-garden parcel depths.")
        adv_lyt.addRow("H-line shift:", self.hline_sb)

        adv_grp.content_layout().addLayout(adv_lyt)
        root.addWidget(adv_grp)

        # output options
        out_grp = QGroupBox("Output Options")
        out_lyt = QFormLayout(out_grp)

        self.output_name_le = QLineEdit("ParcelFlux Parcels")
        self.output_name_le.setToolTip("Name for the output layer in the QGIS table of contents.")
        out_lyt.addRow("Layer name:", self.output_name_le)

        self.add_id_fields_cb = QCheckBox("Add block ID and parcel ID columns")
        self.add_id_fields_cb.setChecked(True)
        self.add_id_fields_cb.setToolTip(
            "When enabled, output includes 'block_id' (source feature ID) and "
            "'parcel_id' (sequential 1..N) columns for traceability. "
            "Disable to keep only the original attributes.")
        out_lyt.addRow(self.add_id_fields_cb)

        self.add_facade_fields_cb = QCheckBox("Add facade tracking columns")
        self.add_facade_fields_cb.setChecked(True)
        self.add_facade_fields_cb.setToolTip(
            "When enabled, output includes 'facade_front', 'facade_side', 'facade_back', "
            "'facade_count', 'is_corner', and 'front_direction' columns. "
            "These are empty placeholders intended to be filled by a downstream facade "
            "labeling step. Disable if you only need the geometry and area.")
        out_lyt.addRow(self.add_facade_fields_cb)

        root.addWidget(out_grp)

        # progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # run button
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("  RUN")
        run_icon = QIcon(":/images/themes/default/mActionStart.svg")
        self.run_btn.setIcon(run_icon)
        self.run_btn.setMinimumHeight(36)
        self.run_btn.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #4CAF50; color: white; "
            "border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #43A047; }"
            "QPushButton:disabled { background-color: #bdbdbd; color: #757575; }")
        self.run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self.run_btn)
        root.addLayout(btn_row)

        # status
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #555; padding: 2px;")
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

        root.addStretch(1)
        scroll.setWidget(container)

        dock_root = QVBoxLayout()
        dock_root.setContentsMargins(0, 0, 0, 0)
        dock_root.addWidget(scroll)
        main_container = QWidget()
        main_container.setLayout(dock_root)
        self.setWidget(main_container)

    # -- Run logic --

    def _on_run(self) -> None:
        if self._worker and self._worker.isRunning():
            self._on_cancel()
            return

        input_layer = self.input_layer_cb.currentLayer()
        if not input_layer:
            QMessageBox.warning(self, "ParcelFlux", "Please select a block layer.")
            return

        if not input_layer.isValid():
            QMessageBox.warning(self, "ParcelFlux", "Selected layer is not valid.")
            return

        params = {
            'input_layer': input_layer,
            'lot_width': self.lot_width_sb.value(),
            'min_area': self.min_area_sb.value(),
            'max_area': self.max_area_sb.value(),
            'merge_threshold': self.merge_sb.value(),
            'uniform_corners': self.uniform_cb.isChecked(),
            'width_variation': self.width_var_sb.value(),
            'fishbone_offset': self.fishbone_sb.value(),
            'row_width_asymmetry': self.row_asym_sb.value(),
            'hline_offset': self.hline_sb.value(),
            'add_id_fields': self.add_id_fields_cb.isChecked(),
            'add_facade_fields': self.add_facade_fields_cb.isChecked(),
        }

        self._set_running(True)

        from ..algorithms.parcel_flux_core import ParcelFluxCore
        core = ParcelFluxCore()

        self._worker = _WorkerThread(core, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_cancel(self) -> None:
        self._set_running(False)
        self.status_lbl.setText("Cancelled.")

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)

    def _on_finished(self, result_layer, error) -> None:
        self._set_running(False)

        if error:
            QMessageBox.critical(self, "ParcelFlux Error", error)
            self.status_lbl.setText(f"Error: {error}")
            return

        if result_layer and result_layer.featureCount() > 0:
            from qgis.core import QgsProject
            output_name = self.output_name_le.text().strip() or "ParcelFlux Parcels"
            result_layer.setName(output_name)
            QgsProject.instance().addMapLayer(result_layer)
            self.status_lbl.setText(
                f"Done — {result_layer.featureCount()} parcels added to '{output_name}'.")
            self.progress_bar.setValue(100)
        else:
            self.status_lbl.setText("No parcels produced. Check input parameters.")

    def _open_docs(self) -> None:
        import webbrowser
        docs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "guide.html")
        if os.path.exists(docs_path):
            webbrowser.open(f"file:///{docs_path.replace(os.sep, '/')}")
        else:
            QMessageBox.information(self, "Documentation",
                                    "Guide not found. See the GitHub repository for documentation.")

    def _set_running(self, running: bool) -> None:
        widgets = [
            self.input_layer_cb, self.lot_width_sb, self.min_area_sb, self.max_area_sb,
            self.merge_sb, self.uniform_cb, self.width_var_sb, self.fishbone_sb,
            self.row_asym_sb, self.hline_sb, self.output_name_le,
            self.add_id_fields_cb, self.add_facade_fields_cb,
        ]
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.setValue(0)
            self.run_btn.setText("  CANCEL")
            self.run_btn.setStyleSheet(
                "QPushButton { font-weight: bold; background-color: #f44336; color: white; "
                "border-radius: 4px; padding: 6px 16px; }"
                "QPushButton:hover { background-color: #e53935; }")
            for w in widgets:
                w.setEnabled(False)
        else:
            self.run_btn.setText("  RUN")
            self.run_btn.setStyleSheet(
                "QPushButton { font-weight: bold; background-color: #4CAF50; color: white; "
                "border-radius: 4px; padding: 6px 16px; }"
                "QPushButton:hover { background-color: #43A047; }")
            for w in widgets:
                w.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1000)
        super().closeEvent(event)
