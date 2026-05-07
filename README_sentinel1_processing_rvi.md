# sentinel1_processing_rvi.py

*Documentazione tecnica dello script Python*

---

## 1. Descrizione generale

Script Python unificato per il processing di immagini radar satellitari Sentinel-1 (SAR). Esegue in sequenza due fasi: prima elabora le scene grezze con il software SNAP GPT producendo immagini calibrate e corrette geometricamente, poi calcola il Radar Vegetation Index (RVI) ritagliando i risultati sui poligoni dell'area di interesse (AOI).

| Campo | Valore |
|-------|--------|
| **File** | sentinel1_processing_rvi.py |
| **Righe totali** | 786 |
| **Formato output** | GeoTIFF (.tif) compresso, float32, tiling 256×256 |

---

## 2. Librerie utilizzate

Lo script utilizza esclusivamente librerie Python standard o installabili via pip. Non è richiesta alcuna dipendenza commerciale ad eccezione di SNAP GPT (software ESA gratuito).

| Libreria | Versione | Uso nello script | Righe |
|----------|----------|------------------|-------|
| os | stdlib | Lettura variabili di ambiente (USERNAME, cpu_count) | 85, 197, 269 |
| re | stdlib | Regex per estrarre la data dal nome del file .SAFE/.dim | 86, 139 |
| sys | stdlib | Uscita controllata (sys.exit) in caso di errori critici | 87 |
| time | stdlib | Misurazione tempi di elaborazione e formattazione HH:MM:SS | 88 |
| shutil | stdlib | Ricerca di GPT nel PATH di sistema (shutil.which) | 89, 189 |
| logging | stdlib | Logging su schermo e su file .log con timestamp | 90, 122-130 |
| argparse | stdlib | Parsing degli argomenti da riga di comando | 91, 751-784 |
| subprocess | stdlib | Lancio del processo SNAP GPT e lettura output in tempo reale | 92, 274 |
| warnings | stdlib | Soppressione dei warning rasterio durante il mascheramento | 93, 549 |
| pathlib.Path | stdlib | Gestione portabile dei percorsi file su tutti i sistemi operativi | 94 |
| concurrent.futures | stdlib | Esecuzione parallela delle scene con ThreadPoolExecutor | 95 |
| numpy | pip | Calcolo RVI, mascheramento pixel, operazioni array float32 | 97, 554-564 |
| pandas | pip | Lettura CSV qualità S2 e inventory_dates.csv | 98, 389, 408, 418 |
| geopandas | pip | Lettura GeoPackage AOI e riproiezione tra CRS | 99, 148, 543 |
| rasterio | pip | Lettura bande .img, mascheramento con poligoni, scrittura GeoTIFF | 100-101, 538-590 |
| shapely.ops | pip | Unione dei poligoni AOI (unary_union) per calcolo bbox WKT | 102, 214 |

**Installazione:**

```bash
pip install geopandas shapely rasterio numpy pandas
```

**Requisito esterno:** SNAP GPT (ESA) installato e accessibile da PATH, oppure percorso specificato con `--gpt`.

---

## 3. Configurazione default (righe 108–119)

Le costanti definite in cima allo script rappresentano i valori di default per tutti i parametri. Possono essere sovrascritte da riga di comando senza modificare il codice.

| Costante | Valore default | Descrizione | Riga |
|----------|---------------|-------------|------|
| SCENES_DIR | output_sentinel1/full_scenes | Cartella contenente le scene .SAFE di input | 108 |
| SNAP_DIR | processed_sentinel1 | Cartella output fase 1 (.dim) e input fase 2 | 109 |
| RVI_DIR | rvi_sentinel1 | Cartella output fase 2 (GeoTIFF RVI) | 110 |
| GRAPH_PATH | s1_processing_graph.xml | Grafo XML che definisce la catena SNAP | 111 |
| GPKG_PATH | AOI_JOLANDA_SELECTION.gpkg | GeoPackage con i 154 poligoni dell'AOI | 112 |
| CSV_PATH | Valid_date_S2.csv | CSV qualità S2 (righe=date, colonne=poligoni P001...) | 114 |
| INVENTORY_PATH | inventory_output/inventory_dates.csv | Mappatura date S2 → scene S1 più vicine | 115 |
| GPT_EXE | gpt | Nome/percorso eseguibile SNAP GPT | 116 |
| WORKERS | 1 | Numero di scene elaborate in parallelo | 117 |
| TILE_CACHE | 4096 | Memoria RAM allocata a SNAP in MB | 118 |
| NODATA | -9999.0 | Valore nodata per i pixel fuori dai poligoni | 119 |

