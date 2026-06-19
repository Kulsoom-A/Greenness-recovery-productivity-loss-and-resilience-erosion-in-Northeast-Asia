# Data availability

This repository contains analysis code only. The raster and vector inputs are not
redistributed here because of their size and because each layer is publicly
available from its original provider or regenerable from the included scripts.
The datasets used for the manuscript are listed below with their products and
access routes. Annual layers cover 2001–2024 unless noted; all layers were
resampled to a common 0.1° grid over the Northeast Asia domain and masked to
forest land cover.

## Vegetation greenness and structure

- **kNDVI, NDVI** — derived from MODIS surface reflectance (MOD09/MOD13 family),
  computed as annual composites. Available through NASA LP DAAC and Google Earth
  Engine.
- **EVI, NIRv** — annual composites generated in Google Earth Engine; the
  regeneration script is `objectives/06_sif_productivity_validation/scripts/download_evi_nirv_gee_fixed.py`.

## Productivity

- **MOD17 GPP and NPP (annual)** — MODIS MOD17A2H/MOD17A3HGF gross and net primary
  productivity, NASA LP DAAC.
- **GOSIF (annual)** — global solar-induced chlorophyll fluorescence product
  (Li and Xiao), used as an independent productivity proxy for validation.
  Distributed as annual GeoTIFFs by the University of New Hampshire Global
  Ecology group.

## Climate and drought

- **TerraClimate (annual)** — maximum temperature (`tmx`), minimum temperature
  (`tmn`), precipitation (`pr`), potential evapotranspiration (`pet`), actual
  evapotranspiration (`aet`), climatic water deficit (`def`), and PDSI, from the
  Climatology Lab TerraClimate dataset.
- **SPEI-12 (monthly, 2001–2022)** — 12-month Standardised Precipitation
  Evapotranspiration Index used for event definition (December value per year).

## Land cover and study area

- **Forest land-cover mask** — `LC5_forest_1to6_2024_NEA_0p1deg.tif`, derived from
  a MODIS/ESA land-cover product reclassified to forest classes 1–6 at 0.1°.
- **Study-area boundary** — `NEAFinal` shapefile delineating the Northeast Asia
  analysis domain.

## External disturbance controls

- Fire, harvest, and other disturbance layers used to screen confounding events
  are generated in Google Earth Engine by
  `objectives/99_validation_robustness/scripts/download_disturbance_controls_gee.py`.

## Regenerating and archiving processed data

The processed rasters, panel tables, and figures reported in the manuscript can be
reproduced by running the objective scripts in order against the inputs above.
A permanent archive of the processed outputs (for example a Zenodo deposit) can be
created at acceptance and its DOI added to this file and to the manuscript's data
availability statement.

> Zenodo/figshare DOI.
