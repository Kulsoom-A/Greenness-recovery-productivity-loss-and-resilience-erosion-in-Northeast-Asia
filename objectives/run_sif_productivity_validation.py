from __future__ import annotations

import gzip
import json
import shutil
import tempfile
from pathlib import Path

import fiona
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.crs import CRS
from rasterio.warp import reproject


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
SIF_DIR = DATASETS / "WorldAnnualSiF"
STRUCT_DIR = DATASETS / "EVI_NIRv_0p1deg_NEA_fixed"
SHP = ROOT / "shp" / "NEAFinal.shp"
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ4 = ROOT / "Paper2_Objectives" / "04_kNDVI_GPP_recovery_mismatch"
OBJ6 = ROOT / "Paper2_Objectives" / "06_sif_productivity_validation"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
POST_RELS = np.arange(1, 5)
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 47
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}


def ensure_dirs() -> None:
    for path in [
        OBJ6 / "data" / "processed_tif",
        OBJ6 / "data" / "tabular",
        OBJ6 / "outputs" / "tables",
        OBJ6 / "outputs" / "figures",
        OBJ6 / "logs",
        PUBFIG,
    ]:
        path.mkdir(parents=True, exist_ok=True)


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


def write_stack(path: Path, arr: np.ndarray, profile: dict, descriptions: list[str]) -> None:
    out_profile = profile.copy()
    out_profile.update(count=arr.shape[0], dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype("float32"))
        for i, desc in enumerate(descriptions, start=1):
            dst.set_band_description(i, desc)


def write_single(path: Path, arr: np.ndarray, profile: dict, desc: str) -> None:
    write_stack(path, arr[np.newaxis, ...], profile, [desc])


def study_area_mask(profile: dict) -> np.ndarray:
    with fiona.open(SHP) as shp:
        geoms = [feature["geometry"] for feature in shp]
    return geometry_mask(
        geoms,
        out_shape=(profile["height"], profile["width"]),
        transform=profile["transform"],
        invert=True,
    )


def align_gosif_to_analysis_grid(ref_profile: dict) -> np.ndarray:
    out_path = OBJ6 / "data" / "processed_tif" / "GOSIF_annual_2001_2024_aligned_0p1deg.tif"
    if out_path.exists():
        with rasterio.open(out_path) as src:
            return src.read().astype("float32")

    aligned = np.full((len(YEARS), ref_profile["height"], ref_profile["width"]), np.nan, dtype="float32")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for i, year in enumerate(YEARS):
            gz_path = SIF_DIR / f"GOSIF_{year}.tif.gz"
            tif_path = tmpdir_path / f"GOSIF_{year}.tif"
            with gzip.open(gz_path, "rb") as src_gz, tif_path.open("wb") as dst_tif:
                shutil.copyfileobj(src_gz, dst_tif)
            with rasterio.open(tif_path) as src:
                src_arr = src.read(1).astype("float32")
                src_arr[(src_arr == 32767) | ~np.isfinite(src_arr) | (src_arr < 0)] = np.nan
                # GOSIF annual files are distributed as scaled int16. The scale factor
                # does not affect anomaly signs, but applying it keeps values interpretable.
                src_arr *= 0.0001
                dst = np.full((ref_profile["height"], ref_profile["width"]), np.nan, dtype="float32")
                reproject(
                    source=src_arr,
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=np.nan,
                    dst_transform=ref_profile["transform"],
                    dst_crs=ref_profile["crs"],
                    dst_nodata=np.nan,
                    resampling=Resampling.average,
                )
            aligned[i] = dst
            print(f"Aligned GOSIF {year}")
    write_stack(out_path, aligned, ref_profile, [f"GOSIF annual {year}" for year in YEARS])
    return aligned


def src_crs_for_structural_proxy(src: rasterio.io.DatasetReader) -> CRS:
    bounds = src.bounds
    if max(abs(bounds.left), abs(bounds.right)) <= 360 and max(abs(bounds.top), abs(bounds.bottom)) <= 90:
        return src.crs
    return CRS.from_proj4("+proj=sinu +R=6371007.181 +nadgrids=@null +wktext +units=m +no_defs")


