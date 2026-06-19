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
OBJ3 = ROOT / "Paper2_Objectives" / "03_vulnerability_hotspots_resilience_erosion"
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

EVENT_YEARS = np.arange(2004, 2021)
POST_RELS = np.arange(1, 5)
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}
TREE_CLASSES = [1, 2, 3, 4]


def ensure_dirs() -> None:
    for p in [
        OBJ99 / "data" / "processed_tif",
        OBJ99 / "data" / "qgis_ready",
        OBJ99 / "data" / "tabular",
        OBJ99 / "outputs" / "tables",
        OBJ99 / "outputs" / "figures",
        OBJ99 / "logs",
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


def greenness_gpp_mismatch(green_anoms: dict[int, np.ndarray], gpp_anoms: dict[int, np.ndarray], forest: np.ndarray) -> dict[str, np.ndarray]:
    valid_forest = np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    hidden = []
    green_recovered = []
    gpp_suppressed = []
    for rel in POST_RELS:
        green = green_anoms[int(rel)]
        gpp = gpp_anoms[int(rel)]
        valid = valid_forest & np.isfinite(green) & np.isfinite(gpp)
        hidden.append((valid & (green >= 0) & (gpp < 0)).astype("float32"))
        green_recovered.append((valid & (green >= 0)).astype("float32"))
        gpp_suppressed.append((valid & (gpp < 0)).astype("float32"))
    hidden_stack = np.stack(hidden)
    valid_any = np.sum(np.isfinite(hidden_stack), axis=0) > 0
    hidden_duration = np.sum(hidden_stack, axis=0).astype("float32")
    green_recovery = np.sum(np.stack(green_recovered), axis=0).astype("float32")
    gpp_suppression = np.sum(np.stack(gpp_suppressed), axis=0).astype("float32")
    hidden_fraction = np.divide(hidden_duration, gpp_suppression, out=np.full_like(hidden_duration, np.nan), where=gpp_suppression > 0)
    hidden_duration[~valid_any] = np.nan
    green_recovery[~valid_any] = np.nan
    gpp_suppression[~valid_any] = np.nan
    hidden_fraction[~valid_any] = np.nan
    return {
        "hidden_duration": hidden_duration,
        "green_recovery_duration": green_recovery,
        "gpp_suppression_duration": gpp_suppression,
        "hidden_fraction": hidden_fraction.astype("float32"),
    }


def summarize_mismatch(name: str, maps: dict[str, np.ndarray], forest: np.ndarray, event_count: np.ndarray) -> pd.DataFrame:
    valid = np.isfinite(maps["hidden_duration"]) & (event_count > 0)
    groups: list[tuple[str, np.ndarray]] = [("all_event_forest", valid)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((CLASS_LABELS[cls], valid & (forest == cls)))
    rows = []
    for label, mask in groups:
        rows.append(
            {
                "greenness_index": name,
                "vegetation_class": label,
                "n_pixels": int(np.sum(mask)),
                "hidden_mismatch_prevalence": float(np.nanmean(maps["hidden_duration"][mask] > 0)),
                "hidden_mismatch_duration_mean": float(np.nanmean(maps["hidden_duration"][mask])),
                "green_recovery_duration_mean": float(np.nanmean(maps["green_recovery_duration"][mask])),
                "gpp_suppression_duration_mean": float(np.nanmean(maps["gpp_suppression_duration"][mask])),
                "hidden_fraction_mean": float(np.nanmean(maps["hidden_fraction"][mask])),
            }
        )
    return pd.DataFrame(rows)


def hotspot_leave_one_out(profile: dict, forest: np.ndarray) -> pd.DataFrame:
    component_paths = sorted((OBJ3 / "data" / "processed_tif").glob("component_*_0_1.tif"))
    components = {p.stem.replace("component_", "").replace("_0_1", ""): read_single(p)[0] for p in component_paths}
    original = read_single(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")[0] == 1
    valid = np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    rows = []
    for omitted, _ in components.items():
        use = [arr for name, arr in components.items() if name != omitted]
        index = np.nanmean(np.stack(use), axis=0).astype("float32")
        index[~valid] = np.nan
        thresh = np.nanpercentile(index[valid & np.isfinite(index)], 80)
        severe = index >= thresh
        jaccard = float(np.sum(original & severe) / np.sum(original | severe))
        agreement = float(np.nanmean(original[valid] == severe[valid]))
        rows.append(
            {
                "omitted_component": omitted,
                "severe_threshold_p80": float(thresh),
                "severe_pixels": int(np.sum(severe & valid)),
                "jaccard_with_original_severe": jaccard,
                "pixel_agreement_with_original": agreement,
            }
        )
        write_single(OBJ99 / "data" / "processed_tif" / f"loo_severe_without_{omitted}.tif", np.where(valid, severe, np.nan).astype("float32"), profile, f"Severe hotspot excluding {omitted}")
    return pd.DataFrame(rows)


def tree_only_summary(forest: np.ndarray) -> pd.DataFrame:
    severe = read_single(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")[0] == 1
    vuln = read_single(OBJ3 / "data" / "processed_tif" / "vulnerability_index_0_1.tif")[0]
    event_freq = read_single(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")[0]
    groups = {
        "all_event_vegetation": np.isfinite(event_freq) & (event_freq > 0) & np.isfinite(forest) & (forest >= 1) & (forest <= 6),
        "tree_classes_only": np.isfinite(event_freq) & (event_freq > 0) & np.isin(forest, TREE_CLASSES),
        "non_tree_SHB_GRS": np.isfinite(event_freq) & (event_freq > 0) & np.isin(forest, [5, 6]),
    }
    rows = []
    for label, mask in groups.items():
        rows.append(
            {
                "domain": label,
                "n_pixels": int(np.sum(mask)),
                "mean_event_frequency": float(np.nanmean(event_freq[mask])),
                "mean_vulnerability_index": float(np.nanmean(vuln[mask])),
                "severe_hotspot_percent": float(np.nanmean(severe[mask]) * 100),
            }
        )
    return pd.DataFrame(rows)


def plot_robustness(mismatch_summary: pd.DataFrame, loo: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(8.0, 4.0))
    mm = mismatch_summary[mismatch_summary["vegetation_class"] == "all_event_forest"].copy()
    sns.barplot(data=mm, x="greenness_index", y="hidden_mismatch_prevalence", color="#4C78A8", ax=axes[0])
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Hidden mismatch prevalence")
    axes[0].set_ylim(0, max(0.8, mm["hidden_mismatch_prevalence"].max() * 1.15))
    axes[0].text(0.02, 0.98, "A", transform=axes[0].transAxes, va="top", ha="left", fontsize=12, fontweight="bold")

    plot_loo = loo.sort_values("jaccard_with_original_severe", ascending=True)
    sns.barplot(data=plot_loo, y="omitted_component", x="jaccard_with_original_severe", color="#59A14F", ax=axes[1])
    axes[1].set_xlabel("Jaccard with original severe hotspots")
    axes[1].set_ylabel("")
    axes[1].set_xlim(0, 1)
    axes[1].text(0.02, 0.98, "B", transform=axes[1].transAxes, va="top", ha="left", fontsize=12, fontweight="bold")

    for ax in axes:
        ax.grid(True, axis="x", color="#E5E5E5", linewidth=0.7)
        ax.grid(False, axis="y")
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=2.2)
    fig.savefig(PUBFIG / "figure12_internal_robustness_checks.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure12_internal_robustness_checks.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_qgis_inventory() -> None:
    rows = [
        {
            "raster": str(OBJ99 / "data" / "processed_tif" / "NDVI_hidden_mismatch_duration_t1_t4.tif"),
            "description": "Hidden productivity-loss mismatch duration using NDVI instead of kNDVI.",
            "recommended_use": "Supplementary QGIS robustness map.",
        }
    ]
    for p in sorted((OBJ99 / "data" / "processed_tif").glob("loo_severe_without_*.tif")):
        rows.append(
            {
                "raster": str(p),
                "description": f"Leave-one-component-out severe hotspot map: {p.stem}.",
                "recommended_use": "Supplementary QGIS hotspot-stability map if needed.",
            }
        )
    pd.DataFrame(rows).to_csv(OBJ99 / "data" / "qgis_ready" / "robustness_qgis_raster_inventory.csv", index=False)


def main() -> None:
    ensure_dirs()
    sns.set_theme(context="paper", style="whitegrid", font="Arial")
    events, profile = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    ndvi, _ = read_stack(DATASETS / "NDVI_annual_2001_2024_NEA.tif")
    kndvi, _ = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")

    ndvi_anoms, event_count = compute_event_anomaly_means(ndvi, events)
    kndvi_anoms, _ = compute_event_anomaly_means(kndvi, events)
    gpp_anoms, _ = compute_event_anomaly_means(gpp, events)
    ndvi_mismatch = greenness_gpp_mismatch(ndvi_anoms, gpp_anoms, forest)
    kndvi_mismatch = greenness_gpp_mismatch(kndvi_anoms, gpp_anoms, forest)
    write_single(OBJ99 / "data" / "processed_tif" / "NDVI_hidden_mismatch_duration_t1_t4.tif", ndvi_mismatch["hidden_duration"], profile, "NDVI-GPP hidden mismatch duration")

    mismatch_summary = pd.concat(
        [
            summarize_mismatch("kNDVI", kndvi_mismatch, forest, event_count),
            summarize_mismatch("NDVI", ndvi_mismatch, forest, event_count),
        ],
        ignore_index=True,
    )
    mismatch_summary.to_csv(OBJ99 / "outputs" / "tables" / "greenness_index_mismatch_robustness.csv", index=False)

    loo = hotspot_leave_one_out(profile, forest)
    loo.to_csv(OBJ99 / "outputs" / "tables" / "hotspot_leave_one_component_out_stability.csv", index=False)
    tree = tree_only_summary(forest)
    tree.to_csv(OBJ99 / "outputs" / "tables" / "tree_only_domain_robustness.csv", index=False)

    plot_robustness(mismatch_summary, loo)
    write_qgis_inventory()
    metadata = {
        "checks": [
            "NDVI substituted for kNDVI in hidden productivity-loss mismatch definition.",
            "Severe hotspot stability tested by omitting each vulnerability-index component.",
            "Tree-only domain compared with broader vegetation mask.",
        ],
        "note": "External disturbance validation still requires additional burned-area or forest-loss datasets.",
    }
    (OBJ99 / "logs" / "internal_robustness_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ99 / "outputs" / "tables" / "greenness_index_mismatch_robustness.csv")
    print(OBJ99 / "outputs" / "tables" / "hotspot_leave_one_component_out_stability.csv")


if __name__ == "__main__":
    main()
