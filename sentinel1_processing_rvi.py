"""
sentinel1_processing_rvi.py — Processing SNAP + Calcolo RVI per Sentinel-1
===========================================================================
Script unificato che elabora le scene Sentinel-1 in due fasi consecutive:

  Fase 1 — Processing SNAP GPT (.SAFE -> .dim):
    Lancia il grafo SNAP GPT su tutte le scene .SAFE trovate in --scenes_dir,
    producendo file BEAM-DIMAP calibrati, filtrati e corretti geometricamente,
    ritagliati sulla bounding box dell'AOI.

    Catena di processing (definita nel grafo XML esterno):
      1. Apply Orbit File           -> corregge i metadati orbitali
      2. Calibration (Sigma0)       -> converte DN in backscatter lineare
      3. Speckle Filter (Lee Sigma) -> riduce il rumore tipico delle immagini SAR
      4. Terrain Correction (RD)    -> geolocalizzazione + correzione DEM (SRTM 1")
      5. Subset AOI (bbox)          -> ritaglia sulla bounding box dell'AOI
      6. Output BEAM-DIMAP (.dim)   -> salva con bande Sigma0_VV e Sigma0_VH

  Fase 2 — Calcolo RVI (.dim -> .tif):
    Legge le bande Sigma0_VV e Sigma0_VH dai file .dim prodotti nella fase 1,
    calcola il Radar Vegetation Index e ritaglia le immagini sui poligoni AOI.

    Formula: RVI = 4 * VH / (VV + VH)
    Valori lineari (Sigma0). RVI in [0,1]: ~0 = suolo nudo, ~1 = vegetazione densa.

    Gli ID poligono (P001, P002, ...) vengono letti dalle colonne del CSV
    qualita' S2 e assegnati ai poligoni del GeoPackage nell'ordine in cui compaiono.

    Modalita' di calcolo:
      Default:         RVI solo sui poligoni FALSE nel CSV qualita' S2, cioe'
                       dove S2 non era disponibile (nuvole, ombre, ecc.).
                       Piu' date S2 possono puntare alla stessa scena S1: in
                       quel caso i poligoni FALSE vengono uniti tra tutte le
                       date che condividono quella scena.
                       Produce anche sostituzioni_rvi.csv con il log delle
                       sostituzioni S2->S1 (una riga per data S2).
      --all_polygons:  RVI su tutti i poligoni dell'AOI, indipendentemente
                       dal CSV qualita' S2. Non produce sostituzioni_rvi.csv.

NOTE:
  - Le scene gia' processate in entrambe le fasi vengono saltate automaticamente.
  - outputImageScaleDb=false nel grafo XML: valori lineari, necessari per RVI.
  - SRTM 1Sec HGT: scaricato automaticamente da SNAP al primo utilizzo (~140 MB).
  - Il log viene scritto sia a schermo che su file sentinel1_processing_rvi.log.

STRUTTURA CARTELLE ATTESA:
  output_sentinel1/full_scenes/
    S1A_IW_GRDH_....SAFE/        <- input fase 1
  inventory_output/
    inventory_dates.csv           <- output di sentinel1_pipeline.py

  processed_sentinel1/            <- output fase 1 / input fase 2 (creata automaticamente)
    S1A_IW_GRDH_....dim
    S1A_IW_GRDH_....data/
      Sigma0_VV.img
      Sigma0_VH.img

  rvi_sentinel1/                  <- output fase 2 (creata automaticamente)
    S1A_IW_GRDH_..._RVI.tif
    sostituzioni_rvi.csv

USO:
  python sentinel1_processing_rvi.py [opzioni]

Opzioni principali:
  --scenes_dir    output_sentinel1/full_scenes            Cartella con i .SAFE
  --snap_dir      processed_sentinel1                     Cartella output SNAP (.dim)
  --rvi_dir       rvi_sentinel1                           Cartella output RVI (.tif)
  --graph         s1_processing_graph.xml                 Grafo SNAP GPT XML
  --gpkg          AOI_JOLANDA_SELECTION.gpkg              GeoPackage AOI
  --csv           Valid_date_S2.csv                       CSV qualita' S2 (separatore ;)
  --inventory     inventory_output/inventory_dates.csv    CSV inventario S1
  --layer         None                                    Layer GeoPackage
  --gpt           gpt                                     Percorso eseguibile GPT
  --workers       1                                       Scene in parallelo (default: 1)
  --tile_cache    10240                                   RAM per SNAP in MB (default: 10240)
  --nodata        -9999                                   Valore nodata RVI (default: -9999)
  --all_polygons                                          RVI su tutti i poligoni

Requisiti:
  pip install geopandas shapely rasterio numpy pandas
  SNAP installato con GPT accessibile da PATH (o specificare --gpt)
"""