def align_annual_proxy_to_analysis_grid(proxy: str, ref_profile: dict) -> np.ndarray:
    out_path = OBJ6 / "data" / "processed_tif" / f"{proxy}_annual_2001_2024_aligned_0p1deg.tif"
    fixed_stack = STRUCT_DIR / f"{proxy}_annual_2001_2024_NEA_0p1deg_fixed.tif"
    if fixed_stack.exists():
        with rasterio.open(fixed_stack) as src:
            same_grid = (
                src.crs == ref_profile["crs"]
                and src.transform == ref_profile["transform"]
                and src.height == ref_profile["height"]
                and src.width == ref_profile["width"]
                and src.count == len(YEARS)
            )
            arr = src.read().astype("float32")
            if same_grid:
                write_stack(out_path, arr, ref_profile, [f"{proxy} annual {year}" for year in YEARS])
                return arr
    if out_path.exists():
        with rasterio.open(out_path) as src:
            return src.read().astype("float32")

    folder = STRUCT_DIR / f"{proxy}_Annual_NEA_0p1deg"
    aligned = np.full((len(YEARS), ref_profile["height"], ref_profile["width"]), np.nan, dtype="float32")
    for i, year in enumerate(YEARS):
        src_path = folder / f"{proxy}_{year}_NEA_0p1deg.tif"
        with rasterio.open(src_path) as src:
            src_arr = src.read(1).astype("float32")
            nodata = src.nodata
            if nodata is not None and not np.isnan(nodata):
                src_arr[src_arr == nodata] = np.nan
            src_arr[~np.isfinite(src_arr)] = np.nan
            if proxy.upper() == "EVI":
                src_arr[(src_arr < -0.2) | (src_arr > 1.0)] = np.nan
            else:
                src_arr[(src_arr < -0.2) | (src_arr > 1.0)] = np.nan
            dst = np.full((ref_profile["height"], ref_profile["width"]), np.nan, dtype="float32")
            reproject(
                source=src_arr,
                destination=dst,
                src_transform=src.transform,
                src_crs=src_crs_for_structural_proxy(src),
                src_nodata=np.nan,
                dst_transform=ref_profile["transform"],
                dst_crs=ref_profile["crs"],
                dst_nodata=np.nan,
                resampling=Resampling.average,
            )
        aligned[i] = dst
        print(f"Aligned {proxy} {year}")
    write_stack(out_path, aligned, ref_profile, [f"{proxy} annual {year}" for year in YEARS])
    return aligned


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
    out = (arr - np.nanmean(vals)) / np.nanstd(vals)
    out[~np.isfinite(out)] = np.nan
    return out.astype("float32")


def mismatch_maps(k_anoms: dict[int, np.ndarray], p_anoms: dict[int, np.ndarray], valid_domain: np.ndarray, label: str) -> dict[str, np.ndarray]:
    hidden_years = []
    k_recovered_years = []
    p_suppressed_years = []
    intensity_years = []
    for rel in POST_RELS:
        k = k_anoms[int(rel)]
        p = p_anoms[int(rel)]
        valid = valid_domain & np.isfinite(k) & np.isfinite(p)
        hidden = valid & (k >= 0) & (p < 0)
        hidden_years.append(hidden.astype("float32"))
        k_recovered_years.append((valid & (k >= 0)).astype("float32"))
        p_suppressed_years.append((valid & (p < 0)).astype("float32"))
        intensity_years.append(np.where(valid, standardize_by_valid(k, valid) - standardize_by_valid(p, valid), np.nan).astype("float32"))

    hidden_stack = np.stack(hidden_years)
    k_stack = np.stack(k_recovered_years)
    p_stack = np.stack(p_suppressed_years)
    intensity_stack = np.stack(intensity_years)
    valid_any = np.sum(np.isfinite(intensity_stack), axis=0) > 0
    hidden_duration = np.sum(hidden_stack, axis=0).astype("float32")
    p_suppression_duration = np.sum(p_stack, axis=0).astype("float32")
    hidden_fraction = np.divide(hidden_duration, p_suppression_duration, out=np.full_like(hidden_duration, np.nan), where=p_suppression_duration > 0)
    out = {
        f"{label}_hidden_mismatch_duration_t1_t4": hidden_duration,
        f"{label}_suppression_duration_t1_t4": p_suppression_duration,
        f"kNDVI_recovery_duration_for_{label}_t1_t4": np.sum(k_stack, axis=0).astype("float32"),
        f"hidden_fraction_of_{label}_suppression_t1_t4": hidden_fraction.astype("float32"),
        f"mean_standardized_kNDVI_{label}_mismatch_intensity_t1_t4": np.nanmean(intensity_stack, axis=0).astype("float32"),
    }
    for arr in out.values():
        arr[~valid_any] = np.nan
    return out


