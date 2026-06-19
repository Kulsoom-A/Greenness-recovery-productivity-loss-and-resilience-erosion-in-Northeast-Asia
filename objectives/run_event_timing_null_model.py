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
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
REL_YEARS = np.arange(-3, 5)
POST_RELS = np.arange(1, 5)
N_ITER = 100
SEED = 59
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}


def ensure_dirs() -> None:
    for path in [OBJ99 / "outputs" / "tables", OBJ99 / "outputs" / "figures", OBJ99 / "logs", PUBFIG]:
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


def shift_stack_by_pixel(stack: np.ndarray, shifts: np.ndarray) -> np.ndarray:
    idx = (np.arange(stack.shape[0], dtype=np.int16)[:, None, None] + shifts[None, :, :]) % stack.shape[0]
    return np.take_along_axis(stack, idx, axis=0).astype("float32")


def response_metrics(response: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    h, w = response.shape[1:]
    loss_sum = np.zeros((h, w), dtype="float64")
    legacy_sum = np.zeros((h, w), dtype="float64")
    event_count = np.zeros((h, w), dtype="float64")
    anomaly_by_rel: dict[int, list[np.ndarray]] = {int(r): [] for r in REL_YEARS}

    for e_i, event_year in enumerate(EVENT_YEARS):
        event = events[e_i].astype(bool)
        baseline = np.nanmean(response[event_year - 2001 - 3 : event_year - 2001], axis=0)
        complete = event & np.isfinite(baseline)
        rel_anoms = {}
        for rel in REL_YEARS:
            arr = response[event_year + rel - 2001] - baseline
            rel_anoms[int(rel)] = arr
            anomaly_by_rel[int(rel)].append(np.where(complete, arr, np.nan))

        post = np.stack([rel_anoms[int(r)] for r in range(0, 5)])
        valid = complete & np.all(np.isfinite(post), axis=0)
        cumulative_loss = -np.nansum(np.minimum(post, 0), axis=0)
        legacy_years = np.sum(post[1:5] < 0, axis=0)
        loss_sum[valid] += cumulative_loss[valid]
        legacy_sum[valid] += legacy_years[valid]
        event_count[valid] += 1

    mean_loss = np.divide(loss_sum, event_count, out=np.full((h, w), np.nan), where=event_count > 0)
    mean_legacy = np.divide(legacy_sum, event_count, out=np.full((h, w), np.nan), where=event_count > 0)
    anomaly_mean = {rel: np.nanmean(np.stack(items), axis=0).astype("float32") for rel, items in anomaly_by_rel.items()}
    return mean_loss.astype("float32"), mean_legacy.astype("float32"), anomaly_mean


def hidden_mismatch_duration(k_anoms: dict[int, np.ndarray], g_anoms: dict[int, np.ndarray], valid_domain: np.ndarray) -> np.ndarray:
    hidden = []
    valid_any = np.zeros(valid_domain.shape, dtype=bool)
    for rel in POST_RELS:
        k = k_anoms[int(rel)]
        g = g_anoms[int(rel)]
        valid = valid_domain & np.isfinite(k) & np.isfinite(g)
        valid_any |= valid
        hidden.append((valid & (k >= 0) & (g < 0)).astype("float32"))
    out = np.sum(np.stack(hidden), axis=0).astype("float32")
    out[~valid_any] = np.nan
    return out


def group_masks(forest: np.ndarray, event_frequency: np.ndarray) -> list[tuple[str, np.ndarray]]:
    valid = event_frequency > 0
    groups = [("all_event_forest", valid)]
    for cls in range(1, 7):
        groups.append((CLASS_LABELS[cls], valid & (forest == cls)))
    return groups


def summarize_observed(groups, k_loss, k_legacy, g_loss, g_legacy, mismatch_duration) -> pd.DataFrame:
    rows = []
    metric_arrays = {
        "kNDVI_cumulative_loss": k_loss,
        "GPP_cumulative_loss": g_loss,
        "kNDVI_legacy_duration": k_legacy,
        "GPP_legacy_duration": g_legacy,
        "kNDVI_GPP_hidden_mismatch_duration": mismatch_duration,
        "kNDVI_GPP_hidden_mismatch_prevalence": (mismatch_duration > 0).astype("float32"),
    }
    for group, mask in groups:
        for metric, arr in metric_arrays.items():
            vals = arr[mask]
            vals = vals[np.isfinite(vals)]
            rows.append({"vegetation_class": group, "metric": metric, "observed_mean": float(np.nanmean(vals)), "n_pixels": int(vals.size)})
    return pd.DataFrame(rows)


def append_null_rows(rows, groups, iteration, k_loss, k_legacy, g_loss, g_legacy, mismatch_duration) -> None:
    metric_arrays = {
        "kNDVI_cumulative_loss": k_loss,
        "GPP_cumulative_loss": g_loss,
        "kNDVI_legacy_duration": k_legacy,
        "GPP_legacy_duration": g_legacy,
        "kNDVI_GPP_hidden_mismatch_duration": mismatch_duration,
        "kNDVI_GPP_hidden_mismatch_prevalence": (mismatch_duration > 0).astype("float32"),
    }
    for group, mask in groups:
        for metric, arr in metric_arrays.items():
            vals = arr[mask]
            vals = vals[np.isfinite(vals)]
            if vals.size:
                rows.append({"iteration": iteration, "vegetation_class": group, "metric": metric, "null_mean": float(np.nanmean(vals))})


def summarize_null(observed: pd.DataFrame, null_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, obs in observed.iterrows():
        vals = null_df[
            (null_df["vegetation_class"] == obs["vegetation_class"])
            & (null_df["metric"] == obs["metric"])
        ]["null_mean"].to_numpy()
        obs_mean = float(obs["observed_mean"])
        rows.append(
            {
                "vegetation_class": obs["vegetation_class"],
                "metric": obs["metric"],
                "n_pixels": int(obs["n_pixels"]),
                "observed_mean": obs_mean,
                "null_mean": float(np.mean(vals)),
                "null_ci_low": float(np.percentile(vals, 2.5)),
                "null_ci_high": float(np.percentile(vals, 97.5)),
                "observed_minus_null": float(obs_mean - np.mean(vals)),
                "observed_to_null_ratio": float(obs_mean / np.mean(vals)) if np.mean(vals) != 0 else np.nan,
                "empirical_p_greater": float((1 + np.sum(vals >= obs_mean)) / (len(vals) + 1)),
            }
        )
    return pd.DataFrame(rows)


def plot_null_summary(summary: pd.DataFrame) -> None:
    metrics = [
        "kNDVI_cumulative_loss",
        "GPP_cumulative_loss",
        "kNDVI_legacy_duration",
        "GPP_legacy_duration",
        "kNDVI_GPP_hidden_mismatch_prevalence",
    ]
    labels = {
        "kNDVI_cumulative_loss": "kNDVI loss",
        "GPP_cumulative_loss": "GPP loss",
        "kNDVI_legacy_duration": "kNDVI duration",
        "GPP_legacy_duration": "GPP duration",
        "kNDVI_GPP_hidden_mismatch_prevalence": "Mismatch prevalence",
    }
    df = summary[(summary["vegetation_class"] == "all_event_forest") & (summary["metric"].isin(metrics))].copy()
    df["metric_label"] = df["metric"].map(labels)
    df["observed_to_null_ratio"] = df["observed_to_null_ratio"].astype(float)
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    colors = ["#4C78A8" if v >= 1 else "#BAB0AC" for v in df["observed_to_null_ratio"]]
    sns.barplot(data=df, x="metric_label", y="observed_to_null_ratio", palette=colors, ax=ax)
    ax.axhline(1, color="black", linestyle="--", linewidth=1.0)
    ax.set_xlabel("")
    ax.set_ylabel("Observed / circular-shift null")
    ax.tick_params(axis="x", rotation=22)
    ax.grid(True, axis="y", color="#E5E5E5", linewidth=0.7)
    ax.grid(False, axis="x")
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(0.8)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.tight_layout()
    fig.savefig(PUBFIG / "figure16_event_timing_null_model.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure16_event_timing_null_model.pdf", bbox_inches="tight", facecolor="white")
    fig.savefig(OBJ99 / "outputs" / "figures" / "event_timing_null_model.png", bbox_inches="tight", facecolor="white", dpi=600)
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    sns.set_theme(context="paper", style="whitegrid", font="Arial")
    rng = np.random.default_rng(SEED)

    kndvi, _ = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    events, _ = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    k_loss, _ = read_single(OBJ1 / "data" / "processed_tif" / "kNDVI_cumulative_loss_t0_t4_mean.tif")
    g_loss, _ = read_single(OBJ1 / "data" / "processed_tif" / "GPP_cumulative_loss_t0_t4_mean.tif")
    k_legacy, _ = read_single(OBJ1 / "data" / "processed_tif" / "kNDVI_legacy_years_t1_t4_mean.tif")
    g_legacy, _ = read_single(OBJ1 / "data" / "processed_tif" / "GPP_legacy_years_t1_t4_mean.tif")
    mismatch_duration, _ = read_single(OBJ4 / "data" / "processed_tif" / "hidden_mismatch_duration_t1_t4.tif")

    event_frequency = np.nansum(events, axis=0)
    groups = group_masks(forest, event_frequency)
    valid_domain = np.isfinite(forest) & (forest >= 1) & (forest <= 6) & (event_frequency > 0)
    observed = summarize_observed(groups, k_loss, k_legacy, g_loss, g_legacy, mismatch_duration)

    rows = []
    h, w = event_frequency.shape
    for iteration in range(1, N_ITER + 1):
        shifts = rng.integers(1, len(YEARS), size=(h, w), dtype=np.int16)
        k_shift = shift_stack_by_pixel(kndvi, shifts)
        g_shift = shift_stack_by_pixel(gpp, shifts)
        k_null_loss, k_null_legacy, k_anoms = response_metrics(k_shift, events)
        g_null_loss, g_null_legacy, g_anoms = response_metrics(g_shift, events)
        null_mismatch = hidden_mismatch_duration(k_anoms, g_anoms, valid_domain)
        append_null_rows(rows, groups, iteration, k_null_loss, k_null_legacy, g_null_loss, g_null_legacy, null_mismatch)
        if iteration % 10 == 0:
            print(f"completed null iteration {iteration}/{N_ITER}")

    null_df = pd.DataFrame(rows)
    summary = summarize_null(observed, null_df)
    observed.to_csv(OBJ99 / "outputs" / "tables" / "event_timing_null_observed_metrics.csv", index=False)
    null_df.to_csv(OBJ99 / "outputs" / "tables" / "event_timing_null_iteration_metrics.csv", index=False)
    summary.to_csv(OBJ99 / "outputs" / "tables" / "event_timing_null_summary.csv", index=False)
    plot_null_summary(summary)

    metadata = {
        "null_model": "Per-pixel random nonzero circular shifts of kNDVI and GPP annual time series with observed event years fixed.",
        "n_iterations": N_ITER,
        "seed": SEED,
        "purpose": "Estimate the one-sided metric bias expected from baseline-referenced anomalies when event timing is broken.",
    }
    (OBJ99 / "logs" / "event_timing_null_model_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ99 / "outputs" / "tables" / "event_timing_null_summary.csv")


if __name__ == "__main__":
    main()
