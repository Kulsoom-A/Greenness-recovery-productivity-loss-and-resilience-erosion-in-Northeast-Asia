from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.model_selection import GroupKFold, train_test_split


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ3 = ROOT / "Paper2_Objectives" / "03_vulnerability_hotspots_resilience_erosion"
OBJ5 = ROOT / "Paper2_Objectives" / "05_drivers_controls_legacy_loss"
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"
PROCESSED99 = OBJ99 / "data" / "processed_tif"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
REL_YEARS = np.arange(-3, 5)
SEED = 88
MAX_SAMPLE = 120_000
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}
PREDICTORS = [
    "event_frequency",
    "mean_event_spei12",
    "mean_event_tmax_z",
    "mean_event_def_z",
    "mean_event_pr_z",
    "mean_event_pet_z",
    "mean_event_pdsi",
    "pre_event_gpp_baseline",
]
PREDICTOR_LABELS = {
    "event_frequency": "Event frequency",
    "mean_event_spei12": "Event SPEI-12",
    "mean_event_tmax_z": "Event Tmax z",
    "mean_event_def_z": "Event deficit z",
    "mean_event_pr_z": "Event precipitation z",
    "mean_event_pet_z": "Event PET z",
    "mean_event_pdsi": "Event PDSI",
    "pre_event_gpp_baseline": "Pre-event GPP",
}


def ensure_dirs() -> None:
    for p in [OBJ99 / "outputs" / "tables", OBJ99 / "outputs" / "figures", OBJ99 / "logs", PUBFIG]:
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


def december_spei(spei_monthly: np.ndarray) -> np.ndarray:
    return np.stack([spei_monthly[(year - 2001) * 12 + 11] for year in range(2001, 2023)]).astype("float32")


def zscore_stack(stack: np.ndarray) -> np.ndarray:
    mean = np.nanmean(stack, axis=0)
    sd = np.nanstd(stack, axis=0)
    out = (stack - mean) / sd
    out[~np.isfinite(out)] = np.nan
    return out.astype("float32")


def build_event_masks() -> tuple[dict[str, np.ndarray], dict]:
    kndvi, profile = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    spei_monthly, _ = read_stack(DATASETS / "SPEI12_monthly_2001_2022_NEA.tif")
    tmx, _ = read_stack(DATASETS / "TC_tmx_annual_2001_2024_NEA.tif")
    spei_dec = december_spei(spei_monthly)
    tmax_z = zscore_stack(tmx)
    valid_forest = np.isfinite(kndvi[0]) & np.isfinite(forest) & (forest >= 1) & (forest <= 6)

    masks = {"compound": [], "drought_only": [], "heat_only": []}
    for year in EVENT_YEARS:
        s = spei_dec[year - 2001]
        h = tmax_z[year - 2001]
        dry = s <= -1.0
        hot = h >= 1.0
        masks["compound"].append((dry & hot & valid_forest).astype("float32"))
        masks["drought_only"].append((dry & ~hot & valid_forest).astype("float32"))
        masks["heat_only"].append((~dry & hot & valid_forest).astype("float32"))
    return {k: np.stack(v) for k, v in masks.items()}, profile