---

## 4. Struttura del codice

Lo script è organizzato in cinque blocchi principali, separati visivamente da commenti.

| Blocco | Righe | Contenuto |
|--------|-------|-----------|
| Configurazione e logging | 85–130 | Import, costanti di default, inizializzazione logging |
| Utilità condivise | 133–180 | Funzioni usate da entrambe le fasi: parse_date, load_aoi, print_summary |
| Fase 1 — Processing SNAP | 183–377 | check_gpt, load_aoi_wkt, find_safe_dirs, process_snap_scene, run_snap |
| Fase 2 — Calcolo RVI | 380–702 | assign_poly_ids, load_s2_quality, get_false_polygons, compute_rvi_scene, run_rvi |
| Pipeline e entry point | 705–786 | run() che orchestra le due fasi, argparse, `if __name__ == '__main__'` |

---

## 5. Fase 1 — Processing SNAP (righe 183–377)

La prima fase elabora le scene Sentinel-1 grezze (formato .SAFE) usando SNAP GPT, il processore a riga di comando del software ESA SNAP. Ogni scena viene trasformata in un file BEAM-DIMAP (.dim) con le bande Sigma0_VV e Sigma0_VH calibrate e corrette.

### 5.1 check_gpt (righe 187–208)

Verifica che l'eseguibile GPT sia disponibile sul sistema prima di avviare qualsiasi elaborazione. Su Windows cerca anche nei percorsi di installazione comuni (`C:\snap\bin\`, `AppData\Local\snap\bin\`). Se GPT non viene trovato, lo script termina immediatamente con un messaggio di errore esplicativo.

### 5.2 load_aoi_wkt (righe 211–218)

Calcola il WKT (Well-Known Text) della bounding box dell'AOI usando `unary_union` per unire tutti i 154 poligoni in un'unica geometria, poi `shapely.geometry.box` per ottenere il rettangolo minimo contenente. Il WKT viene passato a SNAP tramite il parametro `-Pwkt_aoi` per ritagliare le immagini durante il processing, evitando di elaborare l'intera scena.

### 5.3 process_snap_scene (righe 239–327)

Elabora una singola scena .SAFE lanciando SNAP GPT come sottoprocesso. Il comando costruito è:

```
gpt s1_processing_graph.xml -Pinput=<scena.SAFE> -Poutput=<output.dim>
    -Pwkt_aoi=<bbox_wkt> -J-Xmx4096m -q <n_core>
```

L'output di SNAP viene letto riga per riga in tempo reale e stampato a schermo con il prefisso `[SNAP]`. In caso di errore (returncode ≠ 0) vengono mostrate le ultime 20 righe di output per facilitare la diagnosi, e l'eventuale file .dim parziale viene eliminato.

### 5.4 Catena di processing SNAP (definita nel grafo XML esterno)

| Passo | Operazione | Effetto |
|-------|-----------|---------|
| 1 | Apply Orbit File | Corregge i metadati orbitali con dati precisi (scaricati automaticamente) |
| 2 | Calibration (Sigma0) | Converte i valori digitali grezzi (DN) in backscatter fisico lineare |
| 3 | Speckle Filter (Lee Sigma) | Riduce il rumore "sale e pepe" tipico delle immagini SAR |
| 4 | Terrain Correction (RD) | Geolocalizzazione precisa e correzione delle distorsioni topografiche con DEM SRTM 1" |
| 5 | Subset AOI | Ritaglia l'immagine sulla bounding box WKT dell'AOI |
| 6 | Output BEAM-DIMAP | Salva il risultato in formato .dim con bande Sigma0_VV e Sigma0_VH (valori lineari) |

