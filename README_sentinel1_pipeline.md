# sentinel1_pipeline.py

*Documentazione tecnica dello script Python*

---

## 1. Descrizione generale

Script Python unificato per l'inventario e il download di immagini radar satellitari Sentinel-1 (SAR) dal portale Copernicus Data Space Ecosystem (CDSE). Lo script interroga il catalogo OData, produce report CSV sulle immagini disponibili e scarica le scene in formato .SAFE, pronte per il processing con SNAP GPT.

| Campo | Valore |
|-------|--------|
| **File** | sentinel1_pipeline.py |
| **Righe totali** | 839 |
| **Formato output** | .SAFE (struttura originale ESA, pronta per SNAP) |
| **Dati cercati** | Sentinel-1 IW GRD VV+VH |
| **Catalogo** | CDSE OData API (dataspace.copernicus.eu) |

---

## 2. Librerie utilizzate

Lo script utilizza esclusivamente librerie Python standard o installabili via pip. Non sono richieste dipendenze commerciali o software esterni.

| Libreria | Versione | Uso nello script |
|----------|----------|-----------------|
| os | stdlib | Lettura variabili d'ambiente CDSE_USER e CDSE_PASS |
| sys | stdlib | Uscita controllata (sys.exit) in caso di errori critici |
| time | stdlib | Gestione token, misura tempi download, ETA |
| json | stdlib | Parsing del footprint GeoJSON dei prodotti OData |
| zipfile | stdlib | Estrazione degli archivi .zip scaricati da CDSE |
| logging | stdlib | Logging su schermo e su file .log con timestamp |
| tempfile | stdlib | Cartelle temporanee per download e decompressione |
| shutil | stdlib | Spostamento cartelle .SAFE e pulizia file temporanei |
| pathlib.Path | stdlib | Gestione portabile dei percorsi file |
| datetime, timedelta | stdlib | Calcolo finestre temporali +/-WINDOW_DAYS |
| argparse | stdlib | Parsing degli argomenti da riga di comando |
| requests | pip | Chiamate HTTP al catalogo OData e download streaming |
| pandas | pip | Lettura CSV qualità S2, produzione report inventario |
| geopandas | pip | Lettura GeoPackage AOI, riproiezione CRS |
| shapely | pip | Unione poligoni (unary_union), calcolo copertura % |
| tqdm | pip | Barre di avanzamento per ricerca e download |
| python-dotenv | pip | Lettura credenziali da file .env |

**Installazione:**

```bash
pip install requests geopandas pandas shapely tqdm python-dotenv
```

---

## 3. Configurazione default (righe 127–146)

Le costanti definite in cima allo script rappresentano i valori di default per tutti i parametri. Possono essere sovrascritte da riga di comando senza modificare il codice.

| Costante | Valore default | Descrizione |
|----------|---------------|-------------|
| CSV_PATH | Valid_date_S2.csv | CSV qualità S2 (righe=date, colonne=poligoni) |
| GPKG_PATH | AOI_JOLANDA_SELECTION.gpkg | GeoPackage con i poligoni dell'area di studio |
| GPKG_LAYER | None | Layer del GeoPackage (None = primo disponibile) |
| INVENTORY_DIR | inventory_output/ | Cartella output report inventario |
| OUTPUT_DIR | output_sentinel1/ | Cartella output download (.SAFE) |
| WINDOW_DAYS | 6 | Finestra di ricerca +/-giorni rispetto alla data S2 |
| ORBIT_DIR | BOTH | Direzione orbitale: ASCENDING, DESCENDING, BOTH |
| PLATFORM | S1A | Satellite: S1A, S1C, BOTH |
| YEAR | None | Anno da analizzare (None = tutti gli anni del CSV) |
| MAX_RETRIES | 3 | Numero massimo di tentativi per ogni richiesta HTTP |
| SLEEP_BETWEEN | 1.0 | Pausa in secondi tra un download e il successivo |

---

## 4. Struttura del codice

Lo script è organizzato in sei blocchi principali, separati visivamente da commenti.

