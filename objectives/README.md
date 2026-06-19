# Paper 2 Objective Workspace

This folder separates the second manuscript workflow by objective while keeping the original downloaded datasets untouched in `../Datasets`.

## Main Manuscript Aim

Quantify whether recurrent compound hot-dry extremes produce multi-year legacy effects, delayed recovery, and progressive resilience erosion in Northeast Asian forest-steppe vegetation, and test whether greenness-based recovery masks persistent productivity losses.

## Folder Map

- `00_shared`: common inventories, manuscript notes, QGIS projects, shared figures, shared tables, and reusable scripts.
- `01_legacy_effects_kNDVI_GPP`: Objective 1, multi-year legacy effects on kNDVI and GPP from pre-event years to four post-event years.
- `02_event_order_resistance_recovery`: Objective 2, comparison of earlier versus later hot-dry events at the same pixel.
- `03_vulnerability_hotspots_resilience_erosion`: Objective 3, link Paper 1 vulnerability classes with Paper 2 erosion hotspots.
- `04_kNDVI_GPP_recovery_mismatch`: Objective 4, identify where greenness recovery overstates productivity recovery.
- `05_drivers_controls_legacy_loss`: Objective 5, explain legacy duration, cumulative loss, and recovery failure using climate, vegetation, recurrence, and vulnerability controls.
- `99_validation_robustness`: sensitivity checks, validation layers, disturbance masks, and alternative event thresholds.

## Standard Subfolders Inside Each Objective

- `data/raw_tif`: raw or copied raster inputs needed only for this objective.
- `data/processed_tif`: cleaned, clipped, resampled, masked, or derived raster outputs.
- `data/tabular`: CSV, Parquet, Excel, or panel tables.
- `data/qgis_ready`: final GIS-ready rasters/vectors prepared for QGIS.
- `scripts`: objective-specific scripts.
- `notebooks`: exploratory notebooks and checked analysis notebooks.
- `outputs/tables`: manuscript-ready tables and intermediate summary tables.
- `outputs/figures`: plots, charts, and figure panels.
- `outputs/spatial_maps`: exported maps and spatial figure panels.
- `outputs/qgis_layers`: shapefiles, geopackages, layer styles, and QGIS exports.
- `outputs/model_outputs`: fitted models, predictions, diagnostics, and saved metrics.
- `docs`: methods notes, assumptions, formulas, and objective-specific decisions.
- `logs`: run logs and processing notes.

## Current Source Data

The current source data remain in `../Datasets` and include:

- Climate and water balance rasters: TerraClimate annual `aet`, `def`, `pdsi`, `pet`, `pr`, `tmn`, `tmx` for 2001-2024.
- Drought raster: monthly `SPEI12` for 2001-2022.
- Vegetation/productivity rasters: annual `kNDVI`, annual `NDVI`, NDVI trend/Sen outputs, MOD17 annual `GPP`, and MOD17 annual `NPP`.
- Land-cover mask: `LC5_forest_1to6_2024_NEA_0p1deg.tif`.

