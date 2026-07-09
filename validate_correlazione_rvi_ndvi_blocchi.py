#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_correlazione_rvi_ndvi_blocchi.py

Validazione predittiva della relazione RVI->NDVI con cross-validation A BLOCCHI
TEMPORALI: si nascondono blocchi contigui di date S2 e si ricostruiscono, perche'
e' esattamente lo scenario reale del gap-filling (un buco nuvoloso = una fila di
date consecutive mancanti per tutta l'AOI). Una CV casuale (k-fold con shuffle)
"spalmerebbe" le date nei fold lasciando i vicini temporali nel training, con un
R2 out-of-sample ottimistico; con i blocchi il numero e' onesto.

I due modelli valutati sono:
  A) retta unica per poligono (RVI -> NDVI)
  B) quattro rette stagionali per poligono (DJF/MAM/JJA/SON), con fallback
     alla retta unica se la stagione ha < --min-season coppie.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - File: training_pairs_rvi_ndvi.csv  (formato LUNGO: una riga per coppia
    poligono-data, prodotto da correlazione_rvi_ndvi.py).
    Colonne richieste:
        poly_id   identificativo poligono (es. P001..P154)
        s2_date   data dell'acquisizione Sentinel-2 (definisce i blocchi)
        rvi       RVI Sentinel-1 medio del poligono in quella data (predittore)
        ndvi      NDVI Sentinel-2 medio del poligono in quella data (target)
  - Parametri CLI:
        --pairs        percorso del CSV di input (default sopra)
        --folds        numero di blocchi temporali contigui (default 5)
        --min-season   min coppie per stagione per la retta stagionale (default 6)
        --outdir       cartella di output (default ".")

OUTPUT
  - File: cv_blocked_compare_per_polygon.csv  (una riga per poligono)
        poly_id, n, rmse_base, rmse_seas, mae_base, mae_seas, delta_rmse,
        slope, intercept, pearson_r, r2_in
        - rmse_* / mae_* : errore OUT-OF-SAMPLE (predizione) dei due modelli
        - delta_rmse = rmse_base - rmse_seas; >0 = la stagionale e' migliore
        - slope/intercept/pearson_r/r2_in : fit su TUTTI i punti del poligono
          (descrivono la RELAZIONE, per il criterio di esclusione:
           pendenza<0  oppure  r2_in<0.2  -> campo "scollegato")
  - A video (stdout):
        * diagnostica dei blocchi (n. date e ampiezza in giorni di ciascuno)
        * metriche globali out-of-sample dei due modelli (RMSE/MAE/R2_oos):
          retta unica  vs  stagionale
        * n. poligoni in cui la stagionale migliora + riduzione mediana di RMSE

