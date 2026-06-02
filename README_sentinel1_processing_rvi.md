# sentinel1_processing_rvi.py

Script Python unificato per il processing di immagini **Sentinel-1 SAR** tramite SNAP GPT e il calcolo del **Radar Vegetation Index (RVI)**.

---

## Panoramica

Lo script esegue una pipeline in due fasi consecutive:

**Fase 1 — Processing SNAP GPT** (`.SAFE` → `.dim`)
Lancia il grafo SNAP GPT su tutte le scene Sentinel-1 trovate nella cartella di input, producendo file BEAM-DIMAP calibrati, filtrati e corretti geometricamente, ritagliati sulla bounding box dell'AOI.

**Fase 2 — Calcolo RVI** (`.dim` → `.tif`)
Legge le bande `Sigma0_VV` e `Sigma0_VH` dai file prodotti nella Fase 1, calcola il Radar Vegetation Index e ritaglia le immagini sui poligoni AOI.

---

## Catena di processing SNAP

| Step | Operazione | Descrizione |
|------|-----------|-------------|
| 1 | Apply Orbit File | Corregge i metadati orbitali |
| 2 | Thermal Noise Removal | Rimuove il rumore termico strumentale (VV e VH) |
| 3 | Calibration (Sigma0) | Converte DN in backscatter lineare |
| 4 | Speckle Filter (Lee Sigma) | Riduce il rumore tipico delle immagini SAR |
| 5 | Terrain Correction (RD) | Geolocalizzazione + correzione DEM (SRTM 1") |
| 6 | Subset AOI (bbox) | Ritaglia sulla bounding box dell'AOI |
| 7 | Output BEAM-DIMAP | Salva con bande `Sigma0_VV` e `Sigma0_VH` |

---

## Formula RVI

```
RVI = 4 * VH / (VV + VH)
```

I valori sono in scala lineare (Sigma0). L'RVI è teoricamente ≥ 0, con valori prossimi a 0 in condizioni di suolo nudo e valori più elevati in presenza di vegetazione densa; può superare 1.

---

## Struttura cartelle attesa

```
progetto/
├── output_sentinel1/full_scenes/       # Input Fase 1
│   └── S1A_IW_GRDH_....SAFE/
├── inventory_output/
│   └── inventory_dates.csv             # Output di sentinel1_pipeline.py
├── processed_sentinel1/                # Output Fase 1 / Input Fase 2 (creata automaticamente)
│   ├── S1A_IW_GRDH_....dim
│   └── S1A_IW_GRDH_....data/
│       ├── Sigma0_VV.img
│       └── Sigma0_VH.img
├── rvi_sentinel1/                      # Output Fase 2 (creata automaticamente)
│   ├── S1A_IW_GRDH_..._RVI.tif
│   └── sostituzioni_rvi.csv
├── AOI_JOLANDA_SELECTION.gpkg
├── Valid_date_S2.csv
└── s1_processing_graph.xml
```

---

## Requisiti

### Python

```bash
pip install geopandas shapely rasterio numpy pandas
```

### Software

- **SNAP** con GPT accessibile dal PATH (o specificare `--gpt`)
  - Non necessario se si usa `--only_rvi`
- **SRTM 1Sec HGT**: scaricato automaticamente da SNAP al primo utilizzo (~140 MB)

---

## Utilizzo

### Comportamento predefinito (entrambe le fasi)

```bash
python sentinel1_processing_rvi.py
```

### Solo RVI — scene closest S1, poligoni FALSE

```bash
python sentinel1_processing_rvi.py --only_rvi
```

### Solo RVI — scene closest S1, tutti i poligoni AOI

```bash
python sentinel1_processing_rvi.py --only_rvi --all_polygons
```

### Solo RVI — tutte le scene `.dim`, tutti i poligoni AOI

```bash
python sentinel1_processing_rvi.py --only_rvi --ignore_inventory
```

---

## Opzioni da riga di comando

| Opzione | Default | Descrizione |
|---------|---------|-------------|
| `--scenes_dir` | `output_sentinel1/full_scenes` | Cartella con i file `.SAFE` |
| `--snap_dir` | `processed_sentinel1` | Cartella output SNAP (`.dim`) |
| `--rvi_dir` | `rvi_sentinel1` | Cartella output RVI (`.tif`) |
| `--graph` | `s1_processing_graph.xml` | Grafo SNAP GPT XML |
| `--gpkg` | `AOI_JOLANDA_SELECTION.gpkg` | GeoPackage AOI |
| `--csv` | `Valid_date_S2.csv` | CSV qualità S2 (separatore `;`) |
| `--inventory` | `inventory_output/inventory_dates.csv` | CSV inventario S1 |
| `--layer` | *(primo layer)* | Layer GeoPackage |
| `--gpt` | `gpt` | Percorso eseguibile GPT |
| `--workers` | `1` | Scene da processare in parallelo |
| `--tile_cache` | `10240` | RAM allocata a SNAP in MB |
| `--nodata` | `-9999` | Valore nodata nei GeoTIFF RVI |
| `--all_polygons` | `False` | Calcola RVI su tutti i poligoni (default: solo poligoni FALSE) |
| `--only_rvi` | `False` | Salta la Fase 1 (SNAP), esegue solo la Fase 2 (RVI) |
| `--ignore_inventory` | `False` | Ignora l'inventario S1: processa tutti i `.dim` presenti in `snap_dir` |

---

## Modalità di calcolo RVI

| Modalità | Scene processate | Poligoni | Output `sostituzioni_rvi.csv` |
|----------|-----------------|----------|-------------------------------|
| Default | Solo scene closest S1 | Solo poligoni FALSE | ✅ Sì |
| `--all_polygons` | Solo scene closest S1 | Tutti i poligoni AOI | ❌ No |
| `--ignore_inventory` | Tutte le scene `.dim` | Tutti i poligoni AOI | ❌ No |

> **Nota:** La combinazione *tutte le scene + poligoni FALSE* non è supportata senza inventario, poiché non è possibile associare i poligoni FALSE a ogni scena S1.

---

## File di input

### `inventory_dates.csv`
Prodotto da `sentinel1_pipeline.py`. Contiene le colonne:
- `date_s2` — data dell'immagine Sentinel-2
- `closest_product_name` — nome del file `.SAFE` più vicino temporalmente
- `closest_delta_days` — delta in giorni tra la data S2 e la scena S1

Le righe con `closest_delta_days` mancante (`NaT`) vengono saltate.

### `Valid_date_S2.csv`
CSV con separatore `;`. La prima colonna è la data S2 (indice), le colonne successive sono i poligoni (`P001`, `P002`, ...) con valori booleani (`True`/`False`) che indicano la qualità dell'immagine S2.

---

## Output

| File | Descrizione |
|------|-------------|
| `*_RVI.tif` | GeoTIFF float32, compresso (DEFLATE), con tag metadata (formula, data, modalità) |
| `sostituzioni_rvi.csv` | Log delle sostituzioni S2→S1 per poligono (solo in modalità default) |
| `sentinel1_processing_rvi.log` | Log completo dell'esecuzione (scritto anche a schermo) |

---

## Note tecniche

- Le scene già processate in entrambe le fasi vengono saltate automaticamente (skip idempotente).
- Il grafo XML deve essere configurato con `outputImageScaleDb=false` (valori lineari, necessari per il calcolo RVI).
- In modalità parallela (`--workers > 1`), la RAM necessaria è circa `workers × tile_cache MB`.
- I GeoTIFF RVI sono salvati con compressione DEFLATE, tiling 256×256 e predictor 3 (ottimizzato per dati float).
