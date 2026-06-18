"""Optional ERCOT power-futures strip — traded calibration for near months.

The gas x heat-rate model gives a full curve from first principles. Where the
user pastes ICE ERCOT hub futures (data/inputs/ercot_power_strip.csv), we blend
those traded settlements into the near months and fade to the model further out
(traded forwards are liquid ~12-24 months, noise beyond). The blend weight on
the traded price decays linearly from 1.0 at asof to 0.0 at ``fade_months``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import pf_paths


def load_strip(hub: str | None = None) -> pd.DataFrame | None:
    """Manual ERCOT power strip; filtered to one hub if given. None if empty."""
    p = pf_paths.POWER_STRIP_CSV
    if not p.exists():
        return None
    df = pd.read_csv(p, comment="#")
    need = {"month", "hub", "block", "price"}
    if df.empty or not need.issubset(df.columns):
        return None
    df["month"] = pd.to_datetime(df["month"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])
    if hub:
        df = df[df["hub"] == hub]
    return df[["month", "hub", "block", "price"]].reset_index(drop=True) if not df.empty else None


def blend(model_curve: pd.DataFrame, hub: str, *, fade_months: int = 18) -> pd.DataFrame:
    """Blend traded futures into the model P50 curve.

    ``model_curve`` columns: month, block, p50 (+ others passed through). Adds
    ``p50_model``, ``traded``, ``blend_w`` and overwrites p50 with the blend.
    All scenario bands are shifted by the same blend delta so the distribution
    re-centers on the traded level without losing its width.
    """
    strip = load_strip(hub)
    out = model_curve.copy()
    out["p50_model"] = out["p50"]
    out["traded"] = np.nan
    out["blend_w"] = 0.0
    if strip is None or strip.empty:
        return out

    months = sorted(out["month"].unique())
    first = pd.Timestamp(min(months))
    s = strip.set_index(["month", "block"])["price"]
    for i, row in out.iterrows():
        key = (row["month"], row["block"])
        if key not in s.index:
            continue
        traded = float(s.loc[key])
        h = (pd.Timestamp(row["month"]).to_period("M") - first.to_period("M")).n
        w = max(0.0, 1.0 - h / float(fade_months))
        if w <= 0:
            continue
        delta = w * (traded - row["p50"])
        out.at[i, "traded"] = traded
        out.at[i, "blend_w"] = w
        for col in ("p50", "mean", "p5", "p10", "p25", "p75", "p90", "p95"):
            if col in out.columns and pd.notna(out.at[i, col]):
                out.at[i, col] = out.at[i, col] + delta
    return out
