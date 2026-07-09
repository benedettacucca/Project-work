#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark_C_rolling_origin.py

VARIANTE ROLLING-ORIGIN del benchmark C (benchmark_C_rvi_vs_interpolazione.py).

Serve a CONSOLIDARE la soglia di crossover (~35 giorni) con lo stesso spirito
della rolling-origin usata per l'R2: la retta RVI viene allenata SOLO sul PASSATO
del buco (osservazioni a date precedenti l'inizio del buco), non piu' su prima E
dopo. E' un regime piu' severo e piu' realistico (si predice il futuro), quindi
uno STRESS TEST della soglia: se il radar batte comunque l'interpolazione oltre
~35 giorni, la soglia e' robusta.

ASIMMETRIA VOLUTA (da dichiarare):
  - RADAR   -> allenato SOLO sul passato del buco (regime rolling-origin).
  - CIECO   -> interpolazione tra l'osservazione prima e quella dopo il buco
               (usa entrambi i lati: e' la sua natura, non puo' fare altrimenti;
               coincide con la regola di gap-filling che si usera' davvero).
  Il radar e' quindi messo in condizioni piu' dure dell'interpolazione: se vince
  lo stesso oltre soglia, il risultato e' conservativo (a favore della robustezza).

Rispetto al benchmark C originale cambia UNA cosa: il training della retta radar
(prima "fuori dal buco" = prima+dopo; ora "solo prima del buco"). Tutto il resto
(generazione dei buchi, metrica di distanza, 110 poligoni buoni, valutazione data
per data, taglio delle fasce rade) e' INVARIATO.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - training_pairs_rvi_ndvi.csv  (poly_id, s2_date, rvi, ndvi)
  - CLI: --pairs, --min-r2 (def 0.2), --kmax (def 12), --bin-days (def 10),
         --max-days (def 90), --min-n (def 1000),
         --min-train (def 10: min osservazioni PASSATE per allenare la retta radar),
         --ref-cross (def 35: crossover di riferimento del C originale), --outdir
OUTPUT
  - benchmark_C_rollingorigin_per_durata.csv : per fascia di distanza -> n, rmse, mae, delta
  - benchmark_C_rollingorigin_stagionale.csv : stessa tabella stratificata per stagione
  - benchmark_C_rollingorigin_ic.csv         : IC 95% (bootstrap a blocchi) di delta_rmse per fascia
  - benchmark_C_rollingorigin_curva.png      : curva errore vs distanza
  - a video: tabella + crossover + crossover stagionale + IC bootstrap + confronto col C originale
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def season_of(m):
    return "DJF" if m in (12, 1, 2) else "MAM" if m in (3, 4, 5) else "JJA" if m in (6, 7, 8) else "SON"


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


def rows_from_stats(stats, bin_days, min_n):
    """Da {bin: [n, ssr, sar, ssi, sai]} alla tabella errore-vs-distanza (fasce con n>=min_n)."""
    rows = []
    for b in sorted(stats):
        n, ssr, sar, ssi, sai = stats[b]
        if n < min_n:
            continue
        rows.append({
            "len_center_days": b * bin_days + bin_days / 2,
            "n_punti": int(n),
            "rmse_radar": np.sqrt(ssr / n),
            "rmse_interp": np.sqrt(ssi / n),
            "mae_radar": sar / n,
            "mae_interp": sai / n,
            "delta_rmse": np.sqrt(ssi / n) - np.sqrt(ssr / n),
        })
    return rows


def crossover_from_rows(rows):
    """Prima fascia (distanza crescente) in cui il radar batte l'interpolazione."""
    for r in rows:
        if r["rmse_radar"] < r["rmse_interp"]:
            return r["len_center_days"]
    return None


def acc(store, b, er, ei):
    """Accumula (n, SS_radar, SA_radar, SS_interp, SA_interp) nella fascia b."""
    st = store.setdefault(b, [0, 0.0, 0.0, 0.0, 0.0])
    st[0] += 1
    st[1] += er * er; st[2] += abs(er)
    st[3] += ei * ei; st[4] += abs(ei)


def fit_with_r2(x, y):
    """Retta su tutti i punti + R2 in-sample (criterio dei poligoni buoni)."""
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
    ap.add_argument("--min-r2", type=float, default=0.2)
    ap.add_argument("--kmax", type=int, default=12)
    ap.add_argument("--bin-days", type=int, default=10)
    ap.add_argument("--max-days", type=int, default=90)
    ap.add_argument("--min-n", type=int, default=1000)
    ap.add_argument("--min-train", type=int, default=10,
                    help="min osservazioni PASSATE per allenare la retta radar "
                         "(regime rolling-origin: solo il passato del buco).")
    ap.add_argument("--ref-cross", type=float, default=35.0,
                    help="crossover di riferimento del benchmark C originale (giorni).")
    ap.add_argument("--min-n-season", type=int, default=250,
                    help="min punti per fascia nella tabella STAGIONALE (piu' basso di "
                         "--min-n perche' i punti si dividono in 4 stagioni).")
    ap.add_argument("--n-boot", type=int, default=1000,
                    help="ripetizioni bootstrap a blocchi sui poligoni per gli IC (0 = disattiva).")
    ap.add_argument("--seed", type=int, default=42, help="seme RNG per il bootstrap.")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    p = pd.read_csv(args.pairs, parse_dates=["s2_date"])
    p = p.dropna(subset=["rvi", "ndvi"])          # difensivo: NaN avvelenerebbero le somme OLS
    p["d"] = p["s2_date"].values.astype("datetime64[D]").astype(np.int64)
    month_by_d = dict(zip(p["d"].values.astype(np.int64), p["s2_date"].dt.month.values))

    # ---- poligoni "buoni": pendenza>0 E R2_in>=min_r2 (ricalcolato) ----
    # ora per ogni poligono servono anche le SOMME CUMULATE (prefix sums) per
    # poter allenare la retta solo sul passato in modo veloce.
    polys = {}   # poly_id -> (dd, rr, nn, Prr, Pnn, Prr2, Prrnn)
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
        # prefix sums (indice j = somma sui primi j elementi, cioe' [0, j) )
        Prr = np.concatenate(([0.0], np.cumsum(rr)))
        Pnn = np.concatenate(([0.0], np.cumsum(nn)))
        Prr2 = np.concatenate(([0.0], np.cumsum(rr * rr)))
        Prrnn = np.concatenate(([0.0], np.cumsum(rr * nn)))
        polys[poly] = (dd, rr, nn, Prr, Pnn, Prr2, Prrnn)
    print(f"=== POLIGONI 'BUONI' (pendenza>0 e R2_in>={args.min_r2}) ===")
    print(f"  tenuti: {len(polys)}   esclusi: pendenza<0={n_neg}, R2_in<{args.min_r2}={n_lowr2}")

    D = np.unique(p["d"].values).astype(np.int64)
    m = len(D)

    stats = {}                 # globale: bin -> [n, ssr, sar, ssi, sai]
    stats_poly = {}            # poly_id -> {bin -> [...]}  (per il bootstrap a blocchi)
    stats_season = {}          # stagione -> {bin -> [...]} (per il crossover stagionale)
    n_skip_train = 0
    for i in range(m - 2):
        before_t = D[i]
        for k in range(1, args.kmax + 1):
            j = i + k + 1
            if j >= m:
                break
            after_t = D[j]
            span = int(after_t - before_t)
            if span >= args.max_days + args.bin_days:
                break
            for poly_id, (dd, rr, nn, Prr, Pnn, Prr2, Prrnn) in polys.items():
                lo = np.searchsorted(dd, before_t, side="right")  # = #date <= before_t = inizio nascoste
                hi = np.searchsorted(dd, after_t, side="left")    # = inizio date >= after_t
                if hi <= lo:
                    continue
                if lo - 1 < 0 or hi >= len(dd):
                    continue
                d0, y0 = dd[lo - 1], nn[lo - 1]      # ancora sinistra (interpolazione)
                d1, y1 = dd[hi], nn[hi]              # ancora destra (interpolazione)
                if d1 == d0:
                    continue
                # === UNICA MODIFICA vs C originale: retta RADAR solo sul PASSATO ===
                # training = osservazioni a date <= before_t, cioe' indici [0, lo).
                # (nel C originale era [0,lo) UNION [hi,N) = prima E dopo il buco).
                n_tr = lo
                if n_tr < args.min_train:
                    n_skip_train += 1
                    continue
                f = linfit_from_sums(n_tr, Prr[lo], Pnn[lo], Prr2[lo], Prrnn[lo])
                if f is None:
                    n_skip_train += 1
                    continue
                s_, ic_ = f
                # =================================================================
                for h in range(lo, hi):
                    yt = nn[h]
                    yr = s_ * rr[h] + ic_                              # radar (solo passato)
                    yi = y0 + (y1 - y0) * (dd[h] - d0) / (d1 - d0)     # cieco (prima+dopo)
                    er = yr - yt; ei = yi - yt
                    dist = int(min(dd[h] - d0, d1 - dd[h]))
                    if dist >= args.max_days:
                        continue
                    b = dist // args.bin_days
                    acc(stats, b, er, ei)
                    acc(stats_poly.setdefault(poly_id, {}), b, er, ei)
                    seas = season_of(month_by_d[int(dd[h])])
                    acc(stats_season.setdefault(seas, {}), b, er, ei)

    rows = rows_from_stats(stats, args.bin_days, args.min_n)
    res = pd.DataFrame(rows)
    res.to_csv(outdir / "benchmark_C_rollingorigin_per_durata.csv", index=False)

    print(f"\n=== ROLLING-ORIGIN: ERRORE vs DISTANZA (radar solo passato) ===")
    print(f"{'dist~gg':>9} {'n':>8} {'RMSE_radar':>11} {'RMSE_interp':>12} {'vince':>7}")
    cross = None
    for _, r in res.iterrows():
        win = "RADAR" if r.rmse_radar < r.rmse_interp else "interp"
        if cross is None and r.rmse_radar < r.rmse_interp:
            cross = r.len_center_days
        print(f"{r.len_center_days:9.0f} {int(r.n_punti):8d} "
              f"{r.rmse_radar:11.4f} {r.rmse_interp:12.4f} {win:>7}")
    if cross is not None:
        print(f"\n  >>> CROSSOVER rolling-origin: ~{cross:.0f} giorni")
        print(f"      (C originale: ~{args.ref_cross:.0f} giorni)")
        diff = cross - args.ref_cross
        if abs(diff) <= args.bin_days:
            print(f"      scarto {diff:+.0f} gg -> CONFERMA (stesso ordine di grandezza)")
        else:
            print(f"      scarto {diff:+.0f} gg -> soglia spostata (regime piu' severo)")
    else:
        print(f"\n  >>> Nessun crossover nell'intervallo esaminato.")
    print(f"  (predizioni radar saltate per training<{args.min_train}: {n_skip_train})")
    print(f"  NB: n_punti conta SCENARI (stessa osservazione ricostruita in piu' buchi),"
          f" NON osservazioni indipendenti -> per l'inferenza usare gli IC bootstrap sotto.")
    print(f"  -> {outdir/'benchmark_C_rollingorigin_per_durata.csv'}")

    # ---- CROSSOVER PER STAGIONE (modello invariato: solo la soglia e' stratificata) ----
    print(f"\n=== CROSSOVER PER STAGIONE (retta unica; min-n stagionale={args.min_n_season}) ===")
    seas_rows = []
    for seas in ("DJF", "MAM", "JJA", "SON"):
        if seas not in stats_season:
            continue
        srows = rows_from_stats(stats_season[seas], args.bin_days, args.min_n_season)
        scross = crossover_from_rows(srows)
        ntot = sum(r["n_punti"] for r in srows)
        etichetta = f"~{scross:.0f} gg" if scross is not None else "nessun crossover"
        print(f"  {seas}: crossover {etichetta}   (scenari={ntot})")
        for r in srows:
            seas_rows.append({"stagione": seas, **r})
    if seas_rows:
        pd.DataFrame(seas_rows).to_csv(outdir / "benchmark_C_rollingorigin_stagionale.csv", index=False)
        print(f"  -> {outdir/'benchmark_C_rollingorigin_stagionale.csv'}")
    print(f"  (analisi DESCRITTIVA sito-specifica: il MODELLO resta la retta unica;")
    print(f"   la stagionalita' del MODELLO e' gia' stata scartata dalla block-CV.)")

    # ---- BOOTSTRAP A BLOCCHI SUI POLIGONI: IC 95% per delta_rmse e crossover ----
    # l'unita' indipendente e' il poligono (gli scenari NON lo sono, punto 1 del
    # relatore): si ricampionano i poligoni con reinserimento, si ri-aggregano le
    # somme per fascia e si ricalcola tutto -> IC corretti nonostante n_punti gonfiato.
    if args.n_boot > 0 and stats_poly:
        poly_ids = list(stats_poly.keys())
        all_bins = sorted(stats)
        rng = np.random.default_rng(args.seed)
        delta_bs = {b: [] for b in all_bins}
        cross_bs = []
        for _ in range(args.n_boot):
            samp = rng.integers(0, len(poly_ids), size=len(poly_ids))
            agg = {}
            for si in samp:
                for b, st in stats_poly[poly_ids[si]].items():
                    a = agg.setdefault(b, [0, 0.0, 0.0, 0.0, 0.0])
                    for q in range(5):
                        a[q] += st[q]
            brows = rows_from_stats(agg, args.bin_days, args.min_n)
            by_center = {r["len_center_days"]: r["delta_rmse"] for r in brows}
            for b in all_bins:
                c = b * args.bin_days + args.bin_days / 2
                if c in by_center:
                    delta_bs[b].append(by_center[c])
            xc = crossover_from_rows(brows)
            if xc is not None:
                cross_bs.append(xc)
        print(f"\n=== IC 95% BOOTSTRAP A BLOCCHI ({len(poly_ids)} poligoni, {args.n_boot} rip.) ===")
        print(f"  delta_rmse = RMSE_interp - RMSE_radar (>0 = radar meglio)")
        print(f"{'dist~gg':>9} {'delta_medio':>11} {'IC95_lo':>9} {'IC95_hi':>9} {'radar_meglio':>13}")
        ci_rows = []
        for b in all_bins:
            if len(delta_bs[b]) < args.n_boot * 0.5:   # fascia instabile nel ricampionamento
                continue
            c = b * args.bin_days + args.bin_days / 2
            lo, hi = np.percentile(delta_bs[b], [2.5, 97.5])
            sig = "SI (p<0.05)" if lo > 0 else ("NO (interp)" if hi < 0 else "non signif.")
            print(f"{c:9.0f} {np.mean(delta_bs[b]):11.4f} {lo:9.4f} {hi:9.4f} {sig:>13}")
            ci_rows.append({"len_center_days": c, "delta_rmse_medio": float(np.mean(delta_bs[b])),
                            "ic95_lo": float(lo), "ic95_hi": float(hi), "radar_meglio_signif": sig})
        if cross_bs:
            clo, chi = np.percentile(cross_bs, [2.5, 97.5])
            print(f"  crossover: mediana ~{np.median(cross_bs):.0f} gg   "
                  f"IC95 [{clo:.0f}, {chi:.0f}] gg   "
                  f"(trovato in {len(cross_bs)}/{args.n_boot} ricampioni)")
        else:
            print(f"  crossover: nessun crossover nella maggioranza dei ricampioni.")
        if ci_rows:
            pd.DataFrame(ci_rows).to_csv(outdir / "benchmark_C_rollingorigin_ic.csv", index=False)
            print(f"  -> {outdir/'benchmark_C_rollingorigin_ic.csv'}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(res.len_center_days, res.rmse_interp, "o-", color="#185FA5", label="Interpolazione temporale (cieco)")
        ax.plot(res.len_center_days, res.rmse_radar, "s-", color="#639922", label="Retta RVI (radar, solo passato)")
        for _, r in res.iterrows():
            ax.annotate(f"n={int(r.n_punti):,}".replace(",", "."),
                        (r.len_center_days, max(r.rmse_interp, r.rmse_radar)),
                        textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=7, color="#555")
        if cross is not None:
            ax.axvline(cross, ls="--", color="#888", lw=1)
            ax.text(cross, ax.get_ylim()[1] * 0.95, f"  crossover ~{cross:.0f} gg", color="#555", va="top")
        ax.set_xlabel("Distanza dall'osservazione NDVI utile pi\u00f9 vicina (giorni)")
        ax.set_ylabel("RMSE su NDVI (data per data)")
        ax.set_title("Benchmark C rolling-origin: RVI (solo passato) vs interpolazione")
        ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / "benchmark_C_rollingorigin_curva.png", dpi=130)
        print(f"  -> {outdir/'benchmark_C_rollingorigin_curva.png'}")
    except Exception as e:
        print(f"  (grafico non generato: {e})")


if __name__ == "__main__":
    main()
