# ParcelFlux

Divide zoning blocks (imar adalar) into parcels with configurable width, asymmetry, and organic boundary offset.

> **Template:** `new_plugin_dock` — toolbar + menu + togglable dock panel. Single-tool dock-based plugin.

## Architecture

- `main_plugin.py` — toolbar/menu registration, dock toggle
- `dialogs/dock.py` — dock widget with parameter form and run button
- `algorithms/parcel_flux_core.py` — standalone parcel subdivision engine

## Features

- Automatic single/double-row detection based on block short-edge width
- Configurable parcel width with random variation
- Row-width asymmetry: different parcel widths for front and rear rows
- Fishbone offset: organic zigzag at division-line endpoints
- H-line shift: move the centerline for asymmetric depth
- 3-pass residual merging: fragments below threshold are merged into the neighbor with the longest shared edge

## Installation

Install from QGIS Plugin Hub or download the zip from [Releases](https://github.com/YusufEminoglu/ParcelFlux/releases).

## License

GPL-3.0-or-later.

[Changelog](CHANGELOG.md) · [PlanX monorepo](https://github.com/YusufEminoglu)
