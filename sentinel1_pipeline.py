"""
sentinel1_pipeline.py — Inventario e Download Sentinel-1 via CDSE
==================================================================
Script unificato che integra inventario e download in un unico workflow
configurabile tramite la modalita' di esecuzione (--mode).

MODALITA':
  --mode inventory
      Solo ricerca sul catalogo CDSE, nessun download.
      Per ogni data S2 nuvolosa cerca i prodotti S1 disponibili nella
      finestra +/-WINDOW_DAYS e produce 3 CSV + 1 file di riepilogo.
      Utile per esplorare la disponibilita' e scegliere orbita e
      satellite prima di avviare il download.

  --mode download
      Legge l'inventory_products.csv gia' prodotto dalla modalita'
      inventory ed esegue il download dei prodotti unici.
      Non rifa' le ricerche sul catalogo CDSE -> piu' veloce.
      Applica i filtri --orbit, --platform e --year sull'inventory
      gia' prodotto, poi mostra un riepilogo pre-download (n. immagini,
      orbite, GB stimati) e chiede conferma prima di procedere.

  --mode full
      Esegue inventario + download in sequenza senza passare per i
      file CSV intermedi. Tutto in un unico lancio.

PRODOTTI CERCATI:
  Sentinel-1 IW GRD con polarizzazione VV+VH.
  La polarizzazione VV+VH e' necessaria per il calcolo dell'indice:
    RVI (Radar Vegetation Index) = 4 x VH / (VV + VH)

FORMATO OUTPUT:
  Le scene vengono scaricate e salvate in formato .SAFE (struttura
  originale ESA), che e' il formato richiesto da SNAP per il
  processing (calibrazione, terrain correction, ecc.).
  Solo dopo il processing con SNAP si procedera' al ritaglio
  sui singoli poligoni.

FILE DI INPUT:
  - CSV qualita' S2: separatore ;, date in formato ISO
    (es. 2024-01-03T00:00:00Z), valori True/False per poligono
  - GeoPackage: poligoni dell'area di studio (anche multipli).
    La bbox globale viene usata per la ricerca, l'unione reale
    dei poligoni per il calcolo della copertura %.

FILE DI OUTPUT (modalita' inventory e full):
  inventory_output/
    inventory_products.csv
      -> un prodotto S1 per riga con: data, orbita relativa,
         orbita assoluta, direzione, satellite, dimensione MB,
         disponibilita' online, copertura % sull'area di studio
    inventory_dates.csv
      -> una riga per data S2 nuvolosa con: n. prodotti trovati,
         prodotto piu' vicino, delta giorni, copertura %,
         tutte le orbite disponibili nella finestra
    inventory_orbits.csv
      -> riepilogo per orbita: n. prodotti, periodo, % online
    inventory_summary.txt
      -> riepilogo testuale stampato anche a schermo

FILE DI OUTPUT (modalita' download e full):
  output_sentinel1/
    full_scenes/
      <product_name>.SAFE/   <- struttura originale ESA, pronta per SNAP

Note:
  - Credenziali CDSE lette dalle variabili d'ambiente CDSE_USER e CDSE_PASS
    (registrazione gratuita su dataspace.copernicus.eu)
  - Le scene gia' scaricate vengono saltate automaticamente: il download
    e' riprendibile in caso di interruzione
  - Durante il download vengono registrati nel log: tempo per singola
    scena, velocita' in MB/s, stima del tempo rimanente e riepilogo
    finale con tempo totale e GB scaricati
  - Con --year si limita l'analisi/download a un singolo anno del CSV
  - Con --platform si sceglie il satellite: S1A (default), S1C, BOTH
    Nota tecnica: il catalogo CDSE restituisce sempre 'SENTINEL-1' nel
    campo platformShortName, senza la lettera finale (A o C). Il filtro
    viene quindi applicato lato Python sul nome del prodotto, che inizia
    sempre con 'S1A_' o 'S1C_' ed e' un'informazione affidabile.
    Cronologia costellazione Sentinel-1:
      S1-A operativo dal 2014
      S1-B operativo 2016-2021 (guasto hardware)
      S1-C operativo da maggio 2025
      S1-D operativo da maggio 2026 (tandem con S1-C)
    Per time series 2023-2025 si raccomanda --platform S1A per coerenza,
    evitando la contaminazione con S1-C entrato in scena a meta' 2025.
  - Rivisit Sentinel-1: ~12gg con un solo satellite attivo, ~6gg con
    due satelliti sfasati sulla stessa orbita (configurazione tandem:
    S1-C+S1-D da maggio 2026; in precedenza S1-A+S1-B fino al 2021)
  - Per una time series agricola coerente usare sempre la stessa
    direzione orbitale (ASCENDING o DESCENDING) e lo stesso satellite

Uso:
  python sentinel1_pipeline.py --mode inventory [opzioni]
  python sentinel1_pipeline.py --mode download  [opzioni]
  python sentinel1_pipeline.py --mode full      [opzioni]

Opzioni principali:
  --csv        Valid_date_S2.csv    CSV qualita' S2 (separatore ;)
  --gpkg       polygons.gpkg        GeoPackage con i poligoni dell'area
  --layer      None                 Layer del GeoPackage (None = primo)
  --window     6                    Finestra +/-giorni (default: 6)
  --orbit      BOTH                 ASCENDING | DESCENDING | BOTH
  --platform   S1A                  S1A | S1C | BOTH (default: S1A)
  --year       None                 Anno da analizzare/scaricare (es. 2024)
  --all_dates  False                Cerca S1 anche per le date S2 senza nuvole
  --clean_only False                Cerca S1 solo per le date S2 completamente pulite (tutte TRUE)
  --inventory  inventory_output     Cartella report inventario
  --outdir     output_sentinel1     Cartella output download (.SAFE)

Requisiti:
  pip install requests geopandas pandas shapely tqdm python-dotenv
"""

