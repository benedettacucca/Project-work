#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
correlazione_rvi_ndvi.py

Costruisce la correlazione RVI (Sentinel-1) -> NDVI (Sentinel-2) per poligono,
da usare per riempire i buchi nuvolosi delle time series NDVI.

CATENA LOGICA
-------------
  1. Carica RVI master (S1) e NDVI (S2), entrambi in formato  date x poligoni
     (separatore ';', prima colonna 'date', colonne P001..P154).
  2. Accoppia ogni data NDVI alla data RVI piu' vicina entro +/- WINDOW_DAYS.
     E' la stessa regola di sentinel1_pipeline.py (pick_closest: |delta| minimo,
     finestra 6 giorni). Qui e' realizzata con pandas.merge_asof(direction='nearest').
  3. Tiene solo le coppie (RVI, NDVI) con ENTRAMBI i valori validi.
     Il filtro nuvole NON lo fa l'RVI (il SAR e' sempre valido): lo fa l'NDVI,
     che e' vuoto (NaN) dove la nuvola lo maschera. Tenendo solo le celle NDVI
     non vuote si usano gia' le sole osservazioni valide.
  4. Per ogni poligono: regressione lineare NDVI ~ RVI
     -> pendenza, intercetta, Pearson r, R2, n, range NDVI/RVI coperto.
  5. Calcola anche la regressione GLOBALE su tutte le coppie (dato di sintesi).

NOTE METODOLOGICHE
------------------
  - La regressione e' NDVI in funzione di RVI (RVI = predittore), perche' lo
    scopo e' STIMARE l'NDVI dall'RVI dove l'NDVI manca.
  - Una singola retta per poligono assume relazione stabile tutto l'anno. Sui
    campi dove il SAR si scollega dall'NDVI (es. risaie allagate, saturazione
    ad alta biomassa) la retta unica fitta male: guardare R2/pendenza negativa
    per individuarli.

OUTPUT
------
  - training_pairs_rvi_ndvi.csv : long format
        poly_id, s2_date, s1_date, delta_days, rvi, ndvi
  - regression_per_polygon.csv  : una riga per poligono
        poly_id, n, ndvi_min, ndvi_max, ndvi_span, rvi_min, rvi_max,
        slope, intercept, pearson_r, spearman_r, r2

  Pearson misura la relazione LINEARE (ed e' la metrica congruente con la
  regressione lineare: pearson_r^2 = r2). Spearman misura la relazione
  MONOTONA: lo scarto Spearman - Pearson segnala non-linearita' (es.
  saturazione dell'RVI), utile per individuare i campi "scollegati".

  Con --plots vengono salvate anche 4 figure riassuntive (richiede matplotlib):
    fig1_scatter_globale.png        relazione globale RVI-NDVI (hexbin + retta)
    fig2_isto_pearson.png           distribuzione del Pearson r sui poligoni
    fig3_pearson_vs_spearman.png    Pearson vs Spearman per poligono (QC)
    fig4_esempio_fit_stagioni.png   miglior vs peggior campo, per stagione

USO
---
  python correlazione_rvi_ndvi.py \
      --rvi  mean_rvi_per_polygon_MASTER.csv \
      --ndvi mean_ndvi_per_polygon.csv \
      --window 6 \
      --outdir . \
      --plots
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────
# PARAMETRI DI DEFAULT
# ─────────────────────────────────────────────────────────────
WINDOW_DAYS_DEFAULT = 6      # finestra di accoppiamento +/- giorni (come la pipeline S1)
MIN_PAIRS           = 3      # minimo coppie per fittare una retta su un poligono


# ─────────────────────────────────────────────────────────────
# CARICAMENTO
# ─────────────────────────────────────────────────────────────
def load_wide(path: Path) -> pd.DataFrame:
    """Carica un CSV  date x poligoni  e restituisce un DataFrame indicizzato per data.

    - separatore ';', decimale '.'
    - le celle vuote diventano NaN (per l'NDVI = nuvola)
    - le date sono normalizzate a mezzanotte naive (si scarta tz/orario)
    """
    df = pd.read_csv(path, sep=";", decimal=".")
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
    df = df.set_index("date").sort_index()
    # forza tutte le colonne poligono a numerico (eventuali '' -> NaN)
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def to_long(df: pd.DataFrame, value_name: str, date_name: str) -> pd.DataFrame:
    """Da matrice  date x poligoni  a formato lungo, scartando i NaN."""
    long = (
        df.reset_index()
        .melt(id_vars="date", var_name="poly_id", value_name=value_name)
        .rename(columns={"date": date_name})
        .dropna(subset=[value_name])
    )
    return long


