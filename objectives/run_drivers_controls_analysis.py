from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns
import statsmodels.api as sm
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.model_selection import GroupKFold, train_test_split
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ3 = ROOT / "Paper2_Objectives" / "03_vulnerability_hotspots_resilience_erosion"
OBJ5 = ROOT / "Paper2_Objectives" / "05_drivers_controls_legacy_loss"
PUBFIG = ROOT / "Manuscript" / "figures_publication"

YEARS = np.arange(2001, 2025)
EVENT_YEARS = np.arange(2004, 2021)
BOOTSTRAP_SEED = 44
MAX_SAMPLE = 120_000
CLASS_LABELS = {1: "ENT", 2: "EBT", 3: "DNT", 4: "DBT", 5: "SHB", 6: "GRS"}

PREDICTOR_LABELS = {
    "event_frequency": "Event frequency",
    "mean_event_spei12": "Event SPEI-12",
    "mean_event_tmax_z": "Event Tmax z",
    "mean_event_def_z": "Event deficit z",
    "mean_event_pr_z": "Event precipitation z",
    "mean_event_pet_z": "Event PET z",
    "mean_event_pdsi": "Event PDSI",
    "pre_event_gpp_baseline": "Pre-event GPP",
    "vegetation_class_code": "Vegetation class",
}


