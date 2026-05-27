"""
extract_mean_rvi_per_polygon.py
===============================
Legge tutti i file .tif RVI in una cartella, calcola il valore medio
per ciascun poligono di un GeoPackage e salva il risultato in un CSV
con la stessa struttura di mean_ndvi_per_polygon.csv.

Gli ID poligono (P001, P002, ...) vengono assegnati progressivamente
nell'ordine in cui i poligoni compaiono nel GeoPackage.

Struttura output:
  date;P001;P002;...;P154
  2023-07-22T00:00:00Z;0.42;0.38;...

Uso:
  python extract_mean_rvi_per_polygon.py \
      --rvi_dir /percorso/cartella/tiff_rvi \
      --gpkg /percorso/poligoni.gpkg \
      --output mean_rvi_per_polygon.csv

Opzioni:
  --rvi_dir   Cartella contenente i file .tif RVI (obbligatorio)
  --gpkg      GeoPackage con i poligoni dei campi (obbligatorio)
  --layer     Nome del layer nel GeoPackage (default: primo layer)
  --output    Nome del file CSV di output (default: mean_rvi_per_polygon.csv)
  --nodata    Valore nodata da escludere (default: -9999)
"""

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping


def parse_args():
    parser = argparse.ArgumentParser(
        description="Estrae valori medi RVI per poligono da file .tif"
    )
    parser.add_argument("--rvi_dir", required=True,
                        help="Cartella con i file .tif RVI")
    parser.add_argument("--gpkg", required=True,
                        help="GeoPackage con i poligoni dei campi")
    parser.add_argument("--layer", default=None,
                        help="Nome layer nel GeoPackage (default: primo layer)")
    parser.add_argument("--output", default="mean_rvi_per_polygon.csv",
                        help="File CSV di output")
    parser.add_argument("--nodata", type=float, default=-9999.0,
                        help="Valore nodata da escludere (default: -9999)")
    return parser.parse_args()


def extract_date_from_filename(filename: str) -> str:
    # Formato Sentinel-1 standard: YYYYMMDDTHHMMSS
    match = re.search(r'(\d{4})(\d{2})(\d{2})T\d{6}', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    # Formato generico YYYYMMDD o YYYY-MM-DD
    match = re.search(r'(\d{4})-?(\d{2})-?(\d{2})', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    print(f"  ATTENZIONE: impossibile estrarre data da '{filename}'")
    return filename


def load_polygons(gpkg_path: Path, layer: str = None) -> gpd.GeoDataFrame:
    import fiona
    layers = fiona.listlayers(str(gpkg_path))
    if layer is None:
        layer = layers[0]
    gdf = gpd.read_file(str(gpkg_path), layer=layer)
    # Assegna ID progressivi P001, P002, ... nell'ordine del GeoPackage
    gdf = gdf.copy()
    gdf["poly_id"] = [f"P{str(i+1).zfill(3)}" for i in range(len(gdf))]
    print(f"Poligoni caricati: {len(gdf)} (P001 ... P{str(len(gdf)).zfill(3)})")
    return gdf


def find_tif_files(rvi_dir: Path) -> list:
    tifs = sorted(rvi_dir.glob("*.tif"))
    if not tifs:
        tifs = sorted(rvi_dir.glob("*.tiff"))
    if not tifs:
        print(f"ERRORE: nessun file .tif trovato in {rvi_dir}")
        sys.exit(1)
    print(f"Trovati {len(tifs)} file .tif RVI")
    return tifs


def extract_mean_rvi(tif_path: Path, gdf: gpd.GeoDataFrame, nodata: float) -> dict:
    means = {}
    with rasterio.open(str(tif_path)) as src:
        if gdf.crs != src.crs:
            gdf_repr = gdf.to_crs(src.crs)
        else:
            gdf_repr = gdf
        for _, row in gdf_repr.iterrows():
            pid = row["poly_id"]
            geom = [mapping(row.geometry)]
            try:
                out_image, _ = mask(src, geom, crop=True, nodata=np.nan)
                data = out_image[0].astype(float)
                # Esclude il valore nodata (-9999)
                data[data == nodata] = np.nan
                valid = data[~np.isnan(data)]
                means[pid] = float(np.mean(valid)) if len(valid) > 0 else np.nan
            except Exception:
                means[pid] = np.nan
    return means


def main():
    args = parse_args()
    rvi_dir   = Path(args.rvi_dir)
    gpkg_path = Path(args.gpkg)

    for p, name in [(rvi_dir, "cartella RVI"), (gpkg_path, "GeoPackage")]:
        if not p.exists():
            print(f"ERRORE: {name} non trovato: {p}")
            sys.exit(1)

    gdf       = load_polygons(gpkg_path, args.layer)
    poly_ids  = gdf["poly_id"].tolist()
    tif_files = find_tif_files(rvi_dir)

    records = []
    for tif in tif_files:
        date_str = extract_date_from_filename(tif.name)
        print(f"  {tif.name} -> {date_str}")
        means = extract_mean_rvi(tif, gdf, nodata=args.nodata)
        row = {"date": date_str}
        for pid in poly_ids:
            row[pid] = means.get(pid, np.nan)
        records.append(row)

    df = pd.DataFrame(records).set_index("date")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df.index = df.index.strftime("%Y-%m-%dT00:00:00Z")

    output_path = Path(args.output)
    df.to_csv(str(output_path), sep=";", na_rep="")

    print(f"\nCSV salvato: {output_path}")
    print(f"Date elaborate: {len(df)}")
    print(f"Poligoni: {len(df.columns)}")
    valid_cells = df.notna().sum().sum()
    total_cells = df.size
    print(f"Valori validi: {valid_cells}/{total_cells} ({100*valid_cells/total_cells:.1f}%)")


if __name__ == "__main__":
    main()
