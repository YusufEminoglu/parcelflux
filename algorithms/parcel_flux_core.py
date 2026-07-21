# -*- coding: utf-8 -*-
"""
ParcelFlux — Block-to-Parcel Subdivision Engine.

Standalone runner class. Does NOT require a ProcessingProvider.
Uses native QGIS algorithms (orientedMinimumBoundingBox, extendLines,
splitWithLines, etc.) via processing.run().

Compatible with QGIS 3.28+ and QGIS 4.x.

Algorithm summary
-----------------
1. Classify block polygons as regular (4-vertex) or irregular (5+ vertex).
   Irregular polygons are repaired and kept as real block geometry; their OBB
   is used only as a layout guide for line orientation.
2. For each block, identify the two shortest edges as the "ends" and compute
   the division direction (perpendicular to the short edges).
3. Detect single-row vs double-row layout based on short-edge width vs
   1.8 * lot_width.
4. Generate per-pendicular division lines at lot_width spacing, with optional:
   - width variation (random +/-X% per segment)
   - fishbone offset (organic zigzag at division endpoints)
   - row width asymmetry (different widths for opposite sides of centerline)
   - h-line shift (move centerline away from geometric mid-point)
5. Extend lines slightly, clip to block boundary, split polygons.
6. 3-pass residual merging: fragments below threshold are merged into
   the neighbor with the longest shared edge.
7. Filter by min/max area and output.
"""
from __future__ import annotations

import math
from typing import Callable

import processing


class _LCG:
    """Deterministic pseudo-random number generator.

    A self-contained 64-bit linear congruential generator (Knuth's MMIX
    constants) providing uniform random distributions. Not for cryptographic use.
    """

    __slots__ = ("_s",)
    _A = 6364136223846793005
    _C = 1442695040888963407
    _M = (1 << 64) - 1

    def __init__(self, seed: int = 0):
        self._s = (int(seed) * 2 + 0x9E3779B97F4A7C15) & self._M

    def uniform(self, lo: float, hi: float) -> float:
        self._s = (self._A * self._s + self._C) & self._M
        frac = (self._s >> 11) / float(1 << 53)  # top 53 bits -> [0, 1)
        return lo + (hi - lo) * frac
