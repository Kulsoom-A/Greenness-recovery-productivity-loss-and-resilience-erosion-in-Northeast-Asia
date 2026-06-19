from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
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
SPEI_SENSITIVITY = [-0.8, -1.0, -1.2]
TMAX_Z_SENSITIVITY = [0.8, 1.0, 1.2]
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
CLASS_LABELS = {
    1: "ENT",
    2: "EBT",
    3: "DNT",
    4: "DBT",
    5: "SHB",
    6: "GRS",
}


def class_label(value):
    try:
        return CLASS_LABELS.get(int(value), value)
    except (TypeError, ValueError):
        return value


def ensure_dirs() -> None:
    for p in [
        OBJ2 / "data" / "processed_tif",
        OBJ2 / "data" / "tabular",
        OBJ2 / "outputs" / "tables",
        OBJ2 / "outputs" / "figures",
        OBJ2 / "outputs" / "spatial_maps",
        OBJ2 / "logs",
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


def spei_december_annual(spei_monthly: np.ndarray) -> np.ndarray:
    annual = []
    for year in range(2001, 2023):
        band_idx = (year - 2001) * 12 + 11
        annual.append(spei_monthly[band_idx])
    return np.stack(annual).astype("float32")


def build_event_mask(tmx: np.ndarray, spei_dec: np.ndarray, forest_mask: np.ndarray, spei_threshold: float, tmax_z_threshold: float) -> np.ndarray:
    tmx_mean = np.nanmean(tmx, axis=0)
    tmx_sd = np.nanstd(tmx, axis=0)
    tmx_z = (tmx - tmx_mean) / tmx_sd
    tmx_z[~np.isfinite(tmx_z)] = np.nan
    valid_forest = np.isfinite(forest_mask) & (forest_mask >= 1) & (forest_mask <= 6)
    events = []
    for year in EVENT_YEARS:
        tmx_i = year - 2001
        spei_i = year - 2001
        event = (
            (spei_dec[spei_i] <= spei_threshold)
            & (tmx_z[tmx_i] >= tmax_z_threshold)
            & valid_forest
        )
        events.append(event.astype("float32"))
    return np.stack(events)


def write_single(path: Path, arr: np.ndarray, profile: dict, desc: str) -> None:
    out_profile = profile.copy()
    out_profile.update(count=1, dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype("float32"), 1)
        dst.set_band_description(1, desc)


def event_metric_maps(response: np.ndarray, events: np.ndarray) -> dict[str, np.ndarray]:
    h, w = response.shape[1:]
    first_resistance = np.full((h, w), np.nan, dtype="float32")
    first_recovery = np.full((h, w), np.nan, dtype="float32")
    first_loss = np.full((h, w), np.nan, dtype="float32")

    later_res_sum = np.zeros((h, w), dtype="float64")
    later_rec_sum = np.zeros((h, w), dtype="float64")
    later_loss_sum = np.zeros((h, w), dtype="float64")
    later_count = np.zeros((h, w), dtype="float64")
    event_count = np.zeros((h, w), dtype="float64")

    for e_i, event_year in enumerate(EVENT_YEARS):
        event = events[e_i].astype(bool)
        baseline = np.nanmean(response[event_year - 2001 - 3 : event_year - 2001], axis=0)
        anoms = np.stack([response[event_year + rel - 2001] - baseline for rel in REL_YEARS])
        valid = event & np.isfinite(baseline) & np.all(np.isfinite(anoms), axis=0)

        resistance = anoms[0]
        cumulative_loss = -np.nansum(np.minimum(anoms, 0), axis=0)
        recovered = anoms[1:] >= 0
        any_recovered = np.any(recovered, axis=0)
        recovery_time = np.where(any_recovered, np.argmax(recovered, axis=0) + 1, 5).astype("float32")

        is_first = valid & (event_count == 0)
        is_later = valid & (event_count > 0)

        first_resistance[is_first] = resistance[is_first]
        first_recovery[is_first] = recovery_time[is_first]
        first_loss[is_first] = cumulative_loss[is_first]

        later_res_sum[is_later] += resistance[is_later]
        later_rec_sum[is_later] += recovery_time[is_later]
        later_loss_sum[is_later] += cumulative_loss[is_later]
        later_count[is_later] += 1
        event_count[valid] += 1

    later_resistance = np.divide(
        later_res_sum, later_count, out=np.full((h, w), np.nan), where=later_count > 0
    ).astype("float32")
    later_recovery = np.divide(
        later_rec_sum, later_count, out=np.full((h, w), np.nan), where=later_count > 0
    ).astype("float32")
    later_loss = np.divide(
        later_loss_sum, later_count, out=np.full((h, w), np.nan), where=later_count > 0
    ).astype("float32")

    recurrent = later_count > 0
    return {
        "event_count": event_count.astype("float32"),
        "later_count": later_count.astype("float32"),
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


def summarize_metric_maps(k: dict[str, np.ndarray], g: dict[str, np.ndarray], forest: np.ndarray) -> pd.DataFrame:
    rows = []
    recurrent = k["later_count"] > 0
    groups: list[tuple[str | int, np.ndarray]] = [("all_recurrent_forest", recurrent)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((cls, recurrent & (forest == cls)))

    for label, mask in groups:
        label_out = class_label(label)
        rows.append(
            {
                "vegetation_class": label_out,
                "n_recurrent_pixels": int(np.sum(mask)),
                "event_count_mean": float(np.nanmean(k["event_count"][mask])),
                "kNDVI_first_resistance_mean": float(np.nanmean(k["first_resistance"][mask])),
                "kNDVI_later_resistance_mean": float(np.nanmean(k["later_resistance"][mask])),
                "kNDVI_delta_resistance_mean": float(np.nanmean(k["delta_resistance_later_minus_first"][mask])),
                "kNDVI_first_recovery_time_mean": float(np.nanmean(k["first_recovery_time"][mask])),
                "kNDVI_later_recovery_time_mean": float(np.nanmean(k["later_recovery_time"][mask])),
                "kNDVI_delta_recovery_time_mean": float(np.nanmean(k["delta_recovery_later_minus_first"][mask])),
                "kNDVI_first_cumulative_loss_mean": float(np.nanmean(k["first_cumulative_loss"][mask])),
                "kNDVI_later_cumulative_loss_mean": float(np.nanmean(k["later_cumulative_loss"][mask])),
                "kNDVI_delta_cumulative_loss_mean": float(np.nanmean(k["delta_cumulative_loss_later_minus_first"][mask])),
                "GPP_first_resistance_mean": float(np.nanmean(g["first_resistance"][mask])),
                "GPP_later_resistance_mean": float(np.nanmean(g["later_resistance"][mask])),
                "GPP_delta_resistance_mean": float(np.nanmean(g["delta_resistance_later_minus_first"][mask])),
                "GPP_first_recovery_time_mean": float(np.nanmean(g["first_recovery_time"][mask])),
                "GPP_later_recovery_time_mean": float(np.nanmean(g["later_recovery_time"][mask])),
                "GPP_delta_recovery_time_mean": float(np.nanmean(g["delta_recovery_later_minus_first"][mask])),
                "GPP_first_cumulative_loss_mean": float(np.nanmean(g["first_cumulative_loss"][mask])),
                "GPP_later_cumulative_loss_mean": float(np.nanmean(g["later_cumulative_loss"][mask])),
                "GPP_delta_cumulative_loss_mean": float(np.nanmean(g["delta_cumulative_loss_later_minus_first"][mask])),
            }
        )
    return pd.DataFrame(rows)


def bootstrap_delta_ci(k: dict[str, np.ndarray], g: dict[str, np.ndarray], forest: np.ndarray) -> pd.DataFrame:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    recurrent = k["later_count"] > 0
    groups: list[tuple[str | int, np.ndarray]] = [("all_recurrent_forest", recurrent)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((cls, recurrent & (forest == cls)))

    rows = []
    metrics = {
        "kNDVI_delta_recovery_time": k["delta_recovery_later_minus_first"],
        "kNDVI_delta_cumulative_loss": k["delta_cumulative_loss_later_minus_first"],
        "GPP_delta_recovery_time": g["delta_recovery_later_minus_first"],
        "GPP_delta_cumulative_loss": g["delta_cumulative_loss_later_minus_first"],
    }
    for label, mask in groups:
        label_out = class_label(label)
        for metric_name, arr in metrics.items():
            vals = arr[mask]
            vals = vals[np.isfinite(vals)]
            if vals.size < 30:
                rows.append(
                    {
                        "vegetation_class": label_out,
                        "metric": metric_name,
                        "n": int(vals.size),
                        "mean": float(np.nanmean(vals)) if vals.size else np.nan,
                        "ci_low": np.nan,
                        "ci_high": np.nan,
                    }
                )
                continue
            sample_idx = rng.integers(0, vals.size, size=(BOOTSTRAP_N, vals.size))
            boot_means = vals[sample_idx].mean(axis=1)
            rows.append(
                {
                    "vegetation_class": label_out,
                    "metric": metric_name,
                    "n": int(vals.size),
                    "mean": float(vals.mean()),
                    "ci_low": float(np.percentile(boot_means, 2.5)),
                    "ci_high": float(np.percentile(boot_means, 97.5)),
                }
            )
    return pd.DataFrame(rows)


def sensitivity_summary(kndvi: np.ndarray, gpp: np.ndarray, tmx: np.ndarray, spei_dec: np.ndarray, forest: np.ndarray) -> pd.DataFrame:
    rows = []
    for spei_threshold in SPEI_SENSITIVITY:
        for tmax_threshold in TMAX_Z_SENSITIVITY:
            events = build_event_mask(tmx, spei_dec, forest, spei_threshold, tmax_threshold)
            k = event_metric_maps(kndvi, events)
            g = event_metric_maps(gpp, events)
            recurrent = k["later_count"] > 0
            rows.append(
                {
                    "spei_threshold": spei_threshold,
                    "tmax_z_threshold": tmax_threshold,
                    "n_recurrent_pixels": int(np.sum(recurrent)),
                    "event_count_mean": float(np.nanmean(k["event_count"][recurrent])),
                    "kNDVI_delta_recovery_time_mean": float(np.nanmean(k["delta_recovery_later_minus_first"][recurrent])),
                    "kNDVI_delta_cumulative_loss_mean": float(np.nanmean(k["delta_cumulative_loss_later_minus_first"][recurrent])),
                    "GPP_delta_recovery_time_mean": float(np.nanmean(g["delta_recovery_later_minus_first"][recurrent])),
                    "GPP_delta_cumulative_loss_mean": float(np.nanmean(g["delta_cumulative_loss_later_minus_first"][recurrent])),
                }
            )
    return pd.DataFrame(rows)


def plot_first_later(summary: pd.DataFrame) -> None:
    row = summary[summary["vegetation_class"].astype(str) == "all_recurrent_forest"].iloc[0]
    plt.rcParams.update(
        {
            "font.size": 13,
            "font.weight": "bold",
            "axes.labelweight": "bold",
            "axes.linewidth": 1.2,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    metrics = [
        ("kNDVI", row["kNDVI_first_cumulative_loss_mean"], row["kNDVI_later_cumulative_loss_mean"]),
        ("GPP", row["GPP_first_cumulative_loss_mean"], row["GPP_later_cumulative_loss_mean"]),
    ]
    for ax, (name, first, later) in zip(axes, metrics):
        ax.bar(["First", "Later"], [first, later], color=["#4C78A8", "#F58518"], width=0.62)
        ax.set_ylabel("Mean cumulative loss")
        ax.text(0.02, 0.95, "A" if name == "kNDVI" else "B", transform=ax.transAxes, fontsize=16, fontweight="bold", va="top")
        ax.text(0.5, 0.90, name, transform=ax.transAxes, ha="center", fontsize=14, fontweight="bold")
        ax.tick_params(axis="both", labelsize=12, width=1.2)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.16, top=0.95, wspace=0.32)
    fig.savefig(OBJ2 / "outputs" / "figures" / "first_vs_later_cumulative_loss.png", dpi=400)
    plt.close(fig)


def plot_classwise_delta(summary: pd.DataFrame, ci: pd.DataFrame) -> None:
    df = summary[summary["vegetation_class"].astype(str) != "all_recurrent_forest"].copy()
    df["vegetation_class"] = df["vegetation_class"].astype(str)
    ci_g = ci[ci["metric"] == "GPP_delta_cumulative_loss"].copy()
    ci_g["vegetation_class"] = ci_g["vegetation_class"].astype(str)
    df = df.merge(ci_g[["vegetation_class", "ci_low", "ci_high"]], on="vegetation_class", how="left")
    vals = df["GPP_delta_cumulative_loss_mean"].to_numpy()
    lower = vals - df["ci_low"].to_numpy()
    upper = df["ci_high"].to_numpy() - vals

    plt.rcParams.update(
        {
            "font.size": 13,
            "font.weight": "bold",
            "axes.labelweight": "bold",
            "axes.linewidth": 1.2,
        }
    )
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    x = np.arange(len(df))
    ax.axhline(0, color="0.25", lw=1.1)
    ax.bar(x, vals, color="#4C78A8", width=0.68)
    ax.errorbar(x, vals, yerr=np.vstack([lower, upper]), fmt="none", ecolor="black", elinewidth=1.2, capsize=4)
    ax.set_xticks(x)
    ax.set_xticklabels(df["vegetation_class"])
    ax.set_xlabel("Vegetation class")
    ax.set_ylabel("Later-minus-first GPP cumulative loss")
    ax.tick_params(axis="both", labelsize=12, width=1.2)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.18, top=0.97)
    fig.savefig(OBJ2 / "outputs" / "figures" / "classwise_GPP_delta_cumulative_loss_ci.png", dpi=400)
    plt.close(fig)


def plot_map(arr: np.ndarray, out_name: str, cmap: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.4))
    im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap)
    ax.set_xticks([])
    ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.82)
    cbar.ax.tick_params(labelsize=11)
    for tick in cbar.ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.tight_layout()
    fig.savefig(OBJ2 / "outputs" / "spatial_maps" / out_name, dpi=400)
    plt.close(fig)


def plot_recurrent_context(event_count: np.ndarray, delta_gpp: np.ndarray) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.0))
    arrays = [np.where(event_count >= 2, event_count, np.nan), delta_gpp]
    cmaps = ["magma", "viridis"]
    labels = ["A", "B"]
    for ax, arr, cmap, label in zip(axes, arrays, cmaps, labels):
        im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(0.02, 0.95, label, transform=ax.transAxes, fontsize=16, fontweight="bold", va="top")
        cbar = fig.colorbar(im, ax=ax, shrink=0.78)
        cbar.ax.tick_params(labelsize=10)
    fig.tight_layout()
    fig.savefig(OBJ2 / "outputs" / "figures" / "recurrent_event_context_and_GPP_loss_map.png", dpi=400)
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    kndvi, profile = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    events, _ = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    tmx, _ = read_stack(DATASETS / "TC_tmx_annual_2001_2024_NEA.tif")
    spei_monthly, _ = read_stack(DATASETS / "SPEI12_monthly_2001_2022_NEA.tif")
    spei_dec = spei_december_annual(spei_monthly)

    k = event_metric_maps(kndvi, events)
    g = event_metric_maps(gpp, events)
    summary = summarize_metric_maps(k, g, forest)
    ci = bootstrap_delta_ci(k, g, forest)
    sensitivity = sensitivity_summary(kndvi, gpp, tmx, spei_dec, forest)

    processed = OBJ2 / "data" / "processed_tif"
    for prefix, metrics in [("kNDVI", k), ("GPP", g)]:
        for key in [
            "event_count",
            "later_count",
            "delta_resistance_later_minus_first",
            "delta_recovery_later_minus_first",
            "delta_cumulative_loss_later_minus_first",
        ]:
            write_single(processed / f"{prefix}_{key}.tif", metrics[key], profile, f"{prefix}_{key}")

    summary.to_csv(OBJ2 / "data" / "tabular" / "event_order_summary_by_class.csv", index=False)
    summary.to_csv(OBJ2 / "outputs" / "tables" / "event_order_summary_by_class.csv", index=False)
    ci.to_csv(OBJ2 / "outputs" / "tables" / "event_order_bootstrap_ci.csv", index=False)
    sensitivity.to_csv(OBJ2 / "outputs" / "tables" / "event_order_threshold_sensitivity.csv", index=False)

    plot_first_later(summary)
    plot_classwise_delta(summary, ci)
    plot_recurrent_context(k["event_count"], g["delta_cumulative_loss_later_minus_first"])
    plot_map(k["delta_recovery_later_minus_first"], "kNDVI_delta_recovery_later_minus_first.png", "coolwarm")
    plot_map(g["delta_recovery_later_minus_first"], "GPP_delta_recovery_later_minus_first.png", "coolwarm")
    plot_map(k["delta_cumulative_loss_later_minus_first"], "kNDVI_delta_cumulative_loss_later_minus_first.png", "viridis")
    plot_map(g["delta_cumulative_loss_later_minus_first"], "GPP_delta_cumulative_loss_later_minus_first.png", "viridis")

    metadata = {
        "analysis": "first versus later compound hot-dry events at recurrent-event pixels",
        "event_mask_source": str((OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif").relative_to(ROOT)),
        "metrics": {
            "resistance": "event-year anomaly relative to t-3 to t-1 baseline",
            "recovery_time": "first post-event year t+1 to t+4 with anomaly >= 0; assigned 5 if not recovered by t+4",
            "cumulative_loss": "positive magnitude of summed negative anomalies from t0 to t+4",
            "delta": "mean later-event metric minus first-event metric at the same pixel",
        },
    }
    (OBJ2 / "logs" / "event_order_run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Event-order analysis complete")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
