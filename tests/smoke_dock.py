"""ParcelFlux dock and plugin-lifecycle checks on a real headless QGIS.

Covers the wiring the core tests cannot see: parameter collection, the preview
Apply/Discard cycle, the report card, the seed gallery, and plugin load/unload.

Run from the plugins parent directory with a QGIS Python launcher:

    python-qgis-ltr.bat -m parcelflux.tests.smoke_dock
    python-qgis.bat     -m parcelflux.tests.smoke_dock
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QAction, QMainWindow, QToolBar  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas  # noqa: E402


def _ok(name, condition, detail=""):
    tag = "PASS" if condition else "FAIL"
    print(f"  [{tag}] {name}{(' — ' + detail) if detail else ''}")
    return bool(condition)


class _Iface(QObject):
    """The slice of QgisInterface the plugin and dock actually touch."""

    currentLayerChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas(self._window)
        self._window.setCentralWidget(self._canvas)
        self._docks = []

    def mainWindow(self):
        return self._window

    def mapCanvas(self):
        return self._canvas

    def activeLayer(self):
        return None

    def addToolBar(self, toolbar):
        self._window.addToolBar(toolbar)

    def addDockWidget(self, area, dock):
        self._docks.append(dock)
        self._window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        if dock in self._docks:
            self._docks.remove(dock)
        self._window.removeDockWidget(dock)


def _block_layer(name="blocks"):
    layer = QgsVectorLayer("Polygon?crs=EPSG:32636", name, "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPolygonXY([[
        QgsPointXY(0, 0), QgsPointXY(120, 0), QgsPointXY(120, 40),
        QgsPointXY(0, 40), QgsPointXY(0, 0),
    ]]))
    layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def _run_sync(dock, layer, **overrides):
    """Run the core with the dock's own parameters, without the worker thread."""
    from parcelflux.algorithms.parcel_flux_core import ParcelFluxCore

    params = dock._collect_params(layer)
    params.update(overrides)
    seed = params.pop("seed")
    input_layer = params.pop("input_layer")
    runner = ParcelFluxCore()
    output, error = runner.run(input_layer=input_layer, seed=seed, **params)
    return runner, output, error


