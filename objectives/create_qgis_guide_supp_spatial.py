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


def lon_fmt(x, _pos):
    return f"{abs(int(round(x)))}°{'E' if x >= 0 else 'W'}"


def lat_fmt(y, _pos):
    return f"{abs(int(round(y)))}°{'N' if y >= 0 else 'S'}"


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
    ax.text(lon0 + length_deg / 2, y + 0.7, f"{length_km} km", transform=ccrs.PlateCarree(), ha="center", va="bottom", fontsize=8, fontweight="bold", zorder=5)


def add_panel(ax, arr, raster_extent, plot_extent, boundary, panel, title, cmap, legend_label, vmin=None, vmax=None, norm=None, ticks=None):
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
    boundary.boundary.plot(ax=ax, transform=ccrs.PlateCarree(), color="black", linewidth=0.65, zorder=4)
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
    cbar = plt.colorbar(im, ax=ax, orientation="horizontal", pad=0.095, shrink=0.60, aspect=28, ticks=ticks)
    cbar.ax.tick_params(labelsize=9, width=0.8, length=3)
    for tick in cbar.ax.get_xticklabels():
        tick.set_fontweight("bold")
    cbar.set_label(legend_label, fontsize=10, fontweight="bold", labelpad=5)


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(OUT / f"{name}.png")
    print(OUT / f"{name}.pdf")


def legacy_effects(boundary) -> None:
    paths = [
        OBJ1 / "data" / "processed_tif" / "kNDVI_cumulative_loss_t0_t4_mean.tif",
        OBJ1 / "data" / "processed_tif" / "GPP_cumulative_loss_t0_t4_mean.tif",
        OBJ1 / "data" / "processed_tif" / "kNDVI_legacy_years_t1_t4_mean.tif",
        OBJ1 / "data" / "processed_tif" / "GPP_legacy_years_t1_t4_mean.tif",
    ]
    arrays_ext = [read_raster(p) for p in paths]
    arrays = [a for a, _ in arrays_ext]
    raster_extent = arrays_ext[0][1]
    plot_extent = valid_extent(arrays, raster_extent)

    fig = plt.figure(figsize=(12.4, 8.2))
    axes = [fig.add_subplot(2, 2, i + 1, projection=ccrs.PlateCarree()) for i in range(4)]
    add_panel(axes[0], arrays[0], raster_extent, plot_extent, boundary, "A", "kNDVI cumulative loss", "viridis", "kNDVI loss", vmin=0, vmax=np.nanpercentile(arrays[0], 98))
    add_panel(axes[1], arrays[1], raster_extent, plot_extent, boundary, "B", "GPP cumulative loss", "viridis", "GPP loss", vmin=0, vmax=np.nanpercentile(arrays[1], 98))
    add_panel(axes[2], arrays[2], raster_extent, plot_extent, boundary, "C", "kNDVI legacy duration", "magma", "years", vmin=0, vmax=4, ticks=[0, 1, 2, 3, 4])
    add_panel(axes[3], arrays[3], raster_extent, plot_extent, boundary, "D", "GPP legacy duration", "magma", "years", vmin=0, vmax=4, ticks=[0, 1, 2, 3, 4])
    fig.subplots_adjust(left=0.055, right=0.985, top=0.955, bottom=0.07, wspace=0.12, hspace=0.34)
    save(fig, "qgis_guide_supp_legacy_effects")


def resistance_recovery_support(boundary) -> None:
    paths = [
        OBJ2 / "data" / "processed_tif" / "GPP_delta_resistance_later_minus_first.tif",
        OBJ2 / "data" / "processed_tif" / "kNDVI_delta_cumulative_loss_later_minus_first.tif",
        OBJ2 / "data" / "processed_tif" / "kNDVI_delta_recovery_later_minus_first.tif",
    ]
    arrays_ext = [read_raster(p) for p in paths]
    arrays = [a for a, _ in arrays_ext]
    raster_extent = arrays_ext[0][1]
    plot_extent = valid_extent(arrays, raster_extent)

    fig = plt.figure(figsize=(12.4, 4.4))
    axes = [fig.add_subplot(1, 3, i + 1, projection=ccrs.PlateCarree()) for i in range(3)]
    lim0 = np.nanpercentile(np.abs(arrays[0]), 98)
    add_panel(axes[0], arrays[0], raster_extent, plot_extent, boundary, "A", "GPP resistance change", "coolwarm", "later-minus-first", norm=TwoSlopeNorm(vcenter=0, vmin=-lim0, vmax=lim0))
    add_panel(axes[1], arrays[1], raster_extent, plot_extent, boundary, "B", "kNDVI cumulative loss change", "viridis", "kNDVI loss difference", vmin=0, vmax=np.nanpercentile(arrays[1], 98))
    lim2 = np.nanpercentile(np.abs(arrays[2]), 98)
    add_panel(axes[2], arrays[2], raster_extent, plot_extent, boundary, "C", "kNDVI recovery-time change", "coolwarm", "years", norm=TwoSlopeNorm(vcenter=0, vmin=-lim2, vmax=lim2))
    fig.subplots_adjust(left=0.045, right=0.99, top=0.92, bottom=0.13, wspace=0.14)
    save(fig, "qgis_guide_supp_resistance_recovery_support")


def main() -> None:
    boundary = gpd.read_file(SHP).to_crs("EPSG:4326")
    legacy_effects(boundary)
    resistance_recovery_support(boundary)


if __name__ == "__main__":
    main()