def summarize_maps(maps: dict[str, np.ndarray], forest: np.ndarray, event_count: np.ndarray, label: str) -> pd.DataFrame:
    hidden = maps[f"{label}_hidden_mismatch_duration_t1_t4"]
    valid = np.isfinite(hidden) & (event_count > 0)
    groups: list[tuple[str, np.ndarray]] = [("all_event_forest", valid)]
    for cls in sorted(np.unique(forest[np.isfinite(forest)]).astype(int)):
        if 1 <= cls <= 6:
            groups.append((CLASS_LABELS[cls], valid & (forest == cls)))
    rows = []
    for veg, mask in groups:
        rows.append(
            {
                "productivity_proxy": label,
                "vegetation_class": veg,
                "n_pixels": int(np.sum(mask)),
                "event_count_mean": float(np.nanmean(event_count[mask])),
                "hidden_mismatch_duration_mean": float(np.nanmean(hidden[mask])),
                "hidden_mismatch_prevalence": float(np.nanmean(hidden[mask] > 0)),
                "productivity_suppression_duration_mean": float(np.nanmean(maps[f"{label}_suppression_duration_t1_t4"][mask])),
                "hidden_fraction_of_productivity_suppression_mean": float(np.nanmean(maps[f"hidden_fraction_of_{label}_suppression_t1_t4"][mask])),
                "mismatch_intensity_mean": float(np.nanmean(maps[f"mean_standardized_kNDVI_{label}_mismatch_intensity_t1_t4"][mask])),
            }
        )
    return pd.DataFrame(rows)


def bootstrap_prevalence(hidden: np.ndarray, valid: np.ndarray) -> dict[str, float]:
    vals = (hidden[valid] > 0).astype("float32")
    vals = vals[np.isfinite(vals)]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, vals.size, size=(BOOTSTRAP_N, vals.size))
    boot = vals[idx].mean(axis=1)
    return {
        "n": int(vals.size),
        "mean": float(vals.mean()),
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
    }


def compare_with_gpp(sif_maps: dict[str, np.ndarray], forest: np.ndarray, event_count: np.ndarray) -> pd.DataFrame:
    gpp_hidden, _ = read_single(OBJ4 / "data" / "processed_tif" / "hidden_mismatch_duration_t1_t4.tif")
    sif_hidden = sif_maps["GOSIF_hidden_mismatch_duration_t1_t4"]
    valid = np.isfinite(gpp_hidden) & np.isfinite(sif_hidden) & (event_count > 0) & np.isfinite(forest) & (forest >= 1) & (forest <= 6)
    rows = []
    for label, mask in [("all_event_forest", valid)] + [(CLASS_LABELS[c], valid & (forest == c)) for c in range(1, 7)]:
        if np.sum(mask) < 30:
            continue
        g = gpp_hidden[mask] > 0
        s = sif_hidden[mask] > 0
        rows.append(
            {
                "vegetation_class": label,
                "n_pixels": int(np.sum(mask)),
                "GPP_hidden_mismatch_prevalence": float(np.mean(g)),
                "GOSIF_hidden_mismatch_prevalence": float(np.mean(s)),
                "both_GPP_and_GOSIF_mismatch": float(np.mean(g & s)),
                "GOSIF_given_GPP_mismatch": float(np.sum(g & s) / np.sum(g)) if np.sum(g) else np.nan,
                "GPP_given_GOSIF_mismatch": float(np.sum(g & s) / np.sum(s)) if np.sum(s) else np.nan,
                "duration_correlation": float(np.corrcoef(gpp_hidden[mask], sif_hidden[mask])[0, 1]),
            }
        )
    all_ci = bootstrap_prevalence(sif_hidden, valid)
    rows[0]["GOSIF_prevalence_ci_low"] = all_ci["ci_low"]
    rows[0]["GOSIF_prevalence_ci_high"] = all_ci["ci_high"]
    return pd.DataFrame(rows)


