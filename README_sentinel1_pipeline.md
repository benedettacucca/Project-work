# sentinel1_pipeline.py

Inventario e download di immagini **Sentinel-1** via Copernicus Data Space Ecosystem (CDSE).

Script unificato che integra ricerca sul catalogo e download in un unico workflow configurabile tramite la modalità di esecuzione (`--mode`).

---

## Contesto e obiettivo

Le immagini Sentinel-2 sono soggette a copertura nuvolosa. Quando una data S2 non è utilizzabile per uno o più poligoni dell'area di studio, viene sostituita con un'immagine Sentinel-1. Essendo un sensore radar (SAR), Sentinel-1 acquisisce indipendentemente dalle condizioni atmosferiche.

Lo script permette anche di scaricare S1 per le sole date S2 completamente pulite (nessuna nuvola), tramite il flag `--clean_only`.

---

## Modalità di esecuzione

| Modalità | Descrizione |
|----------|-------------|
| `--mode inventory` | Solo ricerca sul catalogo CDSE, nessun download. Produce 3 CSV + 1 file di riepilogo. Utile per esplorare la disponibilità e scegliere orbita e satellite prima di avviare il download. |
| `--mode download` | Legge l'`inventory_products.csv` già prodotto e scarica i prodotti unici. Non rifà le ricerche sul catalogo → più veloce. Mostra un riepilogo pre-download (n. immagini, orbite, GB stimati) e chiede conferma prima di procedere. |
| `--mode full` | Esegue inventario + download in sequenza senza passare per i file CSV intermedi. |

---

## Prodotti cercati

Sentinel-1 **IW GRD** con polarizzazione **VV+VH**.

La polarizzazione doppia è necessaria per il calcolo dell'indice:

```
RVI (Radar Vegetation Index) = 4 × VH / (VV + VH)
```

---

## File di input

| File | Descrizione |
|------|-------------|
| CSV qualità S2 | Separatore `;`, date in formato ISO (es. `2024-01-03T00:00:00Z`), valori `True`/`False` per poligono |
| GeoPackage | Poligoni dell'area di studio. La bbox globale viene usata per la ricerca sul catalogo; l'unione reale dei poligoni per il calcolo della copertura %. |

---

## File di output

### Modalità `inventory` e `full`

```
inventory_output/
├── inventory_products.csv   # un prodotto S1 per riga: data, orbita relativa/assoluta,
│                            # direzione, satellite, dimensione MB, disponibilità online,
│                            # copertura % sull'area di studio
├── inventory_dates.csv      # una riga per data S2 cercata: n. prodotti trovati,
│                            # prodotto più vicino, delta giorni, copertura %,
│                            # orbite disponibili nella finestra
├── inventory_orbits.csv     # riepilogo per orbita: n. prodotti, periodo, % online
└── inventory_summary.txt    # riepilogo testuale (stampato anche a schermo)
```

### Modalità `download` e `full`

```
output_sentinel1/
└── full_scenes/
    └── <product_name>.SAFE/   # struttura originale ESA, pronta per SNAP
```

Le scene vengono scaricate in formato `.SAFE`, che è il formato richiesto da SNAP per il processing (calibrazione, terrain correction, ecc.). Il ritaglio sui singoli poligoni avviene dopo il processing.

---

## Opzioni da riga di comando

```
python sentinel1_pipeline.py --mode inventory [opzioni]
python sentinel1_pipeline.py --mode download  [opzioni]
python sentinel1_pipeline.py --mode full      [opzioni]
```

