from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colorbar import ColorbarBase
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter


ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "Datasets"
OBJ3 = ROOT / "Paper2_Objectives" / "03_vulnerability_hotspots_resilience_erosion"
SHP = ROOT / "shp" / "NEAFinal.shp"
OUT = ROOT / "Manuscript" / "figures_publication"

HOTSPOT_LABELS = {
    1: "Low-impact recovery",
    2: "Delayed productivity recovery",
    3: "Hidden productivity suppression",
    4: "Recurrent resilience erosion",
    5: "Severe multi-metric hotspot",
}
VEG_LABELS = {
    1: "ENT",
    2: "EBT",
    3: "DNT",
    4: "DBT",
    5: "SHB",
    6: "GRS",
}


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


def base_map(ax, plot_extent, boundary, panel, title):
    ax.set_extent(plot_extent, crs=ccrs.PlateCarree())
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


def add_continuous_panel(ax, cax, arr, raster_extent, plot_extent, boundary, panel, title, cmap, legend_label, vmin=0, vmax=1, ticks=None):
    im = ax.imshow(
        np.ma.masked_invalid(arr),
        extent=raster_extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
        zorder=2,
    )
    base_map(ax, plot_extent, boundary, panel, title)
    cax.set_visible(True)
    cbar = plt.colorbar(im, cax=cax, orientation="horizontal", ticks=ticks)
    cbar.ax.tick_params(labelsize=9, width=0.8, length=3)
    for tick in cbar.ax.get_xticklabels():
        tick.set_fontweight("bold")
    cbar.ax.set_title(legend_label, fontsize=10, fontweight="bold", pad=4)


def add_binary_colorbar(cax, cmap, label):
    cax.set_visible(True)
    cbar = ColorbarBase(cax, cmap=cmap, norm=Normalize(vmin=0, vmax=1), orientation="horizontal", ticks=[0, 1])
    cbar.ax.tick_params(labelsize=9, width=0.8, length=3)
    for tick in cbar.ax.get_xticklabels():
        tick.set_fontweight("bold")
    cbar.ax.set_title(label, fontsize=10, fontweight="bold", pad=4)


def add_categorical_panel(ax, cax, arr, raster_extent, plot_extent, boundary, panel, title, cmap, norm, labels, legend_cols=1):
    ax.imshow(
        np.ma.masked_invalid(arr),
        extent=raster_extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
        zorder=2,
    )
    base_map(ax, plot_extent, boundary, panel, title)
    handles = [Patch(facecolor=cmap(norm(code)), edgecolor="black", linewidth=0.3, label=f"{code}: {label}") for code, label in labels.items()]
    cax.axis("off")
    leg = cax.legend(
        handles=handles,
        loc="center",
        frameon=False,
        ncol=legend_cols,
        fontsize=8.8,
        handlelength=1.1,
        columnspacing=1.0,
    )
    for text in leg.get_texts():
        text.set_fontweight("bold")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hotspot, raster_extent = read_raster(OBJ3 / "data" / "processed_tif" / "hotspot_class.tif")
    vulnerability, _ = read_raster(OBJ3 / "data" / "processed_tif" / "vulnerability_index_0_1.tif")
    severe, _ = read_raster(OBJ3 / "data" / "processed_tif" / "severe_multimetric_hotspot_domain.tif")
    veg, _ = read_raster(DATASETS / "LC5_forest_1to6_2024_NEA_0p1deg.tif")

    hotspot = np.where(hotspot > 0, hotspot, np.nan)
    severe = np.where(severe == 1, severe, np.nan)
    veg = np.where((veg >= 1) & (veg <= 6), veg, np.nan)
    plot_extent = valid_extent([hotspot, vulnerability, severe, veg], raster_extent)
    boundary = gpd.read_file(SHP).to_crs("EPSG:4326")

    hotspot_cmap = ListedColormap(["#66C2A5", "#FC8D62", "#8DA0CB", "#E78AC3", "#A23B3B"])
    hotspot_norm = BoundaryNorm([0.5, 1.5, 2.5, 3.5, 4.5, 5.5], hotspot_cmap.N)
    veg_cmap = ListedColormap(["#1B9E77", "#66A61E", "#A6D854", "#E6AB02", "#A6761D", "#7570B3"])
    veg_norm = BoundaryNorm([0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5], veg_cmap.N)

    fig = plt.figure(figsize=(12.4, 9.4))
    gs = fig.add_gridspec(
        4,
        2,
        height_ratios=[1.0, 0.14, 1.0, 0.12],
        left=0.055,
        right=0.985,
        top=0.965,
        bottom=0.055,
        wspace=0.12,
        hspace=0.42,
        
    )
    ax_a = fig.add_subplot(gs[0, 0], projection=ccrs.PlateCarree())
    ax_b = fig.add_subplot(gs[0, 1], projection=ccrs.PlateCarree())
    ax_c = fig.add_subplot(gs[2, 0], projection=ccrs.PlateCarree())
    ax_d = fig.add_subplot(gs[2, 1], projection=ccrs.PlateCarree())
    cax_a = fig.add_subplot(gs[1, 0])
    cax_b = fig.add_subplot(gs[1, 1])
    cax_c = fig.add_subplot(gs[3, 0])
    cax_d = fig.add_subplot(gs[3, 1])

    add_categorical_panel(ax_a, cax_a, hotspot, raster_extent, plot_extent, boundary, "A", "Hotspot class", hotspot_cmap, hotspot_norm, HOTSPOT_LABELS, legend_cols=1)
    add_continuous_panel(ax_b, cax_b, vulnerability, raster_extent, plot_extent, boundary, "B", "Vulnerability index", "magma", "index", vmin=0, vmax=1, ticks=[0, 0.25, 0.5, 0.75, 1])
    severe_cmap = plt.get_cmap("Reds")
    ax_c.imshow(
        np.ma.masked_invalid(severe),
        extent=raster_extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
        cmap=severe_cmap,
        vmin=0,
        vmax=1,
        interpolation="nearest",
        zorder=2,
    )
    base_map(ax_c, plot_extent, boundary, "C", "Severe hotspot domain")
    add_binary_colorbar(cax_c, severe_cmap, "severe hotspot")
    add_categorical_panel(ax_d, cax_d, veg, raster_extent, plot_extent, boundary, "D", "Vegetation class context", veg_cmap, veg_norm, VEG_LABELS, legend_cols=3)
    out_png = OUT / "qgis_guide_figure4_integrated_vulnerability_hotspots.png"
    out_pdf = OUT / "qgis_guide_figure4_integrated_vulnerability_hotspots.pdf"
    fig.savefig(out_png, dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(out_png)
    print(out_pdf)


if __name__ == "__main__":
    main()