def structural_greenness_validation(
    greenness_maps: dict[str, dict[str, np.ndarray]],
    forest: np.ndarray,
    event_count: np.ndarray,
) -> pd.DataFrame:
    rows = []
    for greenness, maps in greenness_maps.items():
        hidden = maps["GOSIF_hidden_mismatch_duration_t1_t4"]
        valid = np.isfinite(hidden) & (event_count > 0) & np.isfinite(forest) & (forest >= 1) & (forest <= 6)
        groups: list[tuple[str, np.ndarray]] = [("all_event_forest", valid)]
        groups.extend((CLASS_LABELS[c], valid & (forest == c)) for c in range(1, 7))
        for veg, mask in groups:
            if np.sum(mask) < 30:
                continue
            rows.append(
                {
                    "greenness_proxy": greenness,
                    "productivity_proxy": "GOSIF",
                    "vegetation_class": veg,
                    "n_pixels": int(np.sum(mask)),
                    "hidden_mismatch_duration_mean": float(np.nanmean(hidden[mask])),
                    "hidden_mismatch_prevalence": float(np.nanmean(hidden[mask] > 0)),
                    "GOSIF_suppression_duration_mean": float(np.nanmean(maps["GOSIF_suppression_duration_t1_t4"][mask])),
                    "hidden_fraction_of_GOSIF_suppression_mean": float(np.nanmean(maps["hidden_fraction_of_GOSIF_suppression_t1_t4"][mask])),
                    "mismatch_intensity_mean": float(np.nanmean(maps["mean_standardized_kNDVI_GOSIF_mismatch_intensity_t1_t4"][mask])),
                }
            )
    return pd.DataFrame(rows)


def plot_validation(summary: pd.DataFrame, comparison: pd.DataFrame) -> None:
    all_row = comparison[comparison["vegetation_class"] == "all_event_forest"].iloc[0]
    class_rows = comparison[comparison["vegetation_class"] != "all_event_forest"].copy()
    order = ["ENT", "EBT", "DNT", "DBT", "SHB", "GRS"]
    class_rows["vegetation_class"] = pd.Categorical(class_rows["vegetation_class"], categories=order, ordered=True)
    class_rows = class_rows.sort_values("vegetation_class")

    fig, axes = plt.subplots(1, 2, figsize=(8.3, 3.7))
    proxy_df = pd.DataFrame(
        {
            "Proxy": ["MOD17 GPP", "GOSIF"],
            "Hidden mismatch prevalence": [
                all_row["GPP_hidden_mismatch_prevalence"],
                all_row["GOSIF_hidden_mismatch_prevalence"],
            ],
        }
    )
    sns.barplot(data=proxy_df, x="Proxy", y="Hidden mismatch prevalence", ax=axes[0], palette=["#4C78A8", "#F58518"])
    axes[0].set_ylim(0, 1)
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Prevalence")
    axes[0].text(0.02, 0.98, "A", transform=axes[0].transAxes, ha="left", va="top", fontweight="bold", fontsize=12)

    plot_df = class_rows.melt(
        id_vars="vegetation_class",
        value_vars=["GPP_hidden_mismatch_prevalence", "GOSIF_hidden_mismatch_prevalence"],
        var_name="Proxy",
        value_name="Prevalence",
    )
    plot_df["Proxy"] = plot_df["Proxy"].map({"GPP_hidden_mismatch_prevalence": "MOD17 GPP", "GOSIF_hidden_mismatch_prevalence": "GOSIF"})
    sns.barplot(data=plot_df, x="vegetation_class", y="Prevalence", hue="Proxy", ax=axes[1], palette=["#4C78A8", "#F58518"])
    axes[1].set_ylim(0, 1)
    axes[1].set_xlabel("")
    axes[1].set_ylabel("")
    axes[1].legend(frameon=False, loc="upper right")
    axes[1].text(0.02, 0.98, "B", transform=axes[1].transAxes, ha="left", va="top", fontweight="bold", fontsize=12)
    for ax in axes:
        ax.grid(True, axis="y", color="#E5E5E5", linewidth=0.7)
        ax.grid(False, axis="x")
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=2.0)
    fig.savefig(PUBFIG / "figure14_sif_productivity_validation.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure14_sif_productivity_validation.pdf", bbox_inches="tight", facecolor="white")
    fig.savefig(OBJ6 / "outputs" / "figures" / "sif_productivity_validation.png", bbox_inches="tight", facecolor="white", dpi=600)
    plt.close(fig)


