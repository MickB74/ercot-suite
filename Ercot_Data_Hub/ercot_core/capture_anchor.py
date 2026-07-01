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

# ERCOT system-wide offer cap. Lowered to $5,000/MWh on 2024-01-01 (SB3, post-Uri);
# was $9,000 through 2023 and was hit during Winter Storm Uri (Feb-2021, ~$9,174).
# The scarcity calibration (a) uses a recent lookback so the scarce-day composite
# reflects the CURRENT $5,000 regime, and (b) clips prices to the cap so a retired
# $9,000-era interval can never inflate a forward tail.
ERCOT_OFFER_CAP = 5000.0
ERCOT_OFFER_CAP_PRIOR = 9000.0  # pre-2024-01-01


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
          price_node: str | None = None, label: str = "", log=print) -> dict:
    """Compute and cache the capture anchor for an asset.

    ``node`` is the cache key / portal lookup key (the asset's resource_node).
    ``price_node`` is the node whose RT15 price drives the basis — defaults to
    ``node`` but differs for aggregate resources (e.g. Azure Sky's resource_node
    is ``AZURE_SKY_WIND_AGG`` but it's priced at ``AZURE_RN``).
    ``settle_point`` is the contract reference (hub code or the node); ``hub`` is
    the trading hub used for the basis comparison.
    """
    gen = gen_hourly(units)
    hubp = hub_price_hourly(hub)
    nodep = node_price_hourly(price_node or node)
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

# --------------------------------------------------------------------------- #
# Hourly-shaped forward capture — derive the ratio from the joint
# (generation × price) hour-of-day shape, instead of assuming a flat ratio.
# Lets the forward capture DECLINE as the midday duck-curve trough deepens.
# --------------------------------------------------------------------------- #

_MIDDAY = range(9, 16)   # hours 9:00–15:59, the solar-cannibalized belly


def price_hod(hub: str, *, lookback_years: int = 3) -> pd.DataFrame:
    """Normalized hub price by (calendar-month, hour): the duck-curve shape.

    Each month's 24-hour profile is scaled to mean 1.0, so multiplying by a
    monthly ATC forecast reconstructs an hourly price whose monthly mean is the
    ATC. Uses only the most recent ``lookback_years`` so the shape reflects
    *current* cannibalization, not a rosy multi-year average.
    """
    p = hub_price_hourly(hub)
    if p.empty:
        return pd.DataFrame()
    cutoff = p.index.max() - pd.DateOffset(years=lookback_years)
    p = p[p.index >= cutoff].clip(upper=ERCOT_OFFER_CAP)   # current-cap regime
    df = pd.DataFrame({"p": p})
    df["m"], df["h"] = df.index.month, df.index.hour
    mh = df.groupby(["m", "h"])["p"].mean().unstack("h")
    return mh.div(mh.mean(axis=1), axis=0)   # normalize each month to mean 1.0


def gen_hod(units: list[str]) -> pd.DataFrame:
    """Mean generation (MW) by (calendar-month, hour) from SCED — the asset's shape."""
    g = gen_hourly(units)
    if g.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"g": g.clip(lower=0)})
    df["m"], df["h"] = df.index.month, df.index.hour
    return df.groupby(["m", "h"])["g"].mean().unstack("h")


def _deepen_midday(shape_row: pd.Series, pct: float) -> pd.Series:
    """Push the midday belly down by ``pct`` and renormalize the day to mean 1.0
    (weight shifts to evening — i.e. the duck curve deepens)."""
    s = shape_row.astype(float).copy()
    s.loc[[h for h in _MIDDAY if h in s.index]] *= max(0.0, 1.0 - pct / 100.0)
    mean = s.mean()
    return s / mean if mean else s


