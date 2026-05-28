# Changelog

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