# ─────────────────────────────────────────────────────────────
# ACCOPPIAMENTO ±N GIORNI (nearest)
# ─────────────────────────────────────────────────────────────
def build_training_pairs(rvi: pd.DataFrame, ndvi: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Accoppia ogni data NDVI alla data RVI piu' vicina entro +/- window_days
    e costruisce il tavolo lungo delle coppie (RVI, NDVI) per poligono.
    """
    if list(rvi.columns) != list(ndvi.columns):
        raise ValueError("Le colonne poligono di RVI e NDVI non coincidono.")

    # 1) mappa a livello di DATA: ogni data S2 -> data S1 piu' vicina entro finestra.
    #    merge_asof richiede chiavi ordinate; direction='nearest' = |delta| minimo.
    s1_dates = pd.DataFrame({"s1_date": rvi.index}).sort_values("s1_date")
    s2_dates = pd.DataFrame({"s2_date": ndvi.index}).sort_values("s2_date")

    mapping = pd.merge_asof(
        s2_dates,
        s1_dates,
        left_on="s2_date",
        right_on="s1_date",
        direction="nearest",
        tolerance=pd.Timedelta(days=window_days),
    ).dropna(subset=["s1_date"])
    mapping["delta_days"] = (mapping["s1_date"] - mapping["s2_date"]).dt.days

    # 2) porta entrambe le matrici in formato lungo (gia' senza NaN)
    ndvi_long = to_long(ndvi, "ndvi", "s2_date")
    rvi_long = to_long(rvi, "rvi", "s1_date")

    # 3) NDVI valido  ->  aggancia la data S1 accoppiata  ->  prendi l'RVI di quel poligono/data
    pairs = (
        ndvi_long.merge(mapping, on="s2_date", how="inner")
        .merge(rvi_long, on=["s1_date", "poly_id"], how="inner")
    )

    pairs = pairs[["poly_id", "s2_date", "s1_date", "delta_days", "rvi", "ndvi"]]
    pairs = pairs.sort_values(["poly_id", "s2_date"]).reset_index(drop=True)
    return pairs


# ─────────────────────────────────────────────────────────────
# CORRELAZIONI / REGRESSIONI
# ─────────────────────────────────────────────────────────────
def spearman_coef(x: np.ndarray, y: np.ndarray) -> float:
    """Coefficiente di Spearman = Pearson calcolato sui RANGHI.

    Coglie qualsiasi relazione monotona (anche non lineare, es. saturazione
    dell'RVI ad alta biomassa). I pari merito ricevono rango medio (standard
    di Spearman). Implementato senza scipy: solo pandas.rank + np.corrcoef.
    """
    if np.std(x) == 0 or np.std(y) == 0:
        return np.nan
    rx = pd.Series(x).rank(method="average").to_numpy()
    ry = pd.Series(y).rank(method="average").to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def regress_per_polygon(pairs: pd.DataFrame, min_pairs: int = MIN_PAIRS) -> pd.DataFrame:
    """Una regressione lineare NDVI ~ RVI per ciascun poligono."""
    rows = []
    for poly, g in pairs.groupby("poly_id", sort=True):
        x = g["rvi"].to_numpy()
        y = g["ndvi"].to_numpy()
        n = len(x)
        if n < min_pairs:
            continue
        slope, intercept = np.polyfit(x, y, 1)
        # r non definito se x o y sono costanti
        r = np.corrcoef(x, y)[0, 1] if (x.std() > 0 and y.std() > 0) else np.nan
        rho = spearman_coef(x, y)
        rows.append(
            {
                "poly_id": poly,
                "n": n,
                "ndvi_min": round(float(y.min()), 3),
                "ndvi_max": round(float(y.max()), 3),
                "ndvi_span": round(float(y.max() - y.min()), 3),
                "rvi_min": round(float(x.min()), 3),
                "rvi_max": round(float(x.max()), 3),
                "slope": round(float(slope), 4),
                "intercept": round(float(intercept), 4),
                "pearson_r": round(float(r), 3),
                "spearman_r": round(float(rho), 3),
                "r2": round(float(r ** 2), 3),
            }
        )
    return pd.DataFrame(rows)


def regress_global(pairs: pd.DataFrame) -> dict:
    """Regressione unica su tutte le coppie (dato di sintesi per il report)."""
    x = pairs["rvi"].to_numpy()
    y = pairs["ndvi"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    r = float(np.corrcoef(x, y)[0, 1])
    rho = spearman_coef(x, y)
    return {
        "n": len(x),
        "slope": round(float(slope), 4),
        "intercept": round(float(intercept), 4),
        "pearson_r": round(r, 3),
        "spearman_r": round(float(rho), 3),
        "r2": round(r ** 2, 3),
    }


# ─────────────────────────────────────────────────────────────
# FIGURE RIASSUNTIVE (opzionali, --plots)
# ─────────────────────────────────────────────────────────────
def _season(month: int):
    """Stagione meteorologica + colore, per la figura d'esempio."""
    if month in (12, 1, 2):
        return "DJF (inverno)", "#3b6fb0"
    if month in (3, 4, 5):
        return "MAM (primavera)", "#3a9d5d"
    if month in (6, 7, 8):
        return "JJA (estate)", "#d1582a"
    return "SON (autunno)", "#9467bd"


def make_summary_plots(pairs: pd.DataFrame, reg: pd.DataFrame, g: dict, outdir: Path):
    """Genera le 4 figure riassuntive per il report. Importa matplotlib solo qui."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    x = pairs["rvi"].to_numpy()
    y = pairs["ndvi"].to_numpy()

    # FIG 1 — scatter globale a densita' (hexbin) + retta globale
    fig, ax = plt.subplots(figsize=(7.5, 6))
    hb = ax.hexbin(x, y, gridsize=55, cmap="viridis", mincnt=1, bins="log")
    xs = np.linspace(x.min(), x.max(), 100)
    ax.plot(xs, g["slope"] * xs + g["intercept"], "r-", lw=2,
            label=f"NDVI = {g['slope']}·RVI {g['intercept']:+}\nr = {g['pearson_r']}  R² = {g['r2']}")
    ax.set_xlabel("RVI (Sentinel-1)"); ax.set_ylabel("NDVI (Sentinel-2)")
    ax.set_title(f"Relazione globale RVI–NDVI ({g['n']} coppie)", fontweight="bold")
    ax.legend(loc="upper left", fontsize=9); ax.grid(alpha=0.2)
    fig.colorbar(hb, ax=ax, label="n coppie (scala log)")
    fig.tight_layout(); fig.savefig(outdir / "fig1_scatter_globale.png", dpi=145); plt.close(fig)

    # FIG 2 — istogramma del Pearson r tra i poligoni
    rr = reg["pearson_r"].dropna()
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.hist(rr, bins=30, color="#6699cc", edgecolor="#333", alpha=0.85)
    ax.axvline(rr.median(), color="#1a5e1a", lw=2, ls="-",
               label=f"mediana per-poligono = {rr.median():.3f}")
    ax.axvline(g["pearson_r"], color="red", lw=2, ls="--",
               label=f"regressione globale = {g['pearson_r']}")
    ax.set_xlabel("Pearson r del poligono"); ax.set_ylabel("n poligoni")
    ax.set_title("Distribuzione del Pearson r sui 154 poligoni", fontweight="bold")
    ax.legend(fontsize=9); ax.grid(alpha=0.2)
    fig.tight_layout(); fig.savefig(outdir / "fig2_isto_pearson.png", dpi=145); plt.close(fig)

    # FIG 3 — Pearson vs Spearman per poligono
    gap = reg["spearman_r"] - reg["pearson_r"]
    fig, ax = plt.subplots(figsize=(7.5, 7))
    sc = ax.scatter(reg["pearson_r"], reg["spearman_r"], c=gap, cmap="coolwarm",
                    vmin=-0.35, vmax=0.35, s=45, edgecolors="#333", linewidths=0.4, zorder=3)
    lim = [-0.45, 1.0]
    ax.plot(lim, lim, "k--", lw=1.3, label="Pearson = Spearman (lineare)")
    ax.axhline(0, color="gray", lw=0.7); ax.axvline(0, color="gray", lw=0.7)
    ax.set_xlim(lim); ax.set_ylim(lim); ax.set_aspect("equal")
    ax.set_xlabel("Pearson r (relazione lineare)")
    ax.set_ylabel("Spearman r (relazione monotona)")
    ax.set_title("Pearson vs Spearman per poligono", fontweight="bold")
    ax.legend(loc="lower right", fontsize=9); ax.grid(alpha=0.2)
    fig.colorbar(sc, ax=ax, shrink=0.75, label="Spearman − Pearson")
    fig.tight_layout(); fig.savefig(outdir / "fig3_pearson_vs_spearman.png", dpi=145); plt.close(fig)

    # FIG 4 — esempio fit: campo migliore vs peggiore (per Pearson), per stagione
    best = reg.loc[reg["pearson_r"].idxmax()]
    worst = reg.loc[reg["pearson_r"].idxmin()]
    pp = pairs.copy()
    pp["month"] = pp["s2_date"].dt.month
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for ax, row in zip(axes, [best, worst]):
        pid = row["poly_id"]
        sub = pp[pp["poly_id"] == pid]
        seen = set()
        for _, q in sub.iterrows():
            lab, col = _season(int(q["month"]))
            ax.scatter(q["rvi"], q["ndvi"], c=col, s=22, alpha=0.7, edgecolors="none",
                       label=lab if lab not in seen else None); seen.add(lab)
        xs = np.linspace(sub["rvi"].min(), sub["rvi"].max(), 50)
        ax.plot(xs, row["slope"] * xs + row["intercept"], "k--", lw=1.8, label="retta del poligono")
        ax.set_xlabel("RVI"); ax.set_ylabel("NDVI"); ax.set_ylim(-0.3, 1.05)
        ax.set_title(f"{pid}  —  r = {row['pearson_r']}", fontweight="bold")
        ax.legend(fontsize=8); ax.grid(alpha=0.25)
    fig.suptitle("Esempio di fit per poligono, colorato per stagione", fontweight="bold")
    fig.tight_layout(); fig.savefig(outdir / "fig4_esempio_fit_stagioni.png", dpi=145); plt.close(fig)

    print(f"\n4 figure riassuntive salvate in: {outdir}")
    for f in ["fig1_scatter_globale", "fig2_isto_pearson",
              "fig3_pearson_vs_spearman", "fig4_esempio_fit_stagioni"]:
        print(f"  -> {f}.png")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Correlazione RVI->NDVI per poligono.")
    ap.add_argument("--rvi", default="mean_rvi_per_polygon_MASTER.csv",
                    help="CSV RVI master (date x poligoni).")
    ap.add_argument("--ndvi", default="mean_ndvi_per_polygon.csv",
                    help="CSV NDVI (date x poligoni, celle vuote = nuvola).")
    ap.add_argument("--window", type=int, default=WINDOW_DAYS_DEFAULT,
                    help="Finestra di accoppiamento +/- giorni (default: 6).")
    ap.add_argument("--outdir", default=".", help="Cartella di output.")
    ap.add_argument("--plots", action="store_true",
                    help="Genera anche le 4 figure riassuntive (richiede matplotlib).")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Carico RVI : {args.rvi}")
    rvi = load_wide(Path(args.rvi))
    print(f"Carico NDVI: {args.ndvi}")
    ndvi = load_wide(Path(args.ndvi))
    print(f"  RVI : {rvi.shape[0]} date x {rvi.shape[1]} poligoni")
    print(f"  NDVI: {ndvi.shape[0]} date x {ndvi.shape[1]} poligoni "
          f"({int(ndvi.notna().any(axis=1).sum())} date con almeno un valore)")

    # 1-3) coppie di training
    pairs = build_training_pairs(rvi, ndvi, args.window)
    pairs_path = outdir / "training_pairs_rvi_ndvi.csv"
    pairs.to_csv(pairs_path, index=False, date_format="%Y-%m-%d")
    print(f"\nCoppie (RVI, NDVI) valide : {len(pairs)}")
    print(f"  date NDVI accoppiate    : {pairs['s2_date'].nunique()}")
    print(f"  finestra usata          : +/-{args.window} giorni")
    print(f"  -> {pairs_path}")

    # 4) regressione per poligono
    reg = regress_per_polygon(pairs)
    reg_path = outdir / "regression_per_polygon.csv"
    reg.to_csv(reg_path, index=False)
    rr = reg["pearson_r"].dropna()
    rho = reg["spearman_r"].dropna()
    print(f"\nRegressione per poligono  : {len(reg)} poligoni")
    print(f"  Pearson r mediano       : {rr.median():.3f}")
    print(f"  Spearman r mediano      : {rho.median():.3f}")
    print(f"  R2 mediano              : {reg['r2'].median():.3f}")
    print(f"  poligoni Pearson >= 0.5 : {(rr >= 0.5).sum()}")
    print(f"  poligoni pendenza < 0   : {(reg['slope'] < 0).sum()}")
    print(f"  -> {reg_path}")

    # 5) regressione globale
    g = regress_global(pairs)
    print(f"\nRegressione GLOBALE (tutte le coppie):")
    print(f"  NDVI = {g['slope']} * RVI + {g['intercept']}")
    print(f"  n = {g['n']}   Pearson = {g['pearson_r']}   Spearman = {g['spearman_r']}   R2 = {g['r2']}")

    # 6) figure riassuntive (opzionale)
    if args.plots:
        make_summary_plots(pairs, reg, g, outdir)


if __name__ == "__main__":
    main()
