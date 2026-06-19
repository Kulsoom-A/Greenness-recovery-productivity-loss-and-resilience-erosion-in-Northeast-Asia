from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import TwoSlopeNorm
from mpl_toolkits.axes_grid1.inset_locator import inset_axes


ROOT = Path(__file__).resolve().parents[3]
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ2 = ROOT / "Paper2_Objectives" / "02_event_order_resistance_recovery"
OBJ4 = ROOT / "Paper2_Objectives" / "04_kNDVI_GPP_recovery_mismatch"
PUBFIG = ROOT / "Manuscript" / "figures_publication"


def read_raster(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        if nodata is not None and not np.isnan(nodata):
            arr[arr == nodata] = np.nan
        bounds = src.bounds
    return arr, (bounds.left, bounds.right, bounds.bottom, bounds.top)


def valid_extent(arrays: list[np.ndarray], extent: tuple[float, float, float, float], pad: float = 4.0):
    left, right, bottom, top = extent
    valid = np.zeros_like(arrays[0], dtype=bool)
    for arr in arrays:
        valid |= np.isfinite(arr)
    rows, cols = np.where(valid)
    if rows.size == 0:
        return extent
    dx = (right - left) / arrays[0].shape[1]
    dy = (top - bottom) / arrays[0].shape[0]
    xmin = left + cols.min() * dx - pad
    xmax = left + (cols.max() + 1) * dx + pad
    ymax = top - rows.min() * dy + pad
    ymin = top - (rows.max() + 1) * dy - pad
    return max(left, xmin), min(right, xmax), max(bottom, ymin), min(top, ymax)


def add_scalebar(ax, extent, length_km=500):
    xmin, xmax, ymin, ymax = extent
    lat = ymin + 0.10 * (ymax - ymin)
    lon0 = xmin + 0.08 * (xmax - xmin)
    km_per_degree = 111.32 * np.cos(np.deg2rad(lat))
    if km_per_degree <= 0:
        return
    length_deg = length_km / km_per_degree
    y = lat
    ax.plot([lon0, lon0 + length_deg], [y, y], transform=ccrs.PlateCarree(), color="black", lw=1.4, solid_capstyle="butt")
    ax.plot([lon0, lon0], [y - 0.22, y + 0.22], transform=ccrs.PlateCarree(), color="black", lw=1.0)
    ax.plot([lon0 + length_deg, lon0 + length_deg], [y - 0.22, y + 0.22], transform=ccrs.PlateCarree(), color="black", lw=1.0)
    ax.text(lon0 + length_deg / 2, y + 0.42, f"{length_km} km", transform=ccrs.PlateCarree(), ha="center", va="bottom", fontsize=5.8, fontweight="bold")


def add_panel(
    ax,
    arr,
    extent,
    panel,
    title,
    cmap,
    cbar_label,
    vmin=None,
    vmax=None,
    norm=None,
    left_labels=True,
    bottom_labels=True,
):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    masked = np.ma.masked_invalid(arr)
    im = ax.imshow(
        masked,
        extent=extent_global,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        norm=norm,
        interpolation="nearest",
    )
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        draw_labels=True,
        linewidth=0.35,
        color="0.65",
        alpha=0.7,
        linestyle="--",
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.left_labels = left_labels
    gl.bottom_labels = bottom_labels
    gl.xlabel_style = {"size": 5.8, "weight": "bold"}
    gl.ylabel_style = {"size": 5.8, "weight": "bold"}
    ax.set_title(f"{panel}. {title}", loc="left", fontsize=7.6, fontweight="bold", pad=3)
    add_scalebar(ax, extent)
    cax = inset_axes(ax, width="34%", height="4.8%", loc="lower center", borderpad=1.15)
    cax.set_facecolor((1, 1, 1, 0.88))
    cbar = plt.colorbar(im, cax=cax, orientation="horizontal")
    cbar.ax.tick_params(labelsize=5.5, length=2.2, width=0.6, pad=1)
    cbar.ax.set_title(cbar_label, fontsize=5.6, fontweight="bold", pad=1.5)
    return im


def save(fig, name):
    PUBFIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(PUBFIG / f"{name}.png", dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(PUBFIG / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def recurrent_erosion_panel():
    recurrent, ext = read_raster(OBJ2 / "data" / "processed_tif" / "kNDVI_later_count.tif")
    gpp_loss, _ = read_raster(OBJ2 / "data" / "processed_tif" / "GPP_delta_cumulative_loss_later_minus_first.tif")
    gpp_recovery, _ = read_raster(OBJ2 / "data" / "processed_tif" / "GPP_delta_recovery_later_minus_first.tif")
    event_freq, _ = read_raster(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")

    recurrent = np.where(recurrent > 0, recurrent + 1, np.nan)
    event_freq = np.where(event_freq > 0, event_freq, np.nan)
    extent = valid_extent([recurrent, gpp_loss, gpp_recovery, event_freq], ext, pad=4.0)

    fig = plt.figure(figsize=(12.2, 5.2))
    axes = [fig.add_subplot(2, 2, i + 1, projection=ccrs.PlateCarree()) for i in range(4)]
    add_panel(axes[0], event_freq, extent, "A", "Hot-dry event frequency", "magma", "events", vmin=1, vmax=np.nanpercentile(event_freq, 99), left_labels=True, bottom_labels=False)
    add_panel(axes[1], recurrent, extent, "B", "Recurrent-event domain", "YlOrRd", "events", vmin=2, vmax=np.nanmax(recurrent), left_labels=False, bottom_labels=False)
    add_panel(axes[2], gpp_loss, extent, "C", "Later-minus-first GPP loss", "viridis", "loss", vmin=0, vmax=np.nanpercentile(gpp_loss, 98), left_labels=True, bottom_labels=True)
    lim = np.nanpercentile(np.abs(gpp_recovery), 98)
    add_panel(
        axes[3],
        gpp_recovery,
        extent,
        "D",
        "Later-minus-first GPP recovery time",
        "coolwarm",
        "years",
        norm=TwoSlopeNorm(vcenter=0, vmin=-lim, vmax=lim),
        left_labels=False,
        bottom_labels=True,
    )
    fig.subplots_adjust(left=0.055, right=0.985, bottom=0.055, top=0.95, wspace=0.12, hspace=0.18)
    save(fig, "main_spatial_recurrent_resilience_erosion")


def mismatch_panel():
    duration, ext = read_raster(OBJ4 / "data" / "processed_tif" / "hidden_mismatch_duration_t1_t4.tif")
    intensity, _ = read_raster(OBJ4 / "data" / "processed_tif" / "mean_standardized_mismatch_intensity_t1_t4.tif")
    hidden_fraction, _ = read_raster(OBJ4 / "data" / "processed_tif" / "hidden_fraction_of_GPP_suppression_t1_t4.tif")
    gpp_suppression, _ = read_raster(OBJ4 / "data" / "processed_tif" / "GPP_suppression_duration_t1_t4.tif")
    duration = np.where(duration > 0, duration, np.nan)
    extent = valid_extent([duration, intensity, hidden_fraction, gpp_suppression], ext, pad=4.0)

    fig = plt.figure(figsize=(12.2, 5.2))
    axes = [fig.add_subplot(2, 2, i + 1, projection=ccrs.PlateCarree()) for i in range(4)]
    add_panel(axes[0], duration, extent, "A", "Hidden mismatch duration", "magma", "years", vmin=1, vmax=4, left_labels=True, bottom_labels=False)
    lim = np.nanpercentile(np.abs(intensity), 98)
    add_panel(
        axes[1],
        intensity,
        extent,
        "B",
        "Standardized mismatch intensity",
        "coolwarm",
        "z-score difference",
        norm=TwoSlopeNorm(vcenter=0, vmin=-lim, vmax=lim),
        left_labels=False,
        bottom_labels=False,
    )
    add_panel(axes[2], hidden_fraction, extent, "C", "Hidden fraction of GPP suppression", "YlGnBu", "fraction", vmin=0, vmax=1, left_labels=True, bottom_labels=True)
    add_panel(axes[3], gpp_suppression, extent, "D", "GPP suppression duration", "PuRd", "years", vmin=0, vmax=4, left_labels=False, bottom_labels=True)
    fig.subplots_adjust(left=0.055, right=0.985, bottom=0.055, top=0.95, wspace=0.12, hspace=0.18)
    save(fig, "main_spatial_hidden_productivity_mismatch")


if __name__ == "__main__":
    extent_global = read_raster(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")[1]
    recurrent_erosion_panel()
    mismatch_panel()
    print(PUBFIG)
