"""ParcelFlux core engine checks on a real headless QGIS.

ParcelFlux had no automated coverage through eleven consecutive bug-fix
releases. These checks pin the behaviour that matters: every square metre of
input reaches the output, the same seed reproduces the same parcels, and the
planning constraints do what the dock promises.

Run from the plugins parent directory with a QGIS Python launcher:

    python-qgis-ltr.bat -m parcelflux.tests.smoke_core
    python-qgis.bat     -m parcelflux.tests.smoke_core
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

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)


def _ok(name, condition, detail=""):
    tag = "PASS" if condition else "FAIL"
    print(f"  [{tag}] {name}{(' — ' + detail) if detail else ''}")
    return bool(condition)


def _block_layer(rings, crs="EPSG:32636", name="blocks"):
    """Build an in-memory polygon layer from a list of point-tuple rings."""
    layer = QgsVectorLayer(f"Polygon?crs={crs}", name, "memory")
    provider = layer.dataProvider()
    for ring in rings:
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolygonXY(
            [[QgsPointXY(x, y) for x, y in ring]]
        ))
        provider.addFeature(feature)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def _rect(width, height, x0=0.0, y0=0.0):
    return [(x0, y0), (x0 + width, y0), (x0 + width, y0 + height),
            (x0, y0 + height), (x0, y0)]


def _run(layer, **kwargs):
    from parcelflux.algorithms.parcel_flux_core import ParcelFluxCore

    runner = ParcelFluxCore()
    params = {
        "lot_width": 16.0,
        "min_area": 300.0,
        "max_area": 2000.0,
        "merge_threshold": 35.0,
        "seed": 42,
    }
    params.update(kwargs)
    output, error = runner.run(input_layer=layer, **params)
    return runner, output, error


def _area_of(layer):
    return sum(f.geometry().area() for f in layer.getFeatures()
               if f.geometry() and not f.geometry().isEmpty())


def _wkts(layer):
    return sorted(f.geometry().asWkt(3) for f in layer.getFeatures())


# ── coverage: the defect that motivated 0.3.0 ────────────────────────────────

def test_oversize_block_is_not_dropped():
    """A block whose natural parcels exceed max_area must still be subdivided.

    Before 0.3.0 the whole block silently vanished from the output.
    """
    layer = _block_layer([_rect(100, 60)])
    _runner, output, error = _run(layer, lot_width=50.0, max_area=2000.0)
    if error:
        return _ok("oversize block survives max_area", False, error[:100])
    area_out = _area_of(output)
    return _ok(
        "oversize block survives max_area",
        output.featureCount() > 0 and abs(area_out - 6000.0) < 1.0,
        f"{output.featureCount()} parcels, {area_out:.0f}/6000 m2",
    )


def test_resplit_respects_max_area():
    layer = _block_layer([_rect(100, 60)])
    runner, output, error = _run(layer, lot_width=50.0, max_area=2000.0)
    if error:
        return _ok("re-split brings parcels under max_area", False, error[:100])
    areas = [f.geometry().area() for f in output.getFeatures()]
    return _ok(
        "re-split brings parcels under max_area",
        areas and max(areas) <= 2000.0 + 1e-6 and runner.report["resplit"] > 0,
        f"max parcel {max(areas):.0f} m2, {runner.report['resplit']} re-splits",
    )


def test_coverage_is_complete_on_a_normal_block():
    layer = _block_layer([_rect(100, 40)])
    runner, output, error = _run(layer)
    if error:
        return _ok("normal block keeps full coverage", False, error[:100])
    return _ok(
        "normal block keeps full coverage",
        abs(_area_of(output) - 4000.0) < 1.0
        and abs(runner.report["coverage_pct"] - 100.0) < 0.5,
        f"coverage {runner.report['coverage_pct']:.1f}%",
    )


def test_coverage_with_multiple_blocks():
    layer = _block_layer([_rect(100, 40), _rect(80, 50, x0=200), _rect(120, 30, y0=200)])
    runner, output, error = _run(layer)
    if error:
        return _ok("multi-block run keeps full coverage", False, error[:100])
    expected = 100 * 40 + 80 * 50 + 120 * 30
    return _ok(
        "multi-block run keeps full coverage",
        abs(_area_of(output) - expected) < 2.0,
        f"{_area_of(output):.0f}/{expected} m2, {output.featureCount()} parcels",
    )


def test_undersize_parcels_are_flagged_not_deleted():
    """A tiny block cannot make a 300 m2 parcel; it must be kept and flagged."""
    layer = _block_layer([_rect(20, 10)])
    _runner, output, error = _run(layer, lot_width=16.0, min_area=300.0)
    if error:
        return _ok("undersize parcels are flagged, not deleted", False, error[:100])
    flags = [f["pf_flag"] for f in output.getFeatures()]
    return _ok(
        "undersize parcels are flagged, not deleted",
        output.featureCount() > 0 and abs(_area_of(output) - 200.0) < 1.0
        and all(flag == "undersize" for flag in flags),
        f"{output.featureCount()} parcels, flags={set(flags)}",
    )


# ── determinism ──────────────────────────────────────────────────────────────

def test_same_seed_reproduces_output():
    layer = _block_layer([_rect(120, 45)])
    _r1, out1, err1 = _run(layer, width_variation=15.0, fishbone_offset=8.0, seed=7)
    _r2, out2, err2 = _run(layer, width_variation=15.0, fishbone_offset=8.0, seed=7)
    if err1 or err2:
        return _ok("same seed reproduces the same parcels", False,
                   (err1 or err2)[:100])
    return _ok(
        "same seed reproduces the same parcels",
        _wkts(out1) == _wkts(out2),
        f"{out1.featureCount()} vs {out2.featureCount()} parcels",
    )


def test_different_seed_changes_output():
    layer = _block_layer([_rect(120, 45)])
    _r1, out1, err1 = _run(layer, width_variation=15.0, seed=7)
    _r2, out2, err2 = _run(layer, width_variation=15.0, seed=9999)
    if err1 or err2:
        return _ok("a different seed changes the parcels", False,
                   (err1 or err2)[:100])
    return _ok(
        "a different seed changes the parcels",
        _wkts(out1) != _wkts(out2),
    )


# ── planning constraints ─────────────────────────────────────────────────────

def test_min_frontage_merges_narrow_parcels():
    """Width variation produces narrow parcels; the constraint must absorb them.

    Frontage of a row parcel is its width along the block edge, so a 25%
    variation around a 20 m lot width yields parcels down to ~15 m.
    """
    common = dict(lot_width=20.0, min_area=0.0, merge_threshold=0.0,
                  width_variation=25.0, seed=3)
    layer = _block_layer([_rect(200, 40)])
    _r_off, out_off, err1 = _run(layer, min_frontage=0.0, **common)
    _r_on, out_on, err2 = _run(layer, min_frontage=18.0, **common)
    if err1 or err2:
        return _ok("minimum frontage merges narrow parcels", False,
                   (err1 or err2)[:100])
    return _ok(
        "minimum frontage merges narrow parcels",
        out_on.featureCount() < out_off.featureCount()
        and abs(_area_of(out_on) - 8000.0) < 1.0,
        f"{out_off.featureCount()} -> {out_on.featureCount()} parcels, "
        f"coverage kept at {_area_of(out_on):.0f} m2",
    )


def test_min_frontage_rejects_impossible_value():
    layer = _block_layer([_rect(100, 40)])
    _runner, output, error = _run(layer, lot_width=16.0, min_frontage=20.0)
    return _ok(
        "minimum frontage above lot width is rejected",
        output is None and error is not None and "frontage" in error.lower(),
        (error or "no error")[:70],
    )


def test_parcel_depth_controls_front_row():
    """An explicit depth must move the centreline off the block midpoint."""
    layer = _block_layer([_rect(100, 40)])
    _r_auto, out_auto, err1 = _run(layer, lot_width=20.0)
    _r_deep, out_deep, err2 = _run(layer, lot_width=20.0, parcel_depth=28.0)
    if err1 or err2:
        return _ok("parcel depth shifts the centreline", False, (err1 or err2)[:100])
    auto_areas = sorted(f.geometry().area() for f in out_auto.getFeatures())
    deep_areas = sorted(f.geometry().area() for f in out_deep.getFeatures())
    spread_auto = auto_areas[-1] - auto_areas[0]
    spread_deep = deep_areas[-1] - deep_areas[0]
    return _ok(
        "parcel depth shifts the centreline",
        spread_deep > spread_auto + 1.0
        and abs(sum(deep_areas) - 4000.0) < 1.0,
        f"area spread {spread_auto:.0f} -> {spread_deep:.0f} m2",
    )


def test_corner_widening_grows_end_parcels():
    layer = _block_layer([_rect(160, 40)])
    _r_off, out_off, err1 = _run(layer, lot_width=20.0, min_area=0.0,
                                 merge_threshold=0.0)
    _r_on, out_on, err2 = _run(layer, lot_width=20.0, min_area=0.0,
                               merge_threshold=0.0, corner_widening=40.0)
    if err1 or err2:
        return _ok("corner widening grows the end parcels", False,
                   (err1 or err2)[:100])
    max_off = max(f.geometry().area() for f in out_off.getFeatures())
    max_on = max(f.geometry().area() for f in out_on.getFeatures())
    return _ok(
        "corner widening grows the end parcels",
        max_on > max_off + 1.0 and abs(_area_of(out_on) - 6400.0) < 1.0,
        f"largest parcel {max_off:.0f} -> {max_on:.0f} m2, coverage kept",
    )


# ── report ───────────────────────────────────────────────────────────────────

def test_report_is_populated():
    layer = _block_layer([_rect(100, 40)])
    runner, output, error = _run(layer)
    if error:
        return _ok("report is populated", False, error[:100])
    report = runner.report
    expected_keys = {
        "blocks_in", "parcels", "coverage_pct", "area_min", "area_mean",
        "area_median", "area_max", "frontage_mean", "merged", "resplit",
        "flag_oversize", "flag_undersize", "flag_narrow",
    }
    return _ok(
        "report is populated",
        expected_keys.issubset(report)
        and report["parcels"] == output.featureCount()
        and report["blocks_in"] == 1
        and report["area_min"] <= report["area_median"] <= report["area_max"],
        f"{report['parcels']} parcels, coverage {report['coverage_pct']}%",
    )


def test_report_counts_flags():
    layer = _block_layer([_rect(20, 10)])
    runner, _output, error = _run(layer, lot_width=16.0, min_area=300.0)
    if error:
        return _ok("report counts flagged parcels", False, error[:100])
    return _ok(
        "report counts flagged parcels",
        runner.report["flag_undersize"] > 0,
        f"undersize={runner.report['flag_undersize']}",
    )


# ── validation and robustness ────────────────────────────────────────────────

def test_geographic_crs_is_rejected():
    layer = _block_layer([_rect(0.01, 0.005)], crs="EPSG:4326")
    _runner, output, error = _run(layer)
    return _ok(
        "geographic CRS is rejected with a clear message",
        output is None and error is not None and "projected" in error.lower(),
        (error or "no error")[:70],
    )


def test_invalid_parameters_are_rejected():
    layer = _block_layer([_rect(100, 40)])
    cases = [
        ({"lot_width": 0.0}, "lot width"),
        ({"lot_width": float("nan")}, "finite"),
        ({"corner_widening": 150.0}, "corner widening"),
        ({"parcel_depth": -5.0}, "parcel depth"),
        ({"min_area": 900.0, "max_area": 400.0}, "maximum area"),
    ]
    failures = []
    for kwargs, needle in cases:
        _runner, output, error = _run(layer, **kwargs)
        if output is not None or not error or needle not in error.lower():
            failures.append(f"{kwargs} -> {error}")
    return _ok(
        "invalid parameters are rejected up front",
        not failures,
        "; ".join(failures)[:120] if failures else f"{len(cases)} cases",
    )


def test_irregular_block_stays_inside_its_footprint():
    """An L-shaped block: parcels must not spill into the OBB corner."""
    l_shape = [(0, 0), (100, 0), (100, 40), (60, 40), (60, 80), (0, 80), (0, 0)]
    layer = _block_layer([l_shape])
    _runner, output, error = _run(layer, lot_width=16.0)
    if error:
        return _ok("irregular block output stays inside the block", False,
                   error[:100])
    block_geom = QgsGeometry.fromPolygonXY(
        [[QgsPointXY(x, y) for x, y in l_shape]])
    outside = 0.0
    for feature in output.getFeatures():
        difference = feature.geometry().difference(block_geom)
        if difference and not difference.isEmpty():
            outside += difference.area()
    return _ok(
        "irregular block output stays inside the block",
        outside < 1.0 and output.featureCount() > 0,
        f"{output.featureCount()} parcels, {outside:.3f} m2 outside",
    )


def test_selected_only_without_selection_errors():
    layer = _block_layer([_rect(100, 40)])
    _runner, output, error = _run(layer, selected_only=True)
    return _ok(
        "selected-only with no selection reports a clear error",
        output is None and error is not None and "selected" in error.lower(),
        (error or "no error")[:70],
    )


# ── runner ───────────────────────────────────────────────────────────────────

def run_all():
    print("=" * 62)
    print(" ParcelFlux - core engine checks")
    print("=" * 62)
    results = [
        test_oversize_block_is_not_dropped(),
        test_resplit_respects_max_area(),
        test_coverage_is_complete_on_a_normal_block(),
        test_coverage_with_multiple_blocks(),
        test_undersize_parcels_are_flagged_not_deleted(),
        test_same_seed_reproduces_output(),
        test_different_seed_changes_output(),
        test_min_frontage_merges_narrow_parcels(),
        test_min_frontage_rejects_impossible_value(),
        test_parcel_depth_controls_front_row(),
        test_corner_widening_grows_end_parcels(),
        test_report_is_populated(),
        test_report_counts_flags(),
        test_geographic_crs_is_rejected(),
        test_invalid_parameters_are_rejected(),
        test_irregular_block_stays_inside_its_footprint(),
        test_selected_only_without_selection_errors(),
    ]
    passed = sum(1 for result in results if result)
    print("-" * 62)
    print(f"  {passed}/{len(results)} passed")
    return passed == len(results)


def main():
    app = QgsApplication.instance()
    owns_app = app is None
    profile_dir = None
    if owns_app:
        profile_dir = tempfile.TemporaryDirectory(prefix="parcelflux-test-")
        app = QgsApplication([], False, profile_dir.name, "external")
        app.initQgis()

    # Processing lives under the QGIS plugins directory, which is not on
    # sys.path in a plain python-qgis session.
    plugins_path = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
    if plugins_path not in sys.path:
        sys.path.append(plugins_path)
    from processing.core.Processing import Processing
    from qgis.analysis import QgsNativeAlgorithms

    QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    Processing.initialize()

    try:
        return run_all()
    finally:
        QgsProject.instance().clear()
        if owns_app:
            app.exitQgis()
            profile_dir.cleanup()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
else:
    main()