def _shaped_ratio(ps: pd.DataFrame, gs: pd.DataFrame, m: int, midday_pct: float) -> float | None:
    """Σ(genₕ × price_shapeₕ)/Σ(genₕ) for calendar month ``m``, midday deepened ``midday_pct``."""
    if m not in ps.index or m not in gs.index:
        return None
    pr = _deepen_midday(ps.loc[m], midday_pct) if midday_pct else ps.loc[m]
    g = gs.loc[m].reindex(pr.index).fillna(0.0)
    return float((g * pr).sum() / g.sum()) if g.sum() > 0 else None


def shaped_capture_curve(node: str, *, hub: str, units: list[str],
                         forecast_months: list[str],
                         midday_trend_pct_per_yr: float = 0.0,
                         base_year: int | None = None,
                         lookback_years: int = 3,
                         anchor_realized: bool = True) -> dict[str, float]:
    """Hourly-shaped forward capture ratio per month ("YYYY-MM" → ratio).

    The level comes from the **realized** capture ratio (true gen×price covariance,
    incl. curtailment); the **forward decline** comes from the hour-of-day shapes —
    the midday belly is deepened by ``midday_trend_pct_per_yr × (year−base_year)``
    and the resulting *trend multiplier* (deepened ÷ current shape) is applied to
    the realized level. So with no trend it returns the realized ratio unchanged;
    with a trend, each future year's solar capture erodes as the duck curve deepens.

    The pure shape (no realized anchor) misses day-to-day gen↔price covariance and
    runs ~10 pts optimistic in summer — hence anchoring the level to realized.
    Returns {} if shapes are unavailable (caller falls back to the static ratio).
    """
    ps = price_hod(hub, lookback_years=lookback_years)
    gs = gen_hod(units)
    if ps.empty or gs.empty:
        return {}
    base_year = base_year or pd.Timestamp.today().year
    realized = monthly_capture_ratio(node, "p50") if anchor_realized else None
    out: dict[str, float] = {}
    for ym in forecast_months:
        y, m = int(ym[:4]), int(ym[5:7])
        base = _shaped_ratio(ps, gs, m, 0.0)
        if base is None or base <= 0:
            continue
        trended = _shaped_ratio(ps, gs, m, midday_trend_pct_per_yr * max(0, y - base_year))
        trend_mult = (trended / base) if trended is not None else 1.0
        level = (realized.get(m) if realized else None) or base
        out[ym] = round(float(level * trend_mult), 4)
    return out


def _scenario_shapes(hub: str, lookback_years: int, scarce_pct: float, mild_pct: float):
    """Per calendar-month hour-of-day price shapes for scarce / normal / mild days.

    Ranks each month's days by daily-mean price; the top ``scarce_pct``% are the
    scarcity composite (a spiky evening profile), the bottom ``mild_pct``% the mild
    composite. Each 24-h profile is normalized to its own mean, so it's a pure
    shape a capture ratio can be read off. Returns {month: {scarce/normal/mild: Series}}.
    """
    p = hub_price_hourly(hub)
    if p.empty:
        return {}
    p = p[p.index >= p.index.max() - pd.DateOffset(years=lookback_years)].clip(upper=ERCOT_OFFER_CAP)
    df = pd.DataFrame({"p": p})
    df["date"], df["m"], df["h"] = df.index.normalize(), df.index.month, df.index.hour
    daymean = df.groupby("date")["p"].mean()
    out = {}
    for m in sorted(df["m"].unique()):
        dm = daymean[daymean.index.month == m]
        sub = df[df["m"] == m]
        n = len(dm)
        norm = lambda hod: hod / hod.mean() if hod.mean() else hod
        def shp(days):
            s = sub[sub["date"].isin(days)]
            return norm(s.groupby("h")["p"].mean()) if not s.empty else None
        out[int(m)] = {
            "normal": norm(sub.groupby("h")["p"].mean()),
            "scarce": shp(dm.nlargest(max(1, int(n * scarce_pct / 100))).index),
            "mild": shp(dm.nsmallest(max(1, int(n * mild_pct / 100))).index),
        }
    return out


