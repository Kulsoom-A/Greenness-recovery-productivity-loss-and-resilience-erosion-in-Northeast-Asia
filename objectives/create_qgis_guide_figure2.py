from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import TwoSlopeNorm
from matplotlib.ticker import FuncFormatter


ROOT = Path(__file__).resolve().parents[3]
OBJ1 = ROOT / "Paper2_Objectives" / "01_legacy_effects_kNDVI_GPP"
OBJ2 = ROOT / "Paper2_Objectives" / "02_event_order_resistance_recovery"
SHP = ROOT / "shp" / "NEAFinal.shp"
OUT = ROOT / "Manuscript" / "figures_publication"


def read_raster(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        if nodata is not None and not np.isnan(nodata):
            arr[arr == nodata] = np.nan
        bounds = src.bounds
    return arr, (bounds.left, bounds.right, bounds.bottom, bounds.top)


def valid_extent(arrays: list[np.ndarray], extent: tuple[float, float, float, float], pad: float = 3.5):
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
    lat = ymin + 0.12 * (ymax - ymin)
    lon0 = xmin + 0.075 * (xmax - xmin)
    km_per_degree = 111.32 * np.cos(np.deg2rad(lat))
    if km_per_degree <= 0:
        return
    length_deg = length_km / km_per_degree
    y = lat
    ax.plot([lon0, lon0 + length_deg], [y, y], transform=ccrs.PlateCarree(), color="black", lw=2.0, solid_capstyle="butt", zorder=5)
    ax.text(
        lon0 + length_deg / 2,
        y + 0.7,
        f"{length_km} km",
        transform=ccrs.PlateCarree(),
        ha="center",
        va="bottom",
        fontsize=8,
        fontweight="bold",
        zorder=5,
    )


def lon_fmt(x, _pos):
    return f"{abs(int(round(x)))}°{'E' if x >= 0 else 'W'}"


def lat_fmt(y, _pos):
    return f"{abs(int(round(y)))}°{'N' if y >= 0 else 'S'}"


def add_boundary(ax, boundary):
    boundary.boundary.plot(ax=ax, transform=ccrs.PlateCarree(), color="black", linewidth=0.65, zorder=4)


def add_panel(
    ax,
    arr,
    raster_extent,
    plot_extent,
    boundary,
    panel,
    title,
    cmap,
    legend_label,
    vmin=None,
    vmax=None,
    norm=None,
    cbar_ticks=None,
):
    ax.set_extent(plot_extent, crs=ccrs.PlateCarree())
    im = ax.imshow(
        np.ma.masked_invalid(arr),
        extent=raster_extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        norm=norm,
        interpolation="nearest",
        zorder=2,
    )
    add_boundary(ax, boundary)
    add_scalebar(ax, plot_extent)
    ax.set_title(f"{panel}. {title}", loc="left", fontsize=14, fontweight="bold", pad=7)
    ax.set_xticks(np.arange(40, 181, 20), crs=ccrs.PlateCarree())
    ax.set_yticks(np.arange(20, 81, 10), crs=ccrs.PlateCarree())
    ax.xaxis.set_major_formatter(FuncFormatter(lon_fmt))
    ax.yaxis.set_major_formatter(FuncFormatter(lat_fmt))
    ax.grid(True, linestyle="--", linewidth=0.55, color="0.70", alpha=0.8)
    ax.tick_params(labelsize=9, width=0.8, length=3)
    for tick in ax.get_xticklabels() + ax.get_yticklabels():
        tick.set_fontweight("bold")
    for spine in ax.spines.values():
        spine.set_linewidth(1.1)
        spine.set_color("black")
    cbar = plt.colorbar(im, ax=ax, orientation="horizontal", pad=0.095, shrink=0.60, aspect=28, ticks=cbar_ticks)
    cbar.ax.tick_params(labelsize=9, width=0.8, length=3)
    for tick in cbar.ax.get_xticklabels():
        tick.set_fontweight("bold")
    cbar.set_label(legend_label, fontsize=10, fontweight="bold", labelpad=5)
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    event_freq, raster_extent = read_raster(OBJ1 / "data" / "processed_tif" / "event_frequency_2004_2020.tif")
    later_count, _ = read_raster(OBJ2 / "data" / "processed_tif" / "GPP_later_count.tif")
    gpp_loss, _ = read_raster(OBJ2 / "data" / "processed_tif" / "GPP_delta_cumulative_loss_later_minus_first.tif")
    gpp_recovery, _ = read_raster(OBJ2 / "data" / "processed_tif" / "GPP_delta_recovery_later_minus_first.tif")

    event_freq = np.where(event_freq > 0, event_freq, np.nan)
    recurrent_events = np.where(later_count > 0, later_count + 1, np.nan)
    gpp_loss = np.where(np.isfinite(gpp_loss), gpp_loss, np.nan)
    gpp_recovery = np.where(np.isfinite(gpp_recovery), gpp_recovery, np.nan)

    plot_extent = valid_extent([event_freq, recurrent_events, gpp_loss, gpp_recovery], raster_extent, pad=3.5)
    boundary = gpd.read_file(SHP).to_crs("EPSG:4326")

    fig = plt.figure(figsize=(12.4, 8.2))
    axes = [fig.add_subplot(2, 2, i + 1, projection=ccrs.PlateCarree()) for i in range(4)]
    add_panel(
        axes[0],
        event_freq,
        raster_extent,
        plot_extent,
        boundary,
        "A",
        "Hot-dry event frequency",
        "magma",
        "events",
        vmin=1,
        vmax=4,
        cbar_ticks=[1, 2, 3, 4],
    )
    add_panel(
        axes[1],
        recurrent_events,
        raster_extent,
        plot_extent,
        boundary,
        "B",
        "Recurrent-event domain",
        "YlOrRd",
        "later events",
        vmin=2,
        vmax=5,
        cbar_ticks=[2, 3, 4, 5],
    )
    add_panel(
        axes[2],
        gpp_loss,
        raster_extent,
        plot_extent,
        boundary,
        "C",
        "Later-minus-first GPP cumulative loss",
        "viridis",
        "GPP loss difference",
        vmin=0,
        vmax=np.nanpercentile(gpp_loss, 98),
    )
    lim = np.nanpercentile(np.abs(gpp_recovery), 98)
    add_panel(
        axes[3],
        gpp_recovery,
        raster_extent,
        plot_extent,
        boundary,
        "D",
        "Later-minus-first GPP recovery time",
        "coolwarm",
        "years",
        norm=TwoSlopeNorm(vcenter=0, vmin=-lim, vmax=lim),
    )
    fig.subplots_adjust(left=0.055, right=0.985, top=0.955, bottom=0.07, wspace=0.12, hspace=0.34)
    out_png = OUT / "qgis_guide_figure2_recurrent_resilience_erosion.png"
    out_pdf = OUT / "qgis_guide_figure2_recurrent_resilience_erosion.pdf"
    fig.savefig(out_png, dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(out_png)
    print(out_pdf)


if __name__ == "__main__":
    main()
