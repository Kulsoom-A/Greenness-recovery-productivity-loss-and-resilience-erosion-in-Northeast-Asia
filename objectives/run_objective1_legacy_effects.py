from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
REL_YEARS = np.arange(-3, 5)

SPEI_DRY_THRESHOLD = -1.0
TMAX_Z_THRESHOLD = 1.0
CLASS_LABELS = {
    1: "ENT",
    2: "EBT",
    3: "DNT",
    4: "DBT",
    5: "SHB",
    6: "GRS",
}


def ensure_dirs() -> None:
    for p in [
        OBJ / "data" / "processed_tif",
        OBJ / "data" / "tabular",
        OBJ / "outputs" / "tables",
        OBJ / "outputs" / "figures",
        OBJ / "logs",
    ]:
        p.mkdir(parents=True, exist_ok=True)


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


def align_gpp_to_template(template_profile: dict) -> np.ndarray:
    out_path = OBJ / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif"
    if out_path.exists():
        arr, _ = read_stack(out_path)
        return arr

    dst_shape = (template_profile["height"], template_profile["width"])
    dst_transform = template_profile["transform"]
    dst_crs = template_profile["crs"]
    aligned = np.full((len(YEARS), *dst_shape), np.nan, dtype="float32")

    for i, year in enumerate(YEARS):
        src_path = DATASETS / "GPP_Annual_NEA" / f"MOD17_GPP_Annual_{year}.tif"
        with rasterio.open(src_path) as src:
            src_arr = src.read(1).astype("float32")
            dst_arr = np.full(dst_shape, np.nan, dtype="float32")
            reproject(
                source=src_arr,
                destination=dst_arr,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            aligned[i] = dst_arr

    profile = template_profile.copy()
    profile.update(count=len(YEARS), dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(aligned)
        for i, year in enumerate(YEARS, start=1):
            dst.set_band_description(i, f"GPP_{year}_aligned")
    return aligned


def write_stack(path: Path, arr: np.ndarray, profile: dict, descriptions: list[str]) -> None:
    out_profile = profile.copy()
    out_profile.update(count=arr.shape[0], dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype("float32"))
        for i, desc in enumerate(descriptions, start=1):
            dst.set_band_description(i, desc)


def write_single(path: Path, arr: np.ndarray, profile: dict, desc: str) -> None:
    write_stack(path, arr[np.newaxis, :, :], profile, [desc])


def spei_december_annual(spei_monthly: np.ndarray) -> np.ndarray:
    annual = []
    for year in range(2001, 2023):
        band_idx = (year - 2001) * 12 + 11
        annual.append(spei_monthly[band_idx])
    return np.stack(annual).astype("float32")


def build_event_mask(tmx: np.ndarray, spei_dec: np.ndarray, forest_mask: np.ndarray) -> np.ndarray:
    tmx_mean = np.nanmean(tmx, axis=0)
    tmx_sd = np.nanstd(tmx, axis=0)
    tmx_z = (tmx - tmx_mean) / tmx_sd
    tmx_z[~np.isfinite(tmx_z)] = np.nan

    events = []
    valid_forest = np.isfinite(forest_mask) & (forest_mask >= 1) & (forest_mask <= 6)
    for year in EVENT_YEARS:
        tmx_i = year - 2001
        spei_i = year - 2001
        event = (
            (spei_dec[spei_i] <= SPEI_DRY_THRESHOLD)
            & (tmx_z[tmx_i] >= TMAX_Z_THRESHOLD)
            & valid_forest
        )
        events.append(event.astype("float32"))
    return np.stack(events)


def response_metrics(response: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    h, w = response.shape[1:]
    loss_sum = np.zeros((h, w), dtype="float64")
    legacy_sum = np.zeros((h, w), dtype="float64")
    event_count = np.zeros((h, w), dtype="float64")
    anomaly_by_rel: dict[int, list[np.ndarray]] = {int(r): [] for r in REL_YEARS}

    for e_i, event_year in enumerate(EVENT_YEARS):
        event = events[e_i].astype(bool)
        baseline = np.nanmean(response[event_year - 2001 - 3 : event_year - 2001], axis=0)
        rel_anoms = {}
        complete = event & np.isfinite(baseline)
        for rel in REL_YEARS:
            arr = response[event_year + rel - 2001] - baseline
            rel_anoms[int(rel)] = arr
            anomaly_by_rel[int(rel)].append(np.where(complete, arr, np.nan))

        post = np.stack([rel_anoms[int(r)] for r in range(0, 5)])
        post_valid = np.all(np.isfinite(post), axis=0)
        valid = complete & post_valid
        cumulative_loss = -np.nansum(np.minimum(post, 0), axis=0)
        legacy_years = np.sum(post[1:5] < 0, axis=0)

        loss_sum[valid] += cumulative_loss[valid]
        legacy_sum[valid] += legacy_years[valid]
        event_count[valid] += 1

    mean_loss = np.divide(loss_sum, event_count, out=np.full((h, w), np.nan), where=event_count > 0)
    mean_legacy = np.divide(legacy_sum, event_count, out=np.full((h, w), np.nan), where=event_count > 0)
    anomaly_mean = {rel: np.nanmean(np.stack(items), axis=0) for rel, items in anomaly_by_rel.items()}
    return mean_loss.astype("float32"), mean_legacy.astype("float32"), anomaly_mean


def summarize_panel(
    k_anoms: dict[int, np.ndarray],
    g_anoms: dict[int, np.ndarray],
    events: np.ndarray,
    forest_mask: np.ndarray,
) -> pd.DataFrame:
    rows = []
    event_frequency = np.nansum(events, axis=0)
    valid_event_pixels = event_frequency > 0

    for cls in sorted(np.unique(forest_mask[np.isfinite(forest_mask)]).astype(int)):
        if cls < 1 or cls > 6:
            continue
        mask = valid_event_pixels & (forest_mask == cls)
        for rel in REL_YEARS:
            k_vals = k_anoms[int(rel)][mask]
            g_vals = g_anoms[int(rel)][mask]
            rows.append(
                {
                    "vegetation_class": CLASS_LABELS.get(cls, cls),
                    "relative_year": int(rel),
                    "n_event_pixels": int(np.sum(mask)),
                    "kNDVI_anomaly_mean": float(np.nanmean(k_vals)),
                    "kNDVI_anomaly_median": float(np.nanmedian(k_vals)),
                    "GPP_anomaly_mean": float(np.nanmean(g_vals)),
                    "GPP_anomaly_median": float(np.nanmedian(g_vals)),
                }
            )

    for rel in REL_YEARS:
        mask = valid_event_pixels
        rows.append(
            {
                "vegetation_class": "all_forest",
                "relative_year": int(rel),
                "n_event_pixels": int(np.sum(mask)),
                "kNDVI_anomaly_mean": float(np.nanmean(k_anoms[int(rel)][mask])),
                "kNDVI_anomaly_median": float(np.nanmedian(k_anoms[int(rel)][mask])),
                "GPP_anomaly_mean": float(np.nanmean(g_anoms[int(rel)][mask])),
                "GPP_anomaly_median": float(np.nanmedian(g_anoms[int(rel)][mask])),
            }
        )
    return pd.DataFrame(rows)


def summarize_legacy(
    k_loss: np.ndarray,
    k_legacy: np.ndarray,
    g_loss: np.ndarray,
    g_legacy: np.ndarray,
    events: np.ndarray,
    forest_mask: np.ndarray,
) -> pd.DataFrame:
    rows = []
    event_frequency = np.nansum(events, axis=0)
    for label, mask in [("all_forest", event_frequency > 0)]:
        rows.append(legacy_row(label, mask, event_frequency, k_loss, k_legacy, g_loss, g_legacy))
    for cls in sorted(np.unique(forest_mask[np.isfinite(forest_mask)]).astype(int)):
        if cls < 1 or cls > 6:
            continue
        mask = (event_frequency > 0) & (forest_mask == cls)
        rows.append(legacy_row(CLASS_LABELS.get(cls, cls), mask, event_frequency, k_loss, k_legacy, g_loss, g_legacy))
    return pd.DataFrame(rows)


def legacy_row(label, mask, event_frequency, k_loss, k_legacy, g_loss, g_legacy) -> dict:
    return {
        "vegetation_class": label,
        "n_pixels_with_events": int(np.sum(mask)),
        "event_frequency_mean": float(np.nanmean(event_frequency[mask])),
        "event_frequency_max": float(np.nanmax(event_frequency[mask])),
        "kNDVI_cumulative_loss_mean": float(np.nanmean(k_loss[mask])),
        "kNDVI_legacy_years_t1_t4_mean": float(np.nanmean(k_legacy[mask])),
        "GPP_cumulative_loss_mean": float(np.nanmean(g_loss[mask])),
        "GPP_legacy_years_t1_t4_mean": float(np.nanmean(g_legacy[mask])),
    }


def plot_panel(panel: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.size": 16,
            "font.weight": "bold",
            "axes.labelweight": "bold",
            "axes.linewidth": 1.4,
        }
    )
    fig, ax = plt.subplots(figsize=(10.8, 6.4))
    df = panel[panel["vegetation_class"].astype(str) == "all_forest"].sort_values("relative_year")
    ax.axhline(0, color="0.30", lw=1.2)
    ax.axvline(0, color="0.45", lw=1.2, ls="--")
    ax.plot(df["relative_year"], df["kNDVI_anomaly_mean"], marker="o", markersize=8, lw=2.4, label="kNDVI")
    ax.plot(df["relative_year"], df["GPP_anomaly_mean"], marker="s", markersize=8, lw=2.4, label="GPP")
    ax.set_xlabel("Relative year", fontweight="bold", fontsize=17, labelpad=10)
    ax.set_ylabel("Mean anomaly from baseline", fontweight="bold", fontsize=17, labelpad=14)
    ax.tick_params(axis="both", labelsize=15, width=1.4, length=5)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    ax.legend(frameon=False, prop={"size": 16, "weight": "bold"})
    fig.subplots_adjust(left=0.19, right=0.96, bottom=0.17, top=0.96)
    fig.savefig(OBJ / "outputs" / "figures" / "objective1_event_panel_all_forest.png", dpi=400)
    plt.close(fig)


def plot_map(arr: np.ndarray, title: str, out_name: str, cmap: str = "viridis") -> None:
    fig, ax = plt.subplots(figsize=(10, 5.4))
    masked = np.ma.masked_invalid(arr)
    im = ax.imshow(masked, cmap=cmap)
    ax.set_xticks([])
    ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.82)
    cbar.ax.tick_params(labelsize=12, width=1.2)
    for tick in cbar.ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.tight_layout()
    out_dir = OBJ / "outputs" / "spatial_maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / out_name, dpi=400)
    plt.close(fig)


def write_raster_inventory(paths: list[Path]) -> None:
    rows = []
    for path in paths:
        with rasterio.open(path) as src:
            rows.append(
                {
                    "file": str(path.relative_to(OBJ)),
                    "bands": src.count,
                    "width": src.width,
                    "height": src.height,
                    "crs": str(src.crs),
                    "nodata": src.nodata,
                    "band_descriptions": "; ".join([d or "" for d in src.descriptions]),
                }
            )
    pd.DataFrame(rows).to_csv(OBJ / "data" / "qgis_ready" / "objective1_qgis_raster_inventory.csv", index=False)


def main() -> None:
    ensure_dirs()
    kndvi, profile = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    tmx, _ = read_stack(DATASETS / "TC_tmx_annual_2001_2024_NEA.tif")
    spei_monthly, _ = read_stack(DATASETS / "SPEI12_monthly_2001_2022_NEA.tif")
    forest_mask, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")

    gpp = align_gpp_to_template(profile)
    spei_dec = spei_december_annual(spei_monthly)
    events = build_event_mask(tmx, spei_dec, forest_mask)

    k_loss, k_legacy, k_anoms = response_metrics(kndvi, events)
    g_loss, g_legacy, g_anoms = response_metrics(gpp, events)

    processed = OBJ / "data" / "processed_tif"
    event_mask_path = processed / "compound_hotdry_event_mask_2004_2020.tif"
    event_frequency_path = processed / "event_frequency_2004_2020.tif"
    k_loss_path = processed / "kNDVI_cumulative_loss_t0_t4_mean.tif"
    k_legacy_path = processed / "kNDVI_legacy_years_t1_t4_mean.tif"
    g_loss_path = processed / "GPP_cumulative_loss_t0_t4_mean.tif"
    g_legacy_path = processed / "GPP_legacy_years_t1_t4_mean.tif"

    write_stack(
        event_mask_path,
        events,
        profile,
        [f"event_{y}" for y in EVENT_YEARS],
    )
    event_frequency = np.nansum(events, axis=0)
    write_single(event_frequency_path, event_frequency, profile, "event_frequency")
    write_single(k_loss_path, k_loss, profile, "kNDVI_cumulative_loss_t0_t4_mean")
    write_single(k_legacy_path, k_legacy, profile, "kNDVI_legacy_years_t1_t4_mean")
    write_single(g_loss_path, g_loss, profile, "GPP_cumulative_loss_t0_t4_mean")
    write_single(g_legacy_path, g_legacy, profile, "GPP_legacy_years_t1_t4_mean")

    panel = summarize_panel(k_anoms, g_anoms, events, forest_mask)
    panel.to_csv(OBJ / "data" / "tabular" / "objective1_event_panel_summary.csv", index=False)
    panel.to_csv(OBJ / "outputs" / "tables" / "objective1_event_panel_summary.csv", index=False)

    legacy = summarize_legacy(k_loss, k_legacy, g_loss, g_legacy, events, forest_mask)
    legacy.to_csv(OBJ / "outputs" / "tables" / "objective1_legacy_summary_by_class.csv", index=False)

    plot_panel(panel)
    plot_map(event_frequency, "Compound hot-dry event frequency, 2004-2020", "event_frequency_2004_2020.png", "magma")
    plot_map(k_legacy, "Mean kNDVI legacy years, t+1 to t+4", "kNDVI_legacy_years_t1_t4_mean.png", "YlOrRd")
    plot_map(g_legacy, "Mean GPP legacy years, t+1 to t+4", "GPP_legacy_years_t1_t4_mean.png", "YlOrRd")
    plot_map(k_loss, "Mean kNDVI cumulative loss, t0 to t+4", "kNDVI_cumulative_loss_t0_t4_mean.png", "viridis")
    plot_map(g_loss, "Mean GPP cumulative loss, t0 to t+4", "GPP_cumulative_loss_t0_t4_mean.png", "viridis")
    write_raster_inventory([event_mask_path, event_frequency_path, k_loss_path, k_legacy_path, g_loss_path, g_legacy_path])

    metadata = {
        "event_definition": {
            "spei_metric": "December SPEI-12 from monthly SPEI12 stack",
            "spei_dry_threshold": SPEI_DRY_THRESHOLD,
            "tmax_metric": "annual Tmax z-score by pixel over 2001-2024",
            "tmax_z_threshold": TMAX_Z_THRESHOLD,
            "event_years": [int(y) for y in EVENT_YEARS],
            "panel_relative_years": [int(r) for r in REL_YEARS],
        },
        "notes": [
            "GPP rasters were bilinearly aligned to the kNDVI/TerraClimate 0.1-degree grid.",
            "Baseline is mean response over t-3 to t-1 for each event.",
            "Cumulative loss is the positive magnitude of summed negative anomalies from t0 to t4.",
            "Legacy years count negative post-event anomalies from t+1 to t+4.",
        ],
    }
    (OBJ / "logs" / "objective1_run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Objective 1 complete")
    print(legacy.to_string(index=False))


if __name__ == "__main__":
    main()