import os
import sys
import time
import json
import zipfile
import logging
import tempfile
import requests
import pandas as pd
import geopandas as gpd
import shutil
from shapely.ops import unary_union
from shapely.geometry import shape
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# CONFIGURAZIONE DEFAULT
# ─────────────────────────────────────────────
load_dotenv()

CDSE_USER     = os.getenv("CDSE_USER", "")
CDSE_PASSWORD = os.getenv("CDSE_PASS", "")

CSV_PATH      = "Valid_date_S2.csv"
GPKG_PATH     = "AOI_JOLANDA_SELECTION.gpkg"
GPKG_LAYER    = None

INVENTORY_DIR = Path("inventory_output")
OUTPUT_DIR    = Path("output_sentinel1")
WINDOW_DAYS   = 6
ORBIT_DIR     = "BOTH"   # "ASCENDING", "DESCENDING", "BOTH"
PLATFORM      = "S1A"    # "S1A", "S1C", "BOTH"
YEAR          = None          # None = tutti gli anni; es. 2024 = solo 2024
ALL_DATES     = False         # True = cerca S1 anche per date S2 senza nuvole
CLEAN_ONLY    = False         # True = cerca S1 solo per date S2 completamente pulite (tutte TRUE)
MAX_RETRIES   = 3
SLEEP_BETWEEN = 1.0

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sentinel1_pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

TOKEN_URL    = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ODATA_URL    = "https://catalogue.dataspace.copernicus.eu/odata/v1"
DOWNLOAD_URL = "https://zipper.dataspace.copernicus.eu/odata/v1"


