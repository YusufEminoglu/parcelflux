# Changelog

## [0.3.7] - 2026-10-28

- Help opens the online guide on geophilo.com instead of a copy bundled in the plugin package; the plugin no longer ships documentation HTML.

## [0.3.6] - 2026-10-27

- Hub listing text rewritten; author Geophilo; university affiliation removed; documentation links use the standard geophilo.com pages (manual.html, guide/); manual HTML no longer ships in the plugin package.

## [0.3.5] - 2026-09-17

- Upgraded official plugin icon to high-end tactile 3D brand identity (isometric 45°, slim teal pedestal, full bleed transparent canvas).
- Synchronized documentation, repository and issue tracker endpoints to GeoPhilo and GitLab.

## [0.3.4] - 2026-09-06

- Migrate repository and documentation URLs to GitLab and Cloudflare Pages.

## [0.3.3] - 2026-08-07

- Added online user manual link (https://yusufeminoglu.github.io/parcelflux/) and GitHub repository star call-to-action.

## [0.3.2] - 2026-08-07

- Expanded reference manual: subdivision theory, geometry math, 14 DOI refs

## [0.3.1] - 2026-08-03

### Changed
- Replace every hardcoded colour in the dock and seed gallery with palette-derived
  tokens. The entire UI now stays readable under QGIS light, dark, high-contrast,
  and custom themes while keeping the ParcelFlux green accent identity.
- Add a dedicated `theme.py` module with WCAG contrast-ratio checks, palette-aware
  colour tokens, and a single `apply_adaptive_theme()` entry point.
- The dock listens for `PaletteChange` events and re-applies the theme
  automatically when the user switches between QGIS themes.
- Guide and About HTML content now injects palette colours at render time instead
  of assuming a light background.

## [0.3.0] - 2026-07-29

Complete, verifiable subdivision: nothing is deleted behind your back, planning
rules are enforced during the split, and the plugin finally has a test suite.

### Fixed
- **Oversized parcels are no longer deleted.** `max_area` used to filter parcels out of the output, which punched holes into the block. With the default 2000 m² limit a block whose natural parcels came out larger produced **zero parcels**, and in a multi-block run individual blocks disappeared with no message at all. Oversized parcels are now bisected across the long axis of their oriented bounding box until they fit; a cut that would produce a sliver is abandoned and the parcel is kept whole and flagged. Output area now equals input area.
- **Slivers are merged instead of dropped.** Parcels under 5 m² join the merge passes rather than being discarded at write time; only numerical dust below 0.01 m² is skipped.
- **Plugin unload leaves no orphan toolbar.** `removeToolBar()` only detaches a toolbar from the layout, so the bar stayed a child of the QGIS window until the deferred delete ran — a plugin reload could leave the old bar behind. The toolbar, menu and actions are now explicitly re-parented before deletion.

### Added
- **Minimum frontage** constraint — parcels whose street frontage (the part of their boundary on the block boundary) falls below the threshold are merged into the neighbour sharing the longest edge. Values at or above the lot width are rejected up front instead of merging everything away.
- **Parcel depth** — pin the front row to an explicit depth in metres instead of splitting the block at its midpoint. Clamped to 15–85% of the block's cross dimension, and takes precedence over H-line shift.
- **Corner widening** — grow the first and last parcel of each row by a percentage, as many zoning codes require for corner plots; interior parcels absorb the difference so the row span is unchanged.
- **`pf_flag` output column** — empty for a conforming parcel, otherwise `oversize`, `undersize`, or `narrow`. Flagged parcels stay in the output on purpose.
- **Quality report** after every run: parcel and block counts, coverage %, area min/mean/median/max, mean frontage, and merge/re-split counts. Coverage below 100% now surfaces immediately instead of hiding in the geometry.
- **Preview before adding** (on by default) — results arrive as a dashed temporary layer with *Apply* and *Discard*.
- **Compare seeds** — run the same parameters against four seeds and choose from rendered side-by-side thumbnails with each candidate's statistics.

### Changed
- Frontage measurement is backed by a spatial index over the block boundary edges rather than a linear scan per parcel segment, which is what makes the frontage constraint affordable.
- `max_area` below `min_area` is now rejected as a contradiction instead of silently emptying the output.

### Quality
- **First automated tests in the plugin's history**: `tests/smoke_core.py` (17 engine checks — coverage invariants, seed determinism, each planning constraint, parameter validation, irregular-block containment) and `tests/smoke_dock.py` (16 UI and lifecycle checks — parameter plumbing, report card, preview Apply/Discard, seed gallery rendering, plugin load/unload). Both suites pass on QGIS 3.44 LTR and QGIS 4.2. The unload defect above was found by writing them.

## [0.2.13] - 2026-07-21

- Fix splitwithlines freeze on irregular blocks: split the OBB (fast, 4-vertex rectangle) and clip parcels back to the real block footprint — same accurate result as v0.2.10's "keep real geometry" intent without the hang.

## [0.2.12] - 2026-07-14

- Add width-to-depth facade ratio field and resolve all security scan warnings

## [0.2.11] - 2026-06-18

- docs: add CITATION.cff for Zenodo DOI integration

## [0.2.10] - 2026-06-05

- Keep irregular parcels inside actual block geometry.

## [0.2.9] - 2026-06-05

- Preserve CRS across ParcelFlux memory layers.

## [0.2.8] - 2026-06-05

- Harden core validation and Processing cancellation feedback.

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
