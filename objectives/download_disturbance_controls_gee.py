from __future__ import annotations

import argparse
import os
from pathlib import Path

import ee
import geemap
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject


ROOT = Path(__file__).resolve().parents[3]
SHP = ROOT / "shp" / "NEAFinal.shp"
REF = ROOT / "Datasets" / "LC5_forest_1to6_2024_NEA_0p1deg.tif"
OBJ99 = ROOT / "Paper2_Objectives" / "99_validation_robustness"
RAW = OBJ99 / "data" / "raw_tif"
PROCESSED = OBJ99 / "data" / "processed_tif"
QGIS = OBJ99 / "data" / "qgis_ready"

START_YEAR = 2001
END_YEAR = 2024
EVENT_START = 2004
EVENT_END = 2020
POST_EVENT_END = 2024
EXPORT_SCALE_M = 11132


def aggregate_fraction(image: ee.Image, projection: ee.Projection, name: str, max_pixels: int = 65535) -> ee.Image:
    return (
        image.setDefaultProjection(projection)
        .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=max_pixels)
        .reproject(crs="EPSG:4326", scale=EXPORT_SCALE_M)
        .rename(name)
        .toFloat()
    )


def aggregate_fraction_two_stage(image: ee.Image, projection: ee.Projection, name: str) -> ee.Image:
    stage1 = (
        image.setDefaultProjection(projection)
        .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=4096)
        .reproject(crs=projection.crs(), scale=1000)
    )
    return (
        stage1.setDefaultProjection(stage1.projection())
        .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024)
        .reproject(crs="EPSG:4326", scale=EXPORT_SCALE_M)
        .rename(name)
        .toFloat()
    )


def ensure_dirs() -> None:
    for p in [RAW, PROCESSED, QGIS, OBJ99 / "logs"]:
        p.mkdir(parents=True, exist_ok=True)


def initialize_ee(project: str | None = None) -> None:
    project = project or os.environ.get("EE_PROJECT")
    try:
        if project:
            ee.Initialize(project=project)
        else:
            ee.Initialize()
    except Exception:
        ee.Authenticate()
        if project:
            ee.Initialize(project=project)
        else:
            ee.Initialize()


def get_roi():
    if not SHP.exists():
        raise FileNotFoundError(f"Missing AOI shapefile: {SHP}")
    gdf = gpd.read_file(SHP).to_crs("EPSG:4326")
    return geemap.geopandas_to_ee(gdf).geometry(), gdf


def export_image(image: ee.Image, out_path: Path, roi) -> None:
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"exists: {out_path}")
        return
    print(f"downloading: {out_path.name}")
    geemap.ee_export_image(
        image,
        filename=str(out_path),
        scale=EXPORT_SCALE_M,
        region=roi,
        file_per_band=False,
        crs="EPSG:4326",
    )


def annual_burn_image(year: int, roi) -> ee.Image:
    start = ee.Date.fromYMD(year, 1, 1)
    end = start.advance(1, "year")
    burned = (
        ee.ImageCollection("MODIS/061/MCD64A1")
        .filterDate(start, end)
        .filterBounds(roi)
        .select("BurnDate")
        .map(lambda img: img.gt(0))
        .max()
        .rename(f"burned_{year}")
        .unmask(0)
        .clip(roi)
    )
    return burned


def build_burn_products(roi) -> dict[str, ee.Image]:
    annual = [annual_burn_image(y, roi).rename(f"burned_{y}") for y in range(START_YEAR, END_YEAR + 1)]
    stack = ee.Image.cat(annual)
    event_window = stack.select([f"burned_{y}" for y in range(EVENT_START, EVENT_END + 1)])
    post_window = stack.select([f"burned_{y}" for y in range(EVENT_START, POST_EVENT_END + 1)])
    burn_proj = ee.ImageCollection("MODIS/061/MCD64A1").first().select("BurnDate").projection()
    burn_any = stack.reduce(ee.Reducer.max())
    burn_event = event_window.reduce(ee.Reducer.max())
    burn_post = post_window.reduce(ee.Reducer.max())
    return {
        "MCD64A1_burned_any_2001_2024_raw.tif": burn_any.rename("burned_any_2001_2024").toFloat(),
        "MCD64A1_burned_count_2001_2024_raw.tif": stack.reduce(ee.Reducer.sum()).rename("burned_count_2001_2024").toFloat(),
        "MCD64A1_burned_any_event_window_2004_2020_raw.tif": burn_event.rename("burned_any_2004_2020").toFloat(),
        "MCD64A1_burned_any_event_post_2004_2024_raw.tif": burn_post.rename("burned_any_2004_2024").toFloat(),
        "MCD64A1_burned_fraction_2001_2024_raw.tif": aggregate_fraction(burn_any, burn_proj, "burned_fraction_2001_2024", max_pixels=4096),
        "MCD64A1_burned_fraction_event_window_2004_2020_raw.tif": aggregate_fraction(burn_event, burn_proj, "burned_fraction_2004_2020", max_pixels=4096),
        "MCD64A1_burned_fraction_event_post_2004_2024_raw.tif": aggregate_fraction(burn_post, burn_proj, "burned_fraction_2004_2024", max_pixels=4096),
    }


