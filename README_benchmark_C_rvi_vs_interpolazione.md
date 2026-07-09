# benchmark_C_rvi_vs_interpolazione.py

> Benchmark C — *"Il radar serve davvero, e da quale lunghezza di buco in poi?"*

---

## Descrizione

Lo script simula buchi nuvolosi di durata crescente nella serie NDVI e, su ogni buco, confronta **due** riempitori:

- **RADAR** — retta unica RVI→NDVI del poligono (fit sui dati **fuori** dal buco, poi predice l'NDVI delle date nascoste usando il loro RVI);
- **CIECO** — interpolazione temporale lineare tra l'ultima osservazione NDVI **prima** del buco e la prima **dopo** (non usa il radar).

L'errore è misurato **data per data** e accumulato per fascia di distanza. Ne esce una curva "errore vs distanza" con un punto di **incrocio (crossover)**: oltre quella distanza, il radar batte l'interpolazione.

---

## Contesto

È lo script che fissa la **soglia (~35 giorni)** con cui la cascata di gap-filling decide tra interpolazione (buchi brevi) e regressione radar (buchi lunghi). Vengono usati solo i poligoni **"buoni"** (criterio ricalcolato qui: pendenza > 0 **e** R²_in ≥ `--min-r2`), perché sui campi scollegati nessun metodo radar può funzionare.

**Definizione di "buco":** la nuvola copre tutta l'AOI, quindi un buco è una fila di date osservate consecutive nascoste. La distanza che conta per l'interpolazione **non** è l'ampiezza del buco, ma quanto la singola data nascosta dista dall'osservazione utile più vicina (minimo tra le due ancore): una data a 3 giorni da un'ancora è facile anche se il buco è largo 60 giorni.

---

## Input

- **`training_pairs_rvi_ndvi.csv`** (formato lungo: `poly_id`, `s2_date`, `rvi`, `ndvi`).

### Parametri CLI

| Parametro | Default | Descrizione |
|---|---|---|
| `--pairs` | `training_pairs_rvi_ndvi.csv` | CSV di input |
| `--min-r2` | `0.2` | soglia R²_in per i poligoni "buoni" (con pendenza > 0) |
| `--kmax` | `12` | massimo osservazioni AOI consecutive nascoste in un buco |
| `--bin-days` | `10` | ampiezza delle fasce di distanza (giorni) |
| `--max-days` | `90` | distanza massima considerata (giorni) |
| `--min-n` | `1000` | minimo punti per riportare una fascia (le fasce a grande distanza sono poco popolate e troppo rumorose) |
| `--outdir` | `.` | cartella di output |

---

## Output

- **`benchmark_C_per_durata.csv`** — una riga per fascia di distanza: `len_center_days`, `n_punti`, `rmse_radar`, `rmse_interp`, `mae_radar`, `mae_interp`, `delta_rmse` (= `rmse_interp − rmse_radar`; > 0 = radar meglio).
- **`benchmark_C_curva.png`** — grafico errore vs distanza (se matplotlib è disponibile).
- **A video (stdout):** tabella per fascia + distanza di **incrocio (crossover)**.

---

## Logica di funzionamento

1. **Poligoni buoni:** per ogni poligono si fitta la retta su tutti i punti; si tiene solo se pendenza > 0 e R²_in ≥ `--min-r2`.
2. **Calendario AOI condiviso:** tutte le date osservate, ordinate.
3. **Generazione dei buchi:** per ogni coppia di ancore (data prima `before_t`, data dopo `after_t`) a distanza crescente (fino a `--kmax` date nascoste / `--max-days` giorni), per ciascun poligono si individuano le sue date interne al buco.
4. **RADAR:** la retta è fittata sui punti del poligono **fuori** dal buco (totale meno il blocco nascosto), poi predice l'NDVI di ogni data nascosta dal suo RVI.
5. **CIECO:** interpolazione lineare tra le due ancore del poligono.
6. **Accumulo errori data per data:** ogni data nascosta entra nella fascia corrispondente alla sua **distanza dall'ancora più vicina**; si accumulano RMSE e MAE per fascia.
7. **Crossover:** la prima fascia in cui `rmse_radar < rmse_interp` definisce la soglia oltre la quale conviene il radar.

---

## Requisiti

```
numpy
pandas
matplotlib   # opzionale, solo per il grafico
```

```bash
pip install numpy pandas matplotlib
```

---

## Note e rimandi

- Fornisce la soglia (~35 giorni) usata nella regola (ii)/(iii) del gap-filling. La retta radar qui è addestrata su **prima + dopo** il buco (scenario "archivio", bilaterale).
- La variante `benchmark_C_rolling_origin.py` ripete lo stesso benchmark ma allena la retta radar **solo sul passato** del buco: stress test più severo che sposta il crossover a ~50 giorni e circoscrive la validità della soglia di 35 giorni allo scenario archivio.
