from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns


ROOT = Path(__file__).resolve().parents[3]
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ3 = ROOT / "Paper2_Objectives" / "03_vulnerability_hotspots_resilience_erosion"
OBJ4 = ROOT / "Paper2_Objectives" / "04_kNDVI_GPP_recovery_mismatch"
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"
PROCESSED = OBJ99 / "data" / "processed_tif"
PUBFIG = ROOT / "Manuscript" / "figures_publication"


def ensure_dirs() -> None:
    for p in [OBJ99 / "outputs" / "tables", OBJ99 / "outputs" / "figures", PUBFIG]:
        p.mkdir(parents=True, exist_ok=True)


def read(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
    if nodata is not None and not np.isnan(nodata):
        arr[arr == nodata] = np.nan
    return arr


def summarize_domain(name: str, mask: np.ndarray, metrics: dict[str, np.ndarray]) -> dict:
    out = {"domain": name, "n_pixels": int(np.sum(mask))}
    for metric_name, arr in metrics.items():
        vals = arr[mask & np.isfinite(arr)]
        if vals.size == 0:
            out[f"{metric_name}_mean"] = np.nan
            out[f"{metric_name}_median"] = np.nan
        else:
            out[f"{metric_name}_mean"] = float(np.nanmean(vals))
            out[f"{metric_name}_median"] = float(np.nanmedian(vals))
    out["hidden_mismatch_prevalence"] = float(np.nanmean(metrics["hidden_mismatch_duration"][mask] > 0))
    out["severe_hotspot_percent"] = float(np.nanmean(metrics["severe_hotspot"][mask] == 1) * 100)
    return out


def main() -> None:
    ensure_dirs()
    sns.set_theme(context="paper", style="whitegrid", font="Arial")

    event_freq = read(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")
    gpp_loss = read(OBJ1 / "data" / "processed_tif" / "GPP_cumulative_loss_t0_t4_mean.tif")
    gpp_legacy = read(OBJ1 / "data" / "processed_tif" / "GPP_legacy_years_t1_t4_mean.tif")
    hidden = read(OBJ4 / "data" / "processed_tif" / "hidden_mismatch_duration_t1_t4.tif")
    vulnerability = read(OBJ3 / "data" / "processed_tif" / "vulnerability_index_0_1.tif")
    severe = read(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")

    burned = read(PROCESSED / "MCD64A1_burned_any_event_post_2004_2024_aligned_0p1deg.tif")
    burned = np.nan_to_num(burned, nan=0) > 0
    forest_loss = read(PROCESSED / "Hansen_forest_loss_any_event_post_2004_2024_aligned_0p1deg.tif")
    forest_loss = np.nan_to_num(forest_loss, nan=0) > 0
    disturbed = burned | forest_loss

    valid = np.isfinite(event_freq) & (event_freq > 0) & np.isfinite(gpp_loss) & np.isfinite(vulnerability)
    domains = {
        "all_event_pixels": valid,
        "burned_2004_2024": valid & burned,
        "forest_loss_screen_2004_2024": valid & forest_loss,
        "any_disturbance_screen_2004_2024": valid & disturbed,
        "undisturbed_screen": valid & ~disturbed,
    }
    metrics = {
        "event_frequency": event_freq,
        "GPP_cumulative_loss": gpp_loss,
        "GPP_legacy_years": gpp_legacy,
        "hidden_mismatch_duration": hidden,
        "vulnerability_index": vulnerability,
        "severe_hotspot": severe,
    }
    summary = pd.DataFrame([summarize_domain(name, mask, metrics) for name, mask in domains.items()])
    summary.to_csv(OBJ99 / "outputs" / "tables" / "external_disturbance_control_summary.csv", index=False)

    comparison = summary[summary["domain"].isin(["all_event_pixels", "undisturbed_screen", "any_disturbance_screen_2004_2024"])].copy()
    comparison["domain"] = pd.Categorical(
        comparison["domain"],
        categories=["all_event_pixels", "undisturbed_screen", "any_disturbance_screen_2004_2024"],
        ordered=True,
    )
    comparison = comparison.sort_values("domain")
    fig, axes = plt.subplots(1, 3, figsize=(9.0, 3.4))
    plot_specs = [
        ("GPP_cumulative_loss_mean", "GPP cumulative loss"),
        ("hidden_mismatch_prevalence", "Hidden mismatch prevalence"),
        ("severe_hotspot_percent", "Severe hotspots (%)"),
    ]
    for ax, (col, label), panel in zip(axes, plot_specs, ["A", "B", "C"]):
        sns.barplot(data=comparison, x="domain", y=col, color="#4C78A8", ax=ax)
        ax.text(0.02, 0.98, panel, transform=ax.transAxes, va="top", ha="left", fontsize=12, fontweight="bold")
        ax.set_xlabel("")
        ax.set_ylabel(label)
        ax.set_xticklabels(["All", "Undisturbed", "Disturbed"], rotation=20, ha="right")
        ax.grid(True, axis="y", color="#E5E5E5", linewidth=0.7)
        ax.grid(False, axis="x")
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=1.8)
    fig.savefig(PUBFIG / "figure13_external_disturbance_control.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure13_external_disturbance_control.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    print(OBJ99 / "outputs" / "tables" / "external_disturbance_control_summary.csv")
    print(PUBFIG / "figure13_external_disturbance_control.png")


if __name__ == "__main__":
    main()
