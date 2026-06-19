from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


ROOT = Path(__file__).resolve().parents[3]
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ2 = ROOT / "Paper2_Objectives" / "02_event_order_resistance_recovery"
OUT = ROOT / "Manuscript" / "figures_publication"


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
            "legend.fontsize": 9.5,
            "grid.color": "#E5E5E5",
            "grid.linewidth": 0.7,
            "lines.linewidth": 1.4,
            "patch.edgecolor": "none",
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


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def event_panel() -> None:
    df = pd.read_csv(OBJ1 / "outputs" / "tables" / "objective1_event_panel_summary.csv")
    df = df[df["vegetation_class"].astype(str) == "all_forest"].copy()
    long = df.melt(
        id_vars=["relative_year"],
        value_vars=["kNDVI_anomaly_mean", "GPP_anomaly_mean"],
        var_name="Variable",
        value_name="Mean anomaly",
    )
    long["Variable"] = long["Variable"].map({"kNDVI_anomaly_mean": "kNDVI", "GPP_anomaly_mean": "GPP"})

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    sns.lineplot(
        data=long,
        x="relative_year",
        y="Mean anomaly",
        hue="Variable",
        style="Variable",
        markers=True,
        dashes=False,
        markersize=6,
        palette={"kNDVI": "#1B9E77", "GPP": "#D95F02"},
        ax=ax,
    )
    ax.axhline(0, color="black", linewidth=0.7)
    ax.axvline(0, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Relative year")
    ax.set_ylabel("Mean anomaly from baseline")
    ax.set_xticks(range(-3, 5))
    ax.legend(frameon=False, title=None, loc="upper left")
    finish_axes(ax)
    save(fig, "figure1_event_panel_ggstyle")


def first_later_loss() -> None:
    df = pd.read_csv(OBJ2 / "outputs" / "tables" / "event_order_summary_by_class.csv")
    row = df[df["vegetation_class"].astype(str) == "all_recurrent_forest"].iloc[0]
    plot_df = pd.DataFrame(
        [
            {"Variable": "kNDVI", "Event order": "First", "Mean cumulative loss": row["kNDVI_first_cumulative_loss_mean"]},
            {"Variable": "kNDVI", "Event order": "Later", "Mean cumulative loss": row["kNDVI_later_cumulative_loss_mean"]},
            {"Variable": "GPP", "Event order": "First", "Mean cumulative loss": row["GPP_first_cumulative_loss_mean"]},
            {"Variable": "GPP", "Event order": "Later", "Mean cumulative loss": row["GPP_later_cumulative_loss_mean"]},
        ]
    )

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.6))
    for ax, variable, panel in zip(axes, ["kNDVI", "GPP"], ["A", "B"]):
        sub = plot_df[plot_df["Variable"] == variable]
        sns.barplot(
            data=sub,
            x="Event order",
            y="Mean cumulative loss",
            hue="Event order",
            palette={"First": "#4C78A8", "Later": "#F58518"},
            legend=False,
            ax=ax,
        )
        ax.text(0.02, 0.96, panel, transform=ax.transAxes, va="top", ha="left", fontsize=12, fontweight="bold")
        ax.set_title(variable, fontsize=10.5, fontweight="bold", pad=8)
        ax.set_xlabel("")
        ax.set_ylabel("Mean cumulative loss")
        finish_axes(ax)
    fig.tight_layout(w_pad=2.0)
    save(fig, "figure2_first_later_loss_ggstyle")


def classwise_gpp_delta() -> None:
    summary = pd.read_csv(OBJ2 / "outputs" / "tables" / "event_order_summary_by_class.csv")
    ci = pd.read_csv(OBJ2 / "outputs" / "tables" / "event_order_bootstrap_ci.csv")
    df = summary[summary["vegetation_class"].astype(str) != "all_recurrent_forest"].copy()
    ci = ci[ci["metric"] == "GPP_delta_cumulative_loss"].copy()
    df = df.merge(ci[["vegetation_class", "ci_low", "ci_high"]], on="vegetation_class", how="left")
    order = ["ENT", "EBT", "DNT", "DBT", "SHB", "GRS"]
    df["vegetation_class"] = pd.Categorical(df["vegetation_class"], categories=order, ordered=True)
    df = df.sort_values("vegetation_class")

    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    sns.barplot(
        data=df,
        x="vegetation_class",
        y="GPP_delta_cumulative_loss_mean",
        color="#4C78A8",
        ax=ax,
    )
    x_positions = range(len(df))
    y = df["GPP_delta_cumulative_loss_mean"].to_numpy()
    yerr_lower = y - df["ci_low"].to_numpy()
    yerr_upper = df["ci_high"].to_numpy() - y
    ax.errorbar(x_positions, y, yerr=[yerr_lower, yerr_upper], fmt="none", color="black", capsize=3, linewidth=0.9)
    ax.axhline(0, color="black", linewidth=0.7)
    ax.set_xlabel("Vegetation class")
    ax.set_ylabel("Later-minus-first GPP cumulative loss")
    finish_axes(ax)
    save(fig, "figure3_classwise_gpp_delta_ggstyle")


def main() -> None:
    setup_theme()
    event_panel()
    first_later_loss()
    classwise_gpp_delta()
    print(OUT)


if __name__ == "__main__":
    main()
