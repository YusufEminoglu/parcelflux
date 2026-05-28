# Changelog

## [0.1.1] - 2026-05-28

### Changed
- Full English-localisation pass: all UI labels, tooltips, status messages, and progress text.
- Facade tracking columns (`facade_front`, `facade_side`, `facade_back`, `facade_count`, `is_corner`, `front_direction`) are now optional — controlled by a checkbox in the dock.
- ID columns (`block_id`, `parcel_id`) are now optional — controlled by a checkbox in the dock.
- Column renamed: `ada_id` → `block_id`, `aream2` → `area_m2`.
- "imad adasi" typo fixed in metadata description.

### Added
- QGIS 3 / QGIS 4 compatibility: `QgsWkbTypes.GeometryType` accessed via try/except fallback.
- Comprehensive `docs/guide.html` — 7-section reference with formulas, parameter tables, algorithm flow, single/double-row detection logic, merging algorithm, output schema, and step-by-step workflow.
- "?" help button in the dock widget opens the HTML guide.
- Documentation menu item under the ParcelFlux menu.

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