| Opzione | Default | Descrizione |
|---------|---------|-------------|
| `--csv` | `Valid_date_S2.csv` | CSV qualità S2 (separatore `;`) |
| `--gpkg` | `AOI_JOLANDA_SELECTION.gpkg` | GeoPackage con i poligoni dell'area |
| `--layer` | `None` | Layer del GeoPackage (None = primo layer) |
| `--window` | `6` | Finestra ±giorni per la ricerca S1 intorno a ogni data S2 |
| `--orbit` | `BOTH` | Direzione orbitale: `ASCENDING` \| `DESCENDING` \| `BOTH` |
| `--platform` | `S1A` | Satellite: `S1A` \| `S1C` \| `BOTH` |
| `--year` | `None` | Limita l'analisi/download a un singolo anno (es. `2024`) |
| `--all_dates` | `False` | Cerca S1 per **tutte** le date S2, incluse quelle senza nuvole |
| `--clean_only` | `False` | Cerca S1 **solo** per le date S2 completamente pulite (tutte TRUE) |
| `--inventory` | `inventory_output` | Cartella di output per i report dell'inventario |
| `--outdir` | `output_sentinel1` | Cartella di output per le scene scaricate (`.SAFE`) |

> **Nota:** `--all_dates` e `--clean_only` sono mutualmente esclusivi. Se non viene specificato nessuno dei due, lo script opera nella modalità di default: cerca S1 solo per le date con **almeno un poligono nuvoloso** (almeno un `False` nel CSV).

---

## Logica di selezione delle date

```
Default (nessun flag)   →  date con almeno un False  (sostituisce S2 nuvolosa con S1)
--all_dates             →  tutte le date S2           (inventario completo)
--clean_only            →  solo date tutte True        (date S2 completamente pulite)
```

---

## Note tecniche

### Credenziali CDSE
Le credenziali vengono lette dalle variabili d'ambiente `CDSE_USER` e `CDSE_PASS`. La registrazione è gratuita su [dataspace.copernicus.eu](https://dataspace.copernicus.eu).

### Ripresa download
Le scene già scaricate vengono saltate automaticamente: il download è riprendibile in caso di interruzione senza duplicati.

### Log
Durante il download vengono registrati nel file `sentinel1_pipeline.log`: tempo per singola scena, velocità in MB/s, stima del tempo rimanente e riepilogo finale con tempo totale e GB scaricati.

### Filtro satellite
Il catalogo CDSE restituisce sempre `SENTINEL-1` nel campo `platformShortName`, senza la lettera finale (A o C). Il filtro per `--platform` viene quindi applicato lato Python sul nome del prodotto, che inizia sempre con `S1A_` o `S1C_`.

### Cronologia costellazione Sentinel-1

| Satellite | Periodo |
|-----------|---------|
| S1-A | Operativo dal 2014 |
| S1-B | Operativo 2016–2021 (guasto hardware) |
| S1-C | Operativo da maggio 2025 |
| S1-D | Operativo da maggio 2026 (tandem con S1-C) |

Per time series 2023–2025 si raccomanda `--platform BOTH` per includere sia S1-A (2023–inizio 2025) che S1-C (da maggio 2025 in poi).

### Rivisita Sentinel-1
- ~12 giorni con un solo satellite attivo
- ~6 giorni con due satelliti sfasati sulla stessa orbita (configurazione tandem)

Per una time series agricola coerente, usare sempre la stessa direzione orbitale (`ASCENDING` o `DESCENDING`) e lo stesso satellite.

---

## Requisiti

- `requests`
- `geopandas`
- `pandas`
- `shapely`
- `tqdm`
- `python-dotenv`

```bash
pip install requests geopandas pandas shapely tqdm python-dotenv
```

---

## Esempi d'uso

```bash
# Inventario per il 2024, solo orbite ascendenti
python sentinel1_pipeline.py --mode inventory --year 2024 --orbit ASCENDING

# Download delle scene già inventariate, solo S1A
python sentinel1_pipeline.py --mode download --platform S1A

# Inventario + download completo in un lancio
python sentinel1_pipeline.py --mode full --year 2024

# Inventario per le date S2 senza nuvole
python sentinel1_pipeline.py --mode inventory --clean_only

# Inventario su tutte le date S2
python sentinel1_pipeline.py --mode inventory --all_dates
```