def ensure_dirs() -> None:
    for p in [
        OBJ5 / "data" / "processed_tif",
        OBJ5 / "data" / "qgis_ready",
        OBJ5 / "data" / "tabular",
        OBJ5 / "outputs" / "tables",
        OBJ5 / "outputs" / "figures",
        OBJ5 / "outputs" / "model_outputs",
        OBJ5 / "logs",
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


def december_spei(spei_monthly: np.ndarray) -> np.ndarray:
    annual = []
    for year in range(2001, 2023):
        annual.append(spei_monthly[(year - 2001) * 12 + 11])
    return np.stack(annual).astype("float32")


def zscore_stack(stack: np.ndarray) -> np.ndarray:
    mean = np.nanmean(stack, axis=0)
    sd = np.nanstd(stack, axis=0)
    out = (stack - mean) / sd
    out[~np.isfinite(out)] = np.nan
    return out.astype("float32")


def mean_at_events(driver: np.ndarray, events: np.ndarray) -> np.ndarray:
    sums = np.zeros(events.shape[1:], dtype="float64")
    counts = np.zeros(events.shape[1:], dtype="float64")
    for i, year in enumerate(EVENT_YEARS):
        event = events[i].astype(bool)
        arr = driver[year - 2001]
        valid = event & np.isfinite(arr)
        sums[valid] += arr[valid]
        counts[valid] += 1
    out = np.divide(sums, counts, out=np.full(events.shape[1:], np.nan), where=counts > 0)
    return out.astype("float32")


def pre_event_mean_gpp(gpp: np.ndarray, events: np.ndarray) -> np.ndarray:
    sums = np.zeros(events.shape[1:], dtype="float64")
    counts = np.zeros(events.shape[1:], dtype="float64")
    for i, year in enumerate(EVENT_YEARS):
        event = events[i].astype(bool)
        baseline = np.nanmean(gpp[year - 2001 - 3 : year - 2001], axis=0)
        valid = event & np.isfinite(baseline)
        sums[valid] += baseline[valid]
        counts[valid] += 1
    out = np.divide(sums, counts, out=np.full(events.shape[1:], np.nan), where=counts > 0)
    return out.astype("float32")


def build_driver_maps() -> tuple[dict[str, np.ndarray], dict]:
    events, profile = read_stack(OBJ1 / "data" / "processed_tif" / "compound_hotdry_event_mask_2004_2020.tif")
    spei_monthly, _ = read_stack(DATASETS / "SPEI12_monthly_2001_2022_NEA.tif")
    tmx, _ = read_stack(DATASETS / "TC_tmx_annual_2001_2024_NEA.tif")
    pr, _ = read_stack(DATASETS / "TC_pr_annual_2001_2024_NEA.tif")
    pet, _ = read_stack(DATASETS / "TC_pet_annual_2001_2024_NEA.tif")
    deficit, _ = read_stack(DATASETS / "TC_def_annual_2001_2024_NEA.tif")
    pdsi, _ = read_stack(DATASETS / "TC_pdsi_annual_2001_2024_NEA.tif")
    gpp, _ = read_stack(OBJ1 / "data" / "processed_tif" / "GPP_annual_2001_2024_aligned_to_0p1deg.tif")
    forest, _ = read_single(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")
    event_frequency, _ = read_single(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")

    spei_dec = december_spei(spei_monthly)
    spei_24 = np.full_like(tmx, np.nan, dtype="float32")
    spei_24[: spei_dec.shape[0]] = spei_dec

    driver_maps = {
        "event_frequency": event_frequency,
        "mean_event_spei12": mean_at_events(spei_24, events),
        "mean_event_tmax_z": mean_at_events(zscore_stack(tmx), events),
        "mean_event_def_z": mean_at_events(zscore_stack(deficit), events),
        "mean_event_pr_z": mean_at_events(zscore_stack(pr), events),
        "mean_event_pet_z": mean_at_events(zscore_stack(pet), events),
        "mean_event_pdsi": mean_at_events(pdsi, events),
        "pre_event_gpp_baseline": pre_event_mean_gpp(gpp, events),
        "vegetation_class_code": forest,
    }
    return driver_maps, profile


def sample_dataframe(driver_maps: dict[str, np.ndarray], profile: dict) -> pd.DataFrame:
    outcomes = {
        "GPP_cumulative_loss": read_single(OBJ1 / "data" / "processed_tif" / "GPP_cumulative_loss_t0_t4_mean.tif")[0],
        "GPP_legacy_years": read_single(OBJ1 / "data" / "processed_tif" / "GPP_legacy_years_t1_t4_mean.tif")[0],
        "vulnerability_index": read_single(OBJ3 / "data" / "processed_tif" / "vulnerability_index_0_1.tif")[0],
        "severe_hotspot": read_single(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")[0],
    }
    valid = np.ones_like(outcomes["GPP_cumulative_loss"], dtype=bool)
    for arr in [*driver_maps.values(), *outcomes.values()]:
        valid &= np.isfinite(arr)
    valid &= driver_maps["event_frequency"] > 0
    rows, cols = np.where(valid)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    if rows.size > MAX_SAMPLE:
        idx = rng.choice(rows.size, size=MAX_SAMPLE, replace=False)
        rows, cols = rows[idx], cols[idx]

    data = {name: arr[rows, cols] for name, arr in driver_maps.items()}
    data.update({name: arr[rows, cols] for name, arr in outcomes.items()})
    df = pd.DataFrame(data)
    xs, ys = rasterio.transform.xy(profile["transform"], rows, cols, offset="center")
    df["row"] = rows.astype(int)
    df["col"] = cols.astype(int)
    df["longitude"] = np.asarray(xs, dtype="float32")
    df["latitude"] = np.asarray(ys, dtype="float32")
    block_size_deg = 10.0
    lon_block = np.floor(df["longitude"] / block_size_deg).astype(int)
    lat_block = np.floor(df["latitude"] / block_size_deg).astype(int)
    df["spatial_block_10deg"] = lat_block.astype(str) + "_" + lon_block.astype(str)
    df["severe_hotspot"] = (df["severe_hotspot"] == 1).astype(int)
    df["vegetation_class"] = df["vegetation_class_code"].astype(int).map(CLASS_LABELS)
    return df


def spearman_table(df: pd.DataFrame) -> pd.DataFrame:
    predictors = list(PREDICTOR_LABELS.keys())[:-1]
    outcomes = ["GPP_cumulative_loss", "GPP_legacy_years", "vulnerability_index", "severe_hotspot"]
    rows = []
    for outcome in outcomes:
        for predictor in predictors:
            sub = df[[predictor, outcome]].dropna()
            rho, pval = spearmanr(sub[predictor], sub[outcome])
            rows.append(
                {
                    "outcome": outcome,
                    "predictor": predictor,
                    "predictor_label": PREDICTOR_LABELS[predictor],
                    "spearman_rho": float(rho),
                    "p_value": float(pval),
                    "n": int(len(sub)),
                }
            )
    return pd.DataFrame(rows)


def model_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    continuous = list(PREDICTOR_LABELS.keys())[:-1]
    X = df[continuous + ["vegetation_class"]].copy()
    X = pd.get_dummies(X, columns=["vegetation_class"], drop_first=True, dtype=float)
    feature_names = X.columns.tolist()
    return X, feature_names


def random_forest_regression(df: pd.DataFrame, outcome: str) -> tuple[pd.DataFrame, dict, RandomForestRegressor, pd.DataFrame]:
    X, feature_names = model_matrix(df)
    y = df[outcome].astype(float)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=BOOTSTRAP_SEED)
    model = RandomForestRegressor(n_estimators=250, min_samples_leaf=20, n_jobs=-1, random_state=BOOTSTRAP_SEED)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    perm = permutation_importance(model, X_test, y_test, n_repeats=8, random_state=BOOTSTRAP_SEED, n_jobs=-1)
    importance = pd.DataFrame(
        {
            "feature": feature_names,
            "importance_mean": perm.importances_mean,
            "importance_sd": perm.importances_std,
            "outcome": outcome,
        }
    ).sort_values("importance_mean", ascending=False)
    metrics = {
        "outcome": outcome,
        "model": "RandomForestRegressor",
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "r2": float(r2_score(y_test, pred)),
        "mae": float(mean_absolute_error(y_test, pred)),
    }
    return importance, metrics, model, X


def random_forest_classifier(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, RandomForestClassifier, pd.DataFrame]:
    X, feature_names = model_matrix(df)
    y = df["severe_hotspot"].astype(int)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, stratify=y, random_state=BOOTSTRAP_SEED)
    model = RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=20,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=BOOTSTRAP_SEED,
    )
    model.fit(X_train, y_train)
    prob = model.predict_proba(X_test)[:, 1]
    perm = permutation_importance(model, X_test, y_test, n_repeats=8, random_state=BOOTSTRAP_SEED, n_jobs=-1, scoring="roc_auc")
    importance = pd.DataFrame(
        {
            "feature": feature_names,
            "importance_mean": perm.importances_mean,
            "importance_sd": perm.importances_std,
            "outcome": "severe_hotspot",
        }
    ).sort_values("importance_mean", ascending=False)
    metrics = {
        "outcome": "severe_hotspot",
        "model": "RandomForestClassifier",
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "auc": float(roc_auc_score(y_test, prob)),
        "event_rate_test": float(np.mean(y_test)),
    }
    return importance, metrics, model, X


def spatial_block_cv(df: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    X, _ = model_matrix(df)
    groups = df["spatial_block_10deg"].astype(str)
    group_counts = groups.value_counts()
    viable_groups = group_counts[group_counts >= 100].index
    keep = groups.isin(viable_groups)
    X = X.loc[keep].reset_index(drop=True)
    work = df.loc[keep].reset_index(drop=True)
    groups = work["spatial_block_10deg"].astype(str)
    splitter = GroupKFold(n_splits=n_splits)
    rows = []
    outcomes = [
        ("GPP_cumulative_loss", "RandomForestRegressor"),
        ("vulnerability_index", "RandomForestRegressor"),
        ("severe_hotspot", "RandomForestClassifier"),
    ]
    for fold, (train_idx, test_idx) in enumerate(splitter.split(X, groups=groups), start=1):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        test_groups = groups.iloc[test_idx].nunique()
        for outcome, model_type in outcomes:
            if model_type == "RandomForestRegressor":
                y = work[outcome].astype(float)
                model = RandomForestRegressor(n_estimators=180, min_samples_leaf=20, n_jobs=-1, random_state=BOOTSTRAP_SEED + fold)
                model.fit(X_train, y.iloc[train_idx])
                pred = model.predict(X_test)
                rows.append(
                    {
                        "validation": "spatial_block_10deg",
                        "fold": fold,
                        "outcome": outcome,
                        "model": model_type,
                        "n_train": int(len(train_idx)),
                        "n_test": int(len(test_idx)),
                        "test_blocks": int(test_groups),
                        "r2": float(r2_score(y.iloc[test_idx], pred)),
                        "mae": float(mean_absolute_error(y.iloc[test_idx], pred)),
                        "auc": np.nan,
                        "event_rate_test": np.nan,
                    }
                )
            else:
                y = work[outcome].astype(int)
                if y.iloc[test_idx].nunique() < 2 or y.iloc[train_idx].nunique() < 2:
                    continue
                model = RandomForestClassifier(
                    n_estimators=220,
                    min_samples_leaf=20,
                    class_weight="balanced_subsample",
                    n_jobs=-1,
                    random_state=BOOTSTRAP_SEED + fold,
                )
                model.fit(X_train, y.iloc[train_idx])
                prob = model.predict_proba(X_test)[:, 1]
                rows.append(
                    {
                        "validation": "spatial_block_10deg",
                        "fold": fold,
                        "outcome": outcome,
                        "model": model_type,
                        "n_train": int(len(train_idx)),
                        "n_test": int(len(test_idx)),
                        "test_blocks": int(test_groups),
                        "r2": np.nan,
                        "mae": np.nan,
                        "auc": float(roc_auc_score(y.iloc[test_idx], prob)),
                        "event_rate_test": float(np.mean(y.iloc[test_idx])),
                    }
                )
    fold_df = pd.DataFrame(rows)
    summary_rows = []
    for (outcome, model), sub in fold_df.groupby(["outcome", "model"], sort=False):
        summary_rows.append(
            {
                "validation": "spatial_block_10deg",
                "outcome": outcome,
                "model": model,
                "n_folds": int(sub["fold"].nunique()),
                "mean_n_test": float(sub["n_test"].mean()),
                "mean_test_blocks": float(sub["test_blocks"].mean()),
                "r2_mean": float(sub["r2"].mean()) if sub["r2"].notna().any() else np.nan,
                "r2_sd": float(sub["r2"].std(ddof=1)) if sub["r2"].notna().sum() > 1 else np.nan,
                "mae_mean": float(sub["mae"].mean()) if sub["mae"].notna().any() else np.nan,
                "mae_sd": float(sub["mae"].std(ddof=1)) if sub["mae"].notna().sum() > 1 else np.nan,
                "auc_mean": float(sub["auc"].mean()) if sub["auc"].notna().any() else np.nan,
                "auc_sd": float(sub["auc"].std(ddof=1)) if sub["auc"].notna().sum() > 1 else np.nan,
                "event_rate_test_mean": float(sub["event_rate_test"].mean()) if sub["event_rate_test"].notna().any() else np.nan,
            }
        )
    summary = pd.DataFrame(summary_rows)
    fold_df.to_csv(OBJ5 / "outputs" / "tables" / "spatial_block_cv_fold_metrics.csv", index=False)
    summary.to_csv(OBJ5 / "outputs" / "tables" / "spatial_block_cv_summary.csv", index=False)
    return summary


def logistic_odds_ratios(df: pd.DataFrame) -> pd.DataFrame:
    continuous = list(PREDICTOR_LABELS.keys())[:-1]
    X = df[continuous + ["vegetation_class"]].copy()
    scaler = StandardScaler()
    X[continuous] = scaler.fit_transform(X[continuous])
    X = pd.get_dummies(X, columns=["vegetation_class"], drop_first=True, dtype=float)
    X = sm.add_constant(X, has_constant="add")
    y = df["severe_hotspot"].astype(int)
    model = sm.GLM(y, X, family=sm.families.Binomial()).fit(cov_type="HC3")
    conf = model.conf_int()
    rows = []
    for feature in X.columns:
        if feature == "const":
            continue
        rows.append(
            {
                "feature": feature,
                "odds_ratio": float(np.exp(model.params[feature])),
                "ci_low": float(np.exp(conf.loc[feature, 0])),
                "ci_high": float(np.exp(conf.loc[feature, 1])),
                "p_value": float(model.pvalues[feature]),
            }
        )
    return pd.DataFrame(rows).sort_values("odds_ratio", ascending=False)


def predict_maps(driver_maps: dict[str, np.ndarray], profile: dict, reg_model: RandomForestRegressor, cls_model: RandomForestClassifier, feature_columns: list[str]) -> None:
    valid = np.ones_like(driver_maps["event_frequency"], dtype=bool)
    for arr in driver_maps.values():
        valid &= np.isfinite(arr)
    valid &= driver_maps["event_frequency"] > 0
    rows, cols = np.where(valid)
    df = pd.DataFrame({name: arr[rows, cols] for name, arr in driver_maps.items()})
    df["vegetation_class"] = df["vegetation_class_code"].astype(int).map(CLASS_LABELS)
    X, _ = model_matrix(df)
    X = X.reindex(columns=feature_columns, fill_value=0)
    pred_vuln = np.full(valid.shape, np.nan, dtype="float32")
    pred_prob = np.full(valid.shape, np.nan, dtype="float32")
    pred_vuln[rows, cols] = reg_model.predict(X).astype("float32")
    pred_prob[rows, cols] = cls_model.predict_proba(X)[:, 1].astype("float32")
    write_single(OBJ5 / "data" / "processed_tif" / "rf_predicted_vulnerability_index.tif", pred_vuln, profile, "Random-forest predicted vulnerability index")
    write_single(OBJ5 / "data" / "processed_tif" / "rf_severe_hotspot_probability.tif", pred_prob, profile, "Random-forest severe hotspot probability")


def plot_importance(df: pd.DataFrame, name: str, xlabel: str) -> None:
    plot_df = df.head(10).copy()
    plot_df["feature_label"] = plot_df["feature"].str.replace("vegetation_class_", "Veg: ", regex=False)
    plot_df["feature_label"] = plot_df["feature_label"].map(lambda x: PREDICTOR_LABELS.get(x, x))
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    sns.barplot(data=plot_df, y="feature_label", x="importance_mean", color="#4C78A8", ax=ax)
    ax.errorbar(plot_df["importance_mean"], np.arange(len(plot_df)), xerr=plot_df["importance_sd"], fmt="none", color="black", linewidth=0.8)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("")
    ax.grid(True, axis="x", color="#E5E5E5", linewidth=0.7)
    ax.grid(False, axis="y")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.savefig(PUBFIG / f"{name}.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_driver_gradient(df: pd.DataFrame) -> None:
    sample = df.sample(min(45_000, len(df)), random_state=BOOTSTRAP_SEED)
    fig, ax = plt.subplots(figsize=(6.5, 4.8))
    hb = ax.hexbin(
        sample["mean_event_spei12"],
        sample["mean_event_tmax_z"],
        C=sample["GPP_cumulative_loss"],
        gridsize=42,
        reduce_C_function=np.nanmean,
        cmap="magma",
        mincnt=8,
    )
    cbar = fig.colorbar(hb, ax=ax)
    cbar.set_label("Mean GPP cumulative loss", fontweight="bold")
    ax.set_xlabel("Mean event SPEI-12")
    ax.set_ylabel("Mean event Tmax z")
    ax.grid(True, color="#E5E5E5", linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    fig.savefig(PUBFIG / "figure10_heat_drought_gpp_loss_gradient.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure10_heat_drought_gpp_loss_gradient.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_combined_importance(gpp_imp: pd.DataFrame, severe_imp: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(8.0, 4.2))
    for ax, data, panel, xlabel in [
        (axes[0], gpp_imp, "A", "GPP loss importance"),
        (axes[1], severe_imp, "B", "Severe hotspot importance"),
    ]:
        plot_df = data.head(7).copy()
        plot_df["feature_label"] = plot_df["feature"].str.replace("vegetation_class_", "Veg: ", regex=False)
        plot_df["feature_label"] = plot_df["feature_label"].map(lambda x: PREDICTOR_LABELS.get(x, x))
        sns.barplot(data=plot_df, y="feature_label", x="importance_mean", color="#4C78A8", ax=ax)
        ax.errorbar(plot_df["importance_mean"], np.arange(len(plot_df)), xerr=plot_df["importance_sd"], fmt="none", color="black", linewidth=0.8)
        ax.text(0.02, 0.98, panel, transform=ax.transAxes, va="top", ha="left", fontsize=12, fontweight="bold")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("")
        ax.grid(True, axis="x", color="#E5E5E5", linewidth=0.7)
        ax.grid(False, axis="y")
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("black")
            spine.set_linewidth(0.8)
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontweight("bold")
    fig.tight_layout(w_pad=2.2)
    fig.savefig(PUBFIG / "figure11_driver_importance_combined.png", bbox_inches="tight", facecolor="white", dpi=600)
    fig.savefig(PUBFIG / "figure11_driver_importance_combined.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_qgis_inventory() -> None:
    rows = [
        ("mean_event_spei12.tif", "Mean December SPEI-12 during compound hot-dry event years."),
        ("mean_event_tmax_z.tif", "Mean annual Tmax z-score during compound hot-dry event years."),
        ("mean_event_def_z.tif", "Mean climatic water-deficit z-score during compound hot-dry event years."),
        ("pre_event_gpp_baseline.tif", "Mean pre-event GPP baseline across event windows."),
        ("rf_predicted_vulnerability_index.tif", "Random-forest predicted continuous vulnerability index."),
        ("rf_severe_hotspot_probability.tif", "Random-forest predicted severe-hotspot probability."),
    ]
    pd.DataFrame(
        {
            "raster": [str(OBJ5 / "data" / "processed_tif" / r) for r, _ in rows],
            "description": [d for _, d in rows],
            "recommended_use": [
                "QGIS driver panel or supplement",
                "QGIS driver panel or supplement",
                "QGIS driver panel or supplement",
                "QGIS driver panel or supplement",
                "Main QGIS driver panel",
                "Main QGIS driver panel",
            ],
        }
    ).to_csv(OBJ5 / "data" / "qgis_ready" / "drivers_qgis_raster_inventory.csv", index=False)


def setup_theme() -> None:
    sns.set_theme(context="paper", style="whitegrid", font="Arial")


def main() -> None:
    ensure_dirs()
    setup_theme()
    driver_maps, profile = build_driver_maps()
    for name, arr in driver_maps.items():
        if name != "vegetation_class_code":
            write_single(OBJ5 / "data" / "processed_tif" / f"{name}.tif", arr, profile, name.replace("_", " "))

    df = sample_dataframe(driver_maps, profile)
    df.to_csv(OBJ5 / "data" / "tabular" / "drivers_model_sample.csv", index=False)

    corr = spearman_table(df)
    corr.to_csv(OBJ5 / "outputs" / "tables" / "driver_spearman_correlations.csv", index=False)

    gpp_imp, gpp_metrics, _, _ = random_forest_regression(df, "GPP_cumulative_loss")
    vuln_imp, vuln_metrics, vuln_model, X_all = random_forest_regression(df, "vulnerability_index")
    severe_imp, severe_metrics, severe_model, _ = random_forest_classifier(df)
    spatial_cv = spatial_block_cv(df)
    odds = logistic_odds_ratios(df)

    gpp_imp.to_csv(OBJ5 / "outputs" / "tables" / "rf_importance_GPP_cumulative_loss.csv", index=False)
    vuln_imp.to_csv(OBJ5 / "outputs" / "tables" / "rf_importance_vulnerability_index.csv", index=False)
    severe_imp.to_csv(OBJ5 / "outputs" / "tables" / "rf_importance_severe_hotspot.csv", index=False)
    odds.to_csv(OBJ5 / "outputs" / "tables" / "logistic_odds_ratios_severe_hotspot.csv", index=False)
    pd.DataFrame([gpp_metrics, vuln_metrics, severe_metrics]).to_csv(OBJ5 / "outputs" / "tables" / "model_performance_summary.csv", index=False)

    predict_maps(driver_maps, profile, vuln_model, severe_model, X_all.columns.tolist())
    plot_importance(gpp_imp, "figure8_driver_importance_gpp_loss", "Permutation importance for GPP loss")
    plot_importance(severe_imp, "figure9_driver_importance_severe_hotspot", "Permutation importance for severe hotspot AUC")
    plot_combined_importance(gpp_imp, severe_imp)
    plot_driver_gradient(df)
    write_qgis_inventory()

    metadata = {
        "sample_n": int(len(df)),
        "predictors": PREDICTOR_LABELS,
        "model_performance": [gpp_metrics, vuln_metrics, severe_metrics],
        "spatial_block_cv": spatial_cv.to_dict(orient="records"),
        "notes": "Drivers are event-window summaries on the common 0.1 degree grid. Random-forest importances are permutation importances on held-out test samples. Spatial-block cross-validation uses 10 degree geographic blocks as held-out groups.",
    }
    (OBJ5 / "logs" / "drivers_controls_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(OBJ5 / "outputs" / "tables" / "model_performance_summary.csv")
    print(OBJ5 / "data" / "qgis_ready" / "drivers_qgis_raster_inventory.csv")


if __name__ == "__main__":
    main()
