#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rolling_origin_rvi_ndvi.py

VALIDAZIONE ROLLING-ORIGIN (origine espansa) della retta unica RVI->NDVI.

Serve a CONFERMARE la robustezza del risultato della block-CV (R2_oos ~0.38 della
block-CV), mostrando che NON dipende dal particolare taglio in 5 blocchi. La
block-CV nascondeva un blocco IN MEZZO e si allenava su prima E dopo; qui invece
si predice solo il FUTURO: ci si allena su tutto il passato fino a una data
(l'"origine"), si predice l'osservazione successiva, poi si sposta l'origine in
avanti di una data e si ripete. Origine ESPANSA = il training accumula tutto lo
storico disponibile (non una finestra recente).

E' piu' realistica in senso operativo (si prevede il futuro, non il passato) e,
se il numero resta intorno a 0.38, conferma il risultato della block-CV da un
secondo angolo, indipendente.

  Modello: retta unica per poligono (RVI->NDVI), come nel punto A/B.
  Poligoni: TUTTI (non solo i 110 "buoni"): il filtro dei 110 riguarda il
            benchmark C, non la conferma del risultato A.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - training_pairs_rvi_ndvi.csv  (formato lungo: poly_id, s2_date, rvi, ndvi)
  - CLI: --pairs, --min-train (def 10: min osservazioni passate per predire),
         --ref-r2 (def 0.382: R2_oos della block-CV, per il confronto), --outdir
OUTPUT
  - rolling_origin_per_data.csv : per data di test -> n predizioni, rmse
  - rolling_origin_summary.csv  : una riga coi numeri finali + IC 95% (per la tesi)
  - rolling_origin_curva.png    : R2 cumulato vs tempo (mostra la stabilita')
  - a video: R2_oos / RMSE / MAE complessivi + IC 95% bootstrap, confronto con block-CV

METODO (per ogni poligono, sul suo calendario ordinato)
  Per ogni osservazione al tempo t:
    training = osservazioni del poligono a date STRETTAMENTE precedenti t
               (le date uguali a t sono escluse: niente leakage);
    se il training ha >= --min-train punti, si fitta la retta e si predice
    l'osservazione t. Spostare t in avanti = spostare l'origine in avanti.
  Le prime osservazioni di ogni poligono non sono predicibili (manca il passato):
  e' una caratteristica intrinseca della validazione "solo futuro".
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def linfit(x, y):
    """Retta OLS y~x (closed form); None se degenere."""
    n = len(x)
    if n < 2:
        return None
    sx = x.sum(); sy = y.sum(); sxx = (x * x).sum(); sxy = (x * y).sum()
    d = n * sxx - sx * sx
    if d == 0:
        return None
    slope = (n * sxy - sx * sy) / d
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _metrics(pred, obs):
    """R2 (media globale) / RMSE / MAE su coppie pred-obs."""
    res = pred - obs
    rmse = float(np.sqrt(np.mean(res ** 2)))
    mae = float(np.mean(np.abs(res)))
    ss_res = float(np.sum(res ** 2))
    ss_tot = float(np.sum((obs - obs.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return r2, rmse, mae


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="training_pairs_rvi_ndvi.csv")
    ap.add_argument("--min-train", type=int, default=10,
                    help="min osservazioni passate (training) per fare una predizione.")
    ap.add_argument("--ref-r2", type=float, default=0.382,
                    help="R2_oos di riferimento della block-CV, per il confronto.")
    ap.add_argument("--n-boot", type=int, default=1000,
                    help="ripetizioni bootstrap a blocchi sui poligoni per gli IC (0 = disattiva).")
    ap.add_argument("--seed", type=int, default=42, help="seme RNG per il bootstrap.")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    p = pd.read_csv(args.pairs, parse_dates=["s2_date"])
    p = p.dropna(subset=["rvi", "ndvi"])          # difensivo: NaN avvelenerebbero le somme OLS
    p["d"] = p["s2_date"].values.astype("datetime64[D]").astype(np.int64)

    # ---- rolling-origin espansa, per poligono ----
    dates_pred, pred_all, obs_all, polys_pred = [], [], [], []
    n_pred = n_skip = 0
    for poly, g in p.groupby("poly_id"):
        g = g.sort_values("d")
        dd = g.d.values.astype(np.int64)
        rr = g.rvi.values.astype(float)
        nn = g.ndvi.values.astype(float)
        for t in range(len(dd)):
            # training = SOLO date strettamente precedenti (escluse le date == dd[t])
            cut = np.searchsorted(dd, dd[t], side="left")
            if cut < args.min_train:
                n_skip += 1; continue
            f = linfit(rr[:cut], nn[:cut])
            if f is None:
                n_skip += 1; continue
            s_, ic_ = f
            dates_pred.append(dd[t])
            pred_all.append(s_ * rr[t] + ic_)
            obs_all.append(nn[t])
            polys_pred.append(poly)
            n_pred += 1

    dates_pred = np.array(dates_pred)
    pred_all = np.array(pred_all)
    obs_all = np.array(obs_all)
    polys_pred = np.array(polys_pred)

    # ---- metriche complessive ----
    r2, rmse, mae = _metrics(pred_all, obs_all)

    print(f"=== ROLLING-ORIGIN (origine espansa, retta unica, TUTTI i poligoni) ===")
    print(f"  predizioni: {n_pred}   (saltate per training<{args.min_train}: {n_skip})")
    print(f"  RMSE={rmse:.4f}   MAE={mae:.4f}   R2_oos={r2:.3f}")
    print(f"\n  confronto:  block-CV R2_oos = {args.ref_r2:.3f}  (riferimento)")
    diff = r2 - args.ref_r2
    verdetto = "CONFERMA" if abs(diff) <= 0.05 else "DIVERGE"
    print(f"              rolling-origin = {r2:.3f}   (scarto {diff:+.3f} -> {verdetto})")

    # ---- bootstrap a blocchi sui poligoni: IC 95% per R2/RMSE/MAE ----
    # Le osservazioni entro uno stesso poligono sono correlate: l'unita' statistica
    # indipendente e' il POLIGONO. Si ricampionano i poligoni con reinserimento e si
    # ricalcola la metrica pooled a ogni ripetizione (block bootstrap).
    ci = {}
    if args.n_boot > 0 and n_pred > 0:
        uniq = np.unique(polys_pred)
        idx_by_poly = {u: np.where(polys_pred == u)[0] for u in uniq}
        rng = np.random.default_rng(args.seed)
        bs = {"r2": [], "rmse": [], "mae": []}
        for _ in range(args.n_boot):
            samp = rng.choice(uniq, size=len(uniq), replace=True)
            idx = np.concatenate([idx_by_poly[u] for u in samp])
            r2b, rmseb, maeb = _metrics(pred_all[idx], obs_all[idx])
            bs["r2"].append(r2b); bs["rmse"].append(rmseb); bs["mae"].append(maeb)
        for k, v in bs.items():
            lo, hi = np.percentile(v, [2.5, 97.5])
            ci[k] = (float(lo), float(hi))
        print(f"\n  IC 95% bootstrap a blocchi ({len(uniq)} poligoni, {args.n_boot} rip.):")
        print(f"    R2_oos [{ci['r2'][0]:.3f}, {ci['r2'][1]:.3f}]   "
              f"RMSE [{ci['rmse'][0]:.4f}, {ci['rmse'][1]:.4f}]   "
              f"MAE [{ci['mae'][0]:.4f}, {ci['mae'][1]:.4f}]")
        ref_in = ci['r2'][0] <= args.ref_r2 <= ci['r2'][1]
        print(f"    block-CV R2={args.ref_r2:.3f} -> {'DENTRO' if ref_in else 'FUORI'} l'IC rolling-origin")

    # ---- summary a una riga (numeri finali riproducibili per la tesi) ----
    summ = {"n_pred": n_pred, "n_skip": n_skip, "r2_oos": r2, "rmse": rmse, "mae": mae,
            "ref_r2_blockcv": args.ref_r2}
    if ci:
        summ.update({"r2_ic_lo": ci["r2"][0], "r2_ic_hi": ci["r2"][1],
                     "rmse_ic_lo": ci["rmse"][0], "rmse_ic_hi": ci["rmse"][1],
                     "mae_ic_lo": ci["mae"][0], "mae_ic_hi": ci["mae"][1]})
    pd.DataFrame([summ]).to_csv(outdir / "rolling_origin_summary.csv", index=False)
    print(f"  -> {outdir/'rolling_origin_summary.csv'}")

    # ---- CSV per data di test ----
    df = pd.DataFrame({"d": dates_pred, "pred": pred_all, "obs": obs_all})
    df["s2_date"] = df["d"].values.astype("datetime64[D]")
    per_date = (df.groupby("s2_date")
                  .apply(lambda x: pd.Series({
                      "n": len(x),
                      "rmse": float(np.sqrt(np.mean((x.pred - x.obs) ** 2))),
                  }))
                  .reset_index())
    per_date.to_csv(outdir / "rolling_origin_per_data.csv", index=False)
    print(f"  -> {outdir/'rolling_origin_per_data.csv'}")

    # ---- grafico: R2 CUMULATO vs tempo (mostra la stabilita') ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        order = np.argsort(dates_pred)
        do = dates_pred[order]; pr = pred_all[order]; ob = obs_all[order]
        # R2 cumulato calcolato in ordine temporale
        n = np.arange(1, len(ob) + 1)
        csum_o = np.cumsum(ob); csum_o2 = np.cumsum(ob ** 2)
        csum_res = np.cumsum((pr - ob) ** 2)
        sstot = csum_o2 - csum_o ** 2 / n
        with np.errstate(invalid="ignore", divide="ignore"):
            r2_cum = 1 - csum_res / sstot
        x_dates = do.astype("datetime64[D]")
        fig, ax = plt.subplots(figsize=(7.5, 4.2))
        ax.plot(x_dates, r2_cum, color="#185FA5", lw=1.5, label="R\u00b2 cumulato (rolling-origin)")
        ax.axhline(args.ref_r2, ls="--", color="#639922", lw=1.5, label=f"block-CV = {args.ref_r2:.3f}")
        ax.axhline(r2, ls=":", color="#185FA5", lw=1, alpha=0.7)
        ax.set_ylim(0, max(0.6, np.nanmax(r2_cum[20:]) * 1.1 if len(r2_cum) > 20 else 0.6))
        ax.set_xlabel("Data di test (origine spostata in avanti)")
        ax.set_ylabel("R\u00b2 out-of-sample cumulato")
        ax.set_title("Rolling-origin: stabilit\u00e0 dell'R\u00b2 nel tempo")
        ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / "rolling_origin_curva.png", dpi=130)
        print(f"  -> {outdir/'rolling_origin_curva.png'}")
    except Exception as e:
        print(f"  (grafico non generato: {e})")


if __name__ == "__main__":
    main()