import os
import re
import sys
import time
import shutil
import logging
import argparse
import subprocess
import warnings
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.ops import unary_union

# ─────────────────────────────────────────────
# CONFIGURAZIONE DEFAULT
# ─────────────────────────────────────────────

SCENES_DIR     = Path("output_sentinel1/full_scenes")
SNAP_DIR       = Path("processed_sentinel1")
RVI_DIR        = Path("rvi_sentinel1")
GRAPH_PATH     = Path("s1_processing_graph.xml")
GPKG_PATH      = "AOI_JOLANDA_SELECTION.gpkg"
GPKG_LAYER     = None
CSV_PATH       = "Valid_date_S2.csv"
INVENTORY_PATH = "inventory_output/inventory_dates.csv"
GPT_EXE        = "gpt"
WORKERS        = 1
TILE_CACHE     = 10240
NODATA         = -9999.0

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sentinel1_processing_rvi.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# UTILITÀ CONDIVISE
# ══════════════════════════════════════════════

def parse_date_from_name(name: str) -> str:
    """Estrae la data di acquisizione dal nome di un file .SAFE o .dim."""
    match = re.search(r"_(\d{8})T", name)
    if match:
        d = match.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return "data-sconosciuta"


def load_aoi(gpkg_path: str, layer=None) -> gpd.GeoDataFrame:
    """Legge il GeoPackage e restituisce il GeoDataFrame in WGS84 (EPSG:4326)."""
    gdf = gpd.read_file(gpkg_path, layer=layer)
    if gdf.crs is None:
        log.warning("GeoPackage senza CRS definito, assumo EPSG:4326")
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        log.info(f"Riproiezione AOI da {gdf.crs} a EPSG:4326")
        gdf = gdf.to_crs(epsg=4326)
    log.info(f"AOI caricata: {len(gdf)} poligoni")
    bounds = gdf.total_bounds
    log.info(f"Bbox AOI (WGS84): lon [{bounds[0]:.4f}, {bounds[2]:.4f}]  lat [{bounds[1]:.4f}, {bounds[3]:.4f}]")
    return gdf


def print_summary(results: list, label: str, total_elapsed_s: float, key: str = "dim"):
    ok      = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors  = [r for r in results if r["status"] == "error"]

    total_gb  = sum(r["size_mb"] for r in ok) / 1024
    total_str = time.strftime("%H:%M:%S", time.gmtime(total_elapsed_s))

    log.info("=" * 60)
    log.info(f"RIEPILOGO {label}")
    log.info(f"  Scene processate OK  : {len(ok)}")
    log.info(f"  Scene saltate (skip) : {len(skipped)}")
    log.info(f"  Errori               : {len(errors)}")
    log.info(f"  Dati prodotti        : {total_gb:.2f} GB")
    log.info(f"  Tempo totale         : {total_str}")
    if errors:
        log.info("  Scene con errori:")
        for r in errors:
            log.info(f"    - {r[key]} ({r['date']})")
    log.info("=" * 60)


# ══════════════════════════════════════════════
# FASE 1 — PROCESSING SNAP
# ══════════════════════════════════════════════