def scenario_capture_ratios(node: str, *, hub: str, units: list[str],
                            lookback_years: int = 3, scarce_pct: float = 15.0,
                            mild_pct: float = 15.0) -> dict[int, dict]:
    """Per-band capture ratios {month: {p10, p50, p90}} pairing each PRICE
    percentile with its matching capture SHAPE.

    p50 = the realized capture ratio (covariance-correct level). p90 pairs the
    high-price (scarce-summer) ATC with the *scarce-day* capture shape; p10 with
    the mild-day shape. The band-delta is shape-driven, the level realized-anchored:
        p90 = realized × (scarce_shape_ratio / normal_shape_ratio)
    For solar this makes p90's ratio LOWER than p50 (it misses the evening spikes),
    so a scarce summer lifts the bill mainly through price, not capture — the
    honest asymmetry. Returns {} if shapes/realized are unavailable.
    """
    shapes = _scenario_shapes(hub, lookback_years, scarce_pct, mild_pct)
    gs = gen_hod(units)
    realized = monthly_capture_ratio(node, "p50")
    if not shapes or gs.empty or not realized:
        return {}

    def _ratio(shape, m):
        if shape is None or m not in gs.index:
            return None
        g = gs.loc[m].reindex(shape.index).fillna(0.0)
        return float((g * shape).sum() / g.sum()) if g.sum() > 0 else None

    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    out = {}
    for m, sc in shapes.items():
        base = realized.get(m)
        norm = _ratio(sc["normal"], m)
        if not base or not norm:
            continue
        scarce = _ratio(sc["scarce"], m)
        mild = _ratio(sc["mild"], m)
        # Band-delta = scenario shape ÷ normal shape, but clamped: 3 yrs of daily
        # composites is noisy, so bound how far a band can swing the capture ratio
        # (±25%) and floor/ceiling the absolute ratio. Keeps a bad composite from
        # ever producing a nonsense bill input (e.g. a 0.02 p10).
        d_hi = _clamp(scarce / norm, 0.75, 1.25) if scarce else 1.0
        d_lo = _clamp(mild / norm, 0.75, 1.25) if mild else 1.0
        out[m] = {
            "p50": round(_clamp(base, 0.2, 1.25), 4),
            "p90": round(_clamp(base * d_hi, 0.2, 1.25), 4),
            "p10": round(_clamp(base * d_lo, 0.2, 1.25), 4),
        }
    return out


def forward_band_ratios(node: str, *, hub: str, units: list[str],
                        forecast_months: list[str],
                        midday_trend_pct_per_yr: float = 0.0,
                        base_year: int | None = None) -> dict[str, dict]:
    """Per-forecast-month per-band capture ratios: {"YYYY-MM": {p10, p50, p90}}.

    p50 = the hourly-shaped, year-trended capture (:func:`shaped_capture_curve`);
    the p10/p90 band-delta comes from the scarce/mild day scenarios
    (:func:`scenario_capture_ratios`). So each forecast month pairs the price
    percentile with its matching capture shape — a scarce summer lifts the P90
    bill through price while (for solar) its capture ratio softens. Returns {}
    when shapes are unavailable (caller falls back to the scalar path).
    """
    p50 = shaped_capture_curve(node, hub=hub, units=units,
                               forecast_months=forecast_months,
                               midday_trend_pct_per_yr=midday_trend_pct_per_yr,
                               base_year=base_year)
    if not p50:
        return {}
    scen = scenario_capture_ratios(node, hub=hub, units=units)
    out = {}
    for ym, r50 in p50.items():
        s = scen.get(int(ym[5:7]))
        if s and s.get("p50"):
            out[ym] = {"p50": r50,
                       "p10": round(r50 * s["p10"] / s["p50"], 4),
                       "p90": round(r50 * s["p90"] / s["p50"], 4)}
        else:
            out[ym] = {"p10": r50, "p50": r50, "p90": r50}
    return out


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
