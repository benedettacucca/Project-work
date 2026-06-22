# `correlazione_rvi_ndvi.py` — README

Script per costruire la **correlazione RVI (Sentinel‑1) → NDVI (Sentinel‑2)** poligono per poligono, da usare per **riempire i buchi nuvolosi** delle time series NDVI: dove l'NDVI manca per la copertura nuvolosa, lo si stima dall'RVI tramite la regressione lineare di quel campo.

---

## 1. Scopo

L'NDVI (ottico) è disponibile solo nelle date serene; l'RVI (radar) è disponibile sempre, perché il SAR attraversa le nuvole. Lo script mette in relazione i due indici su tutte le date in cui entrambi sono validi e, per ogni poligono, stima la retta `NDVI = slope·RVI + intercept`. Questa retta è poi lo strumento per predire l'NDVI nelle date nuvolose (fase di *apply*, non inclusa in questo script).

---

## 2. Cosa fa (catena logica)

1. **Carica** RVI master (S1) e NDVI (S2), entrambi in formato `date × poligoni`.
2. **Accoppia** ogni data NDVI alla data RVI più vicina entro ±`WINDOW_DAYS` giorni (default 6). È la stessa regola di `sentinel1_pipeline.py` (`pick_closest`: `|delta|` minimo), qui realizzata con `pandas.merge_asof(direction='nearest')`.
3. **Filtra** tenendo solo le coppie con *entrambi* i valori validi. Il filtro nuvole non lo fa l'RVI (il SAR è sempre valido): lo fa l'NDVI, che è vuoto (NaN) dove la nuvola lo maschera. Tenendo solo le celle NDVI non vuote si usano già le sole osservazioni valide.
4. **Regredisce** per ogni poligono la retta lineare `NDVI ~ RVI`, salvando pendenza, intercetta, Pearson r, Spearman r, R², numerosità e range coperto.
5. **Calcola** anche la regressione globale su tutte le coppie, come dato di sintesi.

---

## 3. File di input

Due CSV, stesso formato:

- separatore `;`, decimale `.`
- prima colonna `date` (es. `2023-07-11T00:00:00Z`), poi una colonna per poligono: `P001 … P154`
- ogni cella è la media dell'indice per quel poligono in quella data

| File | Contenuto | Celle vuote |
|------|-----------|-------------|
| `--rvi` (RVI master, S1) | media RVI per poligono/data, tutte le date S1 disponibili | (normalmente piene) |
| `--ndvi` (NDVI, S2) | media NDVI per poligono/data, tutte le date S2 | **vuote = nuvola** (mascheramento per‑poligono‑per‑data) |

> Le colonne poligono dei due file devono coincidere esattamente, altrimenti lo script si ferma con errore.

---

## 4. File di output

### `training_pairs_rvi_ndvi.csv` — formato lungo, una riga per coppia

| Colonna | Significato |
|---------|-------------|
| `poly_id` | identificativo poligono (P001…P154) |
| `s2_date` | data dell'osservazione NDVI |
| `s1_date` | data della scena RVI accoppiata |
| `delta_days` | scarto in giorni tra le due (s1 − s2) |
| `rvi` | valore RVI |
| `ndvi` | valore NDVI |

### `regression_per_polygon.csv` — una riga per poligono

| Colonna | Significato |
|---------|-------------|
| `poly_id` | poligono |
| `n` | numero di coppie su cui è fittata la retta |
| `ndvi_min`, `ndvi_max`, `ndvi_span` | minimo, massimo ed escursione dell'NDVI del poligono |
| `rvi_min`, `rvi_max` | minimo e massimo dell'RVI |
| `slope`, `intercept` | coefficienti della retta `NDVI = slope·RVI + intercept` |
| `pearson_r` | correlazione lineare (il suo quadrato è `r2`) |
| `spearman_r` | correlazione monotòna (sui ranghi) |
| `r2` | coefficiente di determinazione = `pearson_r²` |

### Figure (solo con `--plots`, richiede `matplotlib`)

- `fig1_scatter_globale.png` — relazione globale RVI–NDVI (hexbin a densità + retta)
- `fig2_isto_pearson.png` — distribuzione del Pearson r sui poligoni
- `fig3_pearson_vs_spearman.png` — Pearson vs Spearman per poligono (controllo qualità)
- `fig4_esempio_fit_stagioni.png` — campo migliore vs peggiore (per Pearson), colorato per stagione

---

## 5. Parametri

| Argomento | Default | Descrizione |
|-----------|---------|-------------|
| `--rvi` | `mean_rvi_per_polygon_MASTER.csv` | percorso del CSV RVI master |
| `--ndvi` | `mean_ndvi_per_polygon.csv` | percorso del CSV NDVI |
| `--window` | `6` | finestra di accoppiamento ±giorni |
| `--outdir` | `.` | cartella di output (creata se non esiste) |
| `--plots` | (assente) | se presente, genera anche le 4 figure |