def check_gpt(gpt_exe: str) -> str:
    """Verifica che GPT sia accessibile e restituisce il percorso completo."""
    if shutil.which(gpt_exe):
        return gpt_exe
    if sys.platform == "win32":
        if shutil.which(gpt_exe + ".exe"):
            return gpt_exe + ".exe"
        common_paths = [
            r"C:\snap\bin\gpt.exe",
            r"C:\Program Files\snap\bin\gpt.exe",
            r"C:\Users\{}\AppData\Local\snap\bin\gpt.exe".format(os.getenv("USERNAME", "")),
        ]
        for p in common_paths:
            if Path(p).exists():
                log.info(f"GPT trovato in: {p}")
                return p
    log.error(
        f"GPT non trovato: '{gpt_exe}'.\n"
        "Assicurati che SNAP sia installato e GPT sia nel PATH, oppure\n"
        "specifica il percorso completo con --gpt (es. C:/snap/bin/gpt.exe)"
    )
    sys.exit(1)


def load_aoi_wkt(gdf: gpd.GeoDataFrame) -> str:
    """Calcola il WKT della bbox dell'unione dei poligoni in WGS84."""
    from shapely.geometry import box
    union    = unary_union(gdf.geometry)
    bounds   = union.bounds
    bbox_wkt = box(*bounds).wkt
    log.info(f"Bbox WKT per SNAP: {bbox_wkt}")
    return bbox_wkt


def find_safe_dirs(scenes_dir: Path) -> list:
    """Trova tutte le cartelle .SAFE nella directory delle scene."""
    safe_dirs = sorted(scenes_dir.glob("*.SAFE"))
    if not safe_dirs:
        log.error(f"Nessuna cartella .SAFE trovata in: {scenes_dir}")
        sys.exit(1)
    log.info(f"Scene .SAFE trovate: {len(safe_dirs)}")
    return safe_dirs


def snap_output_path(safe_dir: Path, snap_dir: Path) -> Path:
    return snap_dir / f"{safe_dir.stem}.dim"


def snap_already_processed(out_path: Path) -> bool:
    return out_path.exists() and out_path.stat().st_size > 10_000


def process_snap_scene(
    safe_dir: Path,
    snap_dir: Path,
    graph_path: Path,
    wkt_aoi: str,
    gpt_exe: str,
    tile_cache: int,
    scene_idx: int,
    total: int,
) -> dict:
    """Lancia SNAP GPT su una singola scena .SAFE."""
    out_path   = snap_output_path(safe_dir, snap_dir)
    scene_date = parse_date_from_name(safe_dir.name)
    result     = {"safe": safe_dir.name, "date": scene_date, "status": None, "elapsed_s": 0, "size_mb": 0}

    if snap_already_processed(out_path):
        log.info(f"[{scene_idx}/{total}] SKIP SNAP (gia' processata): {safe_dir.name}")
        result["status"] = "skipped"
        return result

    log.info(f"[{scene_idx}/{total}] Inizio SNAP: {safe_dir.name} ({scene_date})")
    start = time.time()

    cmd = [
        gpt_exe,
        str(graph_path),
        f"-Pinput={safe_dir}",
        f"-Poutput={out_path}",
        f"-Pwkt_aoi={wkt_aoi}",
        f"-J-Xmx{tile_cache}m",
        "-q", str(os.cpu_count() or 4),
    ]
    log.debug(f"Comando: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        snap_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    [SNAP] {line}")
                snap_lines.append(line)
        proc.wait()
        elapsed = time.time() - start

        if proc.returncode != 0:
            log.error(
                f"[{scene_idx}/{total}] ERRORE GPT per {safe_dir.name}\n"
                f"  Return code: {proc.returncode}\n"
                f"  Ultimi messaggi SNAP:\n" +
                "\n".join(f"    {l}" for l in snap_lines[-20:])
            )
            result["status"]    = "error"
            result["elapsed_s"] = elapsed
            if out_path.exists():
                out_path.unlink()
            return result

        size_mb = 0
        if out_path.exists():
            data_dir = out_path.with_suffix(".data")
            if data_dir.exists():
                size_mb = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6
            else:
                size_mb = out_path.stat().st_size / 1e6

        elapsed_str = time.strftime("%M:%S", time.gmtime(elapsed))
        log.info(
            f"[{scene_idx}/{total}] OK SNAP: {safe_dir.name}\n"
            f"    Data: {scene_date}  |  Dimensione: {size_mb:.0f} MB  |  Tempo: {elapsed_str}"
        )
        result["status"]    = "ok"
        result["elapsed_s"] = elapsed
        result["size_mb"]   = size_mb
        return result

    except Exception as e:
        elapsed = time.time() - start
        log.error(f"[{scene_idx}/{total}] Eccezione SNAP per {safe_dir.name}: {e}")
        result["status"]    = "error"
        result["elapsed_s"] = elapsed
        return result


