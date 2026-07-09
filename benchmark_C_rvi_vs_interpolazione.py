#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark_C_rvi_vs_interpolazione.py

BENCHMARK C — "Il radar serve davvero, e da quale lunghezza di buco in poi?"

Simula buchi nuvolosi di durata crescente nella serie NDVI e, su ogni buco,
confronta DUE riempitori:

  (RADAR)  retta unica RVI->NDVI del poligono (fit sui dati FUORI dal buco,
           poi predice l'NDVI delle date nascoste usando il loro RVI).
  (CIECO)  interpolazione temporale lineare: retta tra l'ultima osservazione
           NDVI PRIMA del buco e la prima DOPO (NON usa il radar).

L'errore e' misurato DATA PER DATA (ogni data nascosta dentro il buco e' una
osservazione), accumulato per fascia di durata del buco (in GIORNI). Ne esce
una curva "errore vs durata del buco" con un punto di INCROCIO: oltre quella
durata, il radar batte l'interpolazione. Quello e' il risultato di punta.

Solo i poligoni "buoni" sono usati (criterio ricalcolato qui: pendenza>0 E
R2_in>=min-r2), perche' sui campi scollegati nessun metodo radar puo' funzionare.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - training_pairs_rvi_ndvi.csv  (formato lungo: poly_id, s2_date, rvi, ndvi)
  - CLI: --pairs, --min-r2 (def 0.2), --kmax (def 12, max osservazioni nascoste
         consecutive), --bin-days (def 10), --max-days (def 90), --min-n (def 1000),
         --outdir
OUTPUT
  - benchmark_C_per_durata.csv : una riga per fascia di durata
        len_center_days, n_punti, rmse_radar, rmse_interp, mae_radar,
        mae_interp, delta_rmse  (delta = rmse_interp - rmse_radar; >0 = radar meglio)
  - benchmark_C_curva.png : grafico errore vs durata (se matplotlib disponibile)
  - a video: tabella per durata + durata di INCROCIO (crossover)

DEFINIZIONE DI "BUCO"
  La nuvola copre tutta l'AOI: un buco = una fila di date osservate consecutive
  nascoste. La sua DURATA (giorni) = (prima data chiara dopo) - (ultima prima).
  Per ogni poligono, gli ancoraggi dell'interpolazione e le date nascoste sono
  presi sulle SUE osservazioni dentro/attorno a quell'intervallo.
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def linfit_from_sums(n, sx, sy, sxx, sxy):
    """Retta OLS y~x dai momenti gia' sommati; None se degenere."""
    if n < 2:
        return None
    d = n * sxx - sx * sx
    if d == 0:
        return None
    slope = (n * sxy - sx * sy) / d
    intercept = (sy - slope * sx) / n
    return slope, intercept


def fit_with_r2(x, y):
    """Retta su tutti i punti + R2 in-sample (per il criterio dei poligoni buoni)."""
    n = len(x)
    f = linfit_from_sums(n, x.sum(), y.sum(), (x * x).sum(), (x * y).sum())
    if f is None:
        return None
    s, i = f
    pred = s * x + i
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return s, i, r2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="training_pairs_rvi_ndvi.csv")
    ap.add_argument("--min-r2", type=float, default=0.2,
                    help="soglia R2_in per i poligoni 'buoni' (e pendenza>0).")
    ap.add_argument("--kmax", type=int, default=12,
                    help="max osservazioni AOI consecutive nascoste in un buco.")
    ap.add_argument("--bin-days", type=int, default=10,
                    help="ampiezza delle fasce di durata del buco (giorni).")
    ap.add_argument("--max-days", type=int, default=90,
                    help="durata massima del buco considerata (giorni).")
    ap.add_argument("--min-n", type=int, default=1000,
                    help="min punti per riportare una fascia di distanza. Le fasce "
                         "a grande distanza sono naturalmente poco popolate (pochi "
                         "buchi lunghi nel calendario): sotto questa soglia la stima "
                         "e' troppo rumorosa e la fascia viene scartata.")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    p = pd.read_csv(args.pairs, parse_dates=["s2_date"])
    # date in interi (giorni dall'epoca) per fare aritmetica veloce
    p["d"] = p["s2_date"].values.astype("datetime64[D]").astype(np.int64)

    # ---- (b) RICALCOLO dei poligoni "buoni": pendenza>0 E R2_in>=min_r2 ----
    polys = {}          # poly_id -> (dd, rr, nn, Sx, Sy, Sxx, Sxy, N)
    n_neg = n_lowr2 = 0
    for poly, g in p.groupby("poly_id"):
        g = g.sort_values("d")
        rr = g.rvi.values.astype(float); nn = g.ndvi.values.astype(float)
        dd = g.d.values.astype(np.int64)
        res = fit_with_r2(rr, nn)
        if res is None:
            continue
        slope, _, r2 = res
        if slope < 0:
            n_neg += 1; continue
        if r2 < args.min_r2:
            n_lowr2 += 1; continue
        polys[poly] = (dd, rr, nn,
                       rr.sum(), nn.sum(), (rr * rr).sum(), (rr * nn).sum(), len(rr))
    print(f"=== POLIGONI 'BUONI' (pendenza>0 e R2_in>={args.min_r2}) ===")
    print(f"  tenuti: {len(polys)}   esclusi: pendenza<0={n_neg}, R2_in<{args.min_r2}={n_lowr2}")

    # calendario AOI condiviso (tutte le date osservate, ordinate)
    D = np.unique(p["d"].values).astype(np.int64)
    m = len(D)

    # ---- accumulo errori DATA PER DATA, per fascia di durata ----
    # stats[bin] = [n, somma_quadr_radar, somma_abs_radar, somma_quadr_int, somma_abs_int]
    stats = {}
    for i in range(m - 2):
        before_t = D[i]
        for k in range(1, args.kmax + 1):
            j = i + k + 1
            if j >= m:
                break
            after_t = D[j]
            span = int(after_t - before_t)          # ampiezza del buco in giorni
            if span >= args.max_days + args.bin_days:
                break                                # span cresce con k -> stop
            for (dd, rr, nn, Sx, Sy, Sxx, Sxy, N) in polys.values():
                # indici delle date nascoste del poligono: (before_t, after_t)
                lo = np.searchsorted(dd, before_t, side="right")   # primo > before_t
                hi = np.searchsorted(dd, after_t, side="left")     # primo >= after_t
                if hi <= lo:
                    continue                          # nessuna data nascosta qui
                if lo - 1 < 0 or hi >= len(dd):
                    continue                          # manca ancora prima o dopo
                # ancoraggi interpolazione (osservazioni del poligono fuori dal buco)
                d0, y0 = dd[lo - 1], nn[lo - 1]
                d1, y1 = dd[hi], nn[hi]
                if d1 == d0:
                    continue
                # retta RADAR: fit sui punti FUORI dal buco (totale meno il blocco nascosto)
                hx = rr[lo:hi]; hy = nn[lo:hi]
                sx = Sx - hx.sum(); sy = Sy - hy.sum()
                sxx = Sxx - (hx * hx).sum(); sxy = Sxy - (hx * hy).sum()
                ntr = N - (hi - lo)
                f = linfit_from_sums(ntr, sx, sy, sxx, sxy)
                if f is None:
                    continue
                s_, ic_ = f
                # valutazione DATA PER DATA dentro il buco.
                # NB: la "lunghezza" che conta per l'interpolazione NON e' l'ampiezza
                # del buco, ma quanto la SINGOLA data nascosta dista dall'osservazione
                # utile piu' vicina (= min distanza dalle due ancore). Una data a 3 gg
                # da un'ancora e' facile anche se il buco e' largo 60 gg. Quindi ogni
                # data nascosta entra nella fascia corrispondente alla sua distanza.
                for h in range(lo, hi):
                    yt = nn[h]
                    yr = s_ * rr[h] + ic_                              # radar
                    yi = y0 + (y1 - y0) * (dd[h] - d0) / (d1 - d0)     # cieco
                    er = yr - yt; ei = yi - yt
                    dist = int(min(dd[h] - d0, d1 - dd[h]))           # dist. dall'ancora piu' vicina
                    if dist >= args.max_days:
                        continue
                    b = dist // args.bin_days
                    st = stats.setdefault(b, [0, 0.0, 0.0, 0.0, 0.0])
                    st[0] += 1
                    st[1] += er * er; st[2] += abs(er)
                    st[3] += ei * ei; st[4] += abs(ei)

    # ---- tabella per fascia di durata ----
    rows = []
    for b in sorted(stats):
        n, ssr, sar, ssi, sai = stats[b]
        if n < args.min_n:
            continue
        rows.append({
            "len_center_days": b * args.bin_days + args.bin_days / 2,
            "n_punti": int(n),
            "rmse_radar": np.sqrt(ssr / n),
            "rmse_interp": np.sqrt(ssi / n),
            "mae_radar": sar / n,
            "mae_interp": sai / n,
        })
    res = pd.DataFrame(rows)
    res["delta_rmse"] = res.rmse_interp - res.rmse_radar      # >0 = radar meglio
    res.to_csv(outdir / "benchmark_C_per_durata.csv", index=False)

    print(f"\n=== ERRORE vs DISTANZA DALL'OSSERVAZIONE PIU' VICINA  (data per data) ===")
    print(f"{'dist~gg':>9} {'n':>7} {'RMSE_radar':>11} {'RMSE_interp':>12} {'vince':>7}")
    cross = None
    for _, r in res.iterrows():
        win = "RADAR" if r.rmse_radar < r.rmse_interp else "interp"
        if cross is None and r.rmse_radar < r.rmse_interp:
            cross = r.len_center_days
        print(f"{r.len_center_days:9.0f} {int(r.n_punti):7d} "
              f"{r.rmse_radar:11.4f} {r.rmse_interp:12.4f} {win:>7}")
    if cross is not None:
        print(f"\n  >>> INCROCIO: oltre ~{cross:.0f} giorni dall'ultima osservazione utile,")
        print(f"      il RADAR batte l'interpolazione temporale.")
    else:
        print(f"\n  >>> Nessun incrocio nell'intervallo esaminato.")
    print(f"  -> {outdir/'benchmark_C_per_durata.csv'}")

    # ---- grafico (opzionale) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(res.len_center_days, res.rmse_interp, "o-", color="#185FA5", label="Interpolazione temporale (cieco)")
        ax.plot(res.len_center_days, res.rmse_radar, "s-", color="#639922", label="Retta RVI (radar)")
        # n punti sopra ogni marcatore (trasparenza sulla numerosita' campionaria)
        ymax = max(res.rmse_interp.max(), res.rmse_radar.max())
        for _, r in res.iterrows():
            ax.annotate(f"n={int(r.n_punti):,}".replace(",", "."),
                        (r.len_center_days, max(r.rmse_interp, r.rmse_radar)),
                        textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=7, color="#555")
        if cross is not None:
            ax.axvline(cross, ls="--", color="#888", lw=1)
            ax.text(cross, ax.get_ylim()[1] * 0.95, f"  incrocio ~{cross:.0f} gg", color="#555", va="top")
        ax.set_xlabel("Distanza dall'osservazione NDVI utile pi\u00f9 vicina (giorni)")
        ax.set_ylabel("RMSE su NDVI (data per data)")
        ax.set_title("Benchmark C: RVI vs interpolazione temporale")
        ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / "benchmark_C_curva.png", dpi=130)
        print(f"  -> {outdir/'benchmark_C_curva.png'}")
    except Exception as e:
        print(f"  (grafico non generato: {e})")


if __name__ == "__main__":
    main()