def plot_structural_validation(df: pd.DataFrame) -> None:
    all_df = df[df["vegetation_class"] == "all_event_forest"].copy()
    class_df = df[df["vegetation_class"] != "all_event_forest"].copy()
    order = ["ENT", "EBT", "DNT", "DBT", "SHB", "GRS"]
    proxy_order = ["kNDVI", "EVI", "NIRv"]
    class_df["vegetation_class"] = pd.Categorical(class_df["vegetation_class"], categories=order, ordered=True)
    class_df["greenness_proxy"] = pd.Categorical(class_df["greenness_proxy"], categories=proxy_order, ordered=True)
    all_df["greenness_proxy"] = pd.Categorical(all_df["greenness_proxy"], categories=proxy_order, ordered=True)
    class_df = class_df.sort_values(["vegetation_class", "greenness_proxy"])
    all_df = all_df.sort_values("greenness_proxy")

    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.8))
    palette = {"kNDVI": "#4C78A8", "EVI": "#59A14F", "NIRv": "#E15759"}
    sns.barplot(data=all_df, x="greenness_proxy", y="hidden_mismatch_prevalence", ax=axes[0], palette=palette)
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Prevalence")
    axes[0].set_ylim(0, 1)
    axes[0].text(0.02, 0.98, "A", transform=axes[0].transAxes, ha="left", va="top", fontweight="bold", fontsize=12)

    sns.barplot(data=class_df, x="vegetation_class", y="hidden_mismatch_prevalence", hue="greenness_proxy", ax=axes[1], palette=palette)
    axes[1].set_xlabel("")
    axes[1].set_ylabel("")
    axes[1].set_ylim(0, 1)
    axes[1].legend(frameon=False, loc="upper right", title="")
    axes[1].text(0.02, 0.98, "B", transform=axes[1].transAxes, ha="left", va="top", fontweight="bold", fontsize=12)
    for ax in axes:
        ax.grid(True, axis="y", color="#E5E5E5", linewidth=0.7)
        ax.grid(False, axis="x")
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=2.0)
    fig.savefig(PUBFIG / "figure15_structural_greenness_sif_validation.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure15_structural_greenness_sif_validation.pdf", bbox_inches="tight", facecolor="white")
    fig.savefig(OBJ6 / "outputs" / "figures" / "structural_greenness_sif_validation.png", bbox_inches="tight", facecolor="white", dpi=600)
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    sns.set_theme(context="paper", style="whitegrid", font="Arial")
    kndvi, profile = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    events, _ = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    boundary = study_area_mask(profile)
    forest_valid = boundary & np.isfinite(forest) & (forest >= 1) & (forest <= 6)

    sif = align_gosif_to_analysis_grid(profile)
    sif[:, ~boundary] = np.nan
    write_stack(OBJ6 / "data" / "processed_tif" / "GOSIF_annual_2001_2024_aligned_0p1deg.tif", sif, profile, [f"GOSIF annual {year}" for year in YEARS])

    k_anoms, k_event_count = compute_event_anomaly_means(kndvi, events)
    sif_anoms, sif_event_count = compute_event_anomaly_means(sif, events)
    event_count = np.minimum(k_event_count, sif_event_count)
    sif_maps = mismatch_maps(k_anoms, sif_anoms, forest_valid & (event_count > 0), "GOSIF")

    for name, arr in sif_maps.items():
        write_single(OBJ6 / "data" / "processed_tif" / f"{name}.tif", arr, profile, name.replace("_", " "))

    summary = summarize_maps(sif_maps, forest, event_count, "GOSIF")
    comparison = compare_with_gpp(sif_maps, forest, event_count)
    greenness_maps = {"kNDVI": sif_maps}
    for proxy in ["EVI", "NIRv"]:
        proxy_stack = align_annual_proxy_to_analysis_grid(proxy, profile)
        proxy_stack[:, ~boundary] = np.nan
        proxy_anoms, proxy_event_count = compute_event_anomaly_means(proxy_stack, events)
        proxy_event_count = np.minimum(proxy_event_count, sif_event_count)
        proxy_maps = mismatch_maps(proxy_anoms, sif_anoms, forest_valid & (proxy_event_count > 0), "GOSIF")
        greenness_maps[proxy] = proxy_maps
        for name, arr in proxy_maps.items():
            renamed = name.replace("kNDVI", proxy)
            write_single(OBJ6 / "data" / "processed_tif" / f"{proxy}_{renamed}.tif", arr, profile, f"{proxy} {renamed.replace('_', ' ')}")
    structural = structural_greenness_validation(greenness_maps, forest, event_count)
    summary.to_csv(OBJ6 / "outputs" / "tables" / "gosif_mismatch_summary_by_class.csv", index=False)
    comparison.to_csv(OBJ6 / "outputs" / "tables" / "gpp_vs_gosif_mismatch_comparison.csv", index=False)
    structural.to_csv(OBJ6 / "outputs" / "tables" / "structural_greenness_gosif_validation.csv", index=False)
    summary.to_csv(OBJ6 / "data" / "tabular" / "gosif_mismatch_summary_by_class.csv", index=False)
    comparison.to_csv(OBJ6 / "data" / "tabular" / "gpp_vs_gosif_mismatch_comparison.csv", index=False)
    structural.to_csv(OBJ6 / "data" / "tabular" / "structural_greenness_gosif_validation.csv", index=False)
    plot_validation(summary, comparison)
    plot_structural_validation(structural)

    inventory = pd.DataFrame(
        {
            "raster": [str(OBJ6 / "data" / "processed_tif" / f"{name}.tif") for name in sif_maps],
            "description": [name.replace("_", " ") for name in sif_maps],
            "recommended_use": ["SIF validation / supplementary spatial map"] * len(sif_maps),
        }
    )
    inventory.to_csv(OBJ6 / "data" / "tabular" / "gosif_validation_raster_inventory.csv", index=False)
    metadata = {
        "years": YEARS.tolist(),
        "event_years": EVENT_YEARS.tolist(),
        "source": str(SIF_DIR),
        "alignment": "GOSIF annual 0.05 degree rasters were averaged to the common 0.1 degree EPSG:4326 grid and masked by NEAFinal plus vegetation classes.",
        "structural_greenness_validation": "EVI and NIRv annual rasters were aligned to the common analysis grid and used as structurally different greenness proxies in the GOSIF mismatch definition.",
        "nodata_handling": "Values equal to 32767, non-finite values, and negative annual SIF values were set to NaN before resampling. A 0.0001 scale factor was applied.",
    }
    (OBJ6 / "logs" / "sif_validation_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ6 / "outputs" / "tables" / "gpp_vs_gosif_mismatch_comparison.csv")


if __name__ == "__main__":
    main()