| Blocco | Righe | Contenuto |
|--------|-------|-----------|
| Configurazione e logging | 127-161 | Import, costanti di default, setup logging, URL CDSE |
| Autenticazione — CDSESession | 164-210 | Classe con gestione token OAuth2, retry con backoff, `ensure_token()` |
| Ricerca prodotti S1 | 212-339 | `search_s1_products`, `extract_attribute`, `compute_coverage`, `parse_product`, `pick_closest` |
| Caricamento dati | 341-376 | `load_csv` (CSV qualità S2), `load_area_polygon` (GeoPackage) |
| Modalità inventory | 378-524 | `run_inventory`: ricerca catalogo, cache, produzione CSV e summary |
| Riepilogo pre-download | 526-562 | `pre_download_summary`: stampa riepilogo e chiede conferma |
| Download | 564-705 | `download_zip` (streaming), `extract_safe` (.zip → .SAFE), `run_download` |
| Pipeline principale e argparse | 707-831 | `run()` che orchestra le modalità, parsing argomenti CLI |

---

## 5. Autenticazione — classe CDSESession (righe 164–210)

La classe `CDSESession` gestisce l'autenticazione OAuth2 con il portale CDSE e incapsula tutta la logica di retry.

### 5.1 Acquisizione token — _refresh_token (righe 174–189)

Il token OAuth2 viene ottenuto con una richiesta POST all'endpoint CDSE usando le credenziali lette dalle variabili d'ambiente `CDSE_USER` e `CDSE_PASS`. Il token ha una durata limitata (tipicamente 10 minuti); lo script memorizza la scadenza e rinnova automaticamente il token prima che scada, con un margine di sicurezza di 30 secondi.

### 5.2 ensure_token (riga 191)

Metodo pubblico che verifica se il token è ancora valido e, in caso contrario, lo rinnova. Viene chiamato esplicitamente prima di ogni download streaming, dove la sessione `requests` non passa per il metodo `get()` della classe.

### 5.3 Retry con backoff esponenziale — get (righe 197–210)

Ogni chiamata HTTP al catalogo OData viene ritentata fino a `MAX_RETRIES` volte in caso di errore. Il tempo di attesa tra un tentativo e il successivo cresce esponenzialmente (2^tentativo secondi). In caso di errore 401 (token scaduto), il token viene rinnovato prima del ritentativo.

---

## 6. Ricerca prodotti S1 (righe 212–339)

### 6.1 search_s1_products (righe 216–280)

Interroga il catalogo CDSE tramite API OData con i seguenti filtri fissi, sempre applicati a ogni ricerca:

| Filtro | Valore |
|--------|--------|
| Collection | SENTINEL-1 |
| productType | GRD |
| operationalMode | IW (Interferometric Wide Swath) |
| polarisationChannels | VV+VH (necessarie per il calcolo RVI) |
| Footprint | Intersects con la bounding box del GeoPackage AOI |
| ContentDate | Finestra [date_s2 - WINDOW_DAYS, date_s2 + WINDOW_DAYS] |

Filtri opzionali aggiuntivi (attivati dai parametri CLI):

| Parametro | Filtro OData aggiunto |
|-----------|----------------------|
| `--orbit ASCENDING/DESCENDING` | orbitDirection eq 'ASCENDING' oppure 'DESCENDING' |

**Nota tecnica — filtro satellite:** il catalogo CDSE restituisce sempre `SENTINEL-1` nel campo `platformShortName`, senza la lettera finale (A o C). Il filtro `--platform` viene quindi applicato lato Python sul nome del prodotto, che inizia sempre con `S1A_` o `S1C_` ed è un'informazione affidabile.

La funzione gestisce la paginazione OData richiedendo 50 prodotti per volta (`$top=50`, `$skip=N`) e iterando fino a quando la pagina ritornata contiene meno di 50 risultati.

### 6.2 compute_coverage (righe 291–308)

Calcola la percentuale di copertura del footprint S1 sull'area di studio. Legge il campo `GeoFootprint` (o `Footprint`) dal prodotto OData, lo converte in geometria shapely e calcola il rapporto tra l'intersezione con l'unione dei poligoni AOI e l'area totale dell'AOI. Restituisce -1.0 se il footprint non è disponibile.