def build_hansen_products(roi) -> dict[str, ee.Image]:
    gfc = ee.Image("UMD/hansen/global_forest_change_2024_v1_12").clip(roi)
    lossyear = gfc.select("lossyear")
    treecover = gfc.select("treecover2000")
    hansen_proj = treecover.projection()
    loss_2001_2024 = lossyear.gt(0).And(lossyear.lte(24))
    loss_event = lossyear.gte(EVENT_START - 2000).And(lossyear.lte(EVENT_END - 2000))
    loss_event_post = lossyear.gte(EVENT_START - 2000).And(lossyear.lte(POST_EVENT_END - 2000))
    return {
        "Hansen_treecover2000_percent_raw.tif": treecover.rename("treecover2000_percent").toFloat(),
        "Hansen_forest_loss_year_2001_2024_raw.tif": lossyear.rename("forest_loss_year").toFloat(),
        "Hansen_forest_loss_any_2001_2024_raw.tif": loss_2001_2024.rename("forest_loss_any_2001_2024").toFloat(),
        "Hansen_forest_loss_any_event_window_2004_2020_raw.tif": loss_event.rename("forest_loss_any_2004_2020").toFloat(),
        "Hansen_forest_loss_any_event_post_2004_2024_raw.tif": loss_event_post.rename("forest_loss_any_2004_2024").toFloat(),
        "Hansen_treecover2000_fraction_raw.tif": aggregate_fraction_two_stage(treecover.divide(100), hansen_proj, "treecover2000_fraction"),
        "Hansen_forest_loss_fraction_2001_2024_raw.tif": aggregate_fraction_two_stage(loss_2001_2024, hansen_proj, "forest_loss_fraction_2001_2024"),
        "Hansen_forest_loss_fraction_event_window_2004_2020_raw.tif": aggregate_fraction_two_stage(loss_event, hansen_proj, "forest_loss_fraction_2004_2020"),
        "Hansen_forest_loss_fraction_event_post_2004_2024_raw.tif": aggregate_fraction_two_stage(loss_event_post, hansen_proj, "forest_loss_fraction_2004_2024"),
    }


def align_to_reference(src_path: Path, out_path: Path, resampling: Resampling = Resampling.nearest) -> None:
    with rasterio.open(REF) as ref:
        ref_profile = ref.profile.copy()
        ref_transform = ref.transform
        ref_crs = ref.crs
        dst = np.full((ref.height, ref.width), np.nan, dtype="float32")

    with rasterio.open(src_path) as src:
        src_arr = src.read(1).astype("float32")
        nodata = src.nodata
        if nodata is not None and not np.isnan(nodata):
            src_arr[src_arr == nodata] = np.nan
        reproject(
            source=src_arr,
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=ref_transform,
            dst_crs=ref_crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )

    profile = ref_profile.copy()
    profile.update(count=1, dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_path, "w", **profile) as dst_file:
        dst_file.write(dst.astype("float32"), 1)
        dst_file.set_band_description(1, out_path.stem)


def align_all() -> None:
    for src_path in sorted(RAW.glob("*_raw.tif")):
        name = src_path.name.replace("_raw", "_aligned_0p1deg")
        out_path = PROCESSED / name
        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"exists: {out_path}")
            continue
        align_to_reference(src_path, out_path)
        print(f"aligned: {out_path}")


def write_inventory() -> None:
    rows = []
    for p in sorted(PROCESSED.glob("*_aligned_0p1deg.tif")):
        rows.append(
            {
                "raster": str(p),
                "description": p.stem.replace("_aligned_0p1deg", "").replace("_", " "),
                "recommended_use": "External disturbance validation/control; align with event and hotspot rasters.",
            }
        )
    import pandas as pd

    pd.DataFrame(rows).to_csv(QGIS / "external_disturbance_qgis_raster_inventory.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download MODIS burned-area and Hansen forest-loss disturbance controls from Google Earth Engine.")
    parser.add_argument("--project", default=None, help="Google Cloud project ID registered for Earth Engine. Can also be set with EE_PROJECT.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_dirs()
    initialize_ee(args.project)
    roi, gdf = get_roi()
    print(f"AOI features: {len(gdf)}")
    print(f"AOI bounds: {gdf.total_bounds}")

    products = {}
    products.update(build_burn_products(roi))
    products.update(build_hansen_products(roi))
    for filename, image in products.items():
        export_image(image, RAW / filename, roi)

    align_all()
    write_inventory()
    print(QGIS / "external_disturbance_qgis_raster_inventory.csv")


if __name__ == "__main__":
    main()
