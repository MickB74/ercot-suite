"""Spread the monthly strip into an 8760 hourly shape for settlement modeling.

The monthly forecast gives a peak and an off-peak price level per month. To get
an hourly curve (for VPPA / load settlement), we borrow the *intra-block* shape
from history: the normalized hour-of-day x month profile, renormalized within
each block so the peak hours average to the forecast peak price and the
off-peak hours average to the forecast off-peak price. Levels come from the
forecast; the within-block shape comes from realized history.

Scenario bands (P10/P90) are carried down by scaling the hourly P50 by the
monthly p10/p50 and p90/p50 ratios for the relevant block.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import pf_history

PEAK_START_HOUR = pf_history.PEAK_START_HOUR
PEAK_END_HOUR = pf_history.PEAK_END_HOUR


def _is_peak(idx: pd.DatetimeIndex) -> np.ndarray:
    return ((idx.dayofweek < 5) & (idx.hour >= PEAK_START_HOUR)
            & (idx.hour < PEAK_END_HOUR))


def build_8760(curve: pd.DataFrame, rt15: pd.DataFrame, *,
               bands=("p10", "p50", "p90"), renew: dict | None = None) -> pd.DataFrame:
    """Hourly shaped forecast over the curve's month span.

    ``curve`` is the monthly output of forecast.run (needs month, block, p50 +
    any requested band columns). Returns one row per hour with columns:
    ts (naive Central), month, is_peak, and one column per band.

    ``renew`` (optional): a renewable-buildout config (see ``renewable_shape``)
    that bends the historical intraday shape for expected solar/wind additions
    — deeper midday troughs, a sharper evening ramp, softer overnights — scaled
    by GW added per forecast year. The monthly *level* is unchanged; only the
    within-day distribution (and thus capture prices) moves.
    """
    shape = pf_history.hourly_shape(rt15)          # month, hour, shape
    shp = shape.set_index(["month", "hour"])["shape"]

    months = pd.to_datetime(sorted(curve["month"].unique()))
    start = months.min()
    end = (months.max() + pd.offsets.MonthBegin(1))
    idx = pd.date_range(start, end, freq="h", inclusive="left")

    df = pd.DataFrame({"ts": idx})
    df["month_start"] = df["ts"].values.astype("datetime64[M]")
    df["moy"] = df["ts"].dt.month
    df["hour"] = df["ts"].dt.hour
    df["is_peak"] = _is_peak(idx)
    df["block"] = np.where(df["is_peak"], "peak", "offpeak")
    df["shape"] = [shp.get((m, h), 1.0) for m, h in zip(df["moy"], df["hour"])]

    # Optional renewable-buildout reshaping of the historical shape, applied
    # before block renormalization so it only redistributes within the day.
    if renew:
        import renewable_shape  # noqa: PLC0415  (optional dependency, lazy)
        df = renewable_shape.reshape_8760(df, renew)

    # renormalize shape to mean 1 within each (month_start, block)
    grp = df.groupby(["month_start", "block"])["shape"].transform("mean")
    df["shape_norm"] = df["shape"] / grp.replace(0, np.nan)
    df["shape_norm"] = df["shape_norm"].fillna(1.0)

    cur = curve.set_index(["month", "block"])
    for band in bands:
        if band not in curve.columns:
            continue
        lvl = [cur.loc[(ms, b), band] if (ms, b) in cur.index else np.nan
               for ms, b in zip(df["month_start"], df["block"])]
        df[band] = np.asarray(lvl, dtype=float) * df["shape_norm"].to_numpy()

    keep = ["ts", "moy", "is_peak"] + [b for b in bands if b in df.columns]
    out = df[keep].rename(columns={"moy": "month"})
    return out.reset_index(drop=True)


def annual_summary(hourly: pd.DataFrame, band: str = "p50") -> pd.DataFrame:
    """Simple ATC / peak / off-peak annual averages from the 8760 (sanity check)."""
    h = hourly.copy()
    h["year"] = h["ts"].dt.year
    rows = []
    for yr, g in h.groupby("year"):
        rows.append({
            "year": yr,
            "atc": g[band].mean(),
            "peak": g.loc[g["is_peak"], band].mean(),
            "offpeak": g.loc[~g["is_peak"], band].mean(),
            "hours": len(g),
        })
    return pd.DataFrame(rows)
