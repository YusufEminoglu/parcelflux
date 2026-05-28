# -*- coding: utf-8 -*-
"""
ParcelFlux — Block-to-Parcel Subdivision Engine.

Standalone runner class adapted from Step 1 (planx_yerlesim_plani_arac_seti).
Does NOT require a ProcessingProvider — uses native QGIS algorithms directly.

Features:
  - Single/double-row auto-detection
  - Width variation per segment
  - Row-width asymmetry (different front/rear parcel widths)
  - Fishbone-style organic boundary offset
  - H-line shift for asymmetric depth
  - 3-pass residual fragment merging
"""
from __future__ import annotations

import math
import random
from typing import Callable

import processing
from qgis.core import (
    QgsFeature, QgsGeometry, QgsWkbTypes,
    QgsField, QgsFields, QgsVectorLayer, QgsFeatureSink,
    QgsPointXY, QgsLineString, QgsSpatialIndex,
)
from qgis.PyQt.QtCore import QVariant


class ParcelFluxCore:
    """Standalone parcel subdivision runner.

    Usage:
        runner = ParcelFluxCore()
        result_layer, error = runner.run(
            input_layer=my_block_layer,
            lot_width=16.0,
            min_area=300.0,
            max_area=2000.0,
            merge_threshold=35.0,
            uniform_corners=True,
            width_variation=0.0,
            fishbone_offset=0.0,
            row_width_asymmetry=0.0,
            hline_offset=0.0,
            progress_callback=lambda pct, msg: print(f"{pct}% {msg}"),
        )
        if error:
            print(f"Hata: {error}")
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
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> tuple[QgsVectorLayer | None, str | None]:
        """Run parcel subdivision.

        Returns (output_layer, None) on success, or (None, error_message) on failure.
        """
        try:
            return self._run_impl(
                input_layer, lot_width, min_area, max_area,
                merge_threshold, uniform_corners, width_variation,
                fishbone_offset, row_width_asymmetry, hline_offset,
                progress_callback,
            )
        except Exception as exc:
            return None, f"Beklenmeyen hata: {exc}"

    # ─────────────────────── implementation ───────────────────────

    def _run_impl(self, source, lot_width, min_area, max_area, merge_threshold,
                  uniform_corners, width_var, fishbone, row_asym, hline_off,
                  progress):
        # ── helpers ──
        def _emit(pct, msg):
            if progress:
                progress(pct, msg)

        _emit(0, "Girdi verisi hazırlanıyor...")

        source_crs = source.sourceCrs()
        crs_authid = source_crs.authid()

        # ── field names ──
        source_field_names = [f.name() for f in source.fields()]
        ada_id_field_name = 'ada_id' if 'ada_id' not in source_field_names else 'planx_ada_id'
        parcel_id_field_name = 'parcel_id' if 'parcel_id' not in source_field_names else 'planx_parcel_id'

        # ── working layer ──
        working_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", "working_input", "memory")
        working_fields = QgsFields()
        for field in source.fields():
            working_fields.append(QgsField(field))
        working_fields.append(QgsField(ada_id_field_name, QVariant.String, 'string', 80))
        working_provider = working_layer.dataProvider()
        working_provider.addAttributes(working_fields)
        working_layer.updateFields()

        raw_features = list(source.getFeatures())
        if not raw_features:
            return None, "Girdi katmanında obje bulunamadı."

        for feat in raw_features:
            wf = QgsFeature(working_fields)
            wf.setGeometry(feat.geometry())
            wf.setAttributes(list(feat.attributes()) + [str(feat.id())])
            working_provider.addFeature(wf)
        working_layer.updateExtents()

        features = list(working_layer.getFeatures())
        _emit(1, f"{len(features)} ada yüklendi. Genişlik: {lot_width}m")

        # ── output fields ──
        fields = QgsFields()
        for field in working_layer.fields():
            fields.append(QgsField(field))
        fields.append(QgsField(parcel_id_field_name, QVariant.Int))
        fields.append(QgsField('aream2', QVariant.Double, 'double', 20, 2))
        fields.append(QgsField('facade_front', QVariant.String, 'string', 100))
        fields.append(QgsField('facade_side', QVariant.String, 'string', 100))
        fields.append(QgsField('facade_back', QVariant.String, 'string', 100))
        fields.append(QgsField('facade_count', QVariant.Int))
        fields.append(QgsField('is_corner', QVariant.Bool))
        fields.append(QgsField('front_direction', QVariant.String, 'string', 20))

        # ── classify polygons ──
        _emit(2, "Poligonlar sınıflandırılıyor...")
        regular_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", "regular", "memory")
        irregular_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", "irregular", "memory")
        reg_prov = regular_layer.dataProvider()
        irr_prov = irregular_layer.dataProvider()
        reg_prov.addAttributes(working_layer.fields())
        irr_prov.addAttributes(working_layer.fields())
        regular_layer.updateFields()
        irregular_layer.updateFields()

        has_irregular = False
        for feat in features:
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for polygon in polys:
                nf = QgsFeature(working_layer.fields())
                nf.setAttributes(feat.attributes())
                if len(polygon[0]) == 5:
                    nf.setGeometry(QgsGeometry.fromPolygonXY(polygon))
                    reg_prov.addFeature(nf)
                else:
                    has_irregular = True
                    nf.setGeometry(QgsGeometry.fromPolygonXY(polygon))
                    irr_prov.addFeature(nf)

        if has_irregular:
            _emit(3, "Düzensiz poligonlar OBB ile işleniyor...")
            omb_result = processing.run("native:orientedminimumboundingbox",
                {'INPUT': irregular_layer, 'OUTPUT': 'memory:'})['OUTPUT']
            merged_layer = processing.run("native:mergevectorlayers",
                {'LAYERS': [regular_layer, omb_result], 'OUTPUT': 'memory:'})['OUTPUT']
        else:
            merged_layer = regular_layer

        # ── build division lines ──
        _emit(5, "Bölme çizgileri oluşturuluyor...")
        h_layer = QgsVectorLayer(f"LineString?crs={crs_authid}", "h", "memory")
        p_layer = QgsVectorLayer(f"LineString?crs={crs_authid}", "p", "memory")
        h_prov = h_layer.dataProvider()
        p_prov = p_layer.dataProvider()
        h_prov.addAttributes([QgsField("polygon_id", QVariant.Int)])
        p_prov.addAttributes([QgsField("polygon_id", QVariant.Int)])
        h_layer.updateFields()
        p_layer.updateFields()

        rng = random.Random(42)

        for feature in merged_layer.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for polygon in polys:
                sides = []
                for i in range(len(polygon[0]) - 1):
                    side = QgsLineString([polygon[0][i], polygon[0][i + 1]])
                    sides.append((side, side.length()))
                if len(sides) < 2:
                    continue
                sides.sort(key=lambda x: x[1])

                shortest_sides = sides[:2]

                # h-line shift
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

                h_feat = QgsFeature()
                h_feat.setGeometry(division_line)
                h_feat.setAttributes([feature.id()])
                h_prov.addFeature(h_feat)

                dx = midpoints[1].x() - midpoints[0].x()
                dy = midpoints[1].y() - midpoints[0].y()
                angle = math.atan2(dy, dx)
                max_width = max(sides[0][1], sides[1][1])
                line_length = division_line.length()
                perp_angle = angle + math.pi / 2
                half_ext = max_width * 0.75

                is_single_row = max_width < (lot_width * 1.8)

                if row_asym > 0 and not is_single_row:
                    shift_pct = rng.uniform(-row_asym, row_asym) / 100.0
                    width_side_A = lot_width * (1 + shift_pct)
                    width_side_B = lot_width * (1 - shift_pct)
                else:
                    width_side_A = lot_width
                    width_side_B = lot_width

                fishbone_max = lot_width * (fishbone / 100.0) if fishbone > 0 else 0

                def _make_widths(base_w, n_seg):
                    if width_var > 0 and n_seg > 1:
                        ws = [base_w * (1 + rng.uniform(-width_var, width_var) / 100.0)
                              for _ in range(n_seg)]
                        if sum(ws) > 0:
                            sc = (line_length - (line_length - n_seg * base_w) / 2) / sum(ws)
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
                    # Side A (+1)
                    n_seg_A = max(1, math.floor(line_length / width_side_A))
                    rem_A = line_length - n_seg_A * width_side_A
                    start_A = rem_A / 2 if uniform_corners else 0
                    widths_A = _make_widths(width_side_A, n_seg_A)
                    cum_A = start_A
                    for i in range(n_seg_A - 1):
                        cum_A += widths_A[i]
                        pt = division_line.interpolate(cum_A).asPoint()
                        fish = rng.uniform(-fishbone_max, fishbone_max) if fishbone > 0 else 0
                        _add_half_line(pt, +1.0, fish)

                    # Side B (-1)
                    n_seg_B = max(1, math.floor(line_length / width_side_B))
                    rem_B = line_length - n_seg_B * width_side_B
                    start_B = rem_B / 2 if uniform_corners else 0
                    widths_B = _make_widths(width_side_B, n_seg_B)
                    cum_B = start_B
                    for i in range(n_seg_B - 1):
                        cum_B += widths_B[i]
                        pt = division_line.interpolate(cum_B).asPoint()
                        fish = rng.uniform(-fishbone_max, fishbone_max) if fishbone > 0 else 0
                        _add_half_line(pt, -1.0, fish)
                else:
                    n_seg = max(1, math.floor(line_length / width_side_A))
                    rem = line_length - n_seg * width_side_A
                    start_off = rem / 2 if uniform_corners else 0
                    widths = _make_widths(width_side_A, n_seg)
                    cumulative = start_off
                    ext_w = max_width * 1.1

                    for i in range(n_seg - 1):
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

        _emit(10, "Çizgiler uzatılıyor...")
        ext_h = processing.run("native:extendlines",
            {'INPUT': h_layer, 'START_DISTANCE': 0.05,
             'END_DISTANCE': 0.05, 'OUTPUT': 'memory:'})['OUTPUT']
        ext_p = processing.run("native:extendlines",
            {'INPUT': p_layer, 'START_DISTANCE': 0.05,
             'END_DISTANCE': 0.05, 'OUTPUT': 'memory:'})['OUTPUT']

        merged_lines = processing.run("native:mergevectorlayers",
            {'LAYERS': [ext_h, ext_p], 'OUTPUT': 'memory:'})['OUTPUT']

        clipped_lines = processing.run("native:clip",
            {'INPUT': merged_lines, 'OVERLAY': working_layer, 'OUTPUT': 'memory:'})['OUTPUT']

        _emit(18, "Poligonlar bölünüyor...")
        split_polygons_layer = processing.run("native:splitwithlines",
            {'INPUT': working_layer, 'LINES': clipped_lines, 'OUTPUT': 'memory:'})['OUTPUT']

        _emit(22, "Küçük ve artık parseller birleştiriliyor...")

        # ── 3-pass residual merging ──
        def _merge_small(layer, threshold_val, min_a):
            areas = {}
            for f in layer.getFeatures():
                g = f.geometry()
                if g and not g.isEmpty():
                    areas[f.id()] = g.area()

            si = QgsSpatialIndex()
            for f in layer.getFeatures():
                si.addFeature(f)

            merged = []
            processed = set()
            for f in layer.getFeatures():
                if f.id() in processed:
                    continue
                fid = f.id()
                if fid not in areas:
                    continue

                should_merge = areas[fid] < threshold_val or areas[fid] < min_a
                if should_merge:
                    nbrs = si.intersects(f.geometry().boundingBox())
                    best = None
                    best_len = 0
                    for nid in nbrs:
                        if nid == fid or nid in processed:
                            continue
                        nf = layer.getFeature(nid)
                        ng = nf.geometry()
                        if ng is None or ng.isEmpty():
                            continue
                        shared = f.geometry().intersection(ng)
                        if shared.type() == QgsWkbTypes.LineGeometry:
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
                    else:
                        merged.append(f)
                        processed.add(fid)
                else:
                    merged.append(f)
                    processed.add(fid)

            merge_count = sum(
                1 for _ in processed if _ in areas and areas.get(_, 999) < threshold_val) // 2
            return merged, merge_count

        fc = split_polygons_layer.featureCount()
        ta = sum(f.geometry().area() for f in split_polygons_layer.getFeatures() if f.geometry())
        thresh = merge_threshold * (ta / fc) if fc > 0 else 0

        current_layer = split_polygons_layer
        final_features = None
        for pass_num in range(3):
            m, mc = _merge_small(current_layer, thresh, min_area)
            _emit(24 + pass_num, f"  Geçiş {pass_num+1}: {mc} parsel birleştirildi.")
            if mc == 0:
                if pass_num == 0:
                    final_features = m
                break
            temp = QgsVectorLayer(f"Polygon?crs={crs_authid}", f"pass{pass_num}", "memory")
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

        # ── build output layer ──
        _emit(28, "Çıktı katmanı oluşturuluyor...")
        output_layer = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "ParcelFlux Parseller", "memory")
        out_provider = output_layer.dataProvider()
        out_provider.addAttributes(fields)
        output_layer.updateFields()

        features_for_sink = []
        count = 0
        parcel_counter = 1
        for feat in final_features:
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            area = geom.area()
            if area > max_area:
                continue
            if area < 5.0:
                continue

            nf = QgsFeature(fields)
            nf.setGeometry(geom)
            src_attrs = list(feat.attributes())
            while len(src_attrs) < working_layer.fields().count():
                src_attrs.append(None)
            src_attrs = src_attrs[:working_layer.fields().count()]
            attrs = src_attrs + [parcel_counter, round(area, 2), '', '', '', 0, False, '']
            nf.setAttributes(attrs)
            features_for_sink.append(nf)
            count += 1
            parcel_counter += 1

        out_provider.addFeatures(features_for_sink)
        output_layer.updateExtents()

        _emit(100, f"Tamamlandı. {count} parsel üretildi.")
        return output_layer, None
