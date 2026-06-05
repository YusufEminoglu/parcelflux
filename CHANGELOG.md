# Changelog

## [0.2.7] - 2026-06-05

- Propagate cooperative cancellation into core processing checkpoints.

## [0.2.6] - 2026-06-05

- Replace unsafe QThread termination with cooperative cancellation and safe worker detach on plugin unload.

## [0.2.5] - 2026-06-05

- Compute facade frontage, side frontage, rear-edge proxy, corner flag, and front direction fields.

## [0.2.4] - 2026-06-05

- Harden dock toggle, plugin unload cleanup, and worker completion signal handling.

## [0.2.3] - 2026-06-05

- Fix single-row subdivision, invalid-geometry OBB splitting, output field collisions, max-area disable behavior, and PNG icon packaging.

## [0.2.2] - 2026-05-30

- Revised plugin icon to SVG

## [0.2.1] - 2026-05-28

### Fixed
- **Invalid-geometry crash.** `native:orientedminimumboundingbox` aborted the whole pipeline when the user's global Processing setting *Invalid features filtering* was set to "Stop algorithm execution" and any input block had a self-intersection or other GEOS-invalid geometry (`Feature (N) from "irregular" has invalid geometry`). Fixed in three layers:
  1. Every `processing.run` call now passes a private `QgsProcessingContext` with `setInvalidGeometryCheck(QgsFeatureRequest.GeometrySkipInvalid)`, so the plugin is independent of the user's global setting.
  2. The irregular layer is pre-fixed through `native:fixgeometries` before being sent to the OBB step.
  3. Self-intersecting 4-vertex polygons (e.g. bowtie shapes) are now routed through the irregular/repair path too — vertex count alone no longer decides "regular".
- Null / empty / unreadable geometries are skipped silently and reported in the status line.

## [0.2.0] - 2026-05-28

### Fixed
- **Layer combo now lists polygon layers.** Previous filter value (`setFilters(3)`) corresponded to *raster + no-geometry* layers, so polygon layers were hidden and the user could not pick a block layer. Now uses `QgsMapLayerProxyModel.PolygonLayer` explicitly.

### Added
- Tabbed dock UI: **Run**, **Guide**, **About**. The Guide tab is a self-contained in-panel HTML reference covering algorithm, every parameter, formulas, recipes and troubleshooting.
- Slider + spinbox combo controls for every numeric parameter — tactile and precise.
- **Configurable random seed** with a re-roll button (🎲). Same seed reproduces the same parcels.
- **"Use selected features only"** mode — run on the current selection instead of the whole layer.
- **Reset defaults** button.
- Live status banner with severity colour coding (info / ok / warn / error).
- Layer metadata strip showing feature count and CRS.

### Changed
- Reject geographic-CRS inputs early with a clear, actionable error message instead of producing garbage at the geometry step.
- Modernised stylesheet (cards, brand green accent, subtle borders) without breaking on dark themes.

## [0.1.1] - 2026-05-28

### Changed
- Full English-localisation pass: all UI labels, tooltips, status messages, and progress text.
- Facade tracking columns (`facade_front`, `facade_side`, `facade_back`, `facade_count`, `is_corner`, `front_direction`) are now optional — controlled by a checkbox in the dock.
- ID columns (`block_id`, `parcel_id`) are now optional — controlled by a checkbox in the dock.
- Column renamed: `ada_id` → `block_id`, `aream2` → `area_m2`.
- "imad adasi" typo fixed in metadata description.

All notable changes to **ParcelFlux** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · versioning: [SemVer](https://semver.org/).

## [0.1.0] - 2026-05-28

### Added
- Initial dock-based release.
- Block-to-parcel subdivision with configurable width, min/max area, and merge threshold.
- Single/double-row auto-detection based on short-edge width.
- Row-width asymmetry for differentiated front/rear parcel widths.
- Fishbone-style organic boundary offset.
- H-line shift for asymmetric depth.
- 3-pass residual fragment merging (longest-shared-edge neighbor).
- Per-parcel width variation with random seed for reproducibility.
- Optional ID columns (block_id, parcel_id) and facade tracking columns.
- Comprehensive HTML documentation with formulas and parameter reference.
- QGIS 3.28+ and QGIS 4.x compatibility.
