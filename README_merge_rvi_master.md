# merge_rvi_master.py

> Ricomposizione delle estrazioni RVI in un'unica serie per poligono (il *master*).

---

## Descrizione

Le scene Sentinel-1 sono state scaricate con **due ricerche distinte**, guidate rispettivamente dalle date Sentinel-2 **con copertura nuvolosa** e da quelle **prive di nuvole**. Ciascuna ricerca ha prodotto la propria matrice RVI (date × poligoni).

Le due ricerche possono però restituire la **medesima scena Sentinel-1**: un'unica acquisizione radar può ricadere entro ±6 giorni sia da una data ottica nuvolosa sia da una serena adiacente. Le date in comune vanno quindi **deduplicate**, non concatenate.

```
105 date (da S2 nuvolose)  ∪  67 date (da S2 serene)  −  61 comuni  =  111 date
```

Il risultato, `mean_rvi_per_polygon_MASTER.csv`, è **l'unica serie RVI operativa**: è da qui che correlazione, regressione e gap-filling pescano i valori, indistintamente dalla ricerca da cui provengono. I due file di partenza documentano solo la *provenienza* del download, non un ruolo diverso del dato: l'RVI è lo stesso indice in entrambi.

---

## Input

Due (o più) matrici RVI in formato *wide*: date × poligoni, separatore `;`, celle vuote = NaN.

| File | Contenuto |
|---|---|
| `mean_rvi_per_polygon_all_pol_no_inv.csv` | RVI sulle date S2 con nuvole (105 date) |
| `mean_rvi_per_polygon_no_nuvole.csv` | RVI sulle date S2 serene (67 date) |

### Parametri CLI

| Parametro | Default | Descrizione |
|---|---|---|
| `--inputs` | *(obbligatorio)* | due o più CSV RVI. **L'ordine definisce la precedenza** in caso di conflitto |
| `--output` | `mean_rvi_per_polygon_MASTER.csv` | CSV di output |
| `--check` | *(flag)* | riporta lo scarto massimo sulle celle presenti in più file |

---

## Output

- **`mean_rvi_per_polygon_MASTER.csv`** — matrice unica, date ordinate cronologicamente, senza duplicati.
- **A video (stdout):** date per file, date comuni, totale nell'unione, numero di poligoni, intervallo temporale, percentuale di valori validi e (con `--check`) eventuali discordanze.

---

## Logica di funzionamento

1. **Lettura** delle matrici, con indice temporale e celle vuote convertite in NaN.
2. **Coerenza delle colonne:** tutti i file devono avere gli stessi poligoni, nello stesso ordine; altrimenti lo script si ferma.
3. **Diagnostica della sovrapposizione:** stampa quante date sono comuni e quante distinte in totale.
4. **Controllo conflitti** (`--check`): sulle celle presenti in più file si calcola lo scarto massimo. Atteso: **zero discordanze**, poiché le date comuni provengono dalla stessa scena e dallo stesso processing. Uno scarto non nullo segnalerebbe che i due file derivano da elaborazioni diverse.
5. **Fusione:** `combine_first` riempie i buchi del primo file con i valori del secondo, senza mai sovrascrivere un valore già presente. La precedenza segue l'ordine di `--inputs`.
6. **Scrittura** con date in formato ISO e separatore `;`, coerente con gli altri CSV della pipeline.

---

## Uso

```bash
python merge_rvi_master.py \
    --inputs mean_rvi_per_polygon_all_pol_no_inv.csv mean_rvi_per_polygon_no_nuvole.csv \
    --output mean_rvi_per_polygon_MASTER.csv --check
```

---

## Requisiti

```
numpy
pandas
```

```bash
pip install numpy pandas
```

---

## Note e rimandi

- I due CSV di input sono prodotti da `extract_mean_rvi_per_polygon.py`, eseguito separatamente sulle due cartelle di raster RVI.
- Il master è l'input RVI di `correlazione_rvi_ndvi.py` e di `gapfilling_ndvi.py`.
- Il calendario radar di **111 date** citato in metodologia è esattamente l'output di questo script.
