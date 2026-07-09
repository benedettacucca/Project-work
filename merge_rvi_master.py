#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_rvi_master.py

Ricompone in un'unica serie RVI per poligono le estrazioni prodotte dalle due
ricerche di scene Sentinel-1 (guidate rispettivamente dalle date Sentinel-2
nuvolose e da quelle prive di nuvole).

Le due ricerche possono restituire la MEDESIMA scena S1: un'unica acquisizione
radar puo' ricadere entro +/-6 giorni sia da una data ottica nuvolosa sia da una
serena adiacente. Le date in comune vanno quindi deduplicate, non concatenate.

  105 date (da S2 nuvolose)  U  67 date (da S2 serene)  -  61 comuni  =  111 date

Il risultato (mean_rvi_per_polygon_MASTER.csv) e' l'unica serie RVI operativa:
e' da qui che correlazione, regressione e gap-filling pescano i valori,
indistintamente dalla ricerca da cui provengono.

================================================================================
INPUT / OUTPUT
================================================================================
INPUT
  - due (o piu') matrici RVI  date x poligoni, separatore ';', celle vuote = NaN:
      mean_rvi_per_polygon_all_pol_no_inv.csv   (date S2 con nuvole)
      mean_rvi_per_polygon_no_nuvole.csv        (date S2 senza nuvole)
  - CLI: --inputs (2+ percorsi), --output, --check
OUTPUT
  - mean_rvi_per_polygon_MASTER.csv : matrice unica, date ordinate, senza duplicati
  - a video: date per file, date comuni, totale nell'unione, colonne (poligoni)

CONFLITTI
  Se una stessa cella (data, poligono) e' presente in piu' file con valori
  diversi, si tiene il primo file in cui compare (ordine di --inputs) e si
  segnala il numero di discordanze: con --check lo scarto massimo viene
  stampato, cosi' da verificare che i due file provengano dallo stesso
  processing (atteso: nessuna discordanza, o solo errore di arrotondamento).

USO
---
  python merge_rvi_master.py \
      --inputs mean_rvi_per_polygon_all_pol_no_inv.csv mean_rvi_per_polygon_no_nuvole.csv \
      --output mean_rvi_per_polygon_MASTER.csv --check
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load_wide(path: Path) -> pd.DataFrame:
    """Legge una matrice date x poligoni (';'); indice = data, celle vuote -> NaN."""
    df = pd.read_csv(path, sep=None, engine="python")
    dcol = df.columns[0]
    df[dcol] = pd.to_datetime(df[dcol])
    df = df.rename(columns={dcol: "date"}).set_index("date").sort_index()
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def main():
    ap = argparse.ArgumentParser(description="Unisce le matrici RVI in un unico MASTER.")
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="due o piu' CSV RVI (date x poligoni). L'ordine definisce "
                         "la precedenza in caso di conflitto.")
    ap.add_argument("--output", default="mean_rvi_per_polygon_MASTER.csv",
                    help="CSV di output.")
    ap.add_argument("--check", action="store_true",
                    help="riporta lo scarto massimo sulle celle presenti in piu' file.")
    args = ap.parse_args()

    frames = []
    for f in args.inputs:
        p = Path(f)
        if not p.exists():
            print(f"ERRORE: file non trovato: {p}")
            sys.exit(1)
        df = load_wide(p)
        print(f"  {p.name:45s} {len(df):4d} date x {df.shape[1]:3d} poligoni")
        frames.append(df)

    # ---- coerenza delle colonne (poligoni) ----
    cols = list(frames[0].columns)
    for f, df in zip(args.inputs, frames):
        if list(df.columns) != cols:
            extra = set(df.columns) ^ set(cols)
            print(f"ERRORE: colonne diverse in {f} (differenze: {sorted(extra)[:5]} ...)")
            sys.exit(1)

    # ---- diagnostica sovrapposizione ----
    sets = [set(df.index) for df in frames]
    union = set().union(*sets)
    print(f"\n=== SOVRAPPOSIZIONE DELLE DATE ===")
    if len(frames) == 2:
        common = sets[0] & sets[1]
        print(f"  {len(sets[0])} + {len(sets[1])} - {len(common)} comuni = {len(union)} date distinte")
    else:
        print(f"  unione: {len(union)} date distinte")

    # ---- controllo conflitti sulle celle condivise ----
    if args.check and len(frames) >= 2:
        worst = 0.0
        n_diff = 0
        base = frames[0]
        for df in frames[1:]:
            idx = base.index.intersection(df.index)
            if len(idx) == 0:
                continue
            a = base.loc[idx, cols]
            b = df.loc[idx, cols]
            both = a.notna() & b.notna()
            d = (a - b).abs().where(both)
            n_diff += int((d > 1e-9).sum().sum())
            m = float(np.nanmax(d.values)) if both.any().any() else 0.0
            worst = max(worst, 0.0 if np.isnan(m) else m)
        print(f"  celle discordanti tra file: {n_diff}   scarto massimo: {worst:.3e}")
        if n_diff == 0:
            print("  -> i file coincidono sulle date comuni (stesso processing).")

    # ---- fusione: precedenza al primo file ----
    master = frames[0]
    for df in frames[1:]:
        master = master.combine_first(df)      # riempie i buchi senza sovrascrivere
    master = master.sort_index()[cols]

    # ---- scrittura ----
    out = master.copy()
    out.index = out.index.strftime("%Y-%m-%dT00:00:00Z")
    out.to_csv(args.output, sep=";", na_rep="")

    print(f"\n=== MASTER SCRITTO ===")
    print(f"  {args.output}")
    print(f"  date: {len(master)}   poligoni: {master.shape[1]}")
    print(f"  intervallo: {master.index.min().date()} -> {master.index.max().date()}")
    valid = int(master.notna().sum().sum())
    tot = master.size
    print(f"  valori validi: {valid}/{tot} ({100*valid/tot:.1f}%)")


if __name__ == "__main__":
    main()
