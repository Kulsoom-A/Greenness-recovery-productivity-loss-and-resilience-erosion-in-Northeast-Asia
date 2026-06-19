# Greenness recovery masks persistent productivity loss and resilience erosion after compound hot–dry events in Northeast Asia

This repository holds the analysis code for the manuscript above. It documents the
processing pipeline used to detect compound hot–dry events, quantify multi-year
greenness and productivity legacies, test for resilience erosion under recurrent
events, identify locations where greenness recovery overstates productivity
recovery, and validate the results against an independent fluorescence-based
productivity proxy.

The code is organised by research objective. Each objective folder is
self-contained and reproduces one stage of the analysis from the input rasters
described in `DATA_AVAILABILITY.md`. Raw and processed raster data are **not**
included here because of their size and because every layer is either publicly
available or regenerable from the scripts; see `DATA_AVAILABILITY.md` for sources
and access.

## Study region and period

Northeast Asia, covering boreal, cold-temperate, montane, and dryland vegetation
transitions. Annual layers span 2001–2024; the SPEI-12 drought series spans
2001–2022. The analysis grid is 0.1° with a forest land-cover mask applied.

## Repository layout

```
.
├── objectives/                     Analysis code, organised by objective
│   ├── 00_shared/                  Reusable helpers and figure-building scripts
│   ├── 01_legacy_effects_kNDVI_GPP/        Obj 1: multi-year kNDVI and GPP legacies
│   ├── 02_event_order_resistance_recovery/ Obj 2: earlier vs later events at a pixel
│   ├── 03_vulnerability_hotspots_resilience_erosion/ Obj 3: vulnerability–hotspot links
│   ├── 04_kNDVI_GPP_recovery_mismatch/     Obj 4: greenness–productivity decoupling
│   ├── 05_drivers_controls_legacy_loss/    Obj 5: drivers of legacy loss and recovery failure
│   ├── 06_sif_productivity_validation/     Obj 6: SIF-based productivity validation
│   └── 99_validation_robustness/           Robustness, sensitivity, disturbance screening
├── manuscript_scripts/             Scripts that assemble manuscript figures and documents
├── notebooks/                      Exploratory notebooks (kNDVI, NDVI trend analysis)
├── DATA_AVAILABILITY.md            Input datasets, sources, and access notes
├── requirements.txt                Python dependencies
├── CITATION.cff                    Citation metadata
└── LICENSE                         MIT licence (code)
```

Each objective folder carries its own `README.md` describing inputs, processing
steps, and outputs. The objective numbering follows the manuscript workflow rather
than the order of figures.

## Processing pipeline

The objectives run in sequence, but each can be executed independently once the
input rasters listed in `DATA_AVAILABILITY.md` are in place.

1. **Event detection.** Compound hot–dry events are identified per pixel and year
   from December SPEI-12 and annual maximum temperature (`objectives/01_*`).
2. **Legacy estimation.** Greenness (kNDVI) and productivity (MOD17 GPP)
   anomalies are tracked from the pre-event year to four post-event years to
   estimate resistance, recovery, and cumulative loss (`objectives/01_*`).
3. **Event order and resilience erosion.** Earlier and later events at the same
   pixel are compared to test whether response weakens with recurrence
   (`objectives/02_*`).
4. **Vulnerability hotspots.** Erosion hotspots are linked to prior vulnerability
   classes (`objectives/03_*`).
5. **Greenness–productivity mismatch.** Pixels where greenness recovery overstates
   productivity recovery are mapped (`objectives/04_*`).
6. **Drivers.** Legacy duration, cumulative loss, and recovery failure are
   explained using climate, vegetation, recurrence, and vulnerability predictors
   (`objectives/05_*`).
7. **Independent validation.** Results are cross-checked against GOSIF
   solar-induced fluorescence and the EVI/NIRv structural indices
   (`objectives/06_*`).
8. **Robustness.** Threshold sensitivity, spatial-block bootstrap, event-timing
   null models, and external disturbance screening (`objectives/99_*`).

## Reproducing the analysis

1. Install dependencies: `pip install -r requirements.txt`.
2. Obtain the input rasters following `DATA_AVAILABILITY.md` and place them where
   each objective's `README.md` expects them.
3. Run the objective scripts in numerical order. Scripts named
   `download_*_gee.py` regenerate the Google Earth Engine inputs (EVI, NIRv, and
   disturbance-control layers) and require an authenticated `earthengine-api`
   account.

GIS layers were inspected and styled in QGIS; the `objectives/00_shared`
scripts generate the QGIS figure guides used for the manuscript maps.

## Data availability

Raster inputs are not stored in this repository. Their sources, products, and
access details are listed in `DATA_AVAILABILITY.md`. Derived rasters, tables, and
figures can be regenerated from the scripts; a permanent archive of the processed
outputs can be added to a data repository (for example Zenodo) and linked here on
acceptance.

## Licence

Code is released under the MIT Licence (`LICENSE`). Third-party datasets remain
under the licences of their original providers as noted in `DATA_AVAILABILITY.md`.
