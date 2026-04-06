# Sentinel-2 Analytics Pipeline

Pipeline di analisi remote sensing per il download, cloud masking, calcolo NDVI e validazione temporale di immagini Sentinel-2 L2A attraverso OpenEO.

---

## 📋 Descrizione Progetto

Questo progetto automatizza l'acquisizione e l'analisi di dati Sentinel-2 multispettrali da Copernicus Data Space Ecosystem per aree di studio definite (AOI). Il flusso di lavoro:

1. **Autentica** su OpenEO tramite OIDC
2. **Scarica** bande B04 (rosso), B08 (infrarosso vicino) e SCL (classificazione nuvole)
3. **Applica cloud mask** ritenendo solo pixel con SCL valido (classi 4-7: vegetazione, suolo, acqua, non classificato)
4. **Calcola NDVI** (Normalized Difference Vegetation Index) su pixel cloud-free
5. **Valida temporalmente** ogni data per ogni poligono (soglia: ≥70% pixel validi)
6. **Esporta** immagini singola-banda (B04 e B08 separati) già mascherati + statistiche CSV

### Perché queste librerie?

| Libreria | Ruolo |
|----------|-------|
| **openeo** | Client ufficiale OpenEO per accesso a Copernicus; scambia con API remota tramite processamento distribuito |
| **geopandas** | Gestione dataset geospaziali (shapefile, GeoJSON) e reprojection; mantiene geometrie + metadati |
| **pandas** | Elaborazione tabelle (CSV, JSON), aggregazioni statistiche, pivot multidimensionali |
| **json** | Parsing/serializzazione output OpenEO (pixel counts, NDVI statistics) |
| **pathlib** | Gestione percorsi cross-platform; fallback filenames su lock files in Windows |

---

## 🏗️ Struttura Codice

### Blocchi Principali

#### **1. Autenticazione & Configurazione** (linee 1-60)
- Connessione a OpenEO Copernicus
- Autenticazione OIDC (single sign-on)
- Parametri dataset: temporal extent, cloud thresholds, alberi SCL validi
- Percorsi input/output e retry policy

#### **2. Funzioni Ausiliarie** (linee 62-165)
- `download_with_retry()` — Download robusti con exponential backoff (6s, 12s, 18s, 24s, 30s)
- `write_csv_with_fallback()` / `write_geojson_with_fallback()` — Salvataggio resiliente su lock Windows
- `apply_cloud_mask()` — Filtraggio SCL (classes < 4 or > 7 → masked)
- `calculate_ndvi()` — NDVI = (B08 - B04) / (B08 + B04)
- `load_aoi()` — Carica poligoni, assegna ID sintetici (P001-P154), salva metadata
- `count_pixels()` — Aggregazione spaziale per conteggio pixel totali/validi

#### **3. Download Immagini Cloud-Masked** (linee 168-210)
- `download_valid_b04_b08_masked_from_csv()` — Lettura CSV di validità, filtro date, download B04 e B08 **separatamente** (due file TIFF per data)
- Aplica SCL mask prima del download
- Retentive naming su errori transitori

#### **4. Flusso Principale** (linee 212+)
1. Carica AOI (GeoJSON)
2. Inizializza cubo Sentinel-2 (B04, B08, SCL) per intervallo temporale
3. Cloud mask + NDVI
4. Conteggio pixel pre/post-mask
5. Parsing JSON → DataFrame validità (True = ≥70% pixel)
6. Download immagini valide (B04_* e B08_* separati)
7. Statistiche a livello pixel e data
8. Export NDVI medio per poligono×data

---

## 📦 Dipendenze

### Installazione

```bash
# Crea ambiente virtuale (raccomandato)
python -m venv venv
source venv/Scripts/activate  # Windows: venv\Scripts\activate.ps1

# Installa pacchetti
pip install openeo geopandas pandas numpy

# Verifica installazione
python -c "import openeo; print(openeo.__version__)"
```

Versioni testate:
- **Python**: 3.9+
- **openeo**: 0.18+
- **geopandas**: 0.12+
- **pandas**: 1.5+

