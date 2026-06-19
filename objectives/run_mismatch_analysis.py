from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ4 = ROOT / "Paper2_Objectives" / "04_kNDVI_GPP_recovery_mismatch"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

EVENT_YEARS = np.arange(2004, 2021)
POST_RELS = np.arange(1, 5)
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 43
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
        OBJ4 / "data" / "processed_tif",
        OBJ4 / "data" / "tabular",
        OBJ4 / "outputs" / "tables",
        OBJ4 / "outputs" / "figures",
        OBJ4 / "outputs" / "spatial_maps",
        OBJ4 / "logs",
        PUBFIG,
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


def write_single(path: Path, arr: np.ndarray, profile: dict, desc: str) -> None:
    out_profile = profile.copy()
    out_profile.update(count=1, dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype("float32"), 1)
        dst.set_band_description(1, desc)


def class_label(value) -> str:
    try:
        return CLASS_LABELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def compute_event_anomaly_means(response: np.ndarray, events: np.ndarray) -> tuple[dict[int, np.ndarray], np.ndarray]:
    h, w = response.shape[1:]
    sums = {int(rel): np.zeros((h, w), dtype="float64") for rel in POST_RELS}
    counts = {int(rel): np.zeros((h, w), dtype="float64") for rel in POST_RELS}
    event_count = np.zeros((h, w), dtype="float64")

    for e_i, event_year in enumerate(EVENT_YEARS):
        event = events[e_i].astype(bool)
        baseline = np.nanmean(response[event_year - 2001 - 3 : event_year - 2001], axis=0)
        complete = event & np.isfinite(baseline)
        for rel in POST_RELS:
            arr = response[event_year + rel - 2001] - baseline
            valid = complete & np.isfinite(arr)
            sums[int(rel)][valid] += arr[valid]
            counts[int(rel)][valid] += 1
        event_count[complete] += 1

    means = {
        rel: np.divide(sums[rel], counts[rel], out=np.full((h, w), np.nan), where=counts[rel] > 0).astype("float32")
        for rel in sums
    }
    return means, event_count.astype("float32")


def standardize_by_valid(arr: np.ndarray, valid: np.ndarray) -> np.ndarray:
    vals = arr[valid & np.isfinite(arr)]
    mean = np.nanmean(vals)
    sd = np.nanstd(vals)
    out = (arr - mean) / sd
    out[~np.isfinite(out)] = np.nan
    return out.astype("float32")


def mismatch_maps(k_anoms: dict[int, np.ndarray], g_anoms: dict[int, np.ndarray], forest: np.ndarray) -> dict[str, np.ndarray]:
    valid_forest = np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    hidden_years = []
    mismatch_intensity = []
    k_recovered_years = []
    g_suppressed_years = []
    for rel in POST_RELS:
        k = k_anoms[int(rel)]
        g = g_anoms[int(rel)]
        valid = valid_forest & np.isfinite(k) & np.isfinite(g)
        hidden = valid & (k >= 0) & (g < 0)
        hidden_years.append(hidden.astype("float32"))
        k_recovered_years.append((valid & (k >= 0)).astype("float32"))
        g_suppressed_years.append((valid & (g < 0)).astype("float32"))

        k_z = standardize_by_valid(k, valid)
        g_z = standardize_by_valid(g, valid)
        mismatch_intensity.append(np.where(valid, k_z - g_z, np.nan).astype("float32"))

    hidden_stack = np.stack(hidden_years)
    k_recovered_stack = np.stack(k_recovered_years)
    g_suppressed_stack = np.stack(g_suppressed_years)
    intensity_stack = np.stack(mismatch_intensity)
    valid_any = np.sum(np.isfinite(intensity_stack), axis=0) > 0

    hidden_duration = np.sum(hidden_stack, axis=0).astype("float32")
    k_recovery_duration = np.sum(k_recovered_stack, axis=0).astype("float32")
    g_suppression_duration = np.sum(g_suppressed_stack, axis=0).astype("float32")
    mean_intensity = np.nanmean(intensity_stack, axis=0).astype("float32")
    hidden_fraction = np.divide(hidden_duration, g_suppression_duration, out=np.full_like(hidden_duration, np.nan), where=g_suppression_duration > 0)

    hidden_duration[~valid_any] = np.nan
    k_recovery_duration[~valid_any] = np.nan
    g_suppression_duration[~valid_any] = np.nan
    hidden_fraction[~valid_any] = np.nan

    return {
        "hidden_mismatch_duration_t1_t4": hidden_duration,
        "kNDVI_recovery_duration_t1_t4": k_recovery_duration,
        "GPP_suppression_duration_t1_t4": g_suppression_duration,
        "mean_standardized_mismatch_intensity_t1_t4": mean_intensity,
        "hidden_fraction_of_GPP_suppression_t1_t4": hidden_fraction.astype("float32"),
    }