Due costanti in cima al file:

- `WINDOW_DAYS_DEFAULT = 6` — finestra di default (allineata alla pipeline S1).
- `MIN_PAIRS = 3` — minimo di coppie per fittare la retta di un poligono; sotto questa soglia il poligono viene saltato (rete di sicurezza, di norma inattiva).

---

## 6. Uso

```bash
python correlazione_rvi_ndvi.py \
    --rvi  mean_rvi_per_polygon_MASTER.csv \
    --ndvi mean_ndvi_per_polygon.csv \
    --window 6 \
    --outdir . \
    --plots
```

Su Windows / PowerShell, in una riga:

```powershell
python correlazione_rvi_ndvi.py --rvi mean_rvi_per_polygon_MASTER.csv --ndvi mean_ndvi_per_polygon.csv --window 6 --outdir . --plots
```

I percorsi sono relativi alla cartella da cui si lancia il comando. Se i file non hanno i nomi di default, vanno passati esplicitamente (o si modificano i `default=` nello script).

---

## 7. Dipendenze

Lo script richiede **Python 3.8 o superiore**. Librerie usate:

| Libreria | Tipo | Usata per | Installazione |
|----------|------|-----------|---------------|
| `pandas` | terze parti | lettura CSV, `merge_asof`, `groupby` | `pip` |
| `numpy` | terze parti | `polyfit`, `corrcoef`, ranghi | `pip` |
| `matplotlib` | terze parti (opzionale) | figure riassuntive (`--plots`) | `pip` |
| `argparse` | standard library | parsing degli argomenti da riga di comando | inclusa in Python |
| `pathlib` | standard library | gestione dei percorsi file | inclusa in Python |

Le librerie della standard library (`argparse`, `pathlib`) sono già incluse in Python: non serve installarle. Le tre di terze parti si installano con:

```bash
pip install pandas numpy matplotlib
```

`matplotlib` serve solo per le figure (`--plots`); senza quel flag bastano `pandas` e `numpy`. È importato in modo pigro (solo dentro la funzione che disegna le figure), quindi lo script gira ugualmente anche se non è installato, purché non si usi `--plots`.

---

## 8. Note metodologiche

- **Direzione della regressione**: NDVI in funzione di RVI (RVI = predittore), perché lo scopo è *stimare l'NDVI dall'RVI* dove l'NDVI manca.
- **Una sola retta per poligono**: assume relazione stabile tutto l'anno. È adeguata sulla maggioranza dei campi, ma dove il SAR si scollega dall'NDVI (risaie allagate, saturazione ad alta biomassa) la retta unica fitta male — vedi Limiti.
- **Pearson vs Spearman**: Pearson misura la relazione *lineare* ed è la metrica congruente con la regressione (`pearson_r² = r2`). Spearman misura la relazione *monotòna* (lavora sui ranghi). Lo scarto Spearman − Pearson segnala non‑linearità: utile come controllo qualità per capire di quali rette fidarsi.
- **Train vs apply**: questo script costruisce e valuta la correlazione (train). L'uso dei coefficienti per stimare l'NDVI sulle date nuvolose (apply) è un passo successivo separato.

---

## 9. Limiti noti

- **Risaie / pendenza negativa**: nei campi allagati il doppio rimbalzo acqua‑stelo manda il segnale sul canale VV, mentre l'RVI è costruito sul VH. L'RVI si scollega dall'NDVI e la retta può avere pendenza negativa. Questi poligoni (R² basso o `slope < 0`) vanno **segnalati e non gap‑fillati alla cieca**.
- **Riuso delle scene S1**: con la finestra ±6 giorni una stessa scena RVI può essere accoppiata a più date NDVI vicine. È fisiologico, ma riduce un po' l'indipendenza dei valori di RVI.
- **Affidabilità del fit**: `MIN_PAIRS = 3` è il minimo *matematico*, non quello *statisticamente robusto*. Su dataset più piccoli conviene alzarlo (es. 8–10).
- **Correlazione ≠ qualità di predizione**: un buon r dice che la relazione c'è, non quanto bene il gap‑filling funzionerà. Quello si misura con una validazione dedicata (nascondere NDVI noti, stimarli, confrontare).

---

## 10. Risultati di riferimento

Valori ottenuti sul dataset di progetto (154 poligoni, area di Jolanda di Savoia), utili per verificare una riesecuzione:

- input: RVI 111 date × 154 poligoni; NDVI 241 date × 154 poligoni (167 con almeno un valore)
- coppie valide: **19.788**, su **167** date NDVI accoppiate a **100** scene S1 distinte (su 111)
- per poligono: Pearson r mediano **0.682**, Spearman mediano **0.625**, R² mediano **0.466**; 103 poligoni con Pearson ≥ 0.5; 12 a pendenza negativa
- globale: **NDVI = 0.8017·RVI − 0.0384**, Pearson 0.605, Spearman 0.616, R² 0.366