### 6.3 parse_product (righe 311–332)

Converte il dizionario grezzo OData in un record strutturato con i campi salvati nei CSV di output:

| Campo | Fonte OData | Descrizione |
|-------|------------|-------------|
| product_id | Id | UUID univoco del prodotto nel catalogo CDSE |
| product_name | Name | Nome ESA del prodotto (es. S1A_IW_GRDH_...) |
| acq_datetime | ContentDate/Start | Data e ora di acquisizione (UTC) |
| acq_date | ContentDate/Start | Solo la data di acquisizione |
| delta_days | calcolato | Giorni tra acquisizione S1 e data S2 (negativo = prima) |
| orbit_direction | orbitDirection | ASCENDING o DESCENDING |
| relative_orbit | relativeOrbitNumber | Numero orbita relativa |
| absolute_orbit | orbitNumber | Numero orbita assoluta |
| platform | platformShortName | Sempre 'SENTINEL-1' (il catalogo CDSE non espone la lettera finale). Il satellite reale (S1A/S1C) si ricava dal product_name. |
| instrument_mode | operationalMode | IW |
| size_mb | ContentLength | Dimensione stimata in MB |
| online | Online | True se disponibile online senza riattivazione |
| coverage_pct | GeoFootprint/Footprint | Copertura % sull'AOI calcolata da compute_coverage |

---

## 7. Modalità inventory — run_inventory (righe 382–524)

La modalità inventory interroga il catalogo CDSE per ogni data S2 nuvolosa presente nel CSV di qualità e produce tre file CSV e un riepilogo testuale. Nessun file .SAFE viene scaricato.

### 7.1 Cache delle ricerche (riga 404)

Per evitare chiamate duplicate al catalogo, i risultati di ogni finestra temporale vengono memorizzati in un dizionario (`cache_key` = intervallo di date). Poiché i parametri di ricerca (orbita, satellite) sono costanti per tutta l'esecuzione, la chiave basata sulle sole date è sufficiente.

### 7.2 File di output prodotti

| File | Descrizione |
|------|-------------|
| inventory_products.csv | Un prodotto S1 per riga: data, orbita relativa/assoluta, direzione, satellite, dimensione MB, disponibilità online, copertura % AOI, data S2 associata. |
| inventory_dates.csv | Una riga per data S2 nuvolosa: n. prodotti trovati, prodotto più vicino (data, delta giorni, nome, orbita, copertura %), tutte le orbite relative disponibili nella finestra. |
| inventory_orbits.csv | Riepilogo per orbita relativa: n. prodotti, periodo, dimensione media, % prodotti online. |
| inventory_summary.txt | Riepilogo testuale con filtri attivi, conteggi e tabella orbite. Stampato anche a schermo. |

---

## 8. Modalità download (righe 619–705)

La modalità download legge il file `inventory_products.csv` già prodotto e scarica i prodotti unici in formato .SAFE, senza ripetere le ricerche sul catalogo CDSE.

### 8.1 Flusso di download

Per ogni prodotto unico: viene costruito l'URL di download dall'endpoint CDSE Zipper, lo zip viene scaricato in streaming in una cartella temporanea, estratto cercando la cartella .SAFE al suo interno, e infine spostato nella cartella output definitiva. Il file zip e la cartella temporanea vengono eliminati al termine.

### 8.2 Filtri applicati in modalità download

I filtri `--orbit`, `--platform` e `--year` vengono applicati sul DataFrame letto da `inventory_products.csv` prima del download. Questo permette ad esempio di produrre un inventory con `BOTH` e poi scaricare selettivamente solo una direzione orbitale.

### 8.3 Statistiche di download

Per ogni scena scaricata viene registrato nel log: dimensione in GB, tempo impiegato, velocità in MB/s e tempo rimanente stimato (ETA). Al termine viene stampato un riepilogo con il totale scaricato e la velocità media.

### 8.4 Riprendibilità

Le scene già scaricate (cartella .SAFE esistente su disco) vengono saltate automaticamente. Il download è quindi riprendibile in caso di interruzione senza perdere il lavoro già fatto.

