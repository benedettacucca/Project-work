# rolling_origin_rvi_ndvi.py

> Validazione **rolling-origin** (origine espansa) della retta unica RVI→NDVI, con intervalli di confidenza via bootstrap a blocchi.

---

## Descrizione

Lo script valuta la capacità predittiva della retta RVI→NDVI predicendo **solo il futuro**: per ogni poligono ci si addestra su tutto lo storico fino a una certa data (l'"origine"), si predice l'osservazione successiva, poi si sposta l'origine in avanti di una data e si ripete. *Origine espansa* significa che il training accumula tutto il passato disponibile (non una finestra recente).

---

## Contesto

Serve a **confermare la robustezza** del risultato della block-CV (`validate_correlazione_rvi_ndvi_blocchi.py`, R²_oos ≈ 0,38), mostrando che non dipende dal particolare taglio in blocchi. La block-CV nasconde un blocco *in mezzo* e si allena su passato **e** futuro; qui si predice unicamente il futuro — regime più realistico in senso operativo. Se il numero resta intorno a 0,38, il risultato è confermato da un secondo angolo indipendente.

Il modello è la retta unica per poligono; vengono usati **tutti** i poligoni (il filtro dei 110 "buoni" riguarda il benchmark C, non questa conferma).

---

## Input

- **`training_pairs_rvi_ndvi.csv`** (formato lungo: `poly_id`, `s2_date`, `rvi`, `ndvi`).

### Parametri CLI

| Parametro | Default | Descrizione |
|---|---|---|
| `--pairs` | `training_pairs_rvi_ndvi.csv` | CSV di input |
| `--min-train` | `10` | minimo osservazioni passate per fare una predizione |
| `--ref-r2` | `0.382` | R²_oos di riferimento della block-CV, per il confronto |
| `--n-boot` | `1000` | ripetizioni del bootstrap a blocchi sui poligoni (`0` disattiva) |
| `--seed` | `42` | seme RNG per il bootstrap |
| `--outdir` | `.` | cartella di output |

---

## Output

- **`rolling_origin_summary.csv`** — una riga coi numeri finali riproducibili: `n_pred`, `n_skip`, `r2_oos`, `rmse`, `mae`, `ref_r2_blockcv` e, se il bootstrap è attivo, gli estremi degli IC 95%.
- **`rolling_origin_per_data.csv`** — per data di test: numero di predizioni e RMSE.
- **`rolling_origin_curva.png`** — R² **cumulato** vs tempo (mostra la stabilità nel tempo).
- **A video (stdout):** R²_oos / RMSE / MAE complessivi, IC 95% bootstrap, confronto con la block-CV (verdetto CONFERMA se lo scarto è ≤ 0,05) e verifica se il valore della block-CV cade **dentro** l'IC.

---

## Logica di funzionamento

Per ciascun poligono, sul suo calendario ordinato, e per ogni osservazione al tempo *t*:

1. **Training** = osservazioni del poligono a date **strettamente precedenti** *t* (le date uguali a *t* sono escluse, per evitare *leakage*).
2. Se il training ha almeno `--min-train` punti, si fitta la retta e si predice l'osservazione *t*.
3. Spostare *t* in avanti equivale a spostare l'origine in avanti.

Le prime osservazioni di ogni poligono non sono predicibili (manca il passato): è una caratteristica intrinseca della validazione "solo futuro".

### Bootstrap a blocchi sui poligoni

Le osservazioni entro uno stesso poligono sono correlate: l'unità statistica indipendente è il **poligono**, non la singola coppia. Per gli IC si ricampionano i poligoni **con reinserimento** e si ricalcola la metrica *pooled* a ogni ripetizione (*block bootstrap*), ottenendo intervalli corretti nonostante l'elevato numero di coppie.

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

- Risultato atteso ≈ 0,41, coerente con la block-CV (0,382): due strategie concettualmente diverse convergono sullo stesso valore.
- Da leggere in coppia con `validate_correlazione_rvi_ndvi_blocchi.py` (la validazione principale). Lo stesso spirito "solo passato" è applicato al benchmark del crossover in `benchmark_C_rolling_origin.py`, che usa un bootstrap a blocchi analogo.
