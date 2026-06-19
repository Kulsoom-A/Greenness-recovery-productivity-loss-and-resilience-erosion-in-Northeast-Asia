from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch, Rectangle
from pyproj import Geod


ROOT = Path(r"F:\HarbinPaper-02-")
SHP = ROOT / "shp" / "NEAFinal.shp"
VEG = ROOT / "Datasets" / "LC5_forest_1to6_2024_NEA_0p1deg.tif"
OUT = ROOT / "Manuscript" / "figures_publication"

OUT.mkdir(parents=True, exist_ok=True)

mpl.rcParams.update(
    {
        "font.family": "Times New Roman",
        "font.size": 12,
        "axes.labelweight": "bold",
        "axes.titleweight": "bold",
        "axes.edgecolor": "black",
        "text.color": "black",
        "axes.labelcolor": "black",
        "xtick.color": "black",
        "ytick.color": "black",
        "savefig.dpi": 600,
    }
)

CLASS_LABELS = {
    1: "ENT",
    2: "EBT",
    3: "DNT",
    4: "DBT",
    5: "SHB",
    6: "GRS",
}

CLASS_COLORS = {
    1: "#1b9e77",  # evergreen needleleaf
    2: "#66a61e",  # evergreen broadleaf
    3: "#a6d854",  # deciduous needleleaf
    4: "#e6ab02",  # deciduous broadleaf
    5: "#a6761d",  # shrub
    6: "#7570b3",  # grass
}


def read_vegetation() -> tuple[np.ndarray, tuple[float, float, float, float]]:
    with rasterio.open(VEG) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        bounds = src.bounds
    if nodata is not None and not np.isnan(nodata):
        arr[arr == nodata] = np.nan
    arr[(arr < 1) | (arr > 6)] = np.nan
    return arr, (bounds.left, bounds.right, bounds.bottom, bounds.top)


def add_common_features(ax, linewidth=0.45) -> None:
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#eaf3fb", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f4f1e8", edgecolor="none", zorder=0)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="black", linewidth=linewidth, zorder=5)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#555555", linewidth=linewidth * 0.8, zorder=5)
    ax.add_feature(cfeature.LAKES.with_scale("50m"), facecolor="#eaf3fb", edgecolor="#8aa9c0", linewidth=0.25, zorder=2)
    ax.add_feature(cfeature.RIVERS.with_scale("50m"), edgecolor="#8aa9c0", linewidth=0.25, zorder=2)


def add_gridlines(ax, xlocs, ylocs, fontsize=10) -> None:
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        draw_labels=True,
        linewidth=0.45,
        color="#9e9e9e",
        alpha=0.75,
        linestyle="--",
        xlocs=xlocs,
        ylocs=ylocs,
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": fontsize, "weight": "bold", "color": "black", "family": "Times New Roman"}
    gl.ylabel_style = {"size": fontsize, "weight": "bold", "color": "black", "family": "Times New Roman"}


def add_scale_bar(ax, lon: float, lat: float, length_km: int = 1000) -> None:
    geod = Geod(ellps="WGS84")
    lon2, lat2, _ = geod.fwd(lon, lat, 90, length_km * 1000)
    ax.plot([lon, lon2], [lat, lat2], transform=ccrs.PlateCarree(), color="black", linewidth=3.0, zorder=20)
    ax.plot([lon, lon], [lat - 0.7, lat + 0.7], transform=ccrs.PlateCarree(), color="black", linewidth=2.2, zorder=20)
    ax.plot([lon2, lon2], [lat - 0.7, lat + 0.7], transform=ccrs.PlateCarree(), color="black", linewidth=2.2, zorder=20)
    ax.text(
        (lon + lon2) / 2,
        lat + 1.5,
        f"{length_km:,} km",
        transform=ccrs.PlateCarree(),
        ha="center",
        va="bottom",
        fontsize=12,
        fontweight="bold",
        color="black",
        zorder=21,
    )


def add_north_arrow(ax, x=0.94, y=0.895) -> None:
    ax.text(
        x,
        y + 0.06,
        "N",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=13,
        fontweight="bold",
        color="black",
        zorder=31,
    )
    ax.annotate(
        "",
        xy=(x, y + 0.035),
        xytext=(x, y - 0.055),
        xycoords="axes fraction",
        ha="center",
        va="center",
        arrowprops=dict(arrowstyle="-|>", facecolor="black", edgecolor="black", lw=1.6, mutation_scale=22),
        zorder=30,
    )