### 5.5 run_snap (righe 330–377)

Coordina il processing di tutte le scene. Prima calcola quante scene sono già state elaborate (file .dim esistente e > 10 KB), poi lancia `process_snap_scene` in modalità sequenziale (`workers=1`) o parallela (`ThreadPoolExecutor`). In modalità parallela mostra un avviso sulla RAM necessaria (`workers × tile_cache` MB). Al termine chiama `print_summary` con il riepilogo.

---

## 6. Fase 2 — Calcolo RVI (righe 380–702)

La seconda fase legge i file .dim prodotti dalla fase 1, calcola il Radar Vegetation Index e produce un GeoTIFF per ogni scena. Il calcolo viene effettuato solo sui poligoni selezionati (tutti o solo quelli FALSE nel CSV qualità S2).

### 6.1 assign_poly_ids (righe 384–405)

Legge solo l'intestazione del CSV qualità S2 (`nrows=0`) ed estrae i nomi delle colonne che iniziano con P seguiti da cifre (P001, P002, ...). Questi nomi vengono assegnati ai poligoni del GeoDataFrame nell'ordine in cui compaiono, aggiungendo la colonna `poly_id`. Verifica che il numero di poligoni nel GeoPackage coincida con il numero di colonne nel CSV; in caso contrario lo script si interrompe.

### 6.2 load_s2_quality (righe 408–413)

Legge l'intero CSV qualità S2 con pandas. L'indice diventa la data S2 (datetime), le colonne sono i poligoni (P001...P154). I valori vengono convertiti in booleani: `True` = immagine S2 buona, `False` = immagine S2 non utilizzabile (nuvole, ombre, ecc.).

### 6.3 load_inventory e build_scene_to_dates (righe 416–432)

Legge `inventory_dates.csv` (prodotto da `sentinel1_pipeline.py`) che mappa ogni data S2 alla scena S1 acquisita più vicina temporalmente. Costruisce un dizionario `scene_stem → lista di (date_s2, delta_giorni)`. Poiché il revisit time di Sentinel-1 (~12 giorni) è più lungo di quello di Sentinel-2 (~5 giorni), una stessa scena S1 può essere la più vicina per più date S2 consecutive.

### 6.4 get_false_polygons (righe 435–467)

Funzione centrale della logica di selezione. Per ogni scena S1, recupera tutte le date S2 che la usano come sostituto, poi per ciascuna data legge la riga del CSV qualità e raccoglie i poligoni con valore FALSE. L'unione di tutti questi poligoni (da date diverse) forma la lista finale dei poligoni su cui calcolare l'RVI.

**Esempio: due date S2 mappano alla stessa scena S1**

```
2024-02-17  →  S1A_..._20240222  →  FALSE: P003, P045, P112
2024-02-22  →  S1A_..._20240222  →  FALSE: P003, P078

Unione per la scena: P003, P045, P078, P112
```

### 6.5 compute_rvi_scene (righe 502–611)

Calcola l'RVI per una singola scena .dim. Il flusso interno è:

- Controlla se il GeoTIFF di output esiste già (> 100 KB) → skip
- Seleziona `gdf_active`: tutti i poligoni (`--all_polygons`) oppure solo i FALSE
- Se nessun poligono è FALSE per questa scena → skip
- Trova i file `Sigma0_VV.img` e `Sigma0_VH.img` nella cartella `.data`
- Riproietta il GeoDataFrame nel CRS del raster se necessario
- Applica `rasterio.mask` con `crop=True`: ritaglia e maschera entrambe le bande
- Costruisce `valid_mask`: esclude pixel nodata, NaN/Inf, e somma VV+VH = 0
- Calcola RVI solo sui pixel validi
- Scrive il GeoTIFF con compressione deflate, predictor=3, tiling 256×256

