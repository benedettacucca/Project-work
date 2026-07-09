# Monitoraggio delle pratiche di agricoltura rigenerativa mediante dati Sentinel-1 e Sentinel-2 e modelli di Machine Learning

Codice a supporto del *project work*.

Il lavoro integra dati **radar (Sentinel-1)** e **ottici (Sentinel-2)** per ricostruire serie
temporali NDVI continue: la copertura nuvolosa rende l'NDVI non osservabile in molte date, e il
radar — che attraversa le nuvole — viene usato per stimarne il valore dove l'ottico non è
disponibile. La permanenza della copertura vegetale del suolo così ricostruita è l'indicatore
con cui si osservano le pratiche di agricoltura rigenerativa.

---

## Dati

| Elemento | Valore |
|---|---|
| Poligoni (campi) | 154 — `P001`…`P154`, EPSG:32632 |
| Periodo | 2023–2025 |
| Date Sentinel-2 | 241 (di cui **167** con almeno un NDVI valido) |
| Date Sentinel-1 (RVI) | **111** |
| Coppie RVI–NDVI per la calibrazione | 19.788 |

---

## Pipeline

Gli script vanno eseguiti nell'ordine seguente. Ogni voce rimanda al proprio README con
parametri, input/output e logica di funzionamento.

### 1. Produzione del dato

| # | Script | Cosa fa |
|---|---|---|
| 1 | [`sentinel1_pipeline.py`](README_sentinel1_pipeline.md) | inventario e download delle scene S1 dal Copernicus Data Space Ecosystem |
| 2 | [`sentinel1_processing_rvi.py`](README_sentinel1_processing_rvi.md) | catena SNAP (orbita, rumore termico, calibrazione, speckle, terrain correction) e calcolo dell'RVI |
| 3 | [`extract_mean_rvi_per_polygon.py`](README_extract_mean_rvi_per_polygon.md) | statistica zonale: RVI medio per poligono e per data |
| 4 | [`merge_rvi_master.py`](README_merge_rvi_master.md) | ricompone le due estrazioni RVI in un'unica serie (il *master*, 111 date) |
| — | [`Pipeline_Sentinel2.py`](README_Pipeline_Sentinel2.md) | ramo ottico: acquisizione S2, *cloud mask*, NDVI medio per poligono |

### 2. Analisi

| # | Script | Cosa fa |
|---|---|---|
| 5 | [`correlazione_rvi_ndvi.py`](README_correlazione_rvi_ndvi.md) | accoppiamento temporale (±6 giorni), correlazione e regressione RVI→NDVI per poligono |

### 3. Validazione

| # | Script | Cosa fa |
|---|---|---|
| 6 | [`validate_correlazione_rvi_ndvi_blocchi.py`](README_validate_correlazione_rvi_ndvi_blocchi.md) | cross-validation a blocchi temporali; definisce il criterio *collegato/scollegato* |
| 7 | [`rolling_origin_rvi_ndvi.py`](README_rolling_origin_rvi_ndvi.md) | validazione "solo futuro" con IC bootstrap: conferma l'R² fuori campione |
| 8 | [`benchmark_C_rvi_vs_interpolazione.py`](README_benchmark_C_rvi_vs_interpolazione.md) | radar vs interpolazione: determina la soglia di *crossover* (~35 giorni) |
| 9 | [`benchmark_C_rolling_origin.py`](README_benchmark_C_rolling_origin.md) | stress test della soglia, con stratificazione stagionale e IC bootstrap |

### 4. Applicazione

| # | Script | Cosa fa |
|---|---|---|
| 10 | [`gapfilling_ndvi.py`](README_gapfilling_ndvi.md) | riempie i buchi reali e produce la serie NDVI continua, tracciando la sorgente di ogni valore |

---

## Metodo in breve

1. **RVI** (*Radar Vegetation Index*) `= 4 · σ°VH / (σ°VV + σ°VH)`, da scene Sentinel-1 GRD in orbita ascendente.
2. **Accoppiamento temporale:** a ogni osservazione NDVI si associa l'RVI più vicino entro **±6 giorni** (tempo di rivisita della costellazione).
3. **Regressione per poligono:** `NDVI = a + b · RVI`, una retta per ciascun campo — un modello di regressione lineare, inquadrabile come approccio di apprendimento supervisionato: i coefficienti sono appresi da coppie di addestramento e usati per predire su dati nuovi. Un poligono è *collegato* se `b > 0` e R² ≥ 0,2; gli altri (risaie allagate, campi a bassa correlazione) sono esclusi dalla stima radar.
4. **Gap-filling a cascata**, per ogni cella poligono–data, applicando la prima regola che ricorre:
   1. NDVI osservato → valore reale
   2. poligono scollegato → interpolazione, marcata inaffidabile
   3. lacuna ≤ 35 giorni → interpolazione lineare temporale
   4. lacuna > 35 giorni → regressione RVI→NDVI

La soglia di 35 giorni non è arbitraria: è il punto in cui, nei test, la stima radar diventa più
accurata dell'interpolazione temporale.

---

## Output principale

`ndvi_gapfilled_long.csv` — serie NDVI continua per i 154 poligoni sulle 241 date S2. Ogni cella
riporta, oltre al valore, la **sorgente** da cui è stata prodotta:

| Sorgente | Quota |
|---|---|
| `osservato` | 53,3 % |
| `interpolato` | 31,5 % |
| `interpolato_inaffidabile` | 12,7 % |
| `radar` | 1,8 % |
| `non_stimabile` | 0,7 % |

La tracciabilità della sorgente è parte del prodotto: consente di pesare o escludere le celle
meno affidabili invece di trattare tutti i valori come equivalenti.

---

## Requisiti

```bash
pip install numpy pandas geopandas rasterio matplotlib
```

Per il pre-processing SAR serve inoltre **ESA SNAP** con il *Graph Processing Tool* (`gpt`)
accessibile da riga di comando. Il grafo di elaborazione è in `s1_processing_graph.xml`.

---

## Struttura del repository

```
├── CSV/                        matrici RVI/NDVI e tabelle di output
│   └── Valid_date_S2.csv       matrice booleana di copertura nuvolosa
│                               (date × poligoni): da qui è partita la ricerca
│                               delle scene Sentinel-1. NON è la fonte dei
│                               conteggi di date NDVI valide, che derivano
│                               da mean_ndvi_per_polygon.csv
├── Vettoriali/                 poligoni dei campi (GeoPackage, EPSG:32632)
├── s1_processing_graph.xml     grafo SNAP per il pre-processing S1
├── *.py                        script della pipeline
└── README_*.md                 documentazione di ciascuno script
```
