# gapfilling_ndvi.py

> Ricostruzione della serie NDVI continua: applicazione del metodo validato ai buchi reali.

---

## Descrizione

A differenza degli script di validazione (block-CV, rolling-origin, benchmark C) qui **non si misura un errore**: si riempiono i buchi veri — le date in cui la copertura nuvolosa rende l'NDVI non osservabile — producendo la serie NDVI continua per ciascun poligono.

Il riempimento segue una **cascata di regole applicate in ordine di priorità**: ogni cella (poligono–data) riceve il trattamento migliore compatibile con la propria situazione, e resta tracciabile la sorgente da cui è stata prodotta.

---

## Regola a cascata

Per ogni cella poligono–data si applica la **prima regola che ricorre**:

1. **NDVI osservato** → si tiene il valore Sentinel-2 reale → `osservato`
2. **Poligono "scollegato"** → solo interpolazione, marcata come inaffidabile → `interpolato_inaffidabile`
3. **Buco ≤ `--soglia` (35 gg)** → interpolazione lineare temporale (solo S2) → `interpolato`
4. **Buco > `--soglia`** → regressione RVI→NDVI calibrata sul poligono → `radar`

La **distanza** è il numero di giorni dall'osservazione NDVI utile più vicina (minimo tra ancora precedente e successiva), non l'ampiezza complessiva del buco — coerentemente con il benchmark C.

> **Nota sull'ordine.** Il controllo di "scollegato" **precede** quello sulla distanza: nei poligoni scollegati ogni cella mancante è interpolata e marcata come inaffidabile, a prescindere dall'ampiezza del buco. Di conseguenza le etichette `interpolato` e `radar` compaiono solo nei poligoni collegati, e `interpolato_inaffidabile` solo negli scollegati.

### Casi particolari

- Buco > soglia ma **nessun RVI** entro `--tol-rvi` giorni → fallback su interpolazione → `interpolato_no_rvi`
- Bordo della serie **senza un'ancora** (prima o dopo) e niente radar → `non_stimabile`

### Poligoni "scollegati"

Sono i campi in cui la relazione RVI→NDVI non è affidabile: **pendenza < 0** (es. risaie allagate, *double-bounce*) **oppure** R²_in < `--min-r2`. Su questi il radar non viene mai usato: solo interpolazione, con flag di inaffidabilità.

---

## Input

| File | Contenuto |
|---|---|
| `mean_ndvi_per_polygon.csv` | matrice NDVI da riempire (date × poligoni, separatore `;`) |
| `mean_rvi_per_polygon_MASTER.csv` | matrice RVI su tutte le date (date × poligoni, `;`) |
| `training_pairs_rvi_ndvi.csv` | coppie usate per fittare le rette per-poligono e classificarli |

### Parametri CLI

| Parametro | Default | Descrizione |
|---|---|---|
| `--ndvi` | `mean_ndvi_per_polygon.csv` | matrice NDVI di input |
| `--rvi` | `mean_rvi_per_polygon_MASTER.csv` | matrice RVI |
| `--pairs` | `training_pairs_rvi_ndvi.csv` | coppie per la calibrazione |
| `--soglia` | `35` | soglia in giorni: buchi ≤ interpolazione, > radar |
| `--tol-rvi` | `6` | tolleranza (giorni) per appaiare l'RVI S1 alla data S2 |
| `--min-r2` | `0.2` | soglia R²_in per classificare un poligono come collegato |
| `--outdir` | `.` | cartella di output |

---

## Output

- **`ndvi_gapfilled_long.csv`** — formato lungo, una riga per cella:
  `poly_id`, `s2_date`, `ndvi_finale`, `sorgente`, `distanza_giorni`, `ndvi_osservato`
- **`ndvi_gapfilled_wide.csv`** — la stessa matrice NDVI di input, con i buchi riempiti.
- **`ndvi_gapfilled_riepilogo_sorgente.csv`** — conteggi e percentuali per sorgente (tabella di copertura).
- **`ndvi_gapfilled_esempio.png`** — serie di esempio (il poligono collegato con più stime radar), con i punti colorati per sorgente.
- **A video (stdout):** conteggio e percentuale delle celle per sorgente, numero di poligoni scollegati.

Il campo **`sorgente`** è la parte più importante dell'output: rende ogni valore tracciabile e permette a chi usa la serie di pesare o escludere le celle meno affidabili.

> I conteggi del riepilogo sono **celle riempite** (una per poligono–data), cioè unità indipendenti: non soffrono del problema di non-indipendenza degli scenari del benchmark C.

---

## Logica di funzionamento

1. **Calibrazione:** dalle coppie di training (ripulite dai NaN) si fitta una retta RVI→NDVI per ciascun poligono (forma chiusa OLS) e se ne ricava `slope`, `intercept`, `r2`. Un poligono è **scollegato** se `slope < 0` oppure `r2 < --min-r2`.
2. **Indicizzazione RVI:** per ogni poligono si costruisce la serie RVI senza NaN, per poter cercare rapidamente il valore radar più vicino a una data.
3. **Lookup radar (`rvi_at`):** cerca il valore RVI entro `--tol-rvi` giorni dalla data richiesta, scegliendo il più vicino; se non esiste, restituisce `None`.
4. **Riempimento cella per cella:** si scorre il calendario NDVI; per ogni cella mancante si individuano le ancore osservate del poligono (precedente e successiva), si calcola la distanza dall'ancora più vicina e l'interpolazione lineare, poi si applica la cascata. Se non esiste alcuna ancora, la distanza è `NaN` (non un valore fittizio, per non contaminare le statistiche).
5. **Clipping:** ogni valore stimato è vincolato all'intervallo fisicamente ammissibile dell'NDVI, [−1, 1].

---

## Requisiti

```
numpy
pandas
matplotlib   # opzionale, solo per il grafico di esempio
```

```bash
pip install numpy pandas matplotlib
```

---

## Note e rimandi

- La **soglia di 35 giorni** non è arbitraria: è il punto di crossover determinato da `benchmark_C_rvi_vs_interpolazione.py`. La variante `benchmark_C_rolling_origin.py` mostra che tale soglia è valida nello scenario "archivio" (ancore su entrambi i lati della lacuna), che è esattamente il regime di questo script.
- Il criterio **collegato/scollegato** (`slope > 0` e R²_in ≥ 0,2) è lo stesso adottato in `validate_correlazione_rvi_ndvi_blocchi.py` e nel benchmark C.
- Le rette per-poligono provengono dalle coppie prodotte da `correlazione_rvi_ndvi.py`.
