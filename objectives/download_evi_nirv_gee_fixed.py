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
REF = ROOT / "Datasets" / "kNDVI_annual_2001_2024_NEA.tif"
OUT = ROOT / "Datasets" / "EVI_NIRv_0p1deg_NEA_fixed"
RAW = OUT / "raw"
ALIGNED = OUT / "aligned"
YEARS = range(2001, 2025)
EXPORT_SCALE_M = 11132


def initialize_ee(project: str | None = None) -> None:
    project = project or os.environ.get("EE_PROJECT")
    try:
        ee.Initialize(project=project) if project else ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project) if project else ee.Initialize()


def ensure_dirs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    ALIGNED.mkdir(parents=True, exist_ok=True)


def get_roi():
    gdf = gpd.read_file(SHP).to_crs("EPSG:4326")
    return geemap.geopandas_to_ee(gdf).geometry()


def annual_evi(year: int, roi) -> ee.Image:
    start = ee.Date.fromYMD(year, 1, 1)
    end = start.advance(1, "year")
    return (
        ee.ImageCollection("MODIS/061/MOD13A2")
        .filterDate(start, end)
        .filterBounds(roi)
        .select("EVI")
        .map(lambda img: img.multiply(0.0001).updateMask(img.neq(-3000)))
        .mean()
        .rename("EVI")
        .clip(roi)
        .toFloat()
    )


def annual_nirv(year: int, roi) -> ee.Image:
    start = ee.Date.fromYMD(year, 1, 1)
    end = start.advance(1, "year")

    def add_nirv(img):
        red_raw = img.select("sur_refl_b01")
        nir_raw = img.select("sur_refl_b02")
        qa = img.select("SummaryQA")
        valid = red_raw.gte(0).And(red_raw.lte(10000)).And(nir_raw.gte(0)).And(nir_raw.lte(10000)).And(qa.lte(1))
        red = red_raw.multiply(0.0001)
        nir = nir_raw.multiply(0.0001)
        ndvi = nir.subtract(red).divide(nir.add(red))
        return ndvi.multiply(nir).updateMask(valid).rename("NIRv").copyProperties(img, ["system:time_start"])

    return (
        ee.ImageCollection("MODIS/061/MOD13A2")
        .filterDate(start, end)
        .filterBounds(roi)
        .map(add_nirv)
        .mean()
        .rename("NIRv")
        .clip(roi)
        .toFloat()
    )


def export_image(image: ee.Image, out_path: Path, roi) -> None:
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"exists: {out_path}")
        return
    print(f"downloading: {out_path.name}")
    geemap.ee_export_image(
        image.reproject(crs="EPSG:4326", scale=EXPORT_SCALE_M),
        filename=str(out_path),
        scale=EXPORT_SCALE_M,
        region=roi,
        file_per_band=False,
        crs="EPSG:4326",
    )


def export_stack(proxy: str, years: list[int], roi) -> Path:
    out_path = RAW / f"{proxy}_{years[0]}_{years[-1]}_raw_stack.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"exists: {out_path}")
        return out_path
    images = []
    for year in years:
        img = annual_evi(year, roi) if proxy == "EVI" else annual_nirv(year, roi)
        images.append(img.rename(f"{proxy}_{year}"))
    stack = ee.Image.cat(images).toFloat().reproject(crs="EPSG:4326", scale=EXPORT_SCALE_M)
    print(f"downloading stack: {out_path.name}")
    geemap.ee_export_image(
        stack,
        filename=str(out_path),
        scale=EXPORT_SCALE_M,
        region=roi,
        file_per_band=False,
        crs="EPSG:4326",
    )
    return out_path


