# validate_correlazione_rvi_ndvi_blocchi.py

> Validazione predittiva *out-of-sample* della relazione RVI→NDVI mediante cross-validation a **blocchi temporali**.

---

## Descrizione

Lo script valuta quanto bene la retta RVI→NDVI (calibrata per poligono) predice l'NDVI su osservazioni **non** usate in addestramento. La cross-validation non è casuale (k-fold con shuffle) ma **a blocchi temporali contigui**: si nascondono blocchi di date S2 consecutive e si ricostruiscono.

Vengono confrontati due modelli:

- **A — retta unica** per poligono (RVI→NDVI);
- **B — quattro rette stagionali** per poligono (DJF/MAM/JJA/SON), con *fallback* alla retta unica se la stagione ha meno di `--min-season` coppie.

---

## Contesto

Una CV casuale sovrastima le prestazioni: due date vicine nel tempo sono fortemente autocorrelate, quindi lasciarne una nel training e una nel test fa filtrare informazione (*leakage*) e gonfia l'R² fuori campione. La CV a blocchi replica invece lo **scenario reale del gap-filling** — un buco nuvoloso copre tutta l'AOI, cioè una fila di date consecutive mancanti — e restituisce un R²_oos onesto e difendibile.

---

## Input

- **`training_pairs_rvi_ndvi.csv`** (formato lungo, prodotto da `correlazione_rvi_ndvi.py`), con colonne:
  - `poly_id` — identificativo poligono (P001…P154)
  - `s2_date` — data Sentinel-2 (definisce i blocchi)
  - `rvi` — RVI Sentinel-1 medio del poligono in quella data (predittore)
  - `ndvi` — NDVI Sentinel-2 medio del poligono in quella data (target)

### Parametri CLI

| Parametro | Default | Descrizione |
|---|---|---|
| `--pairs` | `training_pairs_rvi_ndvi.csv` | CSV di input |
| `--folds` | `5` | numero di **blocchi temporali contigui** in cui spezzare il calendario S2 |
| `--min-season` | `6` | minimo coppie per stagione per fittare la retta stagionale (altrimenti fallback alla retta unica) |
| `--outdir` | `.` | cartella di output |

---

## Output

- **`cv_blocked_compare_per_polygon.csv`** — una riga per poligono:
  - `rmse_base` / `rmse_seas`, `mae_base` / `mae_seas` — errore **out-of-sample** dei due modelli
  - `delta_rmse = rmse_base − rmse_seas` (>0 → la stagionale migliora)
  - `slope`, `intercept`, `pearson_r`, `r2_in` — fit su **tutti** i punti del poligono (descrivono la *relazione*, non la predizione)
- **A video (stdout):**
  - diagnostica dei blocchi (numero di date e ampiezza in giorni di ciascuno)
  - metriche globali out-of-sample dei due modelli (RMSE / MAE / R²_oos): retta unica vs stagionale
  - numero di poligoni in cui la stagionale migliora + riduzione mediana di RMSE
  - sintesi dei campi **"scollegati"** da escludere/segnalare nel gap-filling: pendenza < 0 **oppure** R²_in < 0,2

---

## Logica di funzionamento

1. **Costruzione dei blocchi** (una sola volta, condivisi da tutta l'AOI): le date S2 uniche e ordinate vengono tagliate in `--folds` blocchi contigui; una data finisce *interamente* nel test o nel train (mai spezzata tra poligoni), coerentemente col fatto che la nuvola copre tutta l'area.
2. **Per ciascun poligono**, il fold *k* è costituito dalle sue righe che cadono nel blocco *k*. Si addestra sulle righe fuori dal blocco e si predice quelle dentro. I blocchi in cui il poligono non ha punti (o in cui non resterebbe training) vengono saltati.
3. **Modello A**: una retta RVI→NDVI sul training. **Modello B**: retta della stagione della data di test, con fallback alla retta unica se la stagione ha meno di `--min-season` coppie.
4. **Aggregazione** robusta ai NaN (alcune righe possono restare non predette), confrontando i due modelli **sugli stessi punti** effettivamente predetti.
5. **Criterio di esclusione**: per ogni poligono si fitta anche una retta su *tutti* i punti per ricavare `slope` e `r2_in`; i campi con pendenza < 0 (es. risaie allagate, *double-bounce*) o R²_in < 0,2 sono marcati come "scollegati".

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

- È lo script che produce il numero di **R²_oos della block-CV** citato in metodologia (≈ 0,38) e il **criterio collegato/scollegato** (slope > 0 e R²_in ≥ 0,2) poi usato dal gap-filling.
- Il **modello stagionale (B)** è incluso come confronto: la validazione a blocchi serve a stabilire se stagionalizzare conviene. Va letto in coppia con `rolling_origin_rvi_ndvi.py`, che conferma lo stesso R²_oos da un secondo angolo (validazione solo-futuro).
- Il fit su tutti i punti (`pearson_r`, `r2_in`) deve coincidere con quanto prodotto da `correlazione_rvi_ndvi.py` (Pearson mediano atteso ≈ 0,682): utile come controllo di coerenza.