USO
---
  python validate_correlazione_rvi_ndvi_blocchi.py \
      --pairs training_pairs_rvi_ndvi.csv \
      --folds 5 --min-season 6 --outdir .
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def season_of(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def fit_pred(x_tr, y_tr, x_te):
    """Retta y~x sui dati di training; fallback alla media se degenere."""
    if len(x_tr) < 2 or np.std(x_tr) == 0:
        return np.full(len(x_te), np.mean(y_tr) if len(y_tr) else np.nan)
    s, i = np.polyfit(x_tr, y_tr, 1)
    return s * x_te + i


def metrics(pred, obs):
    rmse = float(np.sqrt(np.mean((pred - obs) ** 2)))
    mae = float(np.mean(np.abs(pred - obs)))
    r2 = float(1 - np.sum((obs - pred) ** 2) / np.sum((obs - obs.mean()) ** 2))
    return rmse, mae, r2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="training_pairs_rvi_ndvi.csv")
    ap.add_argument("--folds", type=int, default=5,
                    help="numero di BLOCCHI temporali contigui in cui spezzare "
                         "il calendario delle date S2 (CV a blocchi, non casuale).")
    ap.add_argument("--min-season", type=int, default=6,
                    help="min coppie per stagione per fittare una retta stagionale, "
                         "altrimenti fallback alla retta unica del poligono.")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    p = pd.read_csv(args.pairs, parse_dates=["s2_date"])
    p["season"] = p["s2_date"].dt.month.map(season_of)

    # ===================== BLOCCHI TEMPORALI =====================
    # Una sola volta, su TUTTA l'AOI: le nuvole coprono l'intera area, quindi il
    # blocco e' lo stesso per tutti i 154 poligoni. Si taglia sul calendario delle
    # DATE uniche (non sulle righe): una data finisce tutta nel test o tutta nel train.
    uniq_dates = np.sort(p["s2_date"].unique())                 # date uniche, ordinate
    pos_blocks = np.array_split(np.arange(len(uniq_dates)), args.folds)  # K blocchi di posizioni
    blk_of_pos = np.empty(len(uniq_dates), dtype=int)
    for k, idx in enumerate(pos_blocks):
        blk_of_pos[idx] = k
    date_block = pd.Series(blk_of_pos, index=pd.DatetimeIndex(uniq_dates))  # data -> blocco
    p["block"] = p["s2_date"].map(date_block).astype(int)       # ogni riga -> suo blocco
    # diagnostica blocchi (date e ampiezza in giorni di ciascun blocco)
    print(f"=== BLOCCHI TEMPORALI  ({args.folds} blocchi su {len(uniq_dates)} date S2) ===")
    for k, idx in enumerate(pos_blocks):
        d0, d1 = uniq_dates[idx[0]], uniq_dates[idx[-1]]
        span = (d1 - d0) / np.timedelta64(1, "D")
        print(f"  blocco {k}: {len(idx):3d} date  "
              f"{str(d0)[:10]} -> {str(d1)[:10]}  ({span:.0f} giorni)")
    # ===========================================================================

    rows = []
    pred_b, pred_s, y_all = [], [], []
    for poly, g in p.groupby("poly_id"):
        # fold k = le righe del poligono che cadono nel blocco temporale k
        g = g.reset_index(drop=True)
        blk = g["block"].values                                 # blocco di ogni riga
        folds = [np.where(blk == k)[0] for k in range(args.folds)]
        yb = np.full(len(g), np.nan); ys = np.full(len(g), np.nan)
        for te in folds:
            te = np.array(te); tr = np.setdiff1d(np.arange(len(g)), te)
            # salta i blocchi in cui il poligono non ha punti, o in cui non
            # resta nulla per il training (poligono tutto in un blocco).
            if len(te) == 0 or len(tr) == 0:
                continue
            Xtr, Ytr, Xte = g.rvi.values[tr], g.ndvi.values[tr], g.rvi.values[te]
            yb[te] = fit_pred(Xtr, Ytr, Xte)                      # modello A
            for j in te:                                          # modello B
                m = g.season.values[tr] == g.season.iloc[j]
                if m.sum() >= args.min_season:
                    ys[j] = fit_pred(Xtr[m], Ytr[m], np.array([g.rvi.iloc[j]]))[0]
                else:
                    ys[j] = fit_pred(Xtr, Ytr, np.array([g.rvi.iloc[j]]))[0]
        y = g.ndvi.values
        # fit su TUTTI i punti del poligono: descrive la RELAZIONE (non la
        # predizione) e serve al criterio di esclusione dei campi "scollegati"
        # -> pendenza<0 (es. riso allagato, double-bounce) oppure R2<0.2.
        xf = g.rvi.values
        if len(xf) >= 2 and np.std(xf) > 0:
            slope, intercept = np.polyfit(xf, y, 1)
            pred_full = slope * xf + intercept
            ss_tot = np.sum((y - y.mean()) ** 2)
            r2_in = float(1 - np.sum((y - pred_full) ** 2) / ss_tot) if ss_tot > 0 else np.nan
            pearson = float(np.corrcoef(xf, y)[0, 1])
        else:
            slope = intercept = r2_in = pearson = np.nan
        # nanmean: alcune righe possono restare non predette (blocchi saltati)
        rows.append({
            "poly_id": poly, "n": len(g),
            "rmse_base": np.sqrt(np.nanmean((yb - y) ** 2)),
            "rmse_seas": np.sqrt(np.nanmean((ys - y) ** 2)),
            "mae_base": np.nanmean(np.abs(yb - y)),
            "mae_seas": np.nanmean(np.abs(ys - y)),
            # descrizione della relazione (fit su tutti i punti del poligono)
            "slope": slope, "intercept": intercept,
            "pearson_r": pearson, "r2_in": r2_in,
        })
        pred_b.append(yb); pred_s.append(ys); y_all.append(y)

    res = pd.DataFrame(rows)
    res["delta_rmse"] = res.rmse_base - res.rmse_seas
    res.to_csv(outdir / "cv_blocked_compare_per_polygon.csv", index=False)

    yb = np.concatenate(pred_b); ys = np.concatenate(pred_s); y = np.concatenate(y_all)
    # maschera finita: confronta i due modelli SUGLI STESSI punti predetti
    ok = np.isfinite(yb) & np.isfinite(ys) & np.isfinite(y)
    n_excl = int((~ok).sum())
    yb, ys, y = yb[ok], ys[ok], y[ok]

    print(f"\n=== VALIDAZIONE PREDITTIVA A BLOCCHI  "
          f"({args.folds} blocchi, {len(y)} punti"
          + (f", {n_excl} esclusi" if n_excl else "") + ") ===")
    for name, pr in [("retta unica", yb), ("stagionale", ys)]:
        rmse, mae, r2 = metrics(pr, y)
        print(f"  {name:12s}  RMSE={rmse:.4f}  MAE={mae:.4f}  R2_oos={r2:.3f}")
    imp = res.delta_rmse
    print(f"\n  stagionale migliore in {int((imp > 0).sum())}/{len(res)} poligoni "
          f"(mediana -{100*(imp/res.rmse_base).median():.1f}% RMSE)")
    # sintesi campi "scollegati" (criterio di esclusione dal gap-filling)
    neg = int((res.slope < 0).sum())
    lowr2 = int((res.r2_in < 0.2).sum())
    flag = int(((res.slope < 0) | (res.r2_in < 0.2)).sum())
    print(f"\n  campi 'scollegati':  pendenza<0 in {neg},  R2_in<0.2 in {lowr2},  "
          f"unione = {flag}/{len(res)} (da escludere/segnalare per il gap-filling)")
    print(f"  Pearson r mediano = {res.pearson_r.median():.3f}  "
          f"(atteso ~0.682 se coincide con correlazione_rvi_ndvi.py)")
    print(f"  -> {outdir/'cv_blocked_compare_per_polygon.csv'}")


if __name__ == "__main__":
    main()