def main() -> None:
    gdf = gpd.read_file(SHP).to_crs("EPSG:4326")
    geom = gdf.geometry
    minx, miny, maxx, maxy = gdf.total_bounds
    veg, veg_extent = read_vegetation()
    colors = [CLASS_COLORS[i] for i in range(1, 7)]
    cmap = ListedColormap(colors)
    cmap.set_bad((1, 1, 1, 0))
    norm = BoundaryNorm(np.arange(0.5, 7.5, 1), cmap.N)

    fig = plt.figure(figsize=(12.2, 9.0), constrained_layout=False)
    gs = fig.add_gridspec(1, 2, width_ratios=[0.95, 2.05], wspace=0.12)

    ax_a = fig.add_subplot(gs[0, 0], projection=ccrs.Robinson(central_longitude=90))
    ax_b = fig.add_subplot(gs[0, 1], projection=ccrs.PlateCarree())

    # Panel A: global locator.
    ax_a.set_global()
    ax_a.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="#eaf3fb", zorder=0)
    ax_a.add_feature(cfeature.LAND.with_scale("110m"), facecolor="#eee9da", edgecolor="#555555", linewidth=0.25, zorder=1)
    ax_a.add_geometries(geom, crs=ccrs.PlateCarree(), facecolor="#d7191c", edgecolor="#7f0000", linewidth=0.75, alpha=0.78, zorder=4)
    rect = Rectangle((minx, miny), maxx - minx, maxy - miny, transform=ccrs.PlateCarree(), fill=False, edgecolor="#7f0000", linewidth=1.3, zorder=5)
    ax_a.add_patch(rect)
    ax_a.text(
        0.5,
        1.05,
        "A",
        transform=ax_a.transAxes,
        fontsize=17,
        fontweight="bold",
        color="black",
        ha="center",
        va="bottom",
        bbox=dict(facecolor="white", edgecolor="black", linewidth=0.6, pad=2.5),
        clip_on=False,
        zorder=30,
    )
    # Panel B: study domain.
    ax_b.set_extent([25, 180, 0, 82], crs=ccrs.PlateCarree())
    add_common_features(ax_b, linewidth=0.5)
    ax_b.imshow(
        veg,
        extent=veg_extent,
        origin="upper",
        cmap=cmap,
        norm=norm,
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        alpha=0.92,
        zorder=3,
    )
    ax_b.add_geometries(geom, crs=ccrs.PlateCarree(), facecolor="none", edgecolor="black", linewidth=1.15, zorder=8)
    add_gridlines(ax_b, xlocs=np.arange(30, 181, 30), ylocs=np.arange(0, 81, 10), fontsize=10)
    add_scale_bar(ax_b, lon=132, lat=8.5, length_km=1000)
    add_north_arrow(ax_b)
    ax_b.text(
        0.015,
        0.975,
        "B",
        transform=ax_b.transAxes,
        fontsize=17,
        fontweight="bold",
        ha="left",
        va="top",
        bbox=dict(facecolor="white", edgecolor="black", linewidth=0.6, pad=2.5),
        zorder=40,
    )
    handles = [
        Patch(facecolor=CLASS_COLORS[i], edgecolor="black", linewidth=0.35, label=f"{i}: {CLASS_LABELS[i]}")
        for i in range(1, 7)
    ]
    leg = ax_b.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.24),
        ncol=6,
        frameon=True,
        framealpha=1,
        edgecolor="black",
        fontsize=10,
        title="Vegetation class",
        title_fontsize=11,
        columnspacing=0.9,
        handlelength=1.2,
    )
    leg.get_title().set_fontweight("bold")
    for text in leg.get_texts():
        text.set_fontweight("bold")
        text.set_color("black")

    fig.subplots_adjust(left=0.035, right=0.99, top=0.91, bottom=0.27)
    out_png = OUT / "figure1_study_area_map.png"
    out_tif = OUT / "figure1_study_area_map.tif"
    out_pdf = OUT / "figure1_study_area_map.pdf"
    fig.savefig(out_png, dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(out_tif, dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(out_png)
    print(out_tif)
    print(out_pdf)


if __name__ == "__main__":
    main()
