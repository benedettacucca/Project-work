
#Sentinel-2 Analytics Pipeline
#Carica AOI, scarica Sentinel-2 L2A, applica cloud mask SCL, calcola NDVI e valida date



#INSTALLAZIONE LIBRERIE
import openeo
import openeo.processes as p
import pandas as pd
import json
import os
import time
import geopandas as gpd
import sys
from datetime import timedelta
from pathlib import Path

# CONNESSIONE OPENEO COPERNICUS
connection = openeo.connect("https://openeo.dataspace.copernicus.eu")

try:
    connection = connection.authenticate_oidc(max_poll_time=180)
    print('✓ Autenticazione riuscita')
except Exception as e:
    sys.exit(f'✗ Autenticazione fallita: {e}')

# CONFIGURAZIONE
COLLECTION_ID = "SENTINEL2_L2A"
BANDS = ["B04", "B08", "SCL"]
TEMPORAL_START = "2023-01-01"
TEMPORAL_END = "2025-12-31"
MAX_CLOUD_PROBABILITY = 30  # Soglia % cloud acceptance
MIN_VALID_SCL = 4  # SCL 4-7: vegetazione, suolo, acqua, non classificato
MAX_VALID_SCL = 7

AOI_PATH = 'C:/Users/b.cucca/Desktop/Codice_Master/AOI.geojson'
OUTPUT_DIR = "C:/Users/b.cucca/Desktop/Codice_Master/output"
VALID_IMAGES_CSV = f"{OUTPUT_DIR}/valid_images_per_polygon.csv"
VALID_IMAGES_DOWNLOAD_DIR = f"{OUTPUT_DIR}/sentinel_valid_images"

MAX_RETRIES = 5
BASE_WAIT_S = 6

# FUNZIONI AUSILIARIE

def download_with_retry(result_obj, output_path, label, max_retries=MAX_RETRIES, base_wait_s=BASE_WAIT_S):
    """Download con retry exp backoff su errori di rete."""
    for attempt in range(1, max_retries + 1):
        try:
            result_obj.download(output_path)
            print(f"✓ Download: {label}")
            return
        except Exception as e:
            if attempt == max_retries:
                raise
            wait_s = base_wait_s * attempt
            print(f"⚠ Tentativo {attempt}/{max_retries} ({label}): {e}... riprovo in {wait_s}s")
            time.sleep(wait_s)


def _fallback_locked_path(path_str):
    path = Path(path_str)
    return path.with_name(f"{path.stem}_locked_{int(time.time())}{path.suffix}")


def write_csv_with_fallback(df, output_path, label):
    """Salva CSV con fallback se file bloccato."""
    try:
        df.to_csv(output_path, index=False)
        return output_path
    except PermissionError:
        fallback_path = _fallback_locked_path(output_path)
        df.to_csv(fallback_path, index=False)
        print(f"⚠ {output_path} bloccato → {fallback_path}")
        return str(fallback_path)


def write_geojson_with_fallback(gdf, output_path, label):
    """Salva GeoJSON con fallback se file bloccato."""
    try:
        gdf.to_file(output_path, driver="GeoJSON")
        return output_path
    except PermissionError:
        fallback_path = _fallback_locked_path(output_path)
        gdf.to_file(fallback_path, driver="GeoJSON")
        print(f"⚠ {output_path} bloccato → {fallback_path}")
        return str(fallback_path)


def apply_cloud_mask(cube):
    # Filtra pixel sulla base della banda SCL (Scene Classification Layer)
    # Invalida pixel con SCL < 4 o SCL > 7 (mantiene solo vegetazione, suolo, acqua, non classificato)
    invalid_scl = cube.band("SCL").apply(
        lambda x: p.or_(p.lt(x, MIN_VALID_SCL), p.gt(x, MAX_VALID_SCL))
    )
    return cube.mask(invalid_scl)

