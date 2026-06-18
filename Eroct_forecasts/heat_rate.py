"""Market-implied heat rate — the 'multiplier' that turns gas into power.

For each historical (year, month, block) we compute

    IHR = mean ERCOT hub price ($/MWh)  /  Henry Hub gas ($/MMBtu)      [MMBtu/MWh]

then pool across years into a distribution per (calendar-month, block). The
*mean* IHR is the P50 multiplier; the *spread* across years (std, quantiles, and
the raw samples for bootstrapping) is what drives the price scenarios later.

Working at monthly-block granularity keeps the ratio well-behaved: monthly mean
prices stay positive even when individual 15-min intervals go negative, so the
multiplicative model doesn't blow up. Average scarcity/congestion is naturally
embedded in the realized heat rate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import gas_curve
import pf_history

BLOCKS = ["peak", "offpeak", "atc"]


def realized(rt15: pd.DataFrame) -> pd.DataFrame:
    """Per-(year, month, block) realized heat rate from one hub's history.

    Columns: year, month, block, price, gas, ihr.
    """
    power = pf_history.monthly_block_mean(rt15)
    gas = gas_curve.monthly_history().copy()
    gas["year"] = gas["month"].dt.year
    gas["mo"] = gas["month"].dt.month
    gas = gas.rename(columns={"henry_hub": "gas"})[["year", "mo", "gas"]]

    df = power.merge(gas, left_on=["year", "month"], right_on=["year", "mo"], how="left")
    df = df.drop(columns=["mo"]).dropna(subset=["gas"])
    df = df[df["gas"] > 0]
    df["ihr"] = df["price"] / df["gas"]
    return df.reset_index(drop=True)


def buckets(rt15: pd.DataFrame, *, min_years: int = 2) -> pd.DataFrame:
    """Distribution of IHR per (calendar-month, block).

    Columns: month, block, n, ihr_mean, ihr_std, ihr_p10/p50/p90, samples (list).
    ``samples`` is the per-year realized IHR array used for bootstrap sampling
    in the scenario engine. Buckets with < min_years samples fall back to a
    block-wide pooled std so scenarios still have dispersion.
    """
    r = realized(rt15)
    rows = []
    for (mo, block), g in r.groupby(["month", "block"]):
        s = g["ihr"].to_numpy(dtype=float)
        rows.append({
            "month": int(mo), "block": block, "n": len(s),
            "ihr_mean": float(np.mean(s)),
            "ihr_std": float(np.std(s, ddof=1)) if len(s) > 1 else np.nan,
            "ihr_p10": float(np.percentile(s, 10)),
            "ihr_p50": float(np.percentile(s, 50)),
            "ihr_p90": float(np.percentile(s, 90)),
            "samples": s,
        })
    out = pd.DataFrame(rows)

    # Fill missing/degenerate std with a block-wide relative dispersion so even
    # thin buckets carry uncertainty into the scenarios.
    for block in BLOCKS:
        m = out["block"] == block
        if not m.any():
            continue
        rel = (out.loc[m, "ihr_std"] / out.loc[m, "ihr_mean"]).replace([np.inf, -np.inf], np.nan)
        rel_med = rel.dropna().median()
        rel_med = 0.20 if pd.isna(rel_med) else float(rel_med)
        need = m & (out["ihr_std"].isna() | (out["n"] < min_years))
        out.loc[need, "ihr_std"] = out.loc[need, "ihr_mean"] * rel_med
    return out.sort_values(["block", "month"]).reset_index(drop=True)


def summary(rt15: pd.DataFrame) -> pd.DataFrame:
    """Compact peak/offpeak heat-rate table by month for eyeballing."""
    b = buckets(rt15)
    piv = b.pivot_table(index="month", columns="block",
                        values=["ihr_mean", "ihr_std", "n"])
    piv.columns = [f"{a}_{c}" for a, c in piv.columns]
    return piv.reset_index()


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def display_table(rt15: pd.DataFrame) -> pd.DataFrame:
    """Human-readable heat-rate table: what the forecast actually uses.

    One row per calendar month with the peak and off-peak median (the central
    multiplier), the typical P10-P90 range, the mean (to expose scarcity-year
    skew), and how many years of history back each bucket.
    """
    b = buckets(rt15).set_index(["month", "block"])
    rows = []
    for mo in range(1, 13):
        rec = {"Month": MONTH_NAMES[mo - 1]}
        n = 0
        for block, lab in (("peak", "Peak"), ("offpeak", "Off-peak")):
            if (mo, block) not in b.index:
                continue
            r = b.loc[(mo, block)]
            n = int(r["n"])
            rec[f"{lab} median"] = round(float(r["ihr_p50"]), 1)
            rec[f"{lab} P10–P90"] = f"{r['ihr_p10']:.1f} – {r['ihr_p90']:.1f}"
            rec[f"{lab} mean"] = round(float(r["ihr_mean"]), 1)
        rec["Years"] = n
        rows.append(rec)
    cols = ["Month",
            "Peak median", "Peak P10–P90", "Peak mean",
            "Off-peak median", "Off-peak P10–P90", "Off-peak mean", "Years"]
    out = pd.DataFrame(rows)
    return out[[c for c in cols if c in out.columns]]
