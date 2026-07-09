# benchmark_C_rolling_origin.py

> Variante **rolling-origin** del Benchmark C: stress test della soglia di crossover, con stratificazione stagionale e IC bootstrap.

---

## Descrizione

Ripete il confronto tra riempitore radar e interpolazione temporale di `benchmark_C_rvi_vs_interpolazione.py`, ma in un regime più severo: la retta RVI→NDVI viene allenata **solo sul passato** del buco (osservazioni a date precedenti l'inizio della lacuna), non più su prima *e* dopo.

Serve a **consolidare la soglia di crossover** (~35 giorni) con lo stesso spirito della rolling-origin usata per l'R²: se il radar batte comunque l'interpolazione oltre una certa distanza anche predicendo il futuro, la soglia è robusta.

---

## Contesto

### Asimmetria voluta (da dichiarare)

- **RADAR** → allenato **solo sul passato** del buco (regime rolling-origin).
- **CIECO** (interpolazione) → usa l'osservazione **prima e dopo** il buco: è la sua natura, non può fare altrimenti, e coincide con la regola di gap-filling effettivamente adottata.

Il radar è quindi messo in condizioni più dure dell'interpolazione. Se vince lo stesso oltre soglia, il risultato è **conservativo**, cioè a favore della robustezza.

### Rapporto con il benchmark C originale

Rispetto a `benchmark_C_rvi_vs_interpolazione.py` cambia **una sola cosa**: il training della retta radar (prima "fuori dal buco" = prima + dopo; ora "solo prima del buco"). Tutto il resto — generazione dei buchi, metrica di distanza, selezione dei poligoni buoni, valutazione data per data, taglio delle fasce rade — è invariato.

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
| `--min-n` | `1000` | minimo punti per riportare una fascia |
| `--min-n-season` | `250` | minimo punti per fascia **stagionale** (più basso: i punti si dividono in 4 stagioni) |
| `--min-train` | `10` | minimo osservazioni **passate** per allenare la retta radar |
| `--ref-cross` | `35` | crossover di riferimento del benchmark C originale (giorni) |
| `--n-boot` | `1000` | ripetizioni del bootstrap a blocchi sui poligoni (`0` disattiva) |
| `--seed` | `42` | seme RNG per il bootstrap |
| `--outdir` | `.` | cartella di output |

---

## Output

- **`benchmark_C_rollingorigin_per_durata.csv`** — per fascia di distanza: `n_punti`, `rmse_radar`, `rmse_interp`, `mae_radar`, `mae_interp`, `delta_rmse`.
- **`benchmark_C_rollingorigin_stagionale.csv`** — stessa tabella stratificata per stagione (DJF/MAM/JJA/SON).
- **`benchmark_C_rollingorigin_ic.csv`** — IC 95% (bootstrap a blocchi) di `delta_rmse` per fascia.
- **`benchmark_C_rollingorigin_curva.png`** — curva errore vs distanza.
- **A video (stdout):** tabella per fascia, crossover, crossover per stagione, IC bootstrap e confronto con il crossover del benchmark C originale.

---

## Logica di funzionamento

1. **Poligoni "buoni":** come nel benchmark C (pendenza > 0 e R²_in ≥ `--min-r2`). Per ciascuno si precalcolano le **somme cumulate** (*prefix sums*) di RVI e NDVI, così da poter fittare la retta solo sul passato in modo efficiente.
2. **Generazione dei buchi:** identica al benchmark C (coppie di ancore a distanza crescente sul calendario AOI condiviso).
3. **RADAR:** il training è costituito dalle sole osservazioni del poligono a date **≤ inizio del buco**; se sono meno di `--min-train`, la predizione viene saltata.
4. **CIECO:** interpolazione lineare tra le ancore prima e dopo (invariato).
5. **Accumulo errori data per data**, per fascia di distanza dall'ancora più vicina; il crossover è la prima fascia in cui `rmse_radar < rmse_interp`. Gli errori sono accumulati anche **per poligono** (per il bootstrap) e **per stagione** (per la stratificazione).

### Bootstrap a blocchi sui poligoni

`n_punti` conta **scenari**, non osservazioni indipendenti: la stessa osservazione viene ricostruita dentro più buchi diversi. L'unità statistica indipendente è il **poligono**. Per gli IC si ricampionano i poligoni con reinserimento, si ri-aggregano le somme per fascia e si ricalcola tutto, ottenendo intervalli corretti nonostante l'`n_punti` gonfiato. Vengono bootstrappati sia `delta_rmse` per fascia sia il crossover stesso.

### Crossover per stagione

La tabella viene ricalcolata separatamente per DJF/MAM/JJA/SON. È un'analisi **descrittiva e sito-specifica**: il modello resta la retta unica: la stagionalizzazione del *modello* era già stata testata e scartata dalla block-CV, perché non migliorava l'R² fuori campione. Qui si stratifica solo la *soglia*, per mostrare come la distanza di convenienza del radar vari con la stagione.

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

- Il crossover che ne risulta si sposta a ~50 giorni (contro i ~35 del benchmark C originale): il regime "solo passato" è più severo, quindi il radar diventa competitivo più tardi. Questo **circoscrive la validità della soglia di 35 giorni allo scenario "archivio"** (ricostruzione storica, con osservazioni su entrambi i lati della lacuna), che è esattamente il contesto d'uso del gap-filling.
- Da leggere in coppia con `benchmark_C_rvi_vs_interpolazione.py`. Lo stesso principio "solo futuro" e lo stesso bootstrap a blocchi sono applicati alla validazione dell'R² in `rolling_origin_rvi_ndvi.py`.
