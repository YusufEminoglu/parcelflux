# -*- coding: utf-8 -*-
"""ParcelFlux dock widget — parameter form + run button + progress."""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QApplication,
)
from qgis.core import QgsProject
from qgis.gui import QgsMapLayerComboBox, QgsFilterLineEdit


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

    # ───────────────────────── UI build ─────────────────────────

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # ── Header ──
        header = QHBoxLayout()
        icon_path = os.path.join(self.icon_dir, "parcelflux.png")
        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(icon_path).pixmap(24, 24))
            header.addWidget(icon_lbl)
        title = QLabel("<b>ParcelFlux</b> — Ada → Parsel Bölme")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # ── Input layer ──
        layer_grp = QGroupBox("Girdi")
        layer_lyt = QFormLayout(layer_grp)
        self.input_layer_cb = QgsMapLayerComboBox()
        self.input_layer_cb.setFilters(3)  # Polygon layers only
        self.input_layer_cb.setCurrentIndex(-1)
        layer_lyt.addRow("Ada katmanı:", self.input_layer_cb)
        root.addWidget(layer_grp)

        # ── Basic parameters ──
        basic_grp = QGroupBox("Temel Parametreler")
        basic_lyt = QFormLayout(basic_grp)

        self.lot_width_sb = QDoubleSpinBox()
        self.lot_width_sb.setRange(5.0, 100.0)
        self.lot_width_sb.setValue(16.0)
        self.lot_width_sb.setSuffix(" m")
        self.lot_width_sb.setToolTip("Hedef parsel genişliği — 16m konut alanında yaygın")
        basic_lyt.addRow("Parsel genişliği:", self.lot_width_sb)

        self.min_area_sb = QDoubleSpinBox()
        self.min_area_sb.setRange(0.0, 999999.0)
        self.min_area_sb.setValue(300.0)
        self.min_area_sb.setSuffix(" m²")
        self.min_area_sb.setToolTip("Bundan küçük parseller komşuya birleştirilir")
        basic_lyt.addRow("Min. alan:", self.min_area_sb)

        self.max_area_sb = QDoubleSpinBox()
        self.max_area_sb.setRange(0.0, 999999.0)
        self.max_area_sb.setValue(2000.0)
        self.max_area_sb.setSuffix(" m²")
        self.max_area_sb.setToolTip("Filtreleme üst sınırı")
        basic_lyt.addRow("Maks. alan:", self.max_area_sb)

        self.merge_sb = QDoubleSpinBox()
        self.merge_sb.setRange(0.0, 100.0)
        self.merge_sb.setValue(35.0)
        self.merge_sb.setSuffix(" %")
        self.merge_sb.setToolTip("Yüksek değer → daha agresif birleştirme")
        basic_lyt.addRow("Birleştirme eşiği:", self.merge_sb)

        root.addWidget(basic_grp)

        # ── Advanced parameters (collapsible) ──
        adv_grp = _CollapsibleGroup("Gelişmiş Parametreler")
        adv_lyt = QFormLayout()
        adv_lyt.setContentsMargins(8, 4, 8, 4)

        self.uniform_cb = QCheckBox("Aktif")
        self.uniform_cb.setChecked(True)
        self.uniform_cb.setToolTip(
            "Köşeleri merkeze göre eşit dağıt — kapatıldığında "
            "parsel sıralaması bir kenardan başlar")
        adv_lyt.addRow("Köşeleri eşit dağıt:", self.uniform_cb)

        self.width_var_sb = QDoubleSpinBox()
        self.width_var_sb.setRange(0.0, 25.0)
        self.width_var_sb.setValue(0.0)
        self.width_var_sb.setSuffix(" %")
        self.width_var_sb.setToolTip(
            "Parsel genişliği varyasyonu — 0=sabit, 15=±%15 doğal çeşitlilik")
        adv_lyt.addRow("Genişlik varyasyonu:", self.width_var_sb)

        self.fishbone_sb = QDoubleSpinBox()
        self.fishbone_sb.setRange(0.0, 15.0)
        self.fishbone_sb.setValue(0.0)
        self.fishbone_sb.setSuffix(" %")
        self.fishbone_sb.setToolTip(
            "Bölme çizgisi uçlarındaki organik kayma — 0=düz")
        adv_lyt.addRow("Fishbone offset:", self.fishbone_sb)

        self.row_asym_sb = QDoubleSpinBox()
        self.row_asym_sb.setRange(0.0, 25.0)
        self.row_asym_sb.setValue(0.0)
        self.row_asym_sb.setSuffix(" %")
        self.row_asym_sb.setToolTip(
            "Sıra genişlik asimetrisi — ön/arka bahçe genişlik farkı\n"
            "0=tüm sıralar aynı genişlikte\n"
            "10=±%10 → bir sıra ~22m, karşı sıra ~18m")
        adv_lyt.addRow("Sıra genişlik asimetrisi:", self.row_asym_sb)

        self.hline_sb = QDoubleSpinBox()
        self.hline_sb.setRange(0.0, 25.0)
        self.hline_sb.setValue(0.0)
        self.hline_sb.setSuffix(" %")
        self.hline_sb.setToolTip(
            "Ortadan bölen çizgi kayması — arka bahçe sınırını kaydırır")
        adv_lyt.addRow("H-line kayması:", self.hline_sb)

        adv_grp.content_layout().addLayout(adv_lyt)
        root.addWidget(adv_grp)

        # ── Output layer name ──
        out_grp = QGroupBox("Çıktı")
        out_lyt = QFormLayout(out_grp)
        self.output_name_le = QLineEdit("ParcelFlux Parseller")
        out_lyt.addRow("Katman adı:", self.output_name_le)
        root.addWidget(out_grp)

        # ── Progress ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # ── Run button ──
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("  ÇALIŞTIR")
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

        # ── Status ──
        self.status_lbl = QLabel("Hazır")
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

    # ───────────────────────── Run logic ─────────────────────────

    def _on_run(self) -> None:
        if self._worker and self._worker.isRunning():
            self._on_cancel()
            return

        input_layer = self.input_layer_cb.currentLayer()
        if not input_layer:
            QMessageBox.warning(self, "ParcelFlux", "Lütfen bir ada katmanı seçin.")
            return

        if not input_layer.isValid():
            QMessageBox.warning(self, "ParcelFlux", "Seçili katman geçerli değil.")
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
        self.status_lbl.setText("İptal edildi.")

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)

    def _on_finished(self, result_layer, error) -> None:
        self._set_running(False)

        if error:
            QMessageBox.critical(self, "ParcelFlux Hatası", error)
            self.status_lbl.setText(f"Hata: {error}")
            return

        if result_layer and result_layer.featureCount() > 0:
            output_name = self.output_name_le.text().strip() or "ParcelFlux Parseller"
            result_layer.setName(output_name)
            QgsProject.instance().addMapLayer(result_layer)
            self.status_lbl.setText(
                f"Tamamlandı — {result_layer.featureCount()} parsel "
                f"'{output_name}' katmanına eklendi.")
            self.progress_bar.setValue(100)
        else:
            self.status_lbl.setText("Hiç parsel üretilemedi. Parametreleri kontrol edin.")

    def _set_running(self, running: bool) -> None:
        self.progress_bar.setVisible(running)
        if running:
            self.progress_bar.setValue(0)
            self.run_btn.setText("  İPTAL")
            self.run_btn.setStyleSheet(
                "QPushButton { font-weight: bold; background-color: #f44336; color: white; "
                "border-radius: 4px; padding: 6px 16px; }"
                "QPushButton:hover { background-color: #e53935; }")
            self.input_layer_cb.setEnabled(False)
            self.lot_width_sb.setEnabled(False)
            self.min_area_sb.setEnabled(False)
            self.max_area_sb.setEnabled(False)
            self.merge_sb.setEnabled(False)
            self.uniform_cb.setEnabled(False)
            self.width_var_sb.setEnabled(False)
            self.fishbone_sb.setEnabled(False)
            self.row_asym_sb.setEnabled(False)
            self.hline_sb.setEnabled(False)
            self.output_name_le.setEnabled(False)
        else:
            self.run_btn.setText("  ÇALIŞTIR")
            self.run_btn.setStyleSheet(
                "QPushButton { font-weight: bold; background-color: #4CAF50; color: white; "
                "border-radius: 4px; padding: 6px 16px; }"
                "QPushButton:hover { background-color: #43A047; }")
            self.input_layer_cb.setEnabled(True)
            self.lot_width_sb.setEnabled(True)
            self.min_area_sb.setEnabled(True)
            self.max_area_sb.setEnabled(True)
            self.merge_sb.setEnabled(True)
            self.uniform_cb.setEnabled(True)
            self.width_var_sb.setEnabled(True)
            self.fishbone_sb.setEnabled(True)
            self.row_asym_sb.setEnabled(True)
            self.hline_sb.setEnabled(True)
            self.output_name_le.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1000)
        super().closeEvent(event)
