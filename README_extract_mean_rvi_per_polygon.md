# extract_mean_rvi_per_polygon.py

> Estrazione del valore medio RVI per poligono da file GeoTIFF Sentinel-1

---

## Descrizione

Lo script legge tutti i file `.tif` RVI contenuti in una cartella, calcola il valore medio dell'indice RVI per ciascun poligono di un GeoPackage e salva il risultato in un file CSV con la stessa struttura del file `mean_ndvi_per_polygon.csv`.

Gli ID poligono (`P001`, `P002`, ..., `P154`) vengono assegnati progressivamente nell'ordine in cui i poligoni compaiono nel GeoPackage. Il CSV prodotto ha righe corrispondenti alle date di acquisizione e colonne corrispondenti ai poligoni.

---

## Contesto

Lo script fa parte di un workflow di **gap-filling di serie temporali NDVI** da Sentinel-2. Nelle date in cui le immagini Sentinel-2 sono inutilizzabili per copertura nuvolosa, vengono utilizzate immagini Sentinel-1 (SAR) per stimare i valori mancanti tramite l'indice RVI (Radar Vegetation Index).

---

## Requisiti

### Dipendenze Python

```
geopandas
numpy
pandas
rasterio
shapely
fiona
```

Installazione:

```bash
pip install geopandas numpy pandas rasterio shapely fiona
```

---

## Input attesi

### File `.tif` RVI

File GeoTIFF contenenti l'indice RVI calcolato da immagini Sentinel-1 GRD preprocessate. I file devono avere nome compatibile con il formato Sentinel-1 standard per consentire l'estrazione automatica della data:

```
S1A_IW_GRDH_1SDV_20230722T170658_20230722T170723_049539_05F4F3_0E04_RVI.tif
```

Valori attesi:
- Valore nodata: `-9999`

### GeoPackage (`.gpkg`)

File vettoriale contenente i poligoni dei campi coltivati. I poligoni vengono numerati progressivamente nell'ordine in cui compaiono nel layer (`P001`, `P002`, ...). Il sistema di riferimento viene riproiettato automaticamente se diverso da quello del raster.

---

## Argomenti da riga di comando

| Argomento | Descrizione |
|-----------|-------------|
| `--rvi_dir` | Cartella contenente i file `.tif` RVI **(obbligatorio)** |
| `--gpkg` | GeoPackage con i poligoni dei campi **(obbligatorio)** |
| `--layer` | Nome del layer nel GeoPackage (default: primo layer) |
| `--output` | Nome del file CSV di output (default: `mean_rvi_per_polygon.csv`) |
| `--nodata` | Valore nodata da escludere (default: `-9999`) |

---

## Utilizzo

```bash
python extract_mean_rvi_per_polygon.py \
    --rvi_dir /percorso/cartella/tiff_rvi \
    --gpkg /percorso/poligoni.gpkg \
    --output mean_rvi_per_polygon.csv
```

---

## Logica di funzionamento

1. **Caricamento poligoni** — I poligoni vengono letti dal GeoPackage nell'ordine originale e numerati progressivamente (`P001`, `P002`, …).
2. **Ricerca file `.tif`** — Lo script cerca tutti i file `.tif` (o `.tiff`) nella cartella specificata, ordinati alfabeticamente per nome.
3. **Estrazione data** — La data viene ricavata dal nome del file:
   - Formato Sentinel-1: `YYYYMMDDTHHMMSS`
   - Formato generico: `YYYYMMDD` o `YYYY-MM-DD`
4. **Mascheratura e calcolo** — Per ogni poligono, lo script ritaglia il raster (riproiettando il GeoDataFrame nel CRS del raster se necessario), esclude i pixel nodata e calcola la media aritmetica dei valori validi.
5. **Salvataggio** — I risultati vengono aggregati in un DataFrame, ordinati per data e salvati in CSV.

---

## Output

### Struttura del CSV

Il file CSV prodotto usa il punto e virgola (`;`) come separatore:

```
date;P001;P002;...;P154
2023-07-22T00:00:00Z;0.42;0.38;...
2023-08-03T00:00:00Z;0.51;0.44;...
```

Le celle senza dati validi (poligoni fuori dall'immagine o con soli pixel nodata) vengono lasciate vuote.

---

## Note

- Lo script esclude solo i pixel con valore nodata (`-9999`). Nessun filtro aggiuntivo viene applicato ai valori RVI.
- Se il GeoPackage contiene più layer, viene usato il primo in ordine alfabetico. È possibile specificarne uno diverso con `--layer`.
- Lo script processa tutti i file `.tif` presenti nella cartella indicata, ordinati per nome. Assicurarsi che la cartella contenga **solo file RVI**.
- Per il 2025, la cartella potrebbe contenere file sia da **Sentinel-1A** che da **Sentinel-1C**: lo script li tratta allo stesso modo.