def response_metrics(response: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    h, w = response.shape[1:]
    loss_sum = np.zeros((h, w), dtype="float64")
    fractional_loss_sum = np.zeros((h, w), dtype="float64")
    duration_sum = np.zeros((h, w), dtype="float64")
    baseline_sum = np.zeros((h, w), dtype="float64")
    count = np.zeros((h, w), dtype="float64")
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
        loss = -np.nansum(np.minimum(post, 0), axis=0)
        fractional_loss = np.divide(loss, baseline * 5.0, out=np.full((h, w), np.nan), where=baseline > 0)
        duration = np.sum(post[1:5] < 0, axis=0)
        loss_sum[valid] += loss[valid]
        fractional_loss_sum[valid] += fractional_loss[valid]
        duration_sum[valid] += duration[valid]
        baseline_sum[valid] += baseline[valid]
        count[valid] += 1
    mean_loss = np.divide(loss_sum, count, out=np.full((h, w), np.nan), where=count > 0)
    mean_fractional_loss = np.divide(fractional_loss_sum, count, out=np.full((h, w), np.nan), where=count > 0)
    mean_duration = np.divide(duration_sum, count, out=np.full((h, w), np.nan), where=count > 0)
    mean_baseline = np.divide(baseline_sum, count, out=np.full((h, w), np.nan), where=count > 0)
    mean_anoms = {rel: np.nanmean(np.stack(items), axis=0) for rel, items in anomaly_by_rel.items()}
    return (
        mean_loss.astype("float32"),
        mean_fractional_loss.astype("float32"),
        mean_duration.astype("float32"),
        mean_baseline.astype("float32"),
        count.astype("float32"),
        mean_anoms,
    )


def summarize_event_type(label: str, events: np.ndarray, gpp: np.ndarray, kndvi: np.ndarray, forest: np.ndarray) -> dict:
    g_loss, g_fractional_loss, g_duration, g_baseline, count, g_anoms = response_metrics(gpp, events)
    k_loss, _, k_duration, _, _, k_anoms = response_metrics(kndvi, events)
    event_pixels = count > 0
    return {
        "event_type": label,
        "n_event_pixels": int(np.sum(event_pixels)),
        "event_count_total": int(np.nansum(events)),
        "event_count_mean": float(np.nanmean(count[event_pixels])),
        "GPP_cumulative_loss_mean": float(np.nanmean(g_loss[event_pixels])),
        "GPP_fractional_cumulative_loss_mean": float(np.nanmean(g_fractional_loss[event_pixels])),
        "GPP_legacy_duration_mean": float(np.nanmean(g_duration[event_pixels])),
        "GPP_pre_event_baseline_mean": float(np.nanmean(g_baseline[event_pixels])),
        "kNDVI_cumulative_loss_mean": float(np.nanmean(k_loss[event_pixels])),
        "kNDVI_legacy_duration_mean": float(np.nanmean(k_duration[event_pixels])),
        "GPP_t2_anomaly_mean": float(np.nanmean(g_anoms[2][event_pixels])),
        "kNDVI_t2_anomaly_mean": float(np.nanmean(k_anoms[2][event_pixels])),
    }


def model_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    X = df[PREDICTORS + ["vegetation_class"]].copy()
    X = pd.get_dummies(X, columns=["vegetation_class"], drop_first=True, dtype=float)
    return X, X.columns.tolist()


def rf_regression(df: pd.DataFrame, outcome: str) -> tuple[pd.DataFrame, dict]:
    X, feature_names = model_matrix(df)
    y = df[outcome].astype(float)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=SEED)
    model = RandomForestRegressor(n_estimators=250, min_samples_leaf=20, n_jobs=-1, random_state=SEED)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    perm = permutation_importance(model, X_test, y_test, n_repeats=8, random_state=SEED, n_jobs=-1)
    importance = pd.DataFrame(
        {"outcome": outcome, "feature": feature_names, "importance_mean": perm.importances_mean, "importance_sd": perm.importances_std}
    ).sort_values("importance_mean", ascending=False)
    metrics = {
        "outcome": outcome,
        "model": "RandomForestRegressor",
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "r2": float(r2_score(y_test, pred)),
        "mae": float(mean_absolute_error(y_test, pred)),
    }
    return importance, metrics


def spatial_cv_fractional(df: pd.DataFrame, outcome: str = "GPP_fractional_cumulative_loss") -> pd.DataFrame:
    X, _ = model_matrix(df)
    groups = df["spatial_block_10deg"].astype(str)
    viable = groups.value_counts()
    keep = groups.isin(viable[viable >= 100].index)
    X = X.loc[keep].reset_index(drop=True)
    work = df.loc[keep].reset_index(drop=True)
    groups = work["spatial_block_10deg"].astype(str)
    rows = []
    for fold, (train_idx, test_idx) in enumerate(GroupKFold(n_splits=5).split(X, groups=groups), start=1):
        y = work[outcome].astype(float)
        model = RandomForestRegressor(n_estimators=180, min_samples_leaf=20, n_jobs=-1, random_state=SEED + fold)
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        pred = model.predict(X.iloc[test_idx])
        rows.append(
            {
                "outcome": outcome,
                "fold": fold,
                "n_train": int(len(train_idx)),
                "n_test": int(len(test_idx)),
                "test_blocks": int(groups.iloc[test_idx].nunique()),
                "r2": float(r2_score(y.iloc[test_idx], pred)),
                "mae": float(mean_absolute_error(y.iloc[test_idx], pred)),
            }
        )
    folds = pd.DataFrame(rows)
    summary = pd.DataFrame(
        [
            {
                "outcome": outcome,
                "n_folds": int(folds["fold"].nunique()),
                "r2_mean": float(folds["r2"].mean()),
                "r2_sd": float(folds["r2"].std(ddof=1)),
                "mae_mean": float(folds["mae"].mean()),
                "mae_sd": float(folds["mae"].std(ddof=1)),
            }
        ]
    )
    folds.to_csv(OBJ99 / "outputs" / "tables" / "fractional_loss_spatial_block_cv_folds.csv", index=False)
    return summary