### Dipendenze Esterne
- Accesso OIDC Copernicus (registrarsi su https://dataspace.copernicus.eu/)
- Connessione internet stabile (retry automatici ogni 6-30s)

---

## 🚀 Come Usare

### Prerequisiti

1. **File AOI**: Posiziona `AOI.geojson` in `C:/Users/b.cucca/Desktop/Codice_Master/`
   - Formato: GeoJSON con poligoni (Feature Collection)
   - CRS: consigliato EPSG:4326 (lat/lon)
   - Supporta qualsiasi numero di poligoni

2. **Credenziali OpenEO**: Al primo run, il browser aprirà una pagina di login OIDC
   - Accedi con credenziali Copernicus Dataspace
   - Autorizza accesso OpenEO
   - Token cachato automaticamente

### Esecuzione

```bash
# Attiva ambiente virtuale
python venv/Scripts/activate

# Esegui pipeline
python c:/Users/b.cucca/Desktop/Codice_Master/test1.py
```

**Tempo stimato**: 
- Query metadati (pixel counts): 30-60 min
- Download immagini: 10-30 min per 100 date valide (parallelizzabile)
- Statistiche: 1-2 min

### Configurazione Personalizzata

Modifica nel file test1.py:

```python
# Intervalli temporali
TEMPORAL_START = "2023-01-01"
TEMPORAL_END = "2025-12-31"

# Soglia cloud
MAX_CLOUD_PROBABILITY = 30  # 30% = 70% pixel validi minimi

# SCL filter (Scene Classification Layer)
MIN_VALID_SCL = 4  # Vegetazione
MAX_VALID_SCL = 7  # Non classificato

# Retry policy
MAX_RETRIES = 5  # Aumenta per reti instabili
BASE_WAIT_S = 6  # Secondi di attesa iniziale
```

---

## 📊 Output

### Struttura Directory

```
C:/Users/b.cucca/Desktop/Codice_Master/output/
├── aoi_attributi.csv                          # ID originali ↔ ID sintetici (P001-P154)
├── aoi_with_id.geojson                        # Geometrie + poly_id
├── valid_images_per_polygon.csv                # Matrice data × poligono (True/False)
├── valid_pixels_long.csv                       # Pixel counts per poligono×data (long format)
├── valid_pixels_per_polygon.csv                # Percentuale pixel validi (wide format)
├── valid_dates_summary.csv                     # Conteggio date valide/totali per poligono
├── mean_ndvi_per_polygon.csv                   # NDVI medio per poligono×data
├── mean_ndvi_per_polygon.json                  # NDVI raw (OpenEO output)
├── total_count_pre_mask_per_polygon.json       # Pixel totali (prima cloud mask)
├── valid_count_post_mask_per_polygon.json      # Pixel validi (dopo mask SCL)
└── sentinel_valid_images/                      # Immagini cloud-masked
    ├── sentinel2_valid_masked_B04_20230128.tif
    ├── sentinel2_valid_masked_B08_20230128.tif
    ├── sentinel2_valid_masked_B04_20230203.tif
    ├── sentinel2_valid_masked_B08_20230203.tif
    └── ...
```

### Descrizione File Output

#### CSV Metadata
- **aoi_attributi.csv**: 2 colonne (field ID originale, poly_id sintetico P001-P154)
- **valid_images_per_polygon.csv**: Date (righe) × Poligoni (colonne); True = data con ≥70% pixel validi
- **valid_pixels_long.csv**: 6 colonne (date, poly_id, total_pixels, valid_pixels, valid_fraction, is_valid)
- **valid_dates_summary.csv**: 5 colonne (poly_id, total_dates, valid_dates, invalid_dates, valid_ratio)

#### TIFF Immagini
- **B04_YYYYMMDD.tif**: Banda rossa (riflettanza, 0-5000 scale ÷10000, cloud-masked)
- **B08_YYYYMMDD.tif**: Banda NIR (riflettanza, 0-5000 scale ÷10000, cloud-masked)
- No data value: 0 (pixel mascherati = nuvole)
- Coordinate: EPSG:4326 (lat/lon)
- Risoluzione: 10m × 10m (Sentinel-2 L2A nativa)

#### JSON Statistiche
- **mean_ndvi_per_polygon.json**: Struttura `{date: [NDVI_P001, NDVI_P002, ...], ...}`
- **total_count_*.json** / **valid_count_*.json**: Struttura `{date: [[count_P001, ...], ...], ...}`

### Interpretazione Validità

```
is_valid = True  ⟺  (valid_pixels / total_pixels) ≥ 0.70  (70% soglia)
```

Significa: immagine per quel poligono/data è utilizzabile (≤30% nuvole secondo SCL).

---

## 🔧 Troubleshooting

### Problema: Browser non si apre per OIDC
**Soluzione**: Accedi manualmente a https://openeo.dataspace.copernicus.eu e segui il prompt terminale

### Problema: "PermissionError" su CSV
**Soluzione**: Chi ha aperto il file in Excel? Chiudi il file. Script crea fallback `_locked_*.csv` se bloccato.

### Problema: "Connection reset by peer"
**Soluzione**: Rete instabile. Aumenta `MAX_RETRIES = 10` e `BASE_WAIT_S = 10` oppure esegui di notte.

### Problema: Immagini scaricate ma valor tutti 0
**Controllo**: Verifica `valid_pixels_long.csv` → Se `is_valid=False`, data era tutta nuvolosa. Se `is_valid=True`, contatta supporto OpenEO.

---

## 📈 Performance & Limiti

| Metrica | Valore |
|---------|--------|
| Poligoni AOI supportati | Illimitati (testato 154) |
| Intervallo temporale max | 3+ anni (suggerito ≤2 anni per performance) |
| Risoluzione spaziale | 10 m × 10 m (B04, B08) / 20 m × 20 m (SCL) |
| Banda Sentinel-2 | L2A bottom-of-atmosphere (BOA) radiometric |
| Retry automatici | 5 tentativi con backoff (6, 12, 18, 24, 30s) |

---

## 📝 License & Credits

- **Data**: Copernicus Sentinel-2 by ESA (https://sentinel.esa.int/)
- **Platform**: OpenEO by Open Data Cube (https://openeo.org/)
- **Source**: Modulo personalizzato Python 3.9+

---