def run_snap(
    scenes_dir:  Path,
    snap_dir:    Path,
    graph_path:  Path,
    gdf_aoi:     gpd.GeoDataFrame,
    gpt_exe:     str,
    workers:     int,
    tile_cache:  int,
):
    """Esegue il processing SNAP su tutte le scene .SAFE."""
    log.info("=" * 60)
    log.info("FASE 1 — PROCESSING SNAP")
    log.info("=" * 60)

    snap_dir.mkdir(parents=True, exist_ok=True)
    wkt_aoi   = load_aoi_wkt(gdf_aoi)
    safe_dirs = find_safe_dirs(scenes_dir)
    total     = len(safe_dirs)

    already_done = sum(1 for s in safe_dirs if snap_already_processed(snap_output_path(s, snap_dir)))
    to_process   = total - already_done
    log.info(f"Scene .SAFE totali: {total}  |  Gia' processate: {already_done}  |  Da processare: {to_process}")

    if to_process == 0:
        log.info("Tutte le scene SNAP sono gia' state processate.")
        return

    start_total = time.time()
    results     = []

    if workers == 1:
        for i, safe_dir in enumerate(safe_dirs, start=1):
            r = process_snap_scene(safe_dir, snap_dir, graph_path, wkt_aoi, gpt_exe, tile_cache, i, total)
            results.append(r)
    else:
        log.warning(f"Modalita' parallela SNAP: {workers} worker. RAM necessaria: ~{workers * tile_cache // 1024} GB.")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_snap_scene, safe_dir, snap_dir, graph_path,
                    wkt_aoi, gpt_exe, tile_cache, i, total
                ): safe_dir
                for i, safe_dir in enumerate(safe_dirs, start=1)
            }
            for future in as_completed(futures):
                results.append(future.result())

    print_summary(results, "PROCESSING SNAP", time.time() - start_total, key="safe")


# ══════════════════════════════════════════════
# FASE 2 — CALCOLO RVI
# ══════════════════════════════════════════════

def assign_poly_ids(gdf: gpd.GeoDataFrame, csv_path: str) -> gpd.GeoDataFrame:
    """
    Legge i nomi dei poligoni (P001, P002, ...) dalle colonne del CSV qualita' S2
    e li assegna al GeoDataFrame nella colonna 'poly_id'.
    """
    df_header = pd.read_csv(csv_path, sep=";", nrows=0)
    poly_ids  = [c for c in df_header.columns if c.startswith("P") and c[1:].isdigit()]

    if not poly_ids:
        log.error(f"Nessuna colonna poligono trovata in: {csv_path}")
        sys.exit(1)
    if len(gdf) != len(poly_ids):
        log.error(
            f"Numero poligoni non corrisponde!\n"
            f"  GeoPackage: {len(gdf)}  |  CSV: {len(poly_ids)} ({poly_ids[0]}...{poly_ids[-1]})"
        )
        sys.exit(1)

    gdf = gdf.copy()
    gdf["poly_id"] = poly_ids
    log.info(f"ID poligono assegnati: {poly_ids[0]} ... {poly_ids[-1]} ({len(poly_ids)} totali)")
    return gdf