def run_all(iface):
    from parcelflux.dialogs.dock import DEFAULTS, ParcelFluxDock, _CORE_PARAM_KEYS
    from parcelflux.dialogs.seed_gallery import SeedGalleryDialog, render_thumbnail
    from parcelflux.main_plugin import ParcelFluxPlugin

    print("=" * 62)
    print(" ParcelFlux - dock and lifecycle checks")
    print("=" * 62)

    checks = []
    layer = _block_layer()
    dock = ParcelFluxDock(iface, os.path.join(str(PLUGIN_PARENT), "parcelflux", "icons"))

    # -- parameter plumbing --
    params = dock._collect_params(layer)
    checks.append((
        "dock collects every core parameter",
        all(key in params for key in _CORE_PARAM_KEYS) and "seed" in params,
        f"{len(params)} params",
    ))

    checks.append((
        "new planning constraints default to disabled",
        params["min_frontage"] == 0.0 and params["parcel_depth"] == 0.0
        and params["corner_widening"] == 0.0,
    ))

    dock.min_frontage.setValue(12.0)
    dock.parcel_depth.setValue(25.0)
    dock.corner_widening.setValue(30.0)
    changed = dock._collect_params(layer)
    checks.append((
        "constraint widgets reach the parameter dict",
        changed["min_frontage"] == 12.0 and changed["parcel_depth"] == 25.0
        and changed["corner_widening"] == 30.0,
    ))

    dock._reset_defaults()
    reset = dock._collect_params(layer)
    checks.append((
        "reset restores the constraint defaults",
        reset["min_frontage"] == DEFAULTS["min_frontage"]
        and reset["parcel_depth"] == DEFAULTS["parcel_depth"]
        and reset["corner_widening"] == DEFAULTS["corner_widening"],
    ))

    # -- report card --
    runner, output, error = _run_sync(dock, layer)
    checks.append(("dock parameters drive a successful run",
                   error is None and output is not None and output.featureCount() > 0,
                   (error or "")[:80]))

    dock._show_report(runner.report)
    report_text = dock.report_lbl.text()
    checks.append((
        "report card carries the run numbers",
        "Coverage" in report_text and "Parcels" in report_text
        and f"{runner.report['parcels']}" in report_text
        and "%" in report_text,
        f"parcels={runner.report['parcels']}",
    ))

    dock._show_report(None)
    checks.append((
        "report card hides itself when there is no report",
        not dock.report_card.isVisible(),
    ))

    # -- preview: Apply keeps the layer, Discard removes it --
    dock.preview_first.setChecked(True)
    dock._deliver(output, runner.report)
    preview_registered = output.id() in QgsProject.instance().mapLayers()
    checks.append((
        "preview adds a clearly named temporary layer",
        preview_registered and output.name().endswith("(preview)")
        and dock.preview_row_widget.isVisible() is not None,
        output.name(),
    ))

    dock._on_apply_preview()
    checks.append((
        "Apply keeps the layer and drops the preview suffix",
        output.id() in QgsProject.instance().mapLayers()
        and not output.name().endswith("(preview)")
        and dock._preview_layer is None,
        output.name(),
    ))

    runner2, output2, error2 = _run_sync(dock, layer)
    dock._deliver(output2, runner2.report)
    # Read the id up front: removing a memory layer from the project destroys
    # the underlying C++ object, so output2 is unusable afterwards.
    preview_id = output2.id()
    dock._on_discard_preview()
    checks.append((
        "Discard removes the preview layer from the project",
        error2 is None and preview_id not in QgsProject.instance().mapLayers()
        and dock._preview_layer is None,
    ))

    # -- direct mode bypasses the preview --
    runner3, output3, error3 = _run_sync(dock, layer)
    dock.preview_first.setChecked(False)
    dock._deliver(output3, runner3.report)
    checks.append((
        "direct mode adds the final layer straight away",
        error3 is None and output3.id() in QgsProject.instance().mapLayers()
        and output3.name() == DEFAULTS["output_name"]
        and dock._preview_layer is None,
        output3.name(),
    ))
    QgsProject.instance().removeMapLayer(output3.id())

    # -- seed gallery --
    candidates = []
    for seed in (11, 22, 33):
        runner_s, out_s, err_s = _run_sync(dock, layer, seed=seed,
                                           width_variation=20.0)
        if err_s is None:
            candidates.append((seed, out_s, runner_s.report))
    thumb = render_thumbnail(candidates[0][1]) if candidates else None
    checks.append((
        "seed thumbnails render to a real pixmap",
        thumb is not None and not thumb.isNull() and thumb.width() > 0,
        f"{thumb.width()}x{thumb.height()}" if thumb else "none",
    ))

    gallery = SeedGalleryDialog(candidates, iface, dock)
    default_index = gallery.selected_index()
    gallery._pick(2)
    checks.append((
        "seed gallery builds a card per candidate and tracks the pick",
        len(gallery._cards) == len(candidates) and default_index == 0
        and gallery.selected_index() == 2
        and gallery._cards[2].isSelected() and not gallery._cards[0].isSelected(),
        f"{len(candidates)} candidates",
    ))
    gallery.close()

    # -- worker builds one job per seed --
    from parcelflux.dialogs.dock import _WorkerThread
    from parcelflux.algorithms.parcel_flux_core import ParcelFluxCore

    single = _WorkerThread(ParcelFluxCore(), dock._collect_params(layer))
    multi = _WorkerThread(ParcelFluxCore(), dock._collect_params(layer),
                          seeds=[1, 2, 3, 4])
    checks.append((
        "worker runs one job per requested seed",
        len(single.seeds) == 1 and len(multi.seeds) == 4,
    ))

    dock.close()
    dock.deleteLater()

    # -- plugin lifecycle --
    plugin = ParcelFluxPlugin(iface)
    plugin.initGui()
    has_toolbar = any(tb.objectName() == "ParcelFluxToolbar"
                      for tb in iface.mainWindow().findChildren(QToolBar))
    plugin._toggle_dock()
    dock_created = plugin._dock is not None
    plugin.unload()
    # unload() relies on deleteLater, so the widgets only disappear once the
    # event loop has drained.
    app = QgsApplication.instance()
    app.sendPostedEvents()
    app.processEvents()
    toolbar_gone = not any(tb.objectName() == "ParcelFluxToolbar"
                           for tb in iface.mainWindow().findChildren(QToolBar))
    menu_gone = not any(
        isinstance(action, QAction) and action.text().startswith("&ParcelFlux")
        for action in iface.mainWindow().menuBar().actions())
    checks.append(("plugin adds its toolbar and dock", has_toolbar and dock_created))
    checks.append(("unload removes the toolbar and menu", toolbar_gone and menu_gone))

    passed_count = 0
    for check in checks:
        name, passed = check[0], check[1]
        detail = check[2] if len(check) > 2 else ""
        passed_count += _ok(name, passed, detail)
    print("-" * 62)
    print(f"  {passed_count}/{len(checks)} passed")
    return passed_count == len(checks)


def main():
    app = QgsApplication.instance()
    owns_app = app is None
    profile_dir = None
    if owns_app:
        profile_dir = tempfile.TemporaryDirectory(prefix="parcelflux-dock-")
        app = QgsApplication([], True, profile_dir.name, "external")
        app.initQgis()

    plugins_path = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
    if plugins_path not in sys.path:
        sys.path.append(plugins_path)
    from processing.core.Processing import Processing
    from qgis.analysis import QgsNativeAlgorithms

    QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    Processing.initialize()

    iface = _Iface()
    try:
        return run_all(iface)
    finally:
        QgsProject.instance().clear()
        iface.mainWindow().close()
        if owns_app:
            app.exitQgis()
            profile_dir.cleanup()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
else:
    main()