def align_to_reference(src_path: Path, out_path: Path, resampling: Resampling = Resampling.average) -> None:
    with rasterio.open(REF) as ref:
        profile = ref.profile.copy()
        transform = ref.transform
        crs = ref.crs
        dst = np.full((ref.height, ref.width), np.nan, dtype="float32")

    with rasterio.open(src_path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        if nodata is not None and not np.isnan(nodata):
            arr[arr == nodata] = np.nan
        arr[~np.isfinite(arr)] = np.nan
        reproject(
            source=arr,
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=transform,
            dst_crs=crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )
    out_profile = profile.copy()
    out_profile.update(count=1, dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_path, "w", **out_profile) as dst_ds:
        dst_ds.write(dst.astype("float32"), 1)


def align_stack_to_reference(src_path: Path, out_path: Path, descriptions: list[str], resampling: Resampling = Resampling.average) -> None:
    with rasterio.open(REF) as ref:
        profile = ref.profile.copy()
        transform = ref.transform
        crs = ref.crs
        height = ref.height
        width = ref.width

    with rasterio.open(src_path) as src:
        out = np.full((src.count, height, width), np.nan, dtype="float32")
        for band in range(1, src.count + 1):
            arr = src.read(band).astype("float32")
            nodata = src.nodata
            if nodata is not None and not np.isnan(nodata):
                arr[arr == nodata] = np.nan
            arr[~np.isfinite(arr)] = np.nan
            reproject(
                source=arr,
                destination=out[band - 1],
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=np.nan,
                dst_transform=transform,
                dst_crs=crs,
                dst_nodata=np.nan,
                resampling=resampling,
            )
    out_profile = profile.copy()
    out_profile.update(count=out.shape[0], dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(out)
        for i, desc in enumerate(descriptions, start=1):
            dst.set_band_description(i, desc)


def build_fixed_stack_from_chunks(proxy: str, roi) -> None:
    if proxy == "NIRv":
        chunks = [list(range(start, min(start + 3, 2025))) for start in range(2001, 2025, 3)]
    else:
        chunks = [
            list(range(2001, 2007)),
            list(range(2007, 2013)),
            list(range(2013, 2019)),
            list(range(2019, 2025)),
        ]
    aligned_chunks = []
    descriptions = []
    for years in chunks:
        raw_stack = export_stack(proxy, years, roi)
        if not raw_stack.exists():
            raise FileNotFoundError(f"Earth Engine download did not create {raw_stack}")
        aligned_stack = ALIGNED / f"{proxy}_{years[0]}_{years[-1]}_aligned_0p1deg.tif"
        align_stack_to_reference(raw_stack, aligned_stack, [f"{proxy}_{year}" for year in years])
        with rasterio.open(aligned_stack) as src:
            aligned_chunks.append(src.read().astype("float32"))
            descriptions.extend([f"{proxy}_{year}" for year in years])
    stack = np.concatenate(aligned_chunks, axis=0)
    with rasterio.open(REF) as ref:
        profile = ref.profile.copy()
    out_profile = profile.copy()
    out_profile.update(count=stack.shape[0], dtype="float32", nodata=np.nan, compress="deflate")
    out_path = OUT / f"{proxy}_annual_2001_2024_NEA_0p1deg_fixed.tif"
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(stack.astype("float32"))
        for i, desc in enumerate(descriptions, start=1):
            dst.set_band_description(i, desc)


def stack_aligned(proxy: str) -> None:
    with rasterio.open(REF) as ref:
        profile = ref.profile.copy()
    arrs = []
    desc = []
    for year in YEARS:
        path = ALIGNED / f"{proxy}_{year}_aligned_0p1deg.tif"
        with rasterio.open(path) as src:
            arrs.append(src.read(1).astype("float32"))
        desc.append(f"{proxy}_{year}")
    stack = np.stack(arrs)
    out_profile = profile.copy()
    out_profile.update(count=len(arrs), dtype="float32", nodata=np.nan, compress="deflate")
    out_path = OUT / f"{proxy}_annual_2001_2024_NEA_0p1deg_fixed.tif"
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(stack.astype("float32"))
        for i, d in enumerate(desc, start=1):
            dst.set_band_description(i, d)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="kaleem-55")
    args = parser.parse_args()
    ensure_dirs()
    initialize_ee(args.project)
    roi = get_roi()
    for proxy in ["EVI", "NIRv"]:
        build_fixed_stack_from_chunks(proxy, roi)
    print(OUT)
    return
    for year in YEARS:
        products = {
            "EVI": annual_evi(year, roi),
            "NIRv": annual_nirv(year, roi),
        }
        for proxy, image in products.items():
            raw = RAW / f"{proxy}_{year}_raw.tif"
            aligned = ALIGNED / f"{proxy}_{year}_aligned_0p1deg.tif"
            export_image(image, raw, roi)
            align_to_reference(raw, aligned)
    stack_aligned("EVI")
    stack_aligned("NIRv")
    print(OUT)


if __name__ == "__main__":
    main()