# ══════════════════════════════════════════════
# AUTENTICAZIONE
# ══════════════════════════════════════════════
class CDSESession:
    def __init__(self, user: str, password: str):
        self.user = user
        self.password = password
        self._expires_at = 0.0
        self.session = requests.Session()
        self._refresh_token()

    def _refresh_token(self):
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "cdse-public",
                "username": self.user,
                "password": self.password,
            },
            timeout=30,
        )
        resp.raise_for_status()
        tok = resp.json()
        self._expires_at = time.time() + tok["expires_in"] - 30
        self.session.headers.update({"Authorization": f"Bearer {tok['access_token']}"})
        log.debug("Token CDSE aggiornato.")

    def ensure_token(self):
        """Rinnova il token se scaduto (metodo pubblico)."""
        if time.time() >= self._expires_at:
            self._refresh_token()

    def get(self, url: str, **kwargs) -> requests.Response:
        if time.time() >= self._expires_at:
            self._refresh_token()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.get(url, timeout=60, **kwargs)
                r.raise_for_status()
                return r
            except requests.HTTPError as e:
                if r.status_code == 401:
                    self._refresh_token()
                log.warning(f"Tentativo {attempt}/{MAX_RETRIES} fallito: {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"GET fallito dopo {MAX_RETRIES} tentativi: {url}")


# ══════════════════════════════════════════════
# RICERCA PRODOTTI S1
# ══════════════════════════════════════════════

def search_s1_products(
    session: CDSESession,
    bbox: tuple,
    date_start: datetime,
    date_end: datetime,
    orbit_dir: str = None,
    platform: str = None,
) -> list[dict]:
    """
    Cerca prodotti S1 IW GRD VV+VH nell'area e nel periodo specificati.
    Gestisce la paginazione OData e recupera gli attributi di orbita.
    platform: "S1A", "S1C" o None/"BOTH" per entrambi.
    Nota: il filtro per satellite viene applicato lato Python sul product_name
    (es. S1A_... o S1C_...) perche' il catalogo CDSE restituisce sempre
    'SENTINEL-1' nel campo platformShortName, senza la lettera finale.
    """
    minx, miny, maxx, maxy = bbox
    footprint = (
        f"POLYGON(({minx} {miny},{maxx} {miny},"
        f"{maxx} {maxy},{minx} {maxy},{minx} {miny}))"
    )

    filters = [
        "Collection/Name eq 'SENTINEL-1'",
        "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
        "and att/OData.CSC.StringAttribute/Value eq 'GRD')",
        "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'operationalMode' "
        "and att/OData.CSC.StringAttribute/Value eq 'IW')",
        "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'polarisationChannels' "
        "and att/OData.CSC.StringAttribute/Value eq 'VV%26VH')",
        f"OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')",
        f"ContentDate/Start ge {date_start.strftime('%Y-%m-%dT00:00:00.000Z')}",
        f"ContentDate/Start le {date_end.strftime('%Y-%m-%dT23:59:59.000Z')}",
    ]

    if orbit_dir and orbit_dir.upper() != "BOTH":
        filters.append(
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'orbitDirection' "
            f"and att/OData.CSC.StringAttribute/Value eq '{orbit_dir.upper()}')"
        )

    # Nota: il filtro satellite NON viene aggiunto alla query OData perche'
    # il catalogo CDSE restituisce sempre 'SENTINEL-1' in platformShortName.
    # Il filtro viene applicato lato Python dopo la ricerca, sul product_name.

    query    = " and ".join(filters)
    all_products = []
    skip = 0

    while True:
        url = (
            f"{ODATA_URL}/Products?$filter={query}"
            f"&$orderby=ContentDate/Start asc"
            f"&$top=50&$skip={skip}&$expand=Attributes"
        )
        r    = session.get(url)
        page = r.json().get("value", [])
        all_products.extend(page)
        if len(page) < 50:
            break
        skip += 50

    return all_products


def extract_attribute(product: dict, attr_name: str) -> str:
    """Estrae il valore di un attributo dal campo Attributes del prodotto OData."""
    for attr in product.get("Attributes", []):
        if attr.get("Name") == attr_name:
            return str(attr.get("Value", "")).strip()
    return "UNK"


def compute_coverage(product: dict, area_geom) -> float:
    """
    Calcola la percentuale di copertura del footprint S1 sull'area di studio.
    Ritorna un valore tra 0.0 e 100.0, oppure -1.0 se il footprint non e' disponibile.
    """
    footprint_raw = product.get("GeoFootprint") or product.get("Footprint")
    if not footprint_raw:
        return -1.0
    try:
        if isinstance(footprint_raw, str):
            footprint_raw = json.loads(footprint_raw)
        s1_geom = shape(footprint_raw)
        intersection = s1_geom.intersection(area_geom)
        if area_geom.area == 0:
            return 0.0
        return round(100.0 * intersection.area / area_geom.area, 2)
    except Exception:
        return -1.0


def parse_product(product: dict, target_date: datetime, area_geom=None) -> dict:
    """Trasforma il dict raw OData in un record pulito."""
    acq_str    = product["ContentDate"]["Start"][:19]
    acq_dt     = datetime.strptime(acq_str, "%Y-%m-%dT%H:%M:%S")
    delta_days = (acq_dt.date() - target_date.date()).days
    coverage   = compute_coverage(product, area_geom) if area_geom is not None else -1.0

    return {
        "product_id":      product["Id"],
        "product_name":    product["Name"],
        "acq_datetime":    acq_str,
        "acq_date":        acq_dt.date().isoformat(),
        "delta_days":      delta_days,
        "orbit_direction": extract_attribute(product, "orbitDirection"),
        "relative_orbit":  extract_attribute(product, "relativeOrbitNumber"),
        "absolute_orbit":  extract_attribute(product, "orbitNumber"),
        "platform":        extract_attribute(product, "platformShortName"),
        "instrument_mode": extract_attribute(product, "operationalMode"),
        "size_mb":         round(product.get("ContentLength", 0) / 1e6, 1),
        "online":          product.get("Online", True),
        "coverage_pct":    coverage,
    }


def pick_closest(records: list[dict]) -> dict | None:
    """Seleziona il prodotto con data di acquisizione piu' vicina alla data S2."""
    if not records:
        return None
    return min(records, key=lambda r: abs(r["delta_days"]))


# ══════════════════════════════════════════════
# CARICAMENTO DATI
# ══════════════════════════════════════════════

def load_csv(csv_path: str, year: int = None) -> pd.DataFrame:
    df = pd.read_csv(csv_path, index_col=0, sep=";", parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df = df.apply(lambda col: col.map(
        lambda v: v if isinstance(v, bool)
        else str(v).strip().lower() in ("true", "1", "yes")
    ))
    if year is not None:
        df = df[df.index.year == year]
        log.info(f"Filtro anno: {year}")
    log.info(f"CSV caricato: {df.shape[0]} date x {df.shape[1]} poligoni")
    log.info(f"Periodo: {df.index.min().date()} -> {df.index.max().date()}")
    return df


def load_area_polygon(gpkg_path: str, layer=None) -> tuple:
    """
    Carica il GeoPackage e restituisce (bbox, area_geom):
      - bbox     : bounding box globale per la ricerca OData
      - area_geom: unione reale dei poligoni per il calcolo copertura %
    """
    gdf = gpd.read_file(gpkg_path, layer=layer).to_crs("EPSG:4326")
    log.info(f"GeoPackage caricato: {len(gdf)} poligoni, CRS -> EPSG:4326")
    area_geom = unary_union(gdf.geometry)
    bbox      = tuple(gdf.total_bounds)
    log.info(
        f"Bbox area di studio: "
        f"lon [{bbox[0]:.5f} -> {bbox[2]:.5f}], "
        f"lat [{bbox[1]:.5f} -> {bbox[3]:.5f}]"
    )
    return bbox, area_geom


# ══════════════════════════════════════════════
# MODALITA' INVENTORY
# ══════════════════════════════════════════════

def run_inventory(
    session: CDSESession,
    df: pd.DataFrame,
    global_bbox: tuple,
    area_geom,
    window_days: int,
    orbit_filter: str,
    platform_filter: str,
) -> pd.DataFrame:
    """
    Esegue la ricerca sul catalogo CDSE e produce i report CSV.
    Restituisce il DataFrame dei prodotti trovati.
    """
    INVENTORY_DIR.mkdir(parents=True, exist_ok=True)

    poly_ids     = list(df.columns)
    cloudy_dates = df.index[df.apply(lambda row: not row.all(), axis=1)]
    log.info(f"Date S2 con almeno una cella nuvolosa: {len(cloudy_dates)}")

    if ALL_DATES:
        search_dates = df.index
        log.info(f"--all_dates attivo: ricerca S1 su tutte le {len(search_dates)} date S2 (incluse quelle senza nuvole)")
    elif CLEAN_ONLY:
        clean_dates = df.index[df.apply(lambda row: row.all(), axis=1)]
        search_dates = clean_dates
        log.info(f"--clean_only attivo: ricerca S1 solo per le {len(search_dates)} date S2 completamente pulite (tutte TRUE)")
    else:
        search_dates = cloudy_dates

    all_products_rows = []
    date_summary_rows = []
    search_cache: dict[str, list[dict]] = {}

    for date_s2 in tqdm(search_dates, desc="Ricerca S1 per data S2", unit="data"):
        d_start   = date_s2 - timedelta(days=window_days)
        d_end     = date_s2 + timedelta(days=window_days)
        cache_key = f"{d_start.date()}|{d_end.date()}"

        if cache_key not in search_cache:
            try:
                raw = search_s1_products(session, global_bbox, d_start, d_end, orbit_dir=orbit_filter, platform=platform_filter)
                # Filtra per satellite sul product_name (es. S1A_... o S1C_...)
                # perche' il campo platformShortName del catalogo CDSE non include la lettera finale
                if platform_filter and platform_filter.upper() != "BOTH":
                    prefix = platform_filter.upper() + "_"
                    raw = [p for p in raw if p.get("Name", "").upper().startswith(prefix)]
                records = [parse_product(p, date_s2, area_geom) for p in raw]
                search_cache[cache_key] = records
            except Exception as e:
                log.error(f"Errore ricerca {date_s2.date()}: {e}")
                search_cache[cache_key] = []

        records = search_cache[cache_key]

        for rec in records:
            all_products_rows.append({**rec, "date_s2": date_s2.date().isoformat()})

        closest    = pick_closest(records)
        rel_orbits = sorted(set(r["relative_orbit"] for r in records if r["relative_orbit"]))
        platforms  = sorted(set(r["platform"] for r in records if r["platform"]))
        n_cloudy   = sum(1 for p in poly_ids if not df.loc[date_s2, p])

        date_summary_rows.append({
            "date_s2":              date_s2.date().isoformat(),
            "n_cloudy_polygons":    n_cloudy,
            "n_s1_found":           len(records),
            "s1_found":             len(records) > 0,
            "closest_s1_date":      closest["acq_date"] if closest else "",
            "closest_delta_days":   closest["delta_days"] if closest else "",
            "closest_product_name": closest["product_name"] if closest else "",
            "closest_orbit_rel":    closest["relative_orbit"] if closest else "",
            "closest_orbit_abs":    closest["absolute_orbit"] if closest else "",
            "closest_orbit_dir":    closest["orbit_direction"] if closest else "",
            "closest_coverage_pct": closest["coverage_pct"] if closest else -1.0,
            "all_relative_orbits":  "; ".join(rel_orbits),
            "n_distinct_orbits":    len(rel_orbits),
            "platforms":            "; ".join(platforms),
        })

        time.sleep(0.3)

    # ── Salvataggio report ──
    df_products = pd.DataFrame(all_products_rows).drop_duplicates(subset=["product_id", "date_s2"])
    if not df_products.empty:
        df_products = df_products.sort_values(["date_s2", "delta_days"])

    products_path = INVENTORY_DIR / "inventory_products.csv"
    df_products.to_csv(products_path, index=False)
    log.info(f"Salvato: {products_path}  ({len(df_products)} righe)")

    df_dates = pd.DataFrame(date_summary_rows)
    dates_path = INVENTORY_DIR / "inventory_dates.csv"
    df_dates.to_csv(dates_path, index=False)
    log.info(f"Salvato: {dates_path}  ({len(df_dates)} righe)")

    if not df_products.empty and "relative_orbit" in df_products.columns:
        orbit_summary = (
            df_products
            .groupby(["relative_orbit", "orbit_direction", "platform"])
            .agg(
                n_products   =("product_id", "nunique"),
                date_min     =("acq_date", "min"),
                date_max     =("acq_date", "max"),
                size_mb_mean =("size_mb", "mean"),
                online_pct   =("online", lambda x: f"{100*x.mean():.0f}%"),
            )
            .reset_index()
            .sort_values("n_products", ascending=False)
        )
        orbits_path = INVENTORY_DIR / "inventory_orbits.csv"
        orbit_summary.to_csv(orbits_path, index=False)
        log.info(f"Salvato: {orbits_path}")
    else:
        orbit_summary = pd.DataFrame()

    # ── Summary testuale ──
    n_with    = int(df_dates["s1_found"].sum()) if not df_dates.empty else 0
    n_without = len(df_dates) - n_with
    n_uniq    = df_products["product_id"].nunique() if not df_products.empty else 0

    lines = [
        "=" * 60,
        "INVENTARIO SENTINEL-1 - RIEPILOGO",
        "=" * 60,
        f"  Finestra temporale        : +/-{window_days} giorni",
        f"  Filtro orbita             : {orbit_filter}",
        f"  Filtro satellite          : {platform_filter}",
        f"  Date S2 analizzate        : {len(cloudy_dates)}",
        f"  Date con S1 trovato       : {n_with}",
        f"  Date SENZA S1 trovato     : {n_without}",
        f"  Prodotti S1 unici totali  : {n_uniq}",
        "",
    ]

    if not orbit_summary.empty:
        lines.append("  Orbite relative trovate:")
        lines.append(f"  {'Orbita':<10} {'Direzione':<12} {'Satellite':<12} {'N prodotti':>10}  {'Dal':<12} {'Al':<12}  {'Online':>8}")
        lines.append("  " + "-" * 70)
        for _, row in orbit_summary.iterrows():
            lines.append(
                f"  {row['relative_orbit']:<10} {row['orbit_direction']:<12} "
                f"{row['platform']:<12} {row['n_products']:>10}  "
                f"{row['date_min']:<12} {row['date_max']:<12}  {row['online_pct']:>8}"
            )

    lines += ["", f"  Report salvati in: {INVENTORY_DIR}", "=" * 60]
    summary_text = "\n".join(lines)
    (INVENTORY_DIR / "inventory_summary.txt").write_text(summary_text, encoding="utf-8")
    print("\n" + summary_text)

    if n_without > 0 and not df_dates.empty:
        missing = df_dates[~df_dates["s1_found"]]["date_s2"].tolist()
        print(f"\n  Attenzione: {n_without} date S2 senza S1 nella finestra +/-{window_days}gg:")
        for d in missing:
            print(f"     - {d}")

    return df_products


# ══════════════════════════════════════════════
# RIEPILOGO PRE-DOWNLOAD E CONFERMA
# ══════════════════════════════════════════════

def pre_download_summary(df_products: pd.DataFrame) -> None:
    """
    Stampa il riepilogo dei prodotti unici da scaricare e chiede conferma.
    """
    unique = df_products.drop_duplicates("product_id")
    total  = len(unique)
    size_gb = unique["size_mb"].sum() / 1024

    orbit_counts = (
        unique.groupby(["relative_orbit", "orbit_direction"])
        .size()
        .reset_index(name="n")
    )

    print("\n" + "=" * 55)
    print("RIEPILOGO PRE-DOWNLOAD")
    print("=" * 55)
    print(f"  Prodotti S1 unici da scaricare : {total}")
    for _, row in orbit_counts.iterrows():
        print(f"  Orbita R{row['relative_orbit']:<6} {row['orbit_direction']:<12} : {row['n']} immagini")
    print(f"  Dimensione totale stimata      : {size_gb:.1f} GB")
    print(f"  Cartella output (.SAFE)        : {OUTPUT_DIR / 'full_scenes'}")
    print("=" * 55)

    if total == 0:
        print("  Nessun prodotto trovato. Controlla i parametri.")
        sys.exit(0)

    answer = input("\nProcedere con il download? [s/N] ").strip().lower()
    if answer not in ("s", "si", "y", "yes"):
        print("Download annullato.")
        sys.exit(0)


# ══════════════════════════════════════════════
# DOWNLOAD
# ══════════════════════════════════════════════

def download_zip(session: CDSESession, product_id: str, dest_dir: Path) -> Path:
    url      = f"{DOWNLOAD_URL}/Products({product_id})/$value"
    zip_path = dest_dir / f"{product_id}.zip"
    if zip_path.exists():
        return zip_path

    # Verifica e rinnova il token prima di ogni download
    session.ensure_token()
    log.debug("Token verificato prima del download.")

    log.info(f"  Downloading {product_id} ...")
    with session.session.get(url, stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(zip_path, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=zip_path.name, leave=False
        ) as pbar:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                pbar.update(len(chunk))
    return zip_path


def extract_safe(zip_path: Path, out_dir: Path) -> Path | None:
    """
    Estrae lo zip CDSE e sposta la cartella .SAFE nella out_dir.
    Restituisce il percorso della cartella .SAFE estratta.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="s1_extract_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp_dir)

    # La cartella .SAFE e' dentro tmp_dir
    safe_dirs = list(tmp_dir.glob("*.SAFE"))
    if not safe_dirs:
        log.error(f"Nessuna cartella .SAFE trovata in {zip_path.name}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    safe_src  = safe_dirs[0]
    safe_dest = out_dir / safe_src.name

    if safe_dest.exists():
        shutil.rmtree(safe_dest)

    shutil.move(str(safe_src), str(safe_dest))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return safe_dest


def run_download(session: CDSESession, df_products: pd.DataFrame):
    """
    Scarica i prodotti unici in formato .SAFE (struttura originale).
    Salta i prodotti gia' presenti su disco.
    Il formato .SAFE e' necessario per il processing con SNAP
    (calibrazione, terrain correction, ecc.).
    Registra nel log il tempo per scena, la velocita' media e il tempo totale.
    """
    scenes_dir = OUTPUT_DIR / "full_scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    unique_products = df_products.drop_duplicates("product_id")
    n_total         = len(unique_products)
    log.info(f"Prodotti unici da scaricare: {n_total}")

    stats = {"found": 0, "skipped": 0, "errors": 0}
    scene_times_s  = []   # tempo in secondi per ogni scena scaricata
    scene_sizes_mb = []   # dimensione in MB per ogni scena scaricata
    start_total    = time.time()

    for i, (_, row) in enumerate(tqdm(unique_products.iterrows(), total=n_total, desc="Download S1", unit="scena"), start=1):
        product_id   = row["product_id"]
        product_name = row["product_name"]

        safe_dest = scenes_dir / f"{product_name}.SAFE"

        if safe_dest.exists():
            log.debug(f"Skip (gia' esistente): {safe_dest.name}")
            stats["skipped"] += 1
            continue

        start_scene = time.time()

        try:
            tmp_dir  = Path(tempfile.mkdtemp(prefix="s1_"))
            zip_path = download_zip(session, product_id, tmp_dir)
            safe_path = extract_safe(zip_path, scenes_dir)
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)

            if safe_path:
                size_mb   = sum(f.stat().st_size for f in safe_path.rglob("*") if f.is_file()) / 1e6
                elapsed_s = time.time() - start_scene
                speed_mbs = size_mb / elapsed_s if elapsed_s > 0 else 0

                scene_times_s.append(elapsed_s)
                scene_sizes_mb.append(size_mb)

                # Stima tempo rimanente
                n_remaining  = n_total - stats["skipped"] - stats["found"] - 1
                avg_time_s   = sum(scene_times_s) / len(scene_times_s)
                eta_s        = avg_time_s * n_remaining
                eta_str      = time.strftime("%H:%M:%S", time.gmtime(eta_s))
                elapsed_str  = time.strftime("%M:%S", time.gmtime(elapsed_s))

                log.info(
                    f"  [{i}/{n_total}] {safe_path.name[:50]}..."
                    f"\n    Dimensione: {size_mb/1024:.2f} GB"
                    f"  |  Tempo scena: {elapsed_str}"
                    f"  |  Velocita': {speed_mbs:.1f} MB/s"
                    f"  |  Tempo rimanente stimato: {eta_str}"
                )
                stats["found"] += 1
            else:
                stats["errors"] += 1

        except Exception as e:
            log.error(f"Errore download {product_id}: {e}")
            stats["errors"] += 1

        time.sleep(SLEEP_BETWEEN)

    # ── Riepilogo finale con tempi ──
    total_elapsed_s  = time.time() - start_total
    total_elapsed_str = time.strftime("%H:%M:%S", time.gmtime(total_elapsed_s))
    total_size_gb    = sum(scene_sizes_mb) / 1024
    avg_speed        = sum(scene_sizes_mb) / total_elapsed_s if total_elapsed_s > 0 else 0

    log.info("=" * 55)
    log.info("RIEPILOGO DOWNLOAD")
    log.info(f"  Scene scaricate (.SAFE) : {stats['found']}")
    log.info(f"  Gia' esistenti          : {stats['skipped']}")
    log.info(f"  Errori                  : {stats['errors']}")
    log.info(f"  Dati scaricati totali   : {total_size_gb:.2f} GB")
    log.info(f"  Tempo totale            : {total_elapsed_str}")
    log.info(f"  Velocita' media         : {avg_speed:.1f} MB/s")
    log.info("=" * 55)


# ══════════════════════════════════════════════
# PIPELINE PRINCIPALE
# ══════════════════════════════════════════════

def run(mode: str, window_days: int):

    if not CDSE_USER or not CDSE_PASSWORD:
        sys.exit(
            "Credenziali CDSE mancanti.\n"
            "Assicurati che le variabili d'ambiente CDSE_USER e CDSE_PASS siano impostate."
        )

    # ── Caricamento dati di base ──
    df              = load_csv(CSV_PATH, year=YEAR)
    global_bbox, area_geom = load_area_polygon(GPKG_PATH, layer=GPKG_LAYER)
    session         = CDSESession(CDSE_USER, CDSE_PASSWORD)

    if mode == "inventory":
        # Solo ricerca, nessun download
        run_inventory(session, df, global_bbox, area_geom, window_days, ORBIT_DIR, PLATFORM)

    elif mode == "download":
        # Legge l'inventory gia' prodotto e scarica
        inv_path = INVENTORY_DIR / "inventory_products.csv"
        if not inv_path.exists():
            sys.exit(
                f"File non trovato: {inv_path}\n"
                f"Esegui prima: python sentinel1_pipeline.py --mode inventory"
            )
        df_products = pd.read_csv(inv_path)
        log.info(f"Inventory caricato: {df_products['product_id'].nunique()} prodotti unici")

        # Filtra per orbita se specificata
        if ORBIT_DIR.upper() != "BOTH":
            df_products = df_products[
                df_products["orbit_direction"].str.upper() == ORBIT_DIR.upper()
            ]
            log.info(f"Filtro orbita {ORBIT_DIR}: {df_products['product_id'].nunique()} prodotti")

        # Filtra per satellite se specificato — usa il product_name (es. S1A_...)
        # perche' la colonna platform del CSV contiene sempre 'SENTINEL-1' senza lettera finale
        if PLATFORM.upper() != "BOTH":
            prefix = PLATFORM.upper() + "_"
            df_products = df_products[
                df_products["product_name"].str.upper().str.startswith(prefix)
            ]
            log.info(f"Filtro satellite {PLATFORM}: {df_products['product_id'].nunique()} prodotti")

        # Filtra per anno se specificato
        if YEAR is not None:
            df_products = df_products[
                pd.to_datetime(df_products["acq_date"]).dt.year == YEAR
            ]
            log.info(f"Filtro anno {YEAR}: {df_products['product_id'].nunique()} prodotti")

        pre_download_summary(df_products)
        run_download(session, df_products)

    elif mode == "full":
        # Inventario + download in sequenza
        df_products = run_inventory(session, df, global_bbox, area_geom, window_days, ORBIT_DIR, PLATFORM)

        # Filtra per orbita se specificata
        if ORBIT_DIR.upper() != "BOTH":
            df_products = df_products[
                df_products["orbit_direction"].str.upper() == ORBIT_DIR.upper()
            ]

        # Filtra per satellite se specificato — usa il product_name (es. S1A_...)
        # perche' la colonna platform del CSV contiene sempre 'SENTINEL-1' senza lettera finale
        if PLATFORM.upper() != "BOTH":
            prefix = PLATFORM.upper() + "_"
            df_products = df_products[
                df_products["product_name"].str.upper().str.startswith(prefix)
            ]

        pre_download_summary(df_products)
        run_download(session, df_products)

    else:
        sys.exit(f"Modalita' non valida: {mode}. Scegli tra: inventory, download, full")


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Sentinel-1 Pipeline: inventario e download via CDSE",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--mode", default="inventory",
        choices=["inventory", "download", "full"],
        help=(
            "Modalita' di esecuzione:\n"
            "  inventory -> solo ricerca, produce i CSV di inventario\n"
            "  download  -> legge l'inventory e scarica le immagini\n"
            "  full      -> inventario + download in sequenza"
        ),
    )
    parser.add_argument("--csv",       default=CSV_PATH,           help="CSV qualita' S2 (separatore ;)")
    parser.add_argument("--gpkg",      default=GPKG_PATH,          help="GeoPackage con i poligoni dell'area di studio")
    parser.add_argument("--layer",     default=None,               help="Layer GeoPackage (default: primo)")
    parser.add_argument("--window",    type=int, default=WINDOW_DAYS, help="Finestra +/-giorni (default: 6)")
    parser.add_argument("--orbit",     default=ORBIT_DIR,          choices=["ASCENDING", "DESCENDING", "BOTH"], help="Direzione orbita")
    parser.add_argument("--platform",  default=PLATFORM,           choices=["S1A", "S1C", "BOTH"],              help="Satellite (default: S1A)")
    parser.add_argument("--year",      type=int, default=None,     help="Anno da analizzare/scaricare (es. 2024)")
    parser.add_argument("--all_dates",  action="store_true",        help="Cerca S1 anche per le date S2 senza nuvole (default: solo date nuvolose)")
    parser.add_argument("--clean_only", action="store_true",        help="Cerca S1 solo per le date S2 completamente pulite (tutte TRUE) — per regressione RVI/NDVI")
    parser.add_argument("--inventory", default=str(INVENTORY_DIR), help="Cartella report inventario")
    parser.add_argument("--outdir",    default=str(OUTPUT_DIR),    help="Cartella output download")
    args = parser.parse_args()

    CSV_PATH      = args.csv
    GPKG_PATH     = args.gpkg
    GPKG_LAYER    = args.layer
    ORBIT_DIR     = args.orbit
    PLATFORM      = args.platform
    YEAR          = args.year
    ALL_DATES     = args.all_dates
    CLEAN_ONLY    = args.clean_only
    INVENTORY_DIR = Path(args.inventory)
    OUTPUT_DIR    = Path(args.outdir)

    run(mode=args.mode, window_days=args.window)