def calculate_ndvi(cube):
    # NDVI = (NIR - Red) / (NIR + Red) dove NIR=B08, Red=B04
    # Indice normalizzato per vegetazione (-1 a +1, positivo su vegetazione)
    B04 = cube.band("B04")
    B08 = cube.band("B08")
    return (B08 - B04) / (B08 + B04)

def load_aoi(aoi_path, output_dir):
    """Carica AOI, assegna poly_id (P001-P154), salva metadata."""
    # Leggi GeoJSON con geopandas
    gdf = gpd.read_file(aoi_path)
    # Assegna ID progressivo P001, P002, ... P154 per univocita
    gdf["poly_id"] = [f"P{i+1:03d}" for i in range(len(gdf))]
    os.makedirs(output_dir, exist_ok=True)
    # Salva mapping ID originali <-> poly_id
    write_csv_with_fallback(gdf.drop(columns="geometry"), f"{output_dir}/aoi_attributi.csv", "AOI CSV")
    # Salva geometrie con poly_id per debugging spaziale
    write_geojson_with_fallback(gdf, f"{output_dir}/aoi_with_id.geojson", "AOI GeoJSON")
    print(f"✓ AOI caricato: {len(gdf)} poligoni, CRS={gdf.crs}")
    return gdf


def count_pixels(cube, cloudless_cube, geo, output_dir):
    """Conta pixel totali e validi (post-cloud mask) per poligono per data."""
    # Aggregazione spaziale: somma/conteggio pixel in ciascun poligono per ogni data
    # Pre-mask: tutti i pixel indipendentemente da nuvole
    total_count_pre = cube.band("B04").aggregate_spatial(geometries=geo, reducer="count")
    # Post-mask: solo pixel dove SCL valido
    total_count_post = cloudless_cube.band("B04").aggregate_spatial(geometries=geo, reducer="count")

    total_path = f"{output_dir}/total_count_pre_mask_per_polygon.json"
    valid_path = f"{output_dir}/valid_count_post_mask_per_polygon.json"

    download_with_retry(total_count_pre, total_path, "pixel totali")
    download_with_retry(total_count_post, valid_path, "pixel validi")

    # Carica JSON in memoria per parsing successivo
    with open(total_path) as f:
        total_result = json.load(f)
    with open(valid_path) as f:
        valid_result = json.load(f)
    return total_result, valid_result

def _to_bool_series(series):
    """Converte una colonna CSV in booleano gestendo stringhe True/False, 1/0, yes/no."""
    return series.apply(lambda v: str(v).strip().lower() in {"true", "1", "yes", "y"})