def fractional_loss_driver_check() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(OBJ5 / "data" / "tabular" / "drivers_model_sample.csv")
    denom = df["pre_event_gpp_baseline"] * 5.0
    df["GPP_fractional_cumulative_loss"] = np.where(denom > 0, df["GPP_cumulative_loss"] / denom, np.nan)
    df = df[np.isfinite(df["GPP_fractional_cumulative_loss"])].copy()
    df["GPP_fractional_cumulative_loss"] = df["GPP_fractional_cumulative_loss"].clip(lower=0, upper=df["GPP_fractional_cumulative_loss"].quantile(0.995))
    imp, metrics = rf_regression(df, "GPP_fractional_cumulative_loss")
    spatial_summary = spatial_cv_fractional(df)
    metrics_df = pd.concat([pd.DataFrame([metrics]), spatial_summary.assign(model="RandomForestRegressor_spatial_block")], ignore_index=True, sort=False)
    imp.to_csv(OBJ99 / "outputs" / "tables" / "rf_importance_GPP_fractional_cumulative_loss.csv", index=False)
    metrics_df.to_csv(OBJ99 / "outputs" / "tables" / "fractional_loss_model_performance.csv", index=False)
    return imp, metrics_df


def compound_univariate_comparison() -> pd.DataFrame:
    masks, _ = build_event_masks()
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    kndvi, _ = read_stack(DATASETS / "kNDVI_annual_2001_2024_NEA.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    rows = [summarize_event_type(label, events, gpp, kndvi, forest) for label, events in masks.items()]
    out = pd.DataFrame(rows)
    out.to_csv(OBJ99 / "outputs" / "tables" / "compound_vs_univariate_event_response.csv", index=False)
    return out


def hotspot_external_validation() -> pd.DataFrame:
    event_freq, _ = read_single(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")
    severe, _ = read_single(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")
    vulnerability, _ = read_single(OBJ3 / "data" / "processed_tif" / "vulnerability_index_0_1.tif")
    forest_loss, _ = read_single(PROCESSED99 / "Hansen_forest_loss_any_event_post_2004_2024_aligned_0p1deg.tif")
    burned, _ = read_single(PROCESSED99 / "MCD64A1_burned_any_event_post_2004_2024_aligned_0p1deg.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    valid = (
        np.isfinite(event_freq)
        & (event_freq > 0)
        & np.isfinite(severe)
        & np.isfinite(vulnerability)
        & np.isfinite(forest_loss)
        & np.isfinite(forest)
        & (forest >= 1)
        & (forest <= 6)
    )
    target = (forest_loss > 0).astype("int8")
    severe_bool = severe == 1
    not_severe = valid & ~severe_bool
    severe_mask = valid & severe_bool
    rows = []
    for label, mask in [("severe_hotspot", severe_mask), ("non_severe_event_pixels", not_severe)]:
        rows.append(
            {
                "domain": label,
                "n_pixels": int(np.sum(mask)),
                "hansen_forest_loss_screen_prevalence": float(np.mean(target[mask])),
                "mean_vulnerability_index": float(np.nanmean(vulnerability[mask])),
                "burned_screen_prevalence": float(np.mean((np.nan_to_num(burned, nan=0) > 0)[mask])),
            }
        )
    severe_prev = rows[0]["hansen_forest_loss_screen_prevalence"]
    non_prev = rows[1]["hansen_forest_loss_screen_prevalence"]
    rows.append(
        {
            "domain": "severe_vs_non_severe_ratio",
            "n_pixels": int(np.sum(valid)),
            "hansen_forest_loss_screen_prevalence": float(severe_prev / non_prev) if non_prev > 0 else np.nan,
            "mean_vulnerability_index": np.nan,
            "burned_screen_prevalence": np.nan,
        }
    )

    rng = np.random.default_rng(SEED)
    rows_idx, cols_idx = np.where(valid)
    if rows_idx.size > MAX_SAMPLE:
        idx = rng.choice(rows_idx.size, size=MAX_SAMPLE, replace=False)
        rows_idx, cols_idx = rows_idx[idx], cols_idx[idx]
    df = pd.DataFrame(
        {
            "vulnerability_index": vulnerability[rows_idx, cols_idx],
            "severe_hotspot": severe_bool[rows_idx, cols_idx].astype(int),
            "event_frequency": event_freq[rows_idx, cols_idx],
            "vegetation_class": pd.Series(forest[rows_idx, cols_idx].astype(int)).map(CLASS_LABELS).values,
            "hansen_loss": target[rows_idx, cols_idx].astype(int),
        }
    )
    X = pd.get_dummies(df[["vulnerability_index", "severe_hotspot", "event_frequency", "vegetation_class"]], columns=["vegetation_class"], drop_first=True, dtype=float)
    y = df["hansen_loss"].astype(int)
    if y.nunique() > 1:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, stratify=y, random_state=SEED)
        model = RandomForestClassifier(n_estimators=250, min_samples_leaf=30, class_weight="balanced_subsample", n_jobs=-1, random_state=SEED)
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_test)[:, 1]
        auc = float(roc_auc_score(y_test, prob))
        rho, pval = spearmanr(df["vulnerability_index"], df["hansen_loss"])
    else:
        auc, rho, pval = np.nan, np.nan, np.nan
    rows.append(
        {
            "domain": "predictive_model_summary",
            "n_pixels": int(len(df)),
            "hansen_forest_loss_screen_prevalence": float(np.mean(y)),
            "mean_vulnerability_index": np.nan,
            "burned_screen_prevalence": np.nan,
            "rf_auc": auc,
            "spearman_vulnerability_loss_rho": float(rho),
            "spearman_p_value": float(pval),
        }
    )
    out = pd.DataFrame(rows)
    out.to_csv(OBJ99 / "outputs" / "tables" / "hotspot_hansen_forest_loss_validation.csv", index=False)
    return out


def plot_summary(frac_imp: pd.DataFrame, event_summary: pd.DataFrame, validation: pd.DataFrame) -> None:
    sns.set_theme(context="paper", style="whitegrid", font="Arial")
    fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.4))

    imp = frac_imp.head(6).copy()
    imp["label"] = imp["feature"].str.replace("vegetation_class_", "Veg: ", regex=False).map(lambda x: PREDICTOR_LABELS.get(x, x))
    sns.barplot(data=imp, y="label", x="importance_mean", color="#4C78A8", ax=axes[0])
    axes[0].errorbar(imp["importance_mean"], np.arange(len(imp)), xerr=imp["importance_sd"], fmt="none", color="black", linewidth=0.8)
    axes[0].set_xlabel("Fractional-loss importance")
    axes[0].set_ylabel("")

    plot_events = event_summary.copy()
    plot_events["event_type"] = plot_events["event_type"].map({"compound": "Compound", "drought_only": "Drought only", "heat_only": "Heat only"})
    sns.barplot(data=plot_events, x="event_type", y="GPP_cumulative_loss_mean", color="#DD8452", ax=axes[1])
    axes[1].set_xlabel("")
    axes[1].set_ylabel("Mean GPP cumulative loss")
    axes[1].tick_params(axis="x", rotation=20)

    val = validation[validation["domain"].isin(["severe_hotspot", "non_severe_event_pixels"])].copy()
    val["domain"] = val["domain"].map({"severe_hotspot": "Severe", "non_severe_event_pixels": "Non-severe"})
    sns.barplot(data=val, x="domain", y="hansen_forest_loss_screen_prevalence", color="#55A868", ax=axes[2])
    axes[2].set_xlabel("")
    axes[2].set_ylabel("Hansen loss-screen prevalence")

    for panel, ax in zip(["A", "B", "C"], axes):
        ax.text(0.02, 0.98, panel, transform=ax.transAxes, va="top", ha="left", fontsize=12, fontweight="bold")
        ax.grid(True, axis="x" if panel == "A" else "y", color="#E5E5E5", linewidth=0.7)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=2.0)
    fig.savefig(PUBFIG / "figure17_supervisor_robustness_checks.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure17_supervisor_robustness_checks.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    frac_imp, frac_metrics = fractional_loss_driver_check()
    event_summary = compound_univariate_comparison()
    validation = hotspot_external_validation()
    plot_summary(frac_imp, event_summary, validation)
    metadata = {
        "comment_6": "GPP cumulative loss normalized by five pre-event baseline years and re-fit with random forest.",
        "comment_7": "Compound events compared with mutually exclusive drought-only and heat-only years using identical event-window response metrics.",
        "comment_8": "Severe hotspot domain evaluated against the Hansen event/post-period forest-loss screen as an external disturbance target.",
    }
    (OBJ99 / "logs" / "supervisor_comment_6_8_robustness_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ99 / "outputs" / "tables" / "rf_importance_GPP_fractional_cumulative_loss.csv")
    print(OBJ99 / "outputs" / "tables" / "compound_vs_univariate_event_response.csv")
    print(OBJ99 / "outputs" / "tables" / "hotspot_hansen_forest_loss_validation.csv")


if __name__ == "__main__":
    main()