**Formula RVI:**

```
RVI = (4 × Sigma0_VH) / (Sigma0_VV + Sigma0_VH)
```

- **Valori in ingresso:** lineari (non in dB), come prodotti da SNAP con `outputImageScaleDb=false`
- **Intervallo output:** [0, 1] — ~0 = suolo nudo / bassa vegetazione, ~1 = vegetazione densa

### 6.6 run_rvi (righe 614–702)

Coordina il calcolo RVI su tutti i file .dim. Carica il CSV qualità S2 e l'inventario solo in modalità default (non con `--all_polygons`). Al termine, se ci sono sostituzioni, scrive il file `sostituzioni_rvi.csv` nella cartella di output.

---

## 7. Pipeline principale — run() (righe 709–744)

La funzione `run()` orchestra le due fasi in sequenza. Prima esegue i controlli preliminari su tutti i file necessari, poi carica il GeoDataFrame dell'AOI una sola volta e lo condivide con entrambe le fasi. Se un file obbligatorio manca, lo script termina immediatamente con un messaggio esplicito.

- Verifica esistenza di: cartella .SAFE, grafo XML, GeoPackage, CSV (righe 725-733)
- Verifica disponibilità di GPT nel sistema (riga 735)
- Carica il GeoPackage AOI in WGS84 (riga 738) — usato da entrambe le fasi
- Lancia `run_snap()` → Processing SNAP (riga 741)
- Lancia `run_rvi()` → Calcolo RVI (riga 744)

---

## 8. File di output

| File | Posizione | Descrizione |
|------|-----------|-------------|
| S1A_..._RVI.tif | rvi_sentinel1/ | GeoTIFF RVI, float32, compresso deflate, tiled 256×256. Un file per scena. I pixel fuori dai poligoni attivi hanno valore nodata (-9999). |
| sostituzioni_rvi.csv | rvi_sentinel1/ | Log delle sostituzioni S2→S1 (solo modalità default). Colonne: date_s2, scena_s1, delta_giorni, n_poligoni, poligoni_sostituiti. |
| S1A_....dim | processed_sentinel1/ | File BEAM-DIMAP prodotto da SNAP. Contiene i metadati della scena. |
| S1A_....data/ | processed_sentinel1/ | Cartella con le bande raster (.img): Sigma0_VV.img e Sigma0_VH.img. |
| sentinel1_processing_rvi.log | cartella di lavoro | Log completo di entrambe le fasi, con timestamp e statistiche per ogni scena. |

---

## 9. Uso da riga di comando

**Comando base (modalità default — solo poligoni FALSE):**

```bash
python sentinel1_processing_rvi.py
```

**Calcolo RVI su tutti i poligoni:**

```bash
python sentinel1_processing_rvi.py --all_polygons
```

**Tutti i parametri esplicitati:**

```bash
python sentinel1_processing_rvi.py \
    --scenes_dir  output_sentinel1/full_scenes \
    --snap_dir    processed_sentinel1 \
    --rvi_dir     rvi_sentinel1 \
    --graph       s1_processing_graph.xml \
    --gpkg        AOI_JOLANDA_SELECTION.gpkg \
    --csv         Valid_date_S2.csv \
    --inventory   inventory_output/inventory_dates.csv \
    --gpt         /path/to/snap/bin/gpt \
    --workers     1 \
    --tile_cache  4096 \
    --nodata      -9999
```

**Note operative:**

- Le scene già elaborate (fase 1 e fase 2) vengono saltate automaticamente al riavvio.
- Il SRTM 1" viene scaricato automaticamente da SNAP al primo utilizzo (~140 MB per l'Italia).
- Con `--workers > 1` la RAM necessaria è circa `workers × tile_cache` MB (sconsigliato sotto 16 GB RAM).
- Senza `--all_polygons` è necessario che `inventory_dates.csv` esista prima di avviare lo script.
