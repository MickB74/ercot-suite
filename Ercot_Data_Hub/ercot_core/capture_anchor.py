"""Realized capture-price anchor — the price-side companion to ``eia_anchor``.

A solar/wind PPA's *capture price* is the generation-weighted market price the
asset realizes; capture ratio = capture ÷ simple-average price. It is a joint
(generation × price) shape problem and it is **falling structurally** in ERCOT as
solar floods midday (see the system analysis). The portals' forward bill currently
scales the hub forecast by a single trailing ratio (``price_forecast.capture_to_hub_ratio``),
computed over whatever window is displayed — which is seasonally biased and
systematically optimistic (back-test: 39-pt MAE vs 11-pt for a seasonal curve).

This module builds, per asset, from SCED generation × settled price history:

  * ``monthly_capture_ratio`` — realized capture ÷ ATC by calendar month, P10/P50/P90
    (the seasonal curve the forward model should use instead of a flat ratio).
  * ``basis`` — node vs hub capture ($/MWh, simple and generation-weighted): what
    the asset earns at its own node vs the hub it settles against.

Settlement uses the contract's ``settle_point`` (hub or node); the node figure is
the asset's true economics. Cache: one JSON per resource node in ``data/capture_anchor/``.

Build with :func:`build`; serve to ``price_forecast`` with :func:`monthly_capture_ratio`.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ercot_core import paths

CAPTURE_DIR = paths.DATA / "capture_anchor"
_NODE_DIR = paths.NODE_DATA_DIR


# --------------------------------------------------------------------------- #
# Loaders → hourly series (naive Central)
# --------------------------------------------------------------------------- #

def _to_hourly(idx, vals) -> pd.Series:
    t = pd.to_datetime(idx)
    t = t.dt.tz_localize(None) if getattr(t.dt, "tz", None) is not None else t
    s = pd.Series(pd.to_numeric(vals, errors="coerce").values, index=t)
    return s[~s.index.duplicated(keep="first")].sort_index().resample("h").mean()


def hub_price_hourly(hub: str) -> pd.Series:
    hp = pd.read_parquet(paths.HUB_PRICES_PARQUET)
    hp = hp[hp["settlement_point"] == hub]
    if hp.empty:
        return pd.Series(dtype=float)
    return _to_hourly(hp["interval_ending_central"], hp["price"])


def node_price_hourly(node: str) -> pd.Series:
    frames = []
    for f in sorted(glob.glob(str(_NODE_DIR / "node_price_*.parquet"))):
        d = pd.read_parquet(f)
        loc = "location" if "location" in d.columns else (
            "settlement_point" if "settlement_point" in d.columns else None)
        if loc is None:
            continue
        d = d[d[loc] == node]
        if not d.empty:
            frames.append(d)
    if not frames:
        return pd.Series(dtype=float)
    d = pd.concat(frames)
    pcol = next((c for c in ("spp", "price", "settlement_point_price") if c in d.columns), None)
    return _to_hourly(d["interval_start"], d[pcol]) if pcol else pd.Series(dtype=float)


def gen_hourly(units: list[str]) -> pd.Series:
    frames = []
    for u in units:
        for f in sorted(glob.glob(str(paths.PLANT_DATA_DIR / f"{u}_*.parquet"))):
            d = pd.read_parquet(f, columns=["sced_timestamp", "telemetered_net_output"])
            frames.append(pd.Series(d["telemetered_net_output"].values,
                                    index=pd.to_datetime(d["sced_timestamp"])))
    if not frames:
        return pd.Series(dtype=float)
    s = pd.concat(frames)
    idx = s.index.tz_localize(None) if s.index.tz is not None else s.index
    s.index = idx
    # multiple units → sum at each timestamp, then hourly mean MW
    return s.groupby(level=0).sum().sort_index().resample("h").mean()


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def anchor_path(node: str) -> Path:
    return CAPTURE_DIR / f"{node}.json"


def load(node: str) -> dict | None:
    p = anchor_path(node)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def _ratio_pcts(df: pd.DataFrame, gcol="g", pcol="p") -> dict:
    """Per-calendar-month capture ratio P10/P50/P90 across years."""
    df = df.copy()
    df["m"] = df.index.month
    df["y"] = df.index.year
    by = {}
    for (y, m), c in df.groupby(["y", "m"]):
        if c[gcol].sum() > 0 and c[pcol].mean() > 0:
            by.setdefault(m, []).append((c[gcol] * c[pcol]).sum() / c[gcol].sum() / c[pcol].mean())
    out = {}
    for m, vals in by.items():
        out[m] = {"p50": round(float(np.median(vals)), 3),
                  "p10": round(float(np.min(vals)), 3),
                  "p90": round(float(np.max(vals)), 3), "n": len(vals)}
    return out


def build(node: str, *, settle_point: str, units: list[str], hub: str,
          label: str = "", log=print) -> dict:
    """Compute and cache the capture anchor for an asset.

    ``settle_point`` is the contract reference (hub code like ``HB_NORTH`` or the
    node itself); ``hub`` is the trading hub used for the basis comparison.
    """
    gen = gen_hourly(units)
    hubp = hub_price_hourly(hub)
    nodep = node_price_hourly(node)
    if gen.empty or hubp.empty:
        raise RuntimeError(f"missing gen or hub price for {node}")

    settle = nodep if (settle_point == node or settle_point not in (hub,)) and not nodep.empty else hubp
    df = pd.DataFrame({"g": gen, "p": settle, "hub": hubp, "node": nodep}).dropna(subset=["g", "p"])
    df = df[df["g"] > 0]
    if df.empty:
        raise RuntimeError(f"no overlapping gen/price for {node}")

    monthly = _ratio_pcts(df)
    blended_cap = float((df["g"] * df["p"]).sum() / df["g"].sum())
    blended_ratio = blended_cap / float(df["p"].mean())

    # basis: node vs hub over hours where both exist
    b = df.dropna(subset=["hub", "node"])
    basis = {}
    if not b.empty and b["g"].sum() > 0:
        ncap = float((b["g"] * b["node"]).sum() / b["g"].sum())
        hcap = float((b["g"] * b["hub"]).sum() / b["g"].sum())
        basis = {
            "node_capture": round(ncap, 2), "hub_capture": round(hcap, 2),
            "basis_simple": round(float(b["node"].mean() - b["hub"].mean()), 2),
            "basis_genweighted": round(ncap - hcap, 2),
            "node_capture_ratio": round(ncap / float(b["node"].mean()), 3) if b["node"].mean() else None,
        }

    out = {
        "node": node, "label": label, "settle_point": settle_point, "hub": hub,
        "units": units, "span": f"{df.index.min():%Y-%m} → {df.index.max():%Y-%m}",
        "n_months": int(df.index.to_period("M").nunique()),
        "blended_capture": round(blended_cap, 2),
        "blended_ratio": round(blended_ratio, 3),
        "monthly_capture_ratio": {str(k): v for k, v in monthly.items()},
        "basis": basis,
        "method": "realized SCED gen × settled price, capture/ATC by calendar month",
        "note": "per-asset; apply a downward spring-trend prior; flat-ratio forward is optimistic",
    }
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    anchor_path(node).write_text(json.dumps(out, indent=2))
    log(f"[capture_anchor] {node}: blended ratio {out['blended_ratio']:.0%} "
        f"@ {settle_point}; basis {basis.get('basis_genweighted','—')}/MWh → {anchor_path(node).name}")
    return out


# --------------------------------------------------------------------------- #
# Serve to price_forecast
# --------------------------------------------------------------------------- #

def monthly_capture_ratio(node: str, band: str = "p50",
                          spring_trend_pct: float = 0.0) -> dict | None:
    """Per-calendar-month {1..12: ratio} for ``node``, or None if no anchor.

    ``spring_trend_pct`` optionally shaves the Feb–May ratios by that % to reflect
    the still-deepening midday cannibalization the static curve under-predicts.
    """
    a = load(node)
    if not a:
        return None
    mc = a.get("monthly_capture_ratio") or {}
    if not mc:
        return None
    out = {}
    for k, v in mc.items():
        r = float(v.get(band, v.get("p50")))
        if spring_trend_pct and int(k) in (2, 3, 4, 5):
            r *= (1.0 - spring_trend_pct / 100.0)
        out[int(k)] = round(r, 4)
    return out
