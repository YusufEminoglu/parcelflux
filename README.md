# ParcelFlux

Block-to-parcel subdivision engine for QGIS. Divides zoning blocks into individual parcels with tunable geometric variation: configurable width, row asymmetry, fishbone boundary offset, H-line shift, and automatic residual fragment merging.

## Features

- **Single/double-row auto-detection** - narrow blocks receive one parcel row; wider blocks split into two opposing rows
- **Width variation** - per-parcel random width within a configurable range for natural-looking streetscapes
- **Row width asymmetry** - different parcel widths on opposite sides of the centerline
- **Fishbone offset** - organic zigzag at division-line endpoints that mimics traditional settlement morphology
- **H-line shift** - asymmetric front/rear depths by moving the centerline away from the geometric midpoint
- **3-pass residual merging** - sliver polygons below a configurable area threshold are merged into the neighbor with the longest shared edge
- **Robust geometry normalization** - irregular or invalid blocks are repaired and subdivided on their oriented bounding box
- **Collision-safe output schema** - generated fields fall back to `planx_*` names when the source layer already has ParcelFlux field names

## Current hardening

- Single-row blocks no longer receive an unwanted h-line split.
- `max_area=0` disables the upper area filter instead of filtering every parcel.
- Residual merge progress now reports actual merge operations.
- QGIS Plugin Hub metadata uses a bundled PNG icon (`icons/parcelflux.png`).

## Architecture

```
parcelflux/
  __init__.py                      # classFactory entry point
  main_plugin.py                   # QGIS plugin lifecycle (menu, toolbar, dock toggle)
  metadata.txt                     # Plugin metadata for QGIS Hub
  dialogs/
    dock.py                        # Dock widget with full parameter UI and run logic
  algorithms/
    parcel_flux_core.py            # Standalone subdivision engine (no ProcessingProvider)
  docs/
    guide.html                     # Comprehensive algorithm & parameter reference
  icons/
    parcelflux.png
```

## Compatibility

| QGIS 3.28+ | QGIS 4.x |
|---|---|
| Minimum | Supported via `qgisMaximumVersion=4.99` |

The plugin uses stable PyQGIS APIs and native Processing algorithms. No external dependencies beyond QGIS itself.

## Installation

Install from the QGIS Plugin Hub or download the latest zip from [Releases](https://github.com/YusufEminoglu/parcelflux/releases).

## Quick Start

1. Load a zoning block polygon layer
2. Open **ParcelFlux** from the menu or toolbar
3. Select the block layer, set your target lot width, and click **RUN**
4. The parcel layer is added to the map

## Documentation

See **[docs/guide.html](docs/guide.html)** for a complete reference covering the algorithm, every parameter with formulas, and a step-by-step workflow.

## License

GPL-3.0-or-later.