def download_valid_b04_b08_masked_from_csv(connection, collection_id, geo, csv_path, output_dir, mode="any"):
    """Scarica B04 e B08 separati (cloud-masked) per date valide nel CSV.
    mode='any': data valida se >=1 poligono ha True; 'all': tutti i poligoni True.
    """
    if mode not in {"any", "all"}:
        raise ValueError("mode deve essere 'any' oppure 'all'.")

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV non trovato: {csv_path}")

    df = pd.read_csv(csv_path)
    if "date" not in df.columns:
        raise ValueError("Il CSV deve contenere la colonna 'date'.")

    polygon_cols = [c for c in df.columns if c != "date"]
    if not polygon_cols:
        raise ValueError("Il CSV non contiene colonne poligono (es. P001, P002, ...).")

    bool_df = df.copy()
    for col in polygon_cols:
        bool_df[col] = _to_bool_series(bool_df[col])

    if mode == "any":
        valid_mask = bool_df[polygon_cols].any(axis=1)
    else:
        valid_mask = bool_df[polygon_cols].all(axis=1)

    valid_dates = bool_df.loc[valid_mask, "date"].tolist()

    if not valid_dates:
        print("Nessuna data valida trovata nel CSV: skip download bande B04/B08 mascherate.")
        return []

    os.makedirs(output_dir, exist_ok=True)
    downloaded_files = []

    print(f"✓ {len(valid_dates)} date valide (mode={mode})")
    # Loop su ogni data valida
    for date_str in valid_dates:
        start_dt = pd.to_datetime(date_str)
        end_dt = start_dt + timedelta(days=1)
        temporal_extent = [start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")]

        # Carica cube OpenEO per questa data (B04, B08, SCL)
        date_cube = connection.load_collection(collection_id, temporal_extent=temporal_extent, bands=["B04", "B08", "SCL"]).filter_spatial(geometries=geo)
        # Applica cloud mask SCL
        masked_cube = apply_cloud_mask(date_cube)
        # Seleziona primo/unico timestep e riduce dimensione temporale
        masked_cube_reduced = masked_cube.reduce_dimension(dimension="t", reducer="first")

        # Scarica B04 separatamente
        b04_cube = masked_cube_reduced.filter_bands(["B04"])
        out_path_b04 = f"{output_dir}/sentinel2_valid_masked_B04_{start_dt.strftime('%Y%m%d')}.tif"
        download_with_retry(b04_cube, out_path_b04, f"B04 {start_dt.strftime('%Y-%m-%d')}")
        downloaded_files.append(out_path_b04)

        # Scarica B08 separatamente (NIR)
        b08_cube = masked_cube_reduced.filter_bands(["B08"])
        out_path_b08 = f"{output_dir}/sentinel2_valid_masked_B08_{start_dt.strftime('%Y%m%d')}.tif"
        download_with_retry(b08_cube, out_path_b08, f"B08 {start_dt.strftime('%Y-%m-%d')}")
        downloaded_files.append(out_path_b08)
    return downloaded_files





# FLUSSO PRINCIPALE
# 1. Carica AOI e crea geometrie filtrate
aoi_gdf = load_aoi(AOI_PATH, OUTPUT_DIR)

# Converti GeoDataFrame in FeatureCollection GeoJSON per OpenEO
geo = json.loads(aoi_gdf.to_json())
# Mantieni lista di poly_id ordinati per mappare risultati aggregati
poly_ids = aoi_gdf["poly_id"].tolist()

# 2. Carica dati Sentinel-2 per intervallo temporale
cube = (
    connection.load_collection(
        COLLECTION_ID,
        temporal_extent=[TEMPORAL_START, TEMPORAL_END],
        bands=BANDS,
    )
    .filter_spatial(geometries=geo)
)

# 3. Applica cloud mask e calcola NDVI
cloudless_cube = apply_cloud_mask(cube)
ndvi_cube = calculate_ndvi(cloudless_cube)
# 4. Conta pixel per validazione
total_result, valid_result = count_pixels(cube, cloudless_cube, geo, OUTPUT_DIR)
# 5. Calcola NDVI medio per poligono per data
mean_ndvi_agg = ndvi_cube.aggregate_spatial(geometries=geo, reducer="mean")
download_with_retry(mean_ndvi_agg, f"{OUTPUT_DIR}/mean_ndvi_per_polygon.json", "NDVI medio")

# 6. Parsing conteggi pixel -> booleano validita per data
# Legge JSON conteggi pixel, calcola frazione valida per ogni poligono per data
# Marca True se frazione >= 70% (1 - MAX_CLOUD_PROBABILITY%)
rows = []
# Per ogni data nel dataset
for date_str in total_result:
    total_values = total_result[date_str]  # Conteggi pixel totali per questa data
    valid_values = valid_result.get(date_str, [])  # Conteggi pixel validi (cloud-free)
    row = {"date": date_str}
    # Per ogni poligono, calcola frazione di pixel validi
    for i, pid in enumerate(poly_ids):
        # Estrai conteggi (possono essere liste o scalari)
        total = total_values[i][0] if isinstance(total_values[i], list) else total_values[i]
        valid = valid_values[i][0] if i < len(valid_values) and isinstance(valid_values[i], list) else (valid_values[i] if i < len(valid_values) else 0)
        # Valida: True se almeno 70% pixel sono cloud-free
        if total and total > 0:
            row[pid] = (valid / total) >= (1 - MAX_CLOUD_PROBABILITY / 100)
        else:
            row[pid] = False
    rows.append(row)

df_valid = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
df_valid.to_csv(f"{OUTPUT_DIR}/valid_images_per_polygon.csv", index=False)
print(f"\n✓ Validità per poligono per data ({len(df_valid)} date, {len(poly_ids)} poligoni)")

# 7. Export immagini cloud-masked (B04 e B08 separati per date valide)
download_valid_b04_b08_masked_from_csv(connection, COLLECTION_ID, geo, VALID_IMAGES_CSV, VALID_IMAGES_DOWNLOAD_DIR, mode="any")

# 8. Calcola statistiche dettagliate pixel per poligono per data
# Utile per debugging e validazione qualita
pixel_rows = []
for date_str in total_result:
    total_values = total_result[date_str]
    valid_values = valid_result.get(date_str, [])
    # Per ogni combinazione data x poligono, salva metriche pixel
    for i, pid in enumerate(poly_ids):
        total = total_values[i][0] if isinstance(total_values[i], list) else total_values[i]
        valid = (
            valid_values[i][0]
            if i < len(valid_values) and isinstance(valid_values[i], list)
            else (valid_values[i] if i < len(valid_values) else 0)
        )
        # Normalizza a interi e calcola frazione e validita
        total = int(total) if total is not None else 0
        valid = int(valid) if valid is not None else 0
        fraction = (valid / total) if total > 0 else 0.0
        is_valid = fraction >= (1 - MAX_CLOUD_PROBABILITY / 100)
        # Salva record dettagliato
        pixel_rows.append(
            {
                "date": date_str,
                "poly_id": pid,
                "total_pixels": total,
                "valid_pixels": valid,
                "valid_fraction": round(fraction, 4),
                "is_valid": is_valid,
            }
        )

# Export in tre formati: long (dettagliato), wide (per pivot), summary (aggregato per poligono)
df_pixels_long = pd.DataFrame(pixel_rows).sort_values(["poly_id", "date"]).reset_index(drop=True)
df_pixels_long.to_csv(f"{OUTPUT_DIR}/valid_pixels_long.csv", index=False)
# Pivot: date x poligoni, valori = frazione pixel validi
df_pixels_wide = df_pixels_long.pivot(index="date", columns="poly_id", values="valid_fraction").reset_index().sort_values("date")
df_pixels_wide.to_csv(f"{OUTPUT_DIR}/valid_pixels_per_polygon.csv", index=False)
# Riepilogo per poligono: conteggio date valide/totali e ratio
df_dates_summary = df_pixels_long.groupby("poly_id", as_index=False).agg(total_dates=("date", "nunique"), valid_dates=("is_valid", "sum"))
df_dates_summary["invalid_dates"] = df_dates_summary["total_dates"] - df_dates_summary["valid_dates"]
df_dates_summary["valid_ratio"] = (df_dates_summary["valid_dates"] / df_dates_summary["total_dates"]).round(4)
df_dates_summary.to_csv(f"{OUTPUT_DIR}/valid_dates_summary.csv", index=False)

# 9. Parsing e export NDVI medio calcolato per poligono per data
with open(f"{OUTPUT_DIR}/mean_ndvi_per_polygon.json") as f:
    ndvi_result = json.load(f)  # Leggi risultati NDVI da OpenEO
ndvi_rows = []
for date_str, values in ndvi_result.items():
    row = {"date": date_str}
    # Mappa valori NDVI a poly_id
    for i, v in enumerate(values):
        pid = poly_ids[i] if i < len(poly_ids) else f"P{i+1:03d}"
        val = v[0] if isinstance(v, list) else v
        row[pid] = round(float(val), 4) if val is not None else None
    ndvi_rows.append(row)
df_ndvi = pd.DataFrame(ndvi_rows).sort_values("date").reset_index(drop=True)
df_ndvi.to_csv(f"{OUTPUT_DIR}/mean_ndvi_per_polygon.csv", index=False)

print(f"\n✓ Pipeline completato!")
print(f"  Output: {OUTPUT_DIR}/")