def summarize(maps: dict[str, np.ndarray], forest: np.ndarray, event_count: np.ndarray) -> pd.DataFrame:
    rows = []
    valid = np.isfinite(maps["hidden_mismatch_duration_t1_t4"]) & (event_count > 0)
    groups: list[tuple[str, np.ndarray]] = [("all_event_forest", valid)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((class_label(cls), valid & (forest == cls)))

    for label, mask in groups:
        rows.append(
            {
                "vegetation_class": label,
                "n_pixels": int(np.sum(mask)),
                "event_count_mean": float(np.nanmean(event_count[mask])),
                "hidden_mismatch_duration_mean": float(np.nanmean(maps["hidden_mismatch_duration_t1_t4"][mask])),
                "hidden_mismatch_prevalence": float(np.nanmean(maps["hidden_mismatch_duration_t1_t4"][mask] > 0)),
                "kNDVI_recovery_duration_mean": float(np.nanmean(maps["kNDVI_recovery_duration_t1_t4"][mask])),
                "GPP_suppression_duration_mean": float(np.nanmean(maps["GPP_suppression_duration_t1_t4"][mask])),
                "mismatch_intensity_mean": float(np.nanmean(maps["mean_standardized_mismatch_intensity_t1_t4"][mask])),
                "hidden_fraction_of_GPP_suppression_mean": float(np.nanmean(maps["hidden_fraction_of_GPP_suppression_t1_t4"][mask])),
            }
        )
    return pd.DataFrame(rows)


def bootstrap_ci(maps: dict[str, np.ndarray], forest: np.ndarray, event_count: np.ndarray) -> pd.DataFrame:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    valid = np.isfinite(maps["hidden_mismatch_duration_t1_t4"]) & (event_count > 0)
    groups: list[tuple[str, np.ndarray]] = [("all_event_forest", valid)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((class_label(cls), valid & (forest == cls)))

    metric_arrays = {
        "hidden_mismatch_duration": maps["hidden_mismatch_duration_t1_t4"],
        "hidden_mismatch_prevalence": (maps["hidden_mismatch_duration_t1_t4"] > 0).astype("float32"),
        "mismatch_intensity": maps["mean_standardized_mismatch_intensity_t1_t4"],
        "hidden_fraction_of_GPP_suppression": maps["hidden_fraction_of_GPP_suppression_t1_t4"],
    }
    rows = []
    for label, mask in groups:
        for metric, arr in metric_arrays.items():
            vals = arr[mask]
            vals = vals[np.isfinite(vals)]
            if vals.size < 30:
                continue
            idx = rng.integers(0, vals.size, size=(BOOTSTRAP_N, vals.size))
            boot_means = vals[idx].mean(axis=1)
            rows.append(
                {
                    "vegetation_class": label,
                    "metric": metric,
                    "n": int(vals.size),
                    "mean": float(vals.mean()),
                    "ci_low": float(np.percentile(boot_means, 2.5)),
                    "ci_high": float(np.percentile(boot_means, 97.5)),
                }
            )
    return pd.DataFrame(rows)


def setup_theme() -> None:
    sns.set_theme(
        context="paper",
        style="whitegrid",
        font="Arial",
        rc={
            "figure.dpi": 120,
            "savefig.dpi": 600,
            "axes.edgecolor": "black",
            "axes.linewidth": 0.8,
            "axes.labelsize": 10.5,
            "axes.labelweight": "bold",
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "grid.color": "#E5E5E5",
            "grid.linewidth": 0.7,
        },
    )


def finish_axes(ax) -> None:
    ax.grid(True, axis="y", color="#E5E5E5", linewidth=0.7)
    ax.grid(False, axis="x")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")


def save_pubfig(fig, name: str) -> None:
    fig.savefig(PUBFIG / f"{name}.png", bbox_inches="tight", facecolor="white")
    fig.savefig(PUBFIG / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_class_mismatch(summary: pd.DataFrame, ci: pd.DataFrame) -> None:
    df = summary[summary["vegetation_class"] != "all_event_forest"].copy()
    ci_df = ci[ci["metric"] == "hidden_mismatch_prevalence"].copy()
    df = df.merge(ci_df[["vegetation_class", "ci_low", "ci_high"]], on="vegetation_class", how="left")
    order = ["ENT", "EBT", "DNT", "DBT", "SHB", "GRS"]
    df["vegetation_class"] = pd.Categorical(df["vegetation_class"], categories=order, ordered=True)
    df = df.sort_values("vegetation_class")

    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    sns.barplot(data=df, x="vegetation_class", y="hidden_mismatch_prevalence", color="#1B9E77", ax=ax)
    y = df["hidden_mismatch_prevalence"].to_numpy()
    ax.errorbar(
        range(len(df)),
        y,
        yerr=[y - df["ci_low"].to_numpy(), df["ci_high"].to_numpy() - y],
        fmt="none",
        color="black",
        capsize=3,
        linewidth=0.9,
    )
    ax.set_xlabel("Vegetation class")
    ax.set_ylabel("Hidden productivity-loss prevalence")
    ax.set_ylim(0, min(1.0, max(0.15, np.nanmax(df["ci_high"]) + 0.05)))
    finish_axes(ax)
    save_pubfig(fig, "figure4_hidden_mismatch_by_class_ggstyle")


def plot_mismatch_scatter(k_anoms: dict[int, np.ndarray], g_anoms: dict[int, np.ndarray], forest: np.ndarray) -> None:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    k = k_anoms[2]
    g = g_anoms[2]
    valid = np.isfinite(k) & np.isfinite(g) & np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    idx = np.flatnonzero(valid.ravel())
    if idx.size > 50000:
        idx = rng.choice(idx, size=50000, replace=False)
    classes = forest.ravel()[idx].astype(int)
    df = pd.DataFrame(
        {
            "kNDVI anomaly": k.ravel()[idx],
            "GPP anomaly": g.ravel()[idx],
            "Vegetation class": [class_label(c) for c in classes],
        }
    )
    order = ["ENT", "EBT", "DNT", "DBT", "SHB", "GRS"]
    df["Vegetation class"] = pd.Categorical(df["Vegetation class"], categories=order, ordered=True)
    fig, ax = plt.subplots(figsize=(5.6, 4.2))
    sns.scatterplot(
        data=df,
        x="kNDVI anomaly",
        y="GPP anomaly",
        hue="Vegetation class",
        alpha=0.28,
        s=8,
        linewidth=0,
        palette="Set2",
        ax=ax,
    )
    ax.axhline(0, color="black", linewidth=0.7)
    ax.axvline(0, color="black", linewidth=0.7)
    ax.legend(frameon=False, title=None, ncol=2, loc="lower left", markerscale=2)
    finish_axes(ax)
    save_pubfig(fig, "figure5_kNDVI_GPP_mismatch_scatter_t2_ggstyle")


def plot_map(arr: np.ndarray, out_name: str, cmap: str = "viridis") -> None:
    fig, ax = plt.subplots(figsize=(10, 5.4))
    im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap)
    ax.set_xticks([])
    ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.82)
    cbar.ax.tick_params(labelsize=11)
    fig.tight_layout()
    fig.savefig(OBJ4 / "outputs" / "spatial_maps" / out_name, dpi=400)
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    setup_theme()
    kndvi, profile = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    events, _ = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")

    k_anoms, event_count = compute_event_anomaly_means(kndvi, events)
    g_anoms, _ = compute_event_anomaly_means(gpp, events)
    maps = mismatch_maps(k_anoms, g_anoms, forest)
    summary = summarize(maps, forest, event_count)
    ci = bootstrap_ci(maps, forest, event_count)

    processed = OBJ4 / "data" / "processed_tif"
    for name, arr in maps.items():
        write_single(processed / f"{name}.tif", arr, profile, name)
    write_single(processed / "event_count_for_mismatch_pixels.tif", event_count, profile, "event_count_for_mismatch_pixels")

    summary.to_csv(OBJ4 / "data" / "tabular" / "mismatch_summary_by_class.csv", index=False)
    summary.to_csv(OBJ4 / "outputs" / "tables" / "mismatch_summary_by_class.csv", index=False)
    ci.to_csv(OBJ4 / "outputs" / "tables" / "mismatch_bootstrap_ci.csv", index=False)

    plot_class_mismatch(summary, ci)
    plot_mismatch_scatter(k_anoms, g_anoms, forest)
    plot_map(maps["hidden_mismatch_duration_t1_t4"], "hidden_mismatch_duration_t1_t4.png", "magma")
    plot_map(maps["mean_standardized_mismatch_intensity_t1_t4"], "mean_standardized_mismatch_intensity_t1_t4.png", "viridis")
    plot_map(maps["hidden_fraction_of_GPP_suppression_t1_t4"], "hidden_fraction_of_GPP_suppression_t1_t4.png", "YlGnBu")

    metadata = {
        "analysis": "kNDVI-GPP hidden productivity-loss mismatch after compound hot-dry events",
        "definition": "hidden mismatch year = kNDVI anomaly >= 0 and GPP anomaly < 0 during t+1 to t+4",
        "baseline": "t-3 to t-1 mean for each event",
        "event_mask_source": str((OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif").relative_to(ROOT)),
    }
    (OBJ4 / "logs" / "mismatch_run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Mismatch analysis complete")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