def load_s2_quality(csv_path: str) -> pd.DataFrame:
    """Legge il CSV qualita' S2. Indice: data S2 (datetime). Colonne: P001...P154 (bool)."""
    df = pd.read_csv(csv_path, sep=";", index_col=0)
    df.index = pd.to_datetime(df.index)
    df = df.map(lambda x: str(x).strip().lower() == "true")
    return df


def load_inventory(inventory_path: str) -> pd.DataFrame:
    """Legge inventory_dates.csv: date_s2, scene_stem, closest_delta_days."""
    df = pd.read_csv(inventory_path)
    df["date_s2"]    = pd.to_datetime(df["date_s2"])
    df["scene_stem"] = df["closest_product_name"].str.replace(r"\.SAFE$", "", regex=True)
    return df[["date_s2", "scene_stem", "closest_delta_days"]]


def build_scene_to_dates(inventory: pd.DataFrame) -> dict:
    """Mappa scene_stem -> lista di (date_s2, delta_giorni)."""
    scene_map = {}
    for _, row in inventory.iterrows():
        stem = row["scene_stem"]
        if stem not in scene_map:
            scene_map[stem] = []
        scene_map[stem].append((row["date_s2"], int(row["closest_delta_days"])))
    return scene_map


def get_false_polygons(
    scene_stem: str,
    scene_to_dates: dict,
    df_quality: pd.DataFrame,
) -> tuple:
    """
    Per una scena S1, trova l'unione dei poligoni FALSE in tutte le date S2
    che mappano a quella scena. Restituisce (false_poly_ids, sostituzioni).
    """
    dates_info = scene_to_dates.get(scene_stem, [])
    if not dates_info:
        return [], []

    poly_cols    = [c for c in df_quality.columns if c.startswith("P") and c[1:].isdigit()]
    all_false    = set()
    sostituzioni = []

    for date_s2, delta in dates_info:
        if date_s2 not in df_quality.index:
            log.warning(f"Data {date_s2.date()} non trovata nel CSV qualita' S2, skip.")
            continue
        row        = df_quality.loc[date_s2]
        false_here = [p for p in poly_cols if not row[p]]
        all_false.update(false_here)
        sostituzioni.append({
            "date_s2":             date_s2.strftime("%Y-%m-%d"),
            "scena_s1":            scene_stem,
            "delta_giorni":        delta,
            "n_poligoni":          len(false_here),
            "poligoni_sostituiti": ", ".join(sorted(false_here)),
        })

    return sorted(all_false), sostituzioni


def find_dim_files(snap_dir: Path) -> list:
    """Trova tutti i file .dim nella cartella SNAP."""
    dim_files = sorted(snap_dir.glob("*.dim"))
    if not dim_files:
        log.error(f"Nessun file .dim trovato in: {snap_dir}")
        sys.exit(1)
    log.info(f"File .dim trovati: {len(dim_files)}")
    return dim_files


def find_band_images(dim_path: Path) -> tuple:
    """Cerca VV e VH nella cartella .data accanto al .dim."""
    data_dir = dim_path.with_suffix(".data")
    if not data_dir.exists():
        raise FileNotFoundError(f"Cartella .data non trovata: {data_dir}")
    vv_files = list(data_dir.glob("*VV*.img"))
    vh_files = list(data_dir.glob("*VH*.img"))
    if not vv_files:
        raise FileNotFoundError(f"Banda VV non trovata in: {data_dir}")
    if not vh_files:
        raise FileNotFoundError(f"Banda VH non trovata in: {data_dir}")
    return vv_files[0], vh_files[0]


def rvi_output_path(dim_path: Path, rvi_dir: Path) -> Path:
    return rvi_dir / f"{dim_path.stem}_RVI.tif"


def rvi_already_processed(out_path: Path) -> bool:
    return out_path.exists() and out_path.stat().st_size > 100_000