from qgis.core import (
    QgsFeature, QgsGeometry, QgsWkbTypes,
    QgsField, QgsFields, QgsVectorLayer,
    QgsPointXY, QgsLineString, QgsSpatialIndex,
    QgsFeatureRequest, QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QVariant

try:
    from qgis.PyQt.QtCore import QMetaType

    _FIELD_STRING = QMetaType.Type.QString
    _FIELD_INT = QMetaType.Type.Int
    _FIELD_DOUBLE = QMetaType.Type.Double
    _FIELD_BOOL = QMetaType.Type.Bool
except Exception:
    _FIELD_STRING = QVariant.String
    _FIELD_INT = QVariant.Int
    _FIELD_DOUBLE = QVariant.Double
    _FIELD_BOOL = QVariant.Bool

# QGIS 3/4 compatibility: QgsWkbTypes.GeometryType moved in QGIS 4.
try:
    _LINE_GEOM = QgsWkbTypes.GeometryType.LineGeometry
    _POLYGON_GEOM = QgsWkbTypes.GeometryType.PolygonGeometry
except AttributeError:
    _LINE_GEOM = QgsWkbTypes.LineGeometry  # QGIS 3 fallback
    _POLYGON_GEOM = QgsWkbTypes.PolygonGeometry  # QGIS 3 fallback


def _cardinal_direction(dx: float, dy: float) -> str:
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return ""
    angle = math.degrees(math.atan2(dy, dx))
    directions = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")
    return directions[int((angle + 22.5) // 45) % 8]


class _CancelledRun(Exception):
    """Internal signal used for cooperative cancellation."""


class _CancelAwareFeedback(QgsProcessingFeedback):
    """Processing feedback that mirrors the worker cancellation flag."""

    def __init__(self, cancel_callback: Callable[[], bool] | None = None):
        super().__init__()
        self._cancel_callback = cancel_callback

    def isCanceled(self):  # noqa: N802 - Qt/QGIS API name
        if self._cancel_callback and self._cancel_callback():
            self.cancel()
        return super().isCanceled()


def _skip_invalid_ctx() -> QgsProcessingContext:
    """A Processing context that tolerates invalid input geometries.

    Without this, QGIS aborts whenever the user's "Invalid features filtering"
    setting is set to *Stop algorithm execution*. ParcelFlux deals with messy
    real-world cadastral data, so we override per-call instead of forcing the
    user to change global Processing settings.
    """
    ctx = QgsProcessingContext()
    ctx.setInvalidGeometryCheck(QgsFeatureRequest.GeometrySkipInvalid)
    return ctx


class ParcelFluxCore:
    """Standalone parcel subdivision runner.

    Usage:
        runner = ParcelFluxCore()
        result_layer, error = runner.run(
            input_layer=my_block_layer,
            lot_width=16.0,
            ...
        )
        if error:
            print(error)
        else:
            QgsProject.instance().addMapLayer(result_layer)
    """

    def run(
        self,
        input_layer: QgsVectorLayer,
        lot_width: float = 16.0,
        min_area: float = 300.0,
        max_area: float = 2000.0,
        merge_threshold: float = 35.0,
        uniform_corners: bool = True,
        width_variation: float = 0.0,
        fishbone_offset: float = 0.0,
        row_width_asymmetry: float = 0.0,
        hline_offset: float = 0.0,
        add_id_fields: bool = True,
        add_facade_fields: bool = True,
        seed: int = 42,
        selected_only: bool = False,
        progress_callback: Callable[[int, str], object] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> tuple[QgsVectorLayer | None, str | None]:
        """Run parcel subdivision.

        Returns (output_layer, None) on success, or (None, error_message) on failure.
        """
        # CRS guard — block widths are in metres
        if input_layer is None:
            return None, "No input layer provided."
        if hasattr(input_layer, "isValid") and not input_layer.isValid():
            return None, "Input layer is not valid."
        if input_layer.geometryType() != _POLYGON_GEOM:
            return None, "Input layer must be a polygon layer."
        input_crs = input_layer.crs()
        if not input_crs.isValid():
            return None, (
                "Input layer CRS is missing or invalid. "
                "Assign or reproject to a projected metric CRS and try again.")
        if input_crs.isGeographic():
            return None, (
                "Layer CRS must be projected (metric). "
                "Reproject to a metric CRS (e.g. EPSG:5254 or UTM EPSG:32636) and try again.")

        try:
            lot_width = self._finite_float(lot_width, "Lot width")
            min_area = self._finite_float(min_area, "Minimum area")
            max_area = self._finite_float(max_area, "Maximum area")
            merge_threshold = self._finite_float(merge_threshold, "Merge threshold")
            width_variation = self._finite_float(width_variation, "Width variation")
            fishbone_offset = self._finite_float(fishbone_offset, "Fishbone offset")
            row_width_asymmetry = self._finite_float(
                row_width_asymmetry, "Row width asymmetry")
            hline_offset = self._finite_float(hline_offset, "H-line offset")
        except (TypeError, ValueError) as exc:
            return None, str(exc)
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            return None, "Seed must be an integer."

        validation_error = self._validate_parameters(
            lot_width, min_area, max_area, merge_threshold,
            width_variation, fishbone_offset, row_width_asymmetry, hline_offset,
        )
        if validation_error:
            return None, validation_error

        try:
            return self._run_impl(
                input_layer, lot_width, min_area, max_area,
                merge_threshold / 100.0, uniform_corners, width_variation / 100.0,
                fishbone_offset, row_width_asymmetry, hline_offset,
                add_id_fields, add_facade_fields, seed, selected_only,
                progress_callback, cancel_callback,
            )
        except _CancelledRun:
            return None, "Cancelled."
        except Exception as exc:
            import traceback
            return None, f"Unexpected error: {exc}\n{traceback.format_exc()}"

    # -- implementation --

    @staticmethod
    def _finite_float(value, label: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a finite number.")
        return number

    @staticmethod
    def _validate_parameters(
        lot_width,
        min_area,
        max_area,
        merge_threshold,
        width_variation,
        fishbone_offset,
        row_width_asymmetry,
        hline_offset,
    ) -> str | None:
        if lot_width <= 0:
            return "Lot width must be greater than 0."
        if min_area < 0:
            return "Minimum area must be 0 or greater."
        if max_area < 0:
            return "Maximum area must be 0 or greater."
        if not 0 <= merge_threshold <= 100:
            return "Merge threshold must be between 0 and 100 percent."
        if not 0 <= width_variation < 100:
            return "Width variation must be at least 0 and less than 100 percent."
        if not 0 <= fishbone_offset <= 100:
            return "Fishbone offset must be between 0 and 100 percent."
        if not 0 <= row_width_asymmetry < 100:
            return "Row width asymmetry must be at least 0 and less than 100 percent."
        if not 0 <= hline_offset < 50:
            return "H-line offset must be at least 0 and less than 50 percent."
        return None

    def _run_impl(self, source, lot_width, min_area, max_area, merge_threshold,
                  uniform_corners, width_var, fishbone, row_asym, hline_off,
                  add_id_fields, add_facade_fields, seed, selected_only,
                  progress, cancel):
        def _check_cancel():
            if cancel and cancel():
                raise _CancelledRun()

        def _emit(pct, msg):
            _check_cancel()
            if progress:
                keep_going = progress(pct, msg)
                if keep_going is False:
                    raise _CancelledRun()
            _check_cancel()

        feedback = _CancelAwareFeedback(cancel)

        def _processing_run(algorithm_id, parameters):
            _check_cancel()
            try:
                result = processing.run(
                    algorithm_id,
                    parameters,
                    context=_skip_invalid_ctx(),
                    feedback=feedback,
                )
            except Exception:
                _check_cancel()
                raise
            _check_cancel()
            return result

        _emit(0, "Preparing input data...")

        source_crs = source.sourceCrs() if hasattr(source, "sourceCrs") else source.crs()
        crs_authid = source_crs.authid()

        def _memory_layer(geometry_uri, name):
            uri = f"{geometry_uri}?crs={crs_authid}" if crs_authid else geometry_uri
            layer = QgsVectorLayer(uri, name, "memory")
            if source_crs.isValid():
                layer.setCrs(source_crs)
            return layer

        # field names (avoid collisions)
        source_field_names = [f.name() for f in source.fields()]
        used_field_names = set(source_field_names)

        def _claim_field_name(base_name):
            candidate = base_name
            suffix = 2
            while candidate in used_field_names:
                candidate = f"planx_{base_name}" if suffix == 2 else f"planx_{base_name}_{suffix}"
                suffix += 1
            used_field_names.add(candidate)
            return candidate

        ada_id_field_name = _claim_field_name('block_id') if add_id_fields else None
        parcel_id_field_name = _claim_field_name('parcel_id') if add_id_fields else None
        area_field_name = _claim_field_name('area_m2')
        facade_field_names = {}
        if add_facade_fields:
            for base in (
                'facade_front', 'facade_side', 'facade_back',
                'facade_count', 'is_corner', 'front_direction',
                'wd_ratio',
            ):
                facade_field_names[base] = _claim_field_name(base)

        # working layer with block ID
        working_layer = _memory_layer("Polygon", "working_input")
        working_fields = QgsFields()
        for field in source.fields():
            working_fields.append(QgsField(field))
        if add_id_fields:
            working_fields.append(QgsField(ada_id_field_name, _FIELD_STRING, 'string', 80))
        working_provider = working_layer.dataProvider()
        working_provider.addAttributes(working_fields)
        working_layer.updateFields()

        if selected_only:
            raw_features = list(source.getSelectedFeatures())
            if not raw_features:
                return None, "No features are selected in the input layer."
        else:
            raw_features = list(source.getFeatures())
            if not raw_features:
                return None, "Input layer contains no features."

        for feat in raw_features:
            _check_cancel()
            wf = QgsFeature(working_fields)
            wf.setGeometry(feat.geometry())
            attrs = list(feat.attributes())
            if add_id_fields:
                attrs.append(str(feat.id()))
            wf.setAttributes(attrs)
            working_provider.addFeature(wf)
        working_layer.updateExtents()

        features = list(working_layer.getFeatures())
        _emit(1, f"Loaded {len(features)} blocks. Target width: {lot_width} m")

        # output fields
        fields = QgsFields()
        for field in working_layer.fields():
            fields.append(QgsField(field))
        if add_id_fields:
            fields.append(QgsField(parcel_id_field_name, _FIELD_INT))
        fields.append(QgsField(area_field_name, _FIELD_DOUBLE, 'double', 20, 2))
        if add_facade_fields:
            fields.append(QgsField(facade_field_names['facade_front'], _FIELD_STRING, 'string', 100))
            fields.append(QgsField(facade_field_names['facade_side'], _FIELD_STRING, 'string', 100))
            fields.append(QgsField(facade_field_names['facade_back'], _FIELD_STRING, 'string', 100))
            fields.append(QgsField(facade_field_names['facade_count'], _FIELD_INT))
            fields.append(QgsField(facade_field_names['is_corner'], _FIELD_BOOL))
            fields.append(QgsField(facade_field_names['front_direction'], _FIELD_STRING, 'string', 20))
            fields.append(QgsField(facade_field_names['wd_ratio'], _FIELD_DOUBLE, 'double', 10, 2))

        # classify polygons: regular (4-vertex) vs irregular
        _emit(2, "Classifying block geometries...")
        regular_layer = _memory_layer("Polygon", "regular")
        irregular_layer = _memory_layer("Polygon", "irregular")
        reg_prov = regular_layer.dataProvider()
        irr_prov = irregular_layer.dataProvider()
        reg_prov.addAttributes(working_layer.fields())
        irr_prov.addAttributes(working_layer.fields())
        regular_layer.updateFields()
        irregular_layer.updateFields()

        has_irregular = False
        skipped = 0
        for feat in features:
            _check_cancel()
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                skipped += 1
                continue

            # If the feature is invalid (e.g. self-intersecting bowtie even with
            # 4 vertices), route it through the irregular path so fixgeometries
            # can repair it before OBB. Don't trust vertex count alone.
            invalid_input = not geom.isGeosValid()

            try:
                polys = (geom.asMultiPolygon() if geom.isMultipart()
                         else [geom.asPolygon()])
            except Exception:
                skipped += 1
                continue

            for polygon in polys:
                _check_cancel()
                if not polygon or not polygon[0]:
                    continue
                nf = QgsFeature(working_layer.fields())
                nf.setAttributes(feat.attributes())
                nf.setGeometry(QgsGeometry.fromPolygonXY(polygon))
                if (not invalid_input) and len(polygon[0]) == 5:
                    # exactly 4 distinct vertices (+ closing) and valid geometry
                    reg_prov.addFeature(nf)
                else:
                    has_irregular = True
                    irr_prov.addFeature(nf)
        if skipped:
            _emit(2, f"Classifying block geometries... ({skipped} feature(s) skipped — null/empty/unreadable)")

        if has_irregular:
            _emit(3, "Repairing irregular block geometries...")
            # Some real-world cadastres contain self-intersecting or sliver-edged blocks.
            # Run native:fixgeometries first so OBB cannot abort the pipeline on
            # the user's QGIS "Invalid features filtering = Stop" global setting.
            fixed_irregular = _processing_run(
                "native:fixgeometries",
                {'INPUT': irregular_layer, 'OUTPUT': 'memory:'},
            )['OUTPUT']

            working_field_count = working_layer.fields().count()

            def _normalize_to_working_schema(input_layer, name):
                normalized_layer = _memory_layer("Polygon", name)
                normalized_provider = normalized_layer.dataProvider()
                normalized_provider.addAttributes(working_layer.fields())
                normalized_layer.updateFields()
                normalized_features = []
                for input_feat in input_layer.getFeatures():
                    _check_cancel()
                    geom = input_feat.geometry()
                    if geom is None or geom.isEmpty():
                        continue
                    nf = QgsFeature(working_layer.fields())
                    nf.setGeometry(geom)
                    attrs = list(input_feat.attributes())[:working_field_count]
                    while len(attrs) < working_field_count:
                        attrs.append(None)
                    nf.setAttributes(attrs)
                    normalized_features.append(nf)
                normalized_provider.addFeatures(normalized_features)
                normalized_layer.updateExtents()
                return normalized_layer

            actual_irregular_layer = _normalize_to_working_schema(
                fixed_irregular, "irregular_repaired")

            _emit(4, "Building OBB layout guides for irregular blocks...")
            omb_result = _processing_run(
                "native:orientedminimumboundingbox",
                {'INPUT': fixed_irregular, 'OUTPUT': 'memory:'},
            )['OUTPUT']

            # native:orientedminimumboundingbox appends measurement fields.
            # Keep OBB as a layout guide.  Split the OBB (fast, 4-vertex
            # rectangle) and clip parcels back to the real block footprint
            # afterwards — native:splitwithlines can hang on complex real
            # geometry, especially when division lines don't perfectly align
            # with concave or high-vertex-count boundaries.
            obb_layer = _normalize_to_working_schema(omb_result, "irregular_layout_obb")

            # split_layer  = regular + OBB (simple geometry for splitwithlines)
            # layout_layer = same (division-line source)
            # real_layer   = regular + repaired real geometry (parent edges +
            #                post-split clip target)
            split_layer = _processing_run(
                "native:mergevectorlayers",
                {'LAYERS': [regular_layer, obb_layer], 'OUTPUT': 'memory:'},
            )['OUTPUT']
            layout_layer = split_layer
            real_layer = _processing_run(
                "native:mergevectorlayers",
                {'LAYERS': [regular_layer, actual_irregular_layer], 'OUTPUT': 'memory:'},
            )['OUTPUT']
        else:
            split_layer = regular_layer
            layout_layer = regular_layer
            real_layer = regular_layer

        if split_layer.featureCount() == 0:
            return None, (
                "No valid polygon geometries remained after repair/normalization. "
                "Run Fix geometries on the source layer and try again."
            )
        if layout_layer.featureCount() == 0:
            return None, (
                "No layout geometry could be built for the input layer. "
                "Run Fix geometries on the source layer and try again."
            )

        def _outer_rings(geometry):
            try:
                polygons = (
                    geometry.asMultiPolygon()
                    if geometry.isMultipart()
                    else [geometry.asPolygon()]
                )
            except Exception:
                return []
            return [polygon[0] for polygon in polygons if polygon and polygon[0]]

        parent_edges = []
        for parent_feat in real_layer.getFeatures():
            _check_cancel()
            parent_geom = parent_feat.geometry()
            if parent_geom is None or parent_geom.isEmpty():
                continue
            for ring in _outer_rings(parent_geom):
                _check_cancel()
                for idx in range(len(ring) - 1):
                    edge = QgsGeometry.fromPolylineXY([ring[idx], ring[idx + 1]])
                    edge_len = edge.length()
                    if edge_len > 0:
                        parent_edges.append(edge)

        # build division lines (h-line and p-lines)
        _emit(5, "Generating division lines...")
        h_layer = _memory_layer("LineString", "h")
        p_layer = _memory_layer("LineString", "p")
        h_prov = h_layer.dataProvider()
        p_prov = p_layer.dataProvider()
        h_prov.addAttributes([QgsField("polygon_id", _FIELD_INT)])
        p_prov.addAttributes([QgsField("polygon_id", _FIELD_INT)])
        h_layer.updateFields()
        p_layer.updateFields()

        rng = _LCG(int(seed))

        for feature in layout_layer.getFeatures():
            _check_cancel()
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for polygon in polys:
                _check_cancel()
                # measure and sort edges by length
                sides = []
                for i in range(len(polygon[0]) - 1):
                    side = QgsLineString([polygon[0][i], polygon[0][i + 1]])
                    sides.append((side, side.length()))
                if len(sides) < 2:
                    continue
                sides.sort(key=lambda x: x[1])

                shortest_sides = sides[:2]

                # h-line shift (optional asymmetry)
                if hline_off > 0:
                    h_shift = rng.uniform(-hline_off, hline_off) / 100.0
                    h_ratio = 0.5 + h_shift
                else:
                    h_ratio = 0.5

                midpoints = []
                for s, _ in shortest_sides:
                    sp = s.startPoint()
                    ep = s.endPoint()
                    mx = sp.x() + h_ratio * (ep.x() - sp.x())
                    my = sp.y() + h_ratio * (ep.y() - sp.y())
                    midpoints.append(QgsPointXY(mx, my))

                division_line = QgsGeometry.fromPolylineXY(midpoints)

                dx = midpoints[1].x() - midpoints[0].x()
                dy = midpoints[1].y() - midpoints[0].y()
                angle = math.atan2(dy, dx)
                max_width = max(sides[0][1], sides[1][1])
                line_length = division_line.length()
                if line_length <= 0 or max_width <= 0:
                    continue
                perp_angle = angle + math.pi / 2
                half_ext = max_width * 0.75

                # single-row detection
                is_single_row = max_width < (lot_width * 1.8)

                if not is_single_row:
                    h_feat = QgsFeature()
                    h_feat.setGeometry(division_line)
                    h_feat.setAttributes([feature.id()])
                    h_prov.addFeature(h_feat)

                if row_asym > 0 and not is_single_row:
                    shift_pct = rng.uniform(-row_asym, row_asym) / 100.0
                    width_side_A = lot_width * (1 + shift_pct)
                    width_side_B = lot_width * (1 - shift_pct)
                else:
                    width_side_A = lot_width
                    width_side_B = lot_width

                fishbone_max = lot_width * (fishbone / 100.0) if fishbone > 0 else 0

                def _make_widths(base_w, n_seg):
                    """Distribute line_length into n_seg segments with optional width variation.

                    When width_var > 0, each segment is scaled by a random factor within
                    +/-width_var, then the generated widths are re-normalized to
                    n_seg * base_w. The remaining residual is handled only by the
                    start offset, keeping uniform-corner and non-uniform modes honest.
                    """
                    if width_var > 0 and n_seg > 1:
                        ws = [base_w * (1 + rng.uniform(-width_var, width_var))
                              for _ in range(n_seg)]
                        if sum(ws) > 0:
                            sc = (n_seg * base_w) / sum(ws)
                            ws = [w * sc for w in ws]
                        return ws
                    return [base_w] * n_seg

                def _add_half_line(pt, direction, fish_off=0):
                    par_angle = angle
                    bx = pt.x() + fish_off * math.cos(par_angle)
                    by = pt.y() + fish_off * math.sin(par_angle)
                    ex = bx + direction * half_ext * math.cos(perp_angle)
                    ey = by + direction * half_ext * math.sin(perp_angle)
                    hl = QgsGeometry.fromPolylineXY([QgsPointXY(bx, by), QgsPointXY(ex, ey)])
                    feat = QgsFeature()
                    feat.setGeometry(hl)
                    feat.setAttributes([feature.id()])
                    p_prov.addFeature(feat)

                if row_asym > 0 and not is_single_row:
                    # SPLIT MODE: independent spacing per side
                    n_seg_A = max(1, math.floor(line_length / width_side_A))
                    rem_A = line_length - n_seg_A * width_side_A
                    start_A = rem_A / 2 if uniform_corners else 0
                    widths_A = _make_widths(width_side_A, n_seg_A)
                    cum_A = start_A
                    for i in range(n_seg_A - 1):
                        _check_cancel()
                        cum_A += widths_A[i]
                        pt = division_line.interpolate(cum_A).asPoint()
                        fish = rng.uniform(-fishbone_max, fishbone_max) if fishbone > 0 else 0
                        _add_half_line(pt, +1.0, fish)

                    n_seg_B = max(1, math.floor(line_length / width_side_B))
                    rem_B = line_length - n_seg_B * width_side_B
                    start_B = rem_B / 2 if uniform_corners else 0
                    widths_B = _make_widths(width_side_B, n_seg_B)
                    cum_B = start_B
                    for i in range(n_seg_B - 1):
                        _check_cancel()
                        cum_B += widths_B[i]
                        pt = division_line.interpolate(cum_B).asPoint()
                        fish = rng.uniform(-fishbone_max, fishbone_max) if fishbone > 0 else 0
                        _add_half_line(pt, -1.0, fish)
                else:
                    # NORMAL MODE: single cross-cutting lines
                    n_seg = max(1, math.floor(line_length / width_side_A))
                    rem = line_length - n_seg * width_side_A
                    start_off = rem / 2 if uniform_corners else 0
                    widths = _make_widths(width_side_A, n_seg)
                    cumulative = start_off
                    ext_w = max_width * 1.1

                    for i in range(n_seg - 1):
                        _check_cancel()
                        cumulative += widths[i]
                        point = division_line.interpolate(cumulative).asPoint()

                        if fishbone > 0:
                            l_off = rng.uniform(-fishbone_max, fishbone_max)
                            r_off = rng.uniform(-fishbone_max, fishbone_max)
                            pa = angle
                            lx = point.x() + l_off * math.cos(pa)
                            ly = point.y() + l_off * math.sin(pa)
                            x1 = lx + ext_w / 2 * math.cos(perp_angle)
                            y1 = ly + ext_w / 2 * math.sin(perp_angle)
                            rx = point.x() + r_off * math.cos(pa)
                            ry = point.y() + r_off * math.sin(pa)
                            x2 = rx - ext_w / 2 * math.cos(perp_angle)
                            y2 = ry - ext_w / 2 * math.sin(perp_angle)
                        else:
                            x1 = point.x() + ext_w / 2 * math.cos(perp_angle)
                            y1 = point.y() + ext_w / 2 * math.sin(perp_angle)
                            x2 = point.x() - ext_w / 2 * math.cos(perp_angle)
                            y2 = point.y() - ext_w / 2 * math.sin(perp_angle)

                        pl = QgsGeometry.fromPolylineXY([QgsPointXY(x1, y1), QgsPointXY(x2, y2)])
                        pf = QgsFeature()
                        pf.setGeometry(pl)
                        pf.setAttributes([feature.id()])
                        p_prov.addFeature(pf)

        _emit(10, "Extending division lines...")
        ext_h = _processing_run(
            "native:extendlines",
            {'INPUT': h_layer, 'START_DISTANCE': 0.05,
             'END_DISTANCE': 0.05, 'OUTPUT': 'memory:'},
        )['OUTPUT']
        ext_p = _processing_run(
            "native:extendlines",
            {'INPUT': p_layer, 'START_DISTANCE': 0.05,
             'END_DISTANCE': 0.05, 'OUTPUT': 'memory:'},
        )['OUTPUT']

        merged_lines = _processing_run(
            "native:mergevectorlayers",
            {'LAYERS': [ext_h, ext_p], 'OUTPUT': 'memory:'},
        )['OUTPUT']

        clipped_lines = _processing_run(
            "native:clip",
            {'INPUT': merged_lines, 'OVERLAY': split_layer, 'OUTPUT': 'memory:'},
        )['OUTPUT']

        _emit(18, "Splitting polygons...")
        split_polygons_layer = _processing_run(
            "native:splitwithlines",
            {'INPUT': split_layer, 'LINES': clipped_lines, 'OUTPUT': 'memory:'},
        )['OUTPUT']

        # When irregular blocks are present the split ran on OBB rectangles
        # (fast + reliable).  Clip parcels back to the real block footprints
        # so they never exceed the actual irregular boundary.
        if has_irregular:
            split_polygons_layer = _processing_run(
                "native:clip",
                {'INPUT': split_polygons_layer,
                 'OVERLAY': real_layer, 'OUTPUT': 'memory:'},
            )['OUTPUT']

        if split_polygons_layer.featureCount() == 0:
            return None, (
                "No polygon output was produced after splitting. "
                "Check the source geometry and parameter values, then try again."
            )

        _emit(22, "Merging residual fragments (3-pass)...")

        # 3-pass residual merging
        def _merge_small(layer, threshold_val, min_a):
            """Merge parcels below threshold into the neighbor with longest shared edge."""
            areas = {}
            for f in layer.getFeatures():
                _check_cancel()
                g = f.geometry()
                if g and not g.isEmpty():
                    areas[f.id()] = g.area()

            si = QgsSpatialIndex()
            for f in layer.getFeatures():
                _check_cancel()
                si.addFeature(f)

            merged = []
            processed = set()
            merge_count = 0
            for f in layer.getFeatures():
                _check_cancel()
                if f.id() in processed:
                    continue
                fid = f.id()
                if fid not in areas:
                    continue

                should_merge = (
                    (threshold_val > 0 and areas[fid] < threshold_val)
                    or (min_a > 0 and areas[fid] < min_a)
                )
                if should_merge:
                    nbrs = si.intersects(f.geometry().boundingBox())
                    best = None
                    best_len = 0
                    for nid in nbrs:
                        _check_cancel()
                        if nid == fid or nid in processed:
                            continue
                        nf = layer.getFeature(nid)
                        ng = nf.geometry()
                        if ng is None or ng.isEmpty():
                            continue
                        shared = f.geometry().intersection(ng)
                        if shared.type() == _LINE_GEOM:
                            sl = shared.length()
                            if sl > best_len:
                                best_len = sl
                                best = nf
                    if best:
                        mg = f.geometry().combine(best.geometry())
                        mf = QgsFeature(f.fields())
                        mf.setGeometry(mg)
                        mf.setAttributes(f.attributes())
                        merged.append(mf)
                        processed.add(fid)
                        processed.add(best.id())
                        merge_count += 1
                    else:
                        merged.append(f)
                        processed.add(fid)
                else:
                    merged.append(f)
                    processed.add(fid)

            return merged, merge_count

        fc = split_polygons_layer.featureCount()
        ta = sum(f.geometry().area() for f in split_polygons_layer.getFeatures() if f.geometry())
        thresh = merge_threshold * (ta / fc) if fc > 0 else 0

        current_layer = split_polygons_layer
        final_features = None
        for pass_num in range(3):
            _check_cancel()
            m, mc = _merge_small(current_layer, thresh, min_area)
            _emit(24 + pass_num, f"  Merge pass {pass_num+1}: {mc} parcel(s) merged.")
            if mc == 0:
                if pass_num == 0:
                    final_features = m
                break
            temp = _memory_layer("Polygon", f"pass{pass_num}")
            tp = temp.dataProvider()
            tp.addAttributes(
                split_polygons_layer.fields() if pass_num == 0 else current_layer.fields())
            temp.updateFields()
            tp.addFeatures(m)
            current_layer = temp
            fc2 = temp.featureCount()
            ta2 = sum(f.geometry().area() for f in temp.getFeatures() if f.geometry())
            thresh = merge_threshold * (ta2 / fc2) if fc2 > 0 else 0
            if pass_num == 2:
                final_features = m

        if final_features is None:
            final_features = list(current_layer.getFeatures())

        # build output layer
        _emit(28, "Building output layer...")
        output_layer = _memory_layer("MultiPolygon", "ParcelFlux Parcels")
        out_provider = output_layer.dataProvider()
        out_provider.addAttributes(fields)
        output_layer.updateFields()

        features_for_sink = []
        count = 0
        parcel_counter = 1
        working_field_count = working_layer.fields().count()

        def _facade_metrics(parcel_geom):
            if not parent_edges:
                return ('0.00 m', '0.00 m', '0.00 m', 0, False, '', 0.0)

            centroid = parcel_geom.centroid().asPoint()
            exterior_segments = []
            interior_lengths = []

            for ring in _outer_rings(parcel_geom):
                _check_cancel()
                for idx in range(len(ring) - 1):
                    _check_cancel()
                    p1 = ring[idx]
                    p2 = ring[idx + 1]
                    segment = QgsGeometry.fromPolylineXY([p1, p2])
                    segment_len = segment.length()
                    if segment_len <= 0:
                        continue
                    segment_bbox = segment.boundingBox()
                    shared_len = 0.0
                    for parent_edge in parent_edges:
                        _check_cancel()
                        if not segment_bbox.intersects(parent_edge.boundingBox()):
                            continue
                        shared = segment.intersection(parent_edge)
                        if shared is not None and not shared.isEmpty():
                            shared_len += shared.length()
                    shared_len = min(shared_len, segment_len)
                    tolerance = max(0.01, segment_len * 1e-6)
                    if shared_len > tolerance:
                        midpoint = QgsPointXY((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
                        exterior_segments.append((shared_len, midpoint))
                    else:
                        interior_lengths.append(segment_len)

            if not exterior_segments:
                back_len = max(interior_lengths, default=0.0)
                return ('0.00 m', '0.00 m', f'{back_len:.2f} m', 0, False, '', 0.0)

            exterior_segments.sort(key=lambda item: item[0], reverse=True)
            front_len, front_midpoint = exterior_segments[0]
            side_len = sum(length for length, _ in exterior_segments[1:])
            back_len = max(interior_lengths, default=0.0)
            facade_count = len(exterior_segments)
            is_corner = facade_count > 1 and side_len > 0.01
            direction = _cardinal_direction(
                front_midpoint.x() - centroid.x(),
                front_midpoint.y() - centroid.y(),
            )
            area = parcel_geom.area()
            wd_ratio = (front_len ** 2) / area if area > 0.01 else 0.0
            return (
                f'{front_len:.2f} m',
                f'{side_len:.2f} m',
                f'{back_len:.2f} m',
                facade_count,
                is_corner,
                direction,
                round(wd_ratio, 2),
            )

        for feat in final_features:
            _check_cancel()
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            area = geom.area()
            if max_area > 0 and area > max_area:
                continue
            if area < 5.0:  # filter out microscopic slivers
                continue

            nf = QgsFeature(fields)
            nf.setGeometry(geom)

            # pad/slice source attributes to match working layer field count
            src_attrs = list(feat.attributes())
            while len(src_attrs) < working_field_count:
                src_attrs.append(None)
            src_attrs = src_attrs[:working_field_count]

            attrs = list(src_attrs)
            if add_id_fields:
                attrs.append(parcel_counter)
            attrs.append(round(area, 2))
            if add_facade_fields:
                attrs.extend(_facade_metrics(geom))
            nf.setAttributes(attrs)
            features_for_sink.append(nf)
            count += 1
            parcel_counter += 1

        out_provider.addFeatures(features_for_sink)
        output_layer.updateExtents()

        _emit(100, f"Done. {count} parcel(s) produced.")
        return output_layer, None
