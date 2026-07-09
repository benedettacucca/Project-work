#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gapfilling_ndvi.py

GAP-FILLING della serie NDVI (applicazione del metodo validato).

A differenza della validazione (A/B/C, rolling-origin) qui NON si misura un
errore: si riempiono i buchi VERI (date nuvolose dove l'NDVI manca) producendo
la serie NDVI continua, secondo la regola ibrida definita con il tutor.

REGOLA (cascata, per ogni cella poligono-data; si applica la prima che ricorre):
  1. NDVI osservato            -> si tiene il valore S2 reale        [osservato]
  2. poligono "scollegato"     -> solo interpolazione, con flag      [interpolato_inaffidabile]
  3. buco <= --soglia (35 gg)  -> interpolazione temporale (solo S2) [interpolato]
  4. buco  > --soglia          -> regressione RVI->NDVI (S1)         [radar]
  (distanza = giorni dall'osservazione NDVI utile piu' vicina, come nel benchmark C)
  NB: il controllo di "scollegato" precede quello sulla distanza: nei poligoni
  scollegati ogni cella mancante e' interpolata e marcata inaffidabile, a
  prescindere dall'ampiezza del buco.

Casi particolari:
  - buco > soglia ma nessun RVI entro --tol-rvi giorni -> fallback interpolazione
    [interpolato_no_rvi];
  - bordo serie senza un'ancora (prima o dopo) e niente radar -> [non_stimabile].

I poligoni "scollegati" (pendenza<0 oppure R2_in<--min-r2) sono i 44 su cui il
radar non e' affidabile: mai usato, solo interpolazione con flag di inaffidabilita'.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - mean_ndvi_per_polygon.csv   (matrice NDVI da riempire: date x poligoni, ';')
  - mean_rvi_per_polygon_MASTER.csv (matrice RVI ovunque: date x poligoni, ';')
  - training_pairs_rvi_ndvi.csv (per fittare le rette per-poligono e classificare)
  - CLI: --soglia (35), --tol-rvi (6), --min-r2 (0.2), --outdir
OUTPUT
  - ndvi_gapfilled_long.csv : poly_id, s2_date, ndvi_finale, sorgente,
                              distanza_giorni, ndvi_osservato
  - ndvi_gapfilled_wide.csv : stessa matrice NDVI di input, con i buchi riempiti
  - ndvi_gapfilled_riepilogo_sorgente.csv : conteggi/percentuali per sorgente (tesi)
  - ndvi_gapfilled_esempio.png : una serie di esempio (osservato vs riempito)
  - a video: conteggio per sorgente
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def linfit(x, y):
    n = len(x)
    if n < 2:
        return None
    sx = x.sum(); sy = y.sum(); sxx = (x * x).sum(); sxy = (x * y).sum()
    d = n * sxx - sx * sx
    if d == 0:
        return None
    slope = (n * sxy - sx * sy) / d
    intercept = (sy - slope * sx) / n
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return slope, intercept, r2


def load_wide(path):
    """Legge una matrice date x poligoni (';'), ritorna (date_int, colonne, DataFrame)."""
    df = pd.read_csv(path, sep=None, engine="python")
    dcol = df.columns[0]
    df[dcol] = pd.to_datetime(df[dcol]).values.astype("datetime64[D]").astype(np.int64)
    df = df.rename(columns={dcol: "d"}).set_index("d").sort_index()
    # forza numerico (celle vuote -> NaN)
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ndvi", default="mean_ndvi_per_polygon.csv")
    ap.add_argument("--rvi", default="mean_rvi_per_polygon_MASTER.csv")
    ap.add_argument("--pairs", default="training_pairs_rvi_ndvi.csv")
    ap.add_argument("--soglia", type=float, default=35.0,
                    help="soglia in giorni: buchi <= interpolazione, > radar.")
    ap.add_argument("--tol-rvi", type=int, default=6,
                    help="tolleranza (giorni) per appaiare l'RVI S1 alla data S2.")
    ap.add_argument("--min-r2", type=float, default=0.2)
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    NDVI = load_wide(args.ndvi)     # index: date_int; columns: P001..P154
    RVI = load_wide(args.rvi)
    polys = [c for c in NDVI.columns if c in RVI.columns]
    dates = NDVI.index.values.astype(np.int64)      # 241 date S2 (calendario di output)

    # ---- rette per-poligono + classificazione (dal training_pairs) ----
    tp = pd.read_csv(args.pairs, sep=None, engine="python", parse_dates=["s2_date"])
    tp = tp.dropna(subset=["rvi", "ndvi"])       # difensivo: NaN romperebbero il fit OLS
    lines = {}   # poly -> (slope, intercept, scollegato)
    for poly, g in tp.groupby("poly_id"):
        f = linfit(g.rvi.values.astype(float), g.ndvi.values.astype(float))
        if f is None:
            lines[poly] = (np.nan, np.nan, True)
            continue
        slope, intercept, r2 = f
        scollegato = (slope < 0) or (r2 < args.min_r2)
        lines[poly] = (slope, intercept, scollegato)

    # ---- RVI per poligono: array (data, valore) senza NaN, per lookup ----
    rvi_series = {}
    for poly in polys:
        s = RVI[poly].dropna()
        rvi_series[poly] = (s.index.values.astype(np.int64), s.values.astype(float))

    def rvi_at(poly, t):
        dd, vv = rvi_series[poly]
        if len(dd) == 0:
            return None
        j = np.searchsorted(dd, t)
        best = None; bestdist = args.tol_rvi + 1
        for jj in (j - 1, j):
            if 0 <= jj < len(dd):
                dist = abs(int(dd[jj] - t))
                if dist <= args.tol_rvi and dist < bestdist:
                    bestdist = dist; best = vv[jj]
        return best

    # ---- riempimento, cella per cella ----
    records = []
    wide_out = NDVI.copy()
    for poly in polys:
        slope, intercept, scollegato = lines.get(poly, (np.nan, np.nan, True))
        col = NDVI[poly]
        obs_mask = col.notna().values
        obs_dates = dates[obs_mask]
        obs_vals = col.values[obs_mask]
        for i, t in enumerate(dates):
            oss = col.values[i]
            if not np.isnan(oss):
                records.append((poly, t, oss, "osservato", 0, oss))
                continue
            # buco: ancore osservate prima/dopo per questo poligono
            left = obs_dates[obs_dates < t]
            right = obs_dates[obs_dates > t]
            has_l, has_r = len(left) > 0, len(right) > 0
            if has_l and has_r:
                d0 = left[-1]; y0 = obs_vals[obs_dates == d0][0]
                d1 = right[0]; y1 = obs_vals[obs_dates == d1][0]
                dist = int(min(t - d0, d1 - t))
                y_interp = y0 + (y1 - y0) * (t - d0) / (d1 - d0)
            else:
                # distanza definita solo se c'e' almeno un'ancora; altrimenti NaN
                # (non -1: -1 si confonderebbe con un giorno reale nelle statistiche)
                dist = int(t - left[-1]) if has_l else (int(right[0] - t) if has_r else np.nan)
                y_interp = None

            if scollegato:
                if y_interp is not None:
                    val = float(np.clip(y_interp, -1, 1)); src = "interpolato_inaffidabile"
                else:
                    val = np.nan; src = "non_stimabile"
            else:
                # poligono buono
                if (y_interp is not None) and (dist <= args.soglia):
                    val = float(np.clip(y_interp, -1, 1)); src = "interpolato"
                else:
                    rv = rvi_at(poly, t)
                    if rv is not None and not np.isnan(slope):
                        val = float(np.clip(slope * rv + intercept, -1, 1)); src = "radar"
                    elif y_interp is not None:
                        val = float(np.clip(y_interp, -1, 1)); src = "interpolato_no_rvi"
                    else:
                        val = np.nan; src = "non_stimabile"
            records.append((poly, t, val, src, dist, np.nan))
            wide_out.at[t, poly] = val

    out = pd.DataFrame(records, columns=["poly_id", "d", "ndvi_finale", "sorgente",
                                         "distanza_giorni", "ndvi_osservato"])
    out["s2_date"] = out["d"].values.astype("datetime64[D]")
    out = out[["poly_id", "s2_date", "ndvi_finale", "sorgente", "distanza_giorni", "ndvi_osservato"]]
    out.to_csv(outdir / "ndvi_gapfilled_long.csv", index=False)

    wide_out.index = wide_out.index.values.astype("datetime64[D]")
    wide_out.to_csv(outdir / "ndvi_gapfilled_wide.csv")

    # ---- riepilogo ----
    n_tot = len(out)
    print("=== GAP-FILLING NDVI: riepilogo per sorgente ===")
    vc = out["sorgente"].value_counts()
    for src, n in vc.items():
        print(f"  {src:28s} {n:7d}  ({100*n/n_tot:.1f}%)")
    n_scoll = sum(1 for p in polys if lines.get(p, (0,0,True))[2])
    print(f"\n  celle totali: {n_tot}   poligoni: {len(polys)}   scollegati: {n_scoll}")
    # conteggi per sorgente su file (tabella di copertura per la tesi).
    # NB: sono conteggi di CELLE riempite (una per poligono-data), unita' indipendenti:
    # NON hanno il problema di non-indipendenza del benchmark C.
    (vc.rename_axis("sorgente").reset_index(name="n")
       .assign(perc=lambda d: (100 * d["n"] / n_tot).round(2))
       .to_csv(outdir / "ndvi_gapfilled_riepilogo_sorgente.csv", index=False))
    print(f"  -> {outdir/'ndvi_gapfilled_long.csv'}")
    print(f"  -> {outdir/'ndvi_gapfilled_wide.csv'}")
    print(f"  -> {outdir/'ndvi_gapfilled_riepilogo_sorgente.csv'}")

    # ---- grafico di esempio: il poligono BUONO con piu' celle 'radar' ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        radar_counts = (out[out.sorgente == "radar"].groupby("poly_id").size()
                        .sort_values(ascending=False))
        if len(radar_counts):
            ex = radar_counts.index[0]
            sub = out[out.poly_id == ex].sort_values("s2_date")
            colors = {"osservato": "#222222", "interpolato": "#185FA5",
                      "radar": "#639922", "interpolato_no_rvi": "#8AB0D6",
                      "interpolato_inaffidabile": "#C0392B", "non_stimabile": "#BBBBBB"}
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(sub.s2_date, sub.ndvi_finale, "-", color="#CCCCCC", lw=1, zorder=1)
            for src, c in colors.items():
                m = sub.sorgente == src
                if m.any():
                    ax.scatter(sub.s2_date[m], sub.ndvi_finale[m], s=28, color=c, label=src, zorder=2)
            ax.set_title(f"Gap-filling NDVI \u2014 poligono {ex} (esempio con piu' stime radar)")
            ax.set_xlabel("data"); ax.set_ylabel("NDVI"); ax.set_ylim(-0.1, 1.0)
            ax.legend(fontsize=8, ncol=2); ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(outdir / "ndvi_gapfilled_esempio.png", dpi=130)
            print(f"  -> {outdir/'ndvi_gapfilled_esempio.png'}  (poligono {ex})")
    except Exception as e:
        print(f"  (grafico non generato: {e})")


if __name__ == "__main__":
    main()