---

## 9. File di output

| File | Posizione | Descrizione |
|------|-----------|-------------|
| inventory_products.csv | inventory_output/ | Prodotti S1 trovati per ogni data S2 nuvolosa. Un prodotto per riga. |
| inventory_dates.csv | inventory_output/ | Riepilogo per data S2: prodotto più vicino, delta giorni, copertura %. |
| inventory_orbits.csv | inventory_output/ | Riepilogo per orbita relativa: n. prodotti, periodo, % online. |
| inventory_summary.txt | inventory_output/ | Riepilogo testuale stampato a schermo e salvato su file. |
| *.SAFE/ | output_sentinel1/full_scenes/ | Struttura originale ESA, pronta per il processing con SNAP GPT. |
| sentinel1_pipeline.log | cartella di lavoro | Log completo con timestamp: ricerche, download, errori, statistiche. |

---

## 10. Uso da riga di comando

**Modalità inventory (solo ricerca, nessun download):**

```bash
python sentinel1_pipeline.py --mode inventory
```

**Modalità download (scarica dall'inventory già prodotto):**

```bash
python sentinel1_pipeline.py --mode download
```

**Modalità full (inventory + download in sequenza):**

```bash
python sentinel1_pipeline.py --mode full
```

**Esempio tipico — time series 2023-2025 con S1-A, orbita ascendente:**

```bash
python sentinel1_pipeline.py \
    --mode     full       \
    --orbit    ASCENDING  \
    --platform S1A        \
    --csv      Valid_date_S2.csv
```

**Tutti i parametri esplicitati:**

```bash
python sentinel1_pipeline.py \
    --mode       inventory                    \
    --csv        Valid_date_S2.csv            \
    --gpkg       AOI_JOLANDA_SELECTION.gpkg   \
    --layer      None                         \
    --window     6                            \
    --orbit      ASCENDING                    \
    --platform   S1A                          \
    --year       2024                         \
    --inventory  inventory_output             \
    --outdir     output_sentinel1
```

**Riferimento opzioni CLI:**

| Opzione | Default | Valori accettati | Descrizione |
|---------|---------|-----------------|-------------|
| `--mode` | inventory | inventory, download, full | Modalità di esecuzione |
| `--csv` | Valid_date_S2.csv | percorso file | CSV qualità S2 (separatore ;) |
| `--gpkg` | AOI_JOLANDA_SELECTION.gpkg | percorso file | GeoPackage con i poligoni AOI |
| `--layer` | None | nome layer o None | Layer del GeoPackage (None = primo) |
| `--window` | 6 | intero (giorni) | Finestra temporale +/-giorni |
| `--orbit` | BOTH | ASCENDING, DESCENDING, BOTH | Direzione orbitale |
| `--platform` | S1A | S1A, S1C, BOTH | Satellite Sentinel-1 |
| `--year` | None | intero (es. 2024) o None | Anno da analizzare (None = tutti) |
| `--inventory` | inventory_output | percorso cartella | Cartella output report inventario |
| `--outdir` | output_sentinel1 | percorso cartella | Cartella output download .SAFE |

**Credenziali CDSE:**

Le credenziali vengono lette dalle variabili d'ambiente `CDSE_USER` e `CDSE_PASS` (registrazione gratuita su dataspace.copernicus.eu). Possono essere definite in un file `.env` nella stessa cartella dello script:

```
CDSE_USER=nome.utente@email.com
CDSE_PASS=password
```

**Note operative:**

- Le scene già scaricate vengono saltate automaticamente: il download è riprendibile.
- Con `--mode download` è possibile filtrare l'inventory con `--orbit`, `--platform` e `--year` diversi rispetto a quelli usati in fase di inventory.
- Rivisit Sentinel-1: ~12gg con un solo satellite attivo; la configurazione tandem a ~6gg era S1-A+S1-B (fino al 2021) e ora è S1-C+S1-D (da maggio 2026).
- Per una time series agricola coerente usare sempre la stessa direzione orbitale e lo stesso satellite.