def compute_rvi_scene(
    dim_path:        Path,
    rvi_dir:         Path,
    gdf_aoi:         gpd.GeoDataFrame,
    nodata:          float,
    scene_idx:       int,
    total:           int,
    active_poly_ids, # list[str] | None — None = tutti i poligoni
) -> dict:
    """Calcola l'RVI per una singola scena .dim."""
    out_path   = rvi_output_path(dim_path, rvi_dir)
    scene_date = parse_date_from_name(dim_path.name)
    result     = {"dim": dim_path.name, "date": scene_date, "status": None, "elapsed_s": 0, "size_mb": 0}

    if rvi_already_processed(out_path):
        log.info(f"[{scene_idx}/{total}] SKIP RVI (gia' processata): {dim_path.name}")
        result["status"] = "skipped"
        return result

    if active_poly_ids is None:
        gdf_active = gdf_aoi
        mode_str   = "tutti i poligoni"
    else:
        if not active_poly_ids:
            log.info(f"[{scene_idx}/{total}] SKIP RVI (nessun poligono FALSE): {dim_path.name}")
            result["status"] = "skipped"
            return result
        gdf_active = gdf_aoi[gdf_aoi["poly_id"].isin(active_poly_ids)]
        mode_str   = f"{len(gdf_active)} poligoni FALSE"

    log.info(f"[{scene_idx}/{total}] Inizio RVI ({mode_str}): {dim_path.name} ({scene_date})")
    start = time.time()

    try:
        vv_path, vh_path = find_band_images(dim_path)

        with rasterio.open(vv_path) as src_vv, rasterio.open(vh_path) as src_vh:
            profile    = src_vv.profile.copy()
            raster_crs = src_vv.crs

            if raster_crs and raster_crs.to_epsg() != 4326:
                gdf_reproj = gdf_active.to_crs(raster_crs)
            else:
                gdf_reproj = gdf_active

            shapes = [geom.__geo_interface__ for geom in gdf_reproj.geometry]

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                vv_masked, transform_out = rio_mask(src_vv, shapes, crop=True, nodata=nodata, filled=True)
                vh_masked, _             = rio_mask(src_vh, shapes, crop=True, nodata=nodata, filled=True)

        vv = vv_masked[0].astype(np.float32)
        vh = vh_masked[0].astype(np.float32)

        valid_mask = (
            (vv != nodata) & (vh != nodata) &
            np.isfinite(vv) & np.isfinite(vh) &
            ((vv + vh) > 0)
        )

        rvi = np.full_like(vv, nodata, dtype=np.float32)
        rvi[valid_mask] = (4.0 * vh[valid_mask]) / (vv[valid_mask] + vh[valid_mask])

        rvi_valid = rvi[valid_mask]
        if rvi_valid.size > 0:
            log.info(
                f"  RVI stats: min={rvi_valid.min():.4f}  max={rvi_valid.max():.4f}"
                f"  mean={rvi_valid.mean():.4f}  pixel_validi={rvi_valid.size:,}"
            )
        else:
            log.warning(f"  Nessun pixel valido per: {dim_path.name}")

        profile.update(
            driver="GTiff", dtype="float32", count=1, nodata=nodata,
            transform=transform_out, height=rvi.shape[0], width=rvi.shape[1],
            compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256,
        )

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(rvi, 1)
            dst.update_tags(
                BAND="RVI (Radar Vegetation Index)",
                FORMULA="4*VH / (VV + VH)",
                SCALE="linear",
                SOURCE=dim_path.name,
                DATE=scene_date,
                MODALITA="all_polygons" if active_poly_ids is None else "false_only",
            )

        elapsed     = time.time() - start
        size_mb     = out_path.stat().st_size / 1e6
        elapsed_str = time.strftime("%M:%S", time.gmtime(elapsed))
        log.info(
            f"[{scene_idx}/{total}] OK RVI: {out_path.name}\n"
            f"    Data: {scene_date}  |  Dimensione: {size_mb:.1f} MB  |  Tempo: {elapsed_str}"
        )
        result["status"]    = "ok"
        result["elapsed_s"] = elapsed
        result["size_mb"]   = size_mb
        return result

    except Exception as e:
        elapsed = time.time() - start
        log.error(f"[{scene_idx}/{total}] Eccezione RVI per {dim_path.name}: {e}", exc_info=True)
        if out_path.exists():
            out_path.unlink()
        result["status"]    = "error"
        result["elapsed_s"] = elapsed
        return result


