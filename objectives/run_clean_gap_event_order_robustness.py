from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ2 = ROOT / "Paper2_Objectives" / "02_event_order_resistance_recovery"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
REL_YEARS = np.arange(0, 5)
CLEAN_GAP_YEARS = 8
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}


def read_stack(path: Path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as src:
        arr = src.read().astype("float32")
        profile = src.profile.copy()
        nodata = src.nodata
    if nodata is not None and not np.isnan(nodata):
        arr[arr == nodata] = np.nan
    return arr, profile


def read_single(path: Path) -> tuple[np.ndarray, dict]:
    arr, profile = read_stack(path)
    return arr[0], profile


def event_metrics(response: np.ndarray, event_year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    baseline = np.nanmean(response[event_year - 2001 - 3 : event_year - 2001], axis=0)
    anoms = np.stack([response[event_year + rel - 2001] - baseline for rel in REL_YEARS])
    valid = np.isfinite(baseline) & np.all(np.isfinite(anoms), axis=0)
    resistance = anoms[0].astype("float32")
    cumulative_loss = (-np.nansum(np.minimum(anoms, 0), axis=0)).astype("float32")
    recovered = anoms[1:] >= 0
    any_recovered = np.any(recovered, axis=0)
    recovery_time = np.where(any_recovered, np.argmax(recovered, axis=0) + 1, 5).astype("float32")
    return resistance, recovery_time, cumulative_loss, valid


def clean_gap_maps(response: np.ndarray, events: np.ndarray) -> dict[str, np.ndarray]:
    h, w = response.shape[1:]
    first_resistance = np.full((h, w), np.nan, dtype="float32")
    first_recovery = np.full((h, w), np.nan, dtype="float32")
    first_loss = np.full((h, w), np.nan, dtype="float32")
    first_year = np.full((h, w), np.nan, dtype="float32")
    previous_year = np.full((h, w), np.nan, dtype="float32")
    event_count = np.zeros((h, w), dtype="float32")

    later_res_sum = np.zeros((h, w), dtype="float64")
    later_rec_sum = np.zeros((h, w), dtype="float64")
    later_loss_sum = np.zeros((h, w), dtype="float64")
    clean_later_count = np.zeros((h, w), dtype="float64")

    for e_i, event_year in enumerate(EVENT_YEARS):
        event = events[e_i].astype(bool)
        resistance, recovery, loss, valid_metric = event_metrics(response, int(event_year))
        valid = event & valid_metric
        is_first = valid & (event_count == 0)
        has_prior_clean_gap = valid & (event_count > 0) & np.isfinite(previous_year) & ((event_year - previous_year) >= CLEAN_GAP_YEARS)

        first_resistance[is_first] = resistance[is_first]
        first_recovery[is_first] = recovery[is_first]
        first_loss[is_first] = loss[is_first]
        first_year[is_first] = event_year

        later_res_sum[has_prior_clean_gap] += resistance[has_prior_clean_gap]
        later_rec_sum[has_prior_clean_gap] += recovery[has_prior_clean_gap]
        later_loss_sum[has_prior_clean_gap] += loss[has_prior_clean_gap]
        clean_later_count[has_prior_clean_gap] += 1

        previous_year[valid] = event_year
        event_count[valid] += 1

    later_resistance = np.divide(later_res_sum, clean_later_count, out=np.full((h, w), np.nan), where=clean_later_count > 0).astype("float32")
    later_recovery = np.divide(later_rec_sum, clean_later_count, out=np.full((h, w), np.nan), where=clean_later_count > 0).astype("float32")
    later_loss = np.divide(later_loss_sum, clean_later_count, out=np.full((h, w), np.nan), where=clean_later_count > 0).astype("float32")
    recurrent = clean_later_count > 0
    return {
        "event_count": event_count,
        "clean_later_count": clean_later_count.astype("float32"),
        "first_resistance": first_resistance,
        "later_resistance": later_resistance,
        "delta_resistance_later_minus_first": np.where(recurrent, later_resistance - first_resistance, np.nan).astype("float32"),
        "first_recovery_time": first_recovery,
        "later_recovery_time": later_recovery,
        "delta_recovery_later_minus_first": np.where(recurrent, later_recovery - first_recovery, np.nan).astype("float32"),
        "first_cumulative_loss": first_loss,
        "later_cumulative_loss": later_loss,
        "delta_cumulative_loss_later_minus_first": np.where(recurrent, later_loss - first_loss, np.nan).astype("float32"),
    }


def summarize(k: dict[str, np.ndarray], g: dict[str, np.ndarray], forest: np.ndarray) -> pd.DataFrame:
    rows = []
    recurrent = k["clean_later_count"] > 0
    groups = [("all_clean_gap_recurrent_forest", recurrent)]
    groups.extend((CLASS_LABELS[c], recurrent & (forest == c)) for c in range(1, 7))
    for label, mask in groups:
        rows.append(
            {
                "vegetation_class": label,
                "n_clean_gap_recurrent_pixels": int(np.sum(mask)),
                "event_count_mean": float(np.nanmean(k["event_count"][mask])),
                "clean_later_count_mean": float(np.nanmean(k["clean_later_count"][mask])),
                "kNDVI_delta_recovery_time_mean": float(np.nanmean(k["delta_recovery_later_minus_first"][mask])),
                "kNDVI_delta_cumulative_loss_mean": float(np.nanmean(k["delta_cumulative_loss_later_minus_first"][mask])),
                "GPP_first_cumulative_loss_mean": float(np.nanmean(g["first_cumulative_loss"][mask])),
                "GPP_later_cumulative_loss_mean": float(np.nanmean(g["later_cumulative_loss"][mask])),
                "GPP_delta_recovery_time_mean": float(np.nanmean(g["delta_recovery_later_minus_first"][mask])),
                "GPP_delta_cumulative_loss_mean": float(np.nanmean(g["delta_cumulative_loss_later_minus_first"][mask])),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    for path in [OBJ2 / "outputs" / "tables", OBJ2 / "logs"]:
        path.mkdir(parents=True, exist_ok=True)
    kndvi, _ = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    events, _ = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")

    k = clean_gap_maps(kndvi, events)
    g = clean_gap_maps(gpp, events)
    summary = summarize(k, g, forest)
    summary.to_csv(OBJ2 / "outputs" / "tables" / "clean_gap_event_order_summary.csv", index=False)
    metadata = {
        "clean_gap_years": CLEAN_GAP_YEARS,
        "definition": "A later event is clean-gap eligible only when the previous valid event at the same pixel occurred at least 8 years earlier, so the later event's t-3..t-1 baseline cannot overlap the previous event's t0..t+4 response window.",
    }
    (OBJ2 / "logs" / "clean_gap_event_order_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ2 / "outputs" / "tables" / "clean_gap_event_order_summary.csv")


if __name__ == "__main__":
    main()
