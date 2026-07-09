# CSV — dati e risultati

Tutti i file usano il punto come separatore decimale. Le matrici *wide* hanno le date sulle
righe e i 154 poligoni (`P001`…`P154`) sulle colonne; le celle vuote sono valori mancanti.

Le date Sentinel-2 del periodo sono **241**, di cui **167** con almeno un NDVI valido.
Il calendario radar (RVI) si compone di **111** date.

---

## 1. Dati di partenza

| File | Contenuto | Prodotto da | Usato da |
|---|---|---|---|
| `Valid_date_S2.csv` | matrice booleana di copertura nuvolosa (241 × 154): per ogni data e poligono, indica se l'NDVI è utilizzabile. **Non** è la fonte dei conteggi di date valide (vedi nota sotto) | pipeline Sentinel-2 | ha guidato la ricerca delle scene Sentinel-1 |
| `mean_ndvi_per_polygon.csv` | NDVI medio per poligono e per data (241 × 154). È la **fonte di verità** delle 167 date valide | `Pipeline_Sentinel2.py` | `correlazione_rvi_ndvi.py`, `gapfilling_ndvi.py` |
| `mean_rvi_per_polygon_all_pol_no_inv.csv` | RVI medio per poligono, sulle 105 date S1 associate a date S2 **con nuvole** | `extract_mean_rvi_per_polygon.py` | `merge_rvi_master.py` |
| `mean_rvi_per_polygon_no_nuvole.csv` | RVI medio per poligono, sulle 67 date S1 associate a date S2 **serene** | `extract_mean_rvi_per_polygon.py` | `merge_rvi_master.py` |

> **Nota su `Valid_date_S2.csv`.** È la matrice booleana da cui è partita la ricerca delle scene
> radar, con un criterio di validità più severo. I conteggi di date NDVI valide citati nella
> relazione (167 / 74) derivano da `mean_ndvi_per_polygon.csv`, non da questo file.

---

## 2. Dati derivati (input degli script a valle)

| File | Contenuto | Prodotto da | Usato da |
|---|---|---|---|
| `mean_rvi_per_polygon_MASTER.csv` | serie RVI unica per poligono, 111 date: unione dei due file RVI, deduplicando le 61 date comuni (105 + 67 − 61) | `merge_rvi_master.py` | `correlazione_rvi_ndvi.py`, `gapfilling_ndvi.py` |
| `training_pairs_rvi_ndvi.csv` | 19.788 coppie (RVI, NDVI) accoppiate entro ±6 giorni: `poly_id`, `s2_date`, `s1_date`, `rvi`, `ndvi`, `delta_days` | `correlazione_rvi_ndvi.py` | i 4 script di validazione |

I due gruppi di scene RVI si distinguono **solo per l'origine del download** (date S2 nuvolose o
serene), non per il tipo di dato: l'RVI è lo stesso indice. Il MASTER è l'unica serie RVI
operativa, da cui correlazione, regressione e gap-filling pescano i valori.

---

## 3. Modello calibrato

| File | Contenuto | Prodotto da |
|---|---|---|
| `regression_per_polygon.csv` | coefficienti delle 154 rette `NDVI = a + b · RVI`: `slope`, `intercept`, `pearson_r`, `spearman_r`, `r2`, `n`, range di NDVI e RVI | `correlazione_rvi_ndvi.py` |

Da qui si ricavano i valori riportati in relazione: Pearson mediano **0,682**, R² mediano
**0,466**, **12** poligoni a pendenza negativa (risaie, *double-bounce*), **44** poligoni
"scollegati" (`slope < 0` **oppure** R² < 0,2) e **110** collegati.

---

## 4. Validazione

| File | Contenuto | Prodotto da |
|---|---|---|
| `cv_blocked_compare_per_polygon.csv` | per poligono: RMSE/MAE *out-of-sample* di retta unica vs stagionale (CV a blocchi temporali), più il fit su tutti i punti | `validate_correlazione_rvi_ndvi_blocchi.py` |
| `rolling_origin_summary.csv` | numeri finali della validazione "solo futuro": R²_oos **0,410**, RMSE, MAE e IC 95% bootstrap **[0,370 – 0,446]**, che contiene l'R² della block-CV (0,382) | `rolling_origin_rvi_ndvi.py` |
| `rolling_origin_per_data.csv` | per data di test: numero di predizioni e RMSE | `rolling_origin_rvi_ndvi.py` |
| `benchmark_C_per_durata.csv` | errore di radar e interpolazione per fascia di distanza (scenario **archivio**, ancore su entrambi i lati). Il *crossover* cade a **35 giorni**: è la soglia adottata nel gap-filling | `benchmark_C_rvi_vs_interpolazione.py` |
| `benchmark_C_rollingorigin_per_durata.csv` | stessa tabella con la retta radar allenata **solo sul passato**: il crossover si sposta a **55 giorni** | `benchmark_C_rolling_origin.py` |
| `benchmark_C_rollingorigin_stagionale.csv` | crossover stratificato per stagione: DJF nessun crossover entro i 55 giorni, MAM e SON ~45, JJA ~55 | `benchmark_C_rolling_origin.py` |
| `benchmark_C_rollingorigin_ic.csv` | IC 95% (bootstrap a blocchi sui poligoni) di `delta_rmse` per fascia: l'interpolazione è significativamente migliore fino a ~45 giorni, il radar solo dai 55 | `benchmark_C_rolling_origin.py` |

---

## 5. Output finale

| File | Contenuto | Prodotto da |
|---|---|---|
| `ndvi_gapfilled_long.csv` | serie NDVI ricostruita, formato lungo (37.114 celle = 241 × 154): `poly_id`, `s2_date`, `ndvi_finale`, **`sorgente`**, `distanza_giorni`, `ndvi_osservato` | `gapfilling_ndvi.py` |
| `ndvi_gapfilled_wide.csv` | la stessa serie in forma matriciale (241 × 154), pronta per il GIS | `gapfilling_ndvi.py` |
| `ndvi_gapfilled_riepilogo_sorgente.csv` | conteggi e percentuali per sorgente | `gapfilling_ndvi.py` |

### Ripartizione per sorgente

| Sorgente | Celle | Quota |
|---|---:|---:|
| `osservato` | 19.788 | 53,32 % |
| `interpolato` | 11.685 | 31,48 % |
| `interpolato_inaffidabile` | 4.717 | 12,71 % |
| `radar` | 660 | 1,78 % |
| `non_stimabile` | 264 | 0,71 % |

La colonna **`sorgente`** è parte integrante del prodotto: le celle `osservato` riproducono
esattamente il dato Sentinel-2 originale, mentre le altre sono stime. Le celle
`interpolato_inaffidabile` appartengono ai 44 poligoni scollegati e vanno pesate o escluse;
le `non_stimabile` sono ai bordi della serie, prive di un'osservazione di riferimento.