def run_rvi(
    snap_dir:       Path,
    rvi_dir:        Path,
    gdf_aoi:        gpd.GeoDataFrame,
    csv_path:       str,
    inventory_path: str,
    nodata:         float,
    workers:        int,
    all_polygons:   bool,
):
    """Esegue il calcolo RVI su tutti i file .dim."""
    log.info("=" * 60)
    log.info("FASE 2 — CALCOLO RVI")
    log.info("=" * 60)

    rvi_dir.mkdir(parents=True, exist_ok=True)

    # Assegna poly_id al GeoDataFrame
    gdf_aoi = assign_poly_ids(gdf_aoi, csv_path)

    # Carica qualita' S2 e inventario (solo modalita' default)
    scene_to_dates = {}
    df_quality     = None

    if not all_polygons:
        if not Path(inventory_path).exists():
            log.error(f"inventory_dates.csv non trovato: {inventory_path}")
            sys.exit(1)
        df_quality     = load_s2_quality(csv_path)
        inventory      = load_inventory(inventory_path)
        scene_to_dates = build_scene_to_dates(inventory)
        log.info(f"Inventario caricato: {len(scene_to_dates)} scene S1 distinte")

    dim_files    = find_dim_files(snap_dir)
    total        = len(dim_files)
    already_done = sum(1 for d in dim_files if rvi_already_processed(rvi_output_path(d, rvi_dir)))
    to_process   = total - already_done
    log.info(f"File .dim totali: {total}  |  Gia' processati: {already_done}  |  Da processare: {to_process}")

    if to_process == 0:
        log.info("Tutti i file RVI sono gia' stati calcolati.")
        return

    start_total      = time.time()
    results          = []
    all_sostituzioni = []

    if workers == 1:
        for i, dim_path in enumerate(dim_files, start=1):
            if all_polygons:
                active, sostituzioni_scene = None, []
            else:
                active, sostituzioni_scene = get_false_polygons(
                    dim_path.stem, scene_to_dates, df_quality
                )
            result = compute_rvi_scene(dim_path, rvi_dir, gdf_aoi, nodata, i, total, active)
            results.append(result)
            all_sostituzioni.extend(sostituzioni_scene)
    else:
        log.warning(f"Modalita' parallela RVI: {workers} worker.")

        def process_one_rvi(i, dim_path):
            if all_polygons:
                active, sostituzioni_scene = None, []
            else:
                active, sostituzioni_scene = get_false_polygons(
                    dim_path.stem, scene_to_dates, df_quality
                )
            result = compute_rvi_scene(dim_path, rvi_dir, gdf_aoi, nodata, i, total, active)
            return result, sostituzioni_scene

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_one_rvi, i, dim_path): dim_path
                for i, dim_path in enumerate(dim_files, start=1)
            }
            for future in as_completed(futures):
                result, sostituzioni_scene = future.result()
                results.append(result)
                all_sostituzioni.extend(sostituzioni_scene)

    # Scrivi sostituzioni_rvi.csv
    if not all_polygons and all_sostituzioni:
        df_sost   = pd.DataFrame(all_sostituzioni).sort_values("date_s2")
        sost_path = rvi_dir / "sostituzioni_rvi.csv"
        df_sost.to_csv(sost_path, index=False)
        log.info(f"Log sostituzioni scritto: {sost_path} ({len(df_sost)} righe)")

    print_summary(results, "CALCOLO RVI", time.time() - start_total, key="dim")


