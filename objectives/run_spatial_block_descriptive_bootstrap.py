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
OBJ4 = ROOT / "Paper2_Objectives" / "04_kNDVI_GPP_recovery_mismatch"
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"

BOOTSTRAP_N = 1000
SEED = 61
BLOCK_DEG = 10.0


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


def block_ids_for_grid(profile: dict) -> np.ndarray:
    h, w = profile["height"], profile["width"]
    transform = profile["transform"]
    cols = np.arange(w, dtype="float32")
    rows = np.arange(h, dtype="float32")
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    lon, lat = np.meshgrid(lon, lat)
    lon_block = np.floor(lon / BLOCK_DEG).astype(np.int16)
    lat_block = np.floor(lat / BLOCK_DEG).astype(np.int16)
    return (lat_block.astype("int32") * 1000 + lon_block.astype("int32")).astype("int32")


def block_bootstrap_metric(arr: np.ndarray, mask: np.ndarray, block_ids: np.ndarray, metric: str) -> dict:
    valid = mask & np.isfinite(arr)
    if not np.any(valid):
        return {}
    vals = arr[valid]
    blocks = block_ids[valid]
    uniq = np.unique(blocks)
    block_sum = np.array([np.nansum(vals[blocks == b]) for b in uniq], dtype="float64")
    block_count = np.array([np.sum(blocks == b) for b in uniq], dtype="float64")
    metric_seed = sum((i + 1) * ord(ch) for i, ch in enumerate(metric))
    rng = np.random.default_rng(SEED + metric_seed % 10000)
    boot = np.empty(BOOTSTRAP_N, dtype="float64")
    n_blocks = len(uniq)
    for i in range(BOOTSTRAP_N):
        idx = rng.integers(0, n_blocks, size=n_blocks)
        boot[i] = block_sum[idx].sum() / block_count[idx].sum()
    observed = float(np.nanmean(vals))
    return {
        "metric": metric,
        "n_pixels": int(vals.size),
        "n_blocks": int(n_blocks),
        "observed_mean": observed,
        "block_bootstrap_mean": float(np.mean(boot)),
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
    }


def main() -> None:
    (OBJ99 / "outputs" / "tables").mkdir(parents=True, exist_ok=True)
    (OBJ99 / "logs").mkdir(parents=True, exist_ok=True)

    events, profile = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    event_frequency = np.nansum(events, axis=0)
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    valid_event = (event_frequency > 0) & np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    block_ids = block_ids_for_grid(profile)

    k_loss, _ = read_single(OBJ1 / "data" / "processed_tif" / "kNDVI_cumulative_loss_t0_t4_mean.tif")
    g_loss, _ = read_single(OBJ1 / "data" / "processed_tif" / "GPP_cumulative_loss_t0_t4_mean.tif")
    k_legacy, _ = read_single(OBJ1 / "data" / "processed_tif" / "kNDVI_legacy_years_t1_t4_mean.tif")
    g_legacy, _ = read_single(OBJ1 / "data" / "processed_tif" / "GPP_legacy_years_t1_t4_mean.tif")
    mismatch, _ = read_single(OBJ4 / "data" / "processed_tif" / "hidden_mismatch_duration_t1_t4.tif")
    k_delta_loss, _ = read_single(OBJ2 / "data" / "processed_tif" / "kNDVI_delta_cumulative_loss_later_minus_first.tif")
    g_delta_loss, _ = read_single(OBJ2 / "data" / "processed_tif" / "GPP_delta_cumulative_loss_later_minus_first.tif")
    k_delta_recovery, _ = read_single(OBJ2 / "data" / "processed_tif" / "kNDVI_delta_recovery_later_minus_first.tif")
    g_delta_recovery, _ = read_single(OBJ2 / "data" / "processed_tif" / "GPP_delta_recovery_later_minus_first.tif")
    recurrent_count, _ = read_single(OBJ2 / "data" / "processed_tif" / "GPP_later_count.tif")
    recurrent = valid_event & np.isfinite(g_delta_loss) & (recurrent_count > 0)

    metrics = [
        (k_loss, valid_event, "kNDVI_cumulative_loss"),
        (g_loss, valid_event, "GPP_cumulative_loss"),
        (k_legacy, valid_event, "kNDVI_legacy_duration"),
        (g_legacy, valid_event, "GPP_legacy_duration"),
        (mismatch, valid_event & np.isfinite(mismatch), "hidden_mismatch_duration"),
        ((mismatch > 0).astype("float32"), valid_event & np.isfinite(mismatch), "hidden_mismatch_prevalence"),
        (k_delta_recovery, recurrent & np.isfinite(k_delta_recovery), "kNDVI_later_minus_first_recovery_time"),
        (k_delta_loss, recurrent & np.isfinite(k_delta_loss), "kNDVI_later_minus_first_cumulative_loss"),
        (g_delta_recovery, recurrent & np.isfinite(g_delta_recovery), "GPP_later_minus_first_recovery_time"),
        (g_delta_loss, recurrent & np.isfinite(g_delta_loss), "GPP_later_minus_first_cumulative_loss"),
    ]
    rows = [block_bootstrap_metric(arr, mask, block_ids, metric) for arr, mask, metric in metrics]
    out = pd.DataFrame([r for r in rows if r])
    out.to_csv(OBJ99 / "outputs" / "tables" / "spatial_block_descriptive_bootstrap.csv", index=False)
    metadata = {
        "block_size_degrees": BLOCK_DEG,
        "n_iterations": BOOTSTRAP_N,
        "seed": SEED,
        "method": "Resample 10-degree geographic blocks with replacement and recompute weighted means from block sums and counts.",
    }
    (OBJ99 / "logs" / "spatial_block_descriptive_bootstrap_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ99 / "outputs" / "tables" / "spatial_block_descriptive_bootstrap.csv")


if __name__ == "__main__":
    main()