# ══════════════════════════════════════════════
# PIPELINE PRINCIPALE
# ══════════════════════════════════════════════

def run(
    scenes_dir:     Path,
    snap_dir:       Path,
    rvi_dir:        Path,
    graph_path:     Path,
    gpkg_path:      str,
    gpkg_layer,
    csv_path:       str,
    inventory_path: str,
    gpt_exe:        str,
    workers:        int,
    tile_cache:     int,
    nodata:         float,
    all_polygons:   bool,
):
    # ── Controlli preliminari ──
    for p, label in [
        (scenes_dir,      "Cartella scene .SAFE"),
        (graph_path,      "Grafo SNAP XML"),
        (Path(gpkg_path), "GeoPackage"),
        (Path(csv_path),  "CSV qualita' S2"),
    ]:
        if not Path(p).exists():
            log.error(f"{label} non trovato: {p}")
            sys.exit(1)

    gpt_exe = check_gpt(gpt_exe)

    # ── Caricamento AOI (condiviso tra le due fasi) ──
    gdf_aoi = load_aoi(gpkg_path, layer=gpkg_layer)

    # ── Fase 1: Processing SNAP ──
    run_snap(scenes_dir, snap_dir, graph_path, gdf_aoi, gpt_exe, workers, tile_cache)

    # ── Fase 2: Calcolo RVI ──
    run_rvi(snap_dir, rvi_dir, gdf_aoi, csv_path, inventory_path, nodata, workers, all_polygons)


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sentinel-1: Processing SNAP + Calcolo RVI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--scenes_dir",  default=str(SCENES_DIR),     help="Cartella con le scene .SAFE (default: output_sentinel1/full_scenes)")
    parser.add_argument("--snap_dir",    default=str(SNAP_DIR),        help="Cartella output SNAP .dim (default: processed_sentinel1)")
    parser.add_argument("--rvi_dir",     default=str(RVI_DIR),         help="Cartella output RVI .tif (default: rvi_sentinel1)")
    parser.add_argument("--graph",       default=str(GRAPH_PATH),      help="Grafo SNAP GPT XML (default: s1_processing_graph.xml)")
    parser.add_argument("--gpkg",        default=GPKG_PATH,            help="GeoPackage AOI (default: AOI_JOLANDA_SELECTION.gpkg)")
    parser.add_argument("--csv",         default=CSV_PATH,             help="CSV qualita' S2 (separatore ;) (default: Valid_date_S2.csv)")
    parser.add_argument("--inventory",   default=INVENTORY_PATH,       help="inventory_dates.csv (default: inventory_output/inventory_dates.csv)")
    parser.add_argument("--layer",       default=None,                 help="Layer GeoPackage (default: primo)")
    parser.add_argument("--gpt",         default=GPT_EXE,              help="Percorso eseguibile GPT (default: gpt)")
    parser.add_argument("--workers",     type=int,   default=WORKERS,  help="Scene in parallelo (default: 1)")
    parser.add_argument("--tile_cache",  type=int,   default=TILE_CACHE, help="RAM per SNAP in MB (default: 10240)")
    parser.add_argument("--nodata",      type=float, default=NODATA,   help="Valore nodata RVI (default: -9999)")
    parser.add_argument("--all_polygons", action="store_true",         help="Calcola RVI su tutti i poligoni (default: solo poligoni FALSE)")
    args = parser.parse_args()

    run(
        scenes_dir     = Path(args.scenes_dir),
        snap_dir       = Path(args.snap_dir),
        rvi_dir        = Path(args.rvi_dir),
        graph_path     = Path(args.graph),
        gpkg_path      = args.gpkg,
        gpkg_layer     = args.layer,
        csv_path       = args.csv,
        inventory_path = args.inventory,
        gpt_exe        = args.gpt,
        workers        = args.workers,
        tile_cache     = args.tile_cache,
        nodata         = args.nodata,
        all_polygons   = args.all_polygons,
    )
