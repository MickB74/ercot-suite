"""Wind capture price & revenue — overlay the price forecast on wind output.

Wind blows hardest overnight and in shoulder seasons, exactly when ERCOT prices
are lowest, so a wind project *captures* less than the round-the-clock average
price. This module quantifies that:

    capture price = Σ(generation × price) / Σ(generation)   ($/MWh)
    cannibalization = capture / ATC average − 1             (negative for wind)
    revenue        = Σ(generation × price)                  ($)

Generation comes from a cached wind-production run (the Wind Forecast page's
8760 ``net_mw``), reduced to a month × hour capacity-factor shape. Price comes
from the price-forecast 8760 (P10/P50/P90). Both are Central-time, so they join
on (month-of-year, hour) — the resolution at which both shapes are defined.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Repo-relative first (sibling folders in this monorepo), legacy home as fallback.
_SIBLINGS = Path(__file__).resolve().parents[1]
_HOME = Path.home() / "Documents" / "Github"
_WIND_CANDIDATES = [
    _SIBLINGS / "Ercot_Data_Hub" / "data" / "wind_forecast",
    _SIBLINGS / "Ercot_Wind_Forecast" / "data",
    _SIBLINGS / "Ercot_Wind_Forecast" / "data" / "cache",
    _HOME / "Ercot_Data_Hub" / "data" / "wind_forecast",
    _HOME / "Ercot_Wind_Forecast" / "data",
    _HOME / "Ercot_Wind_Forecast" / "data" / "cache",
]
_NAME_RE = re.compile(
    r"wind_(?P<lat>-?\d+\.\d+)_(?P<lon>-?\d+\.\d+)_(?P<weather>[A-Za-z0-9]+)-"
    r"(?P<start>\d{4}-\d{2}-\d{2}).*?_(?P<mw>\d+(?:\.\d+)?)mw", re.IGNORECASE)


def wind_cache_dir() -> Path | None:
    override = os.environ.get("WIND_CACHE_DIR")
    cands = [Path(override)] if override else []
    cands += _WIND_CANDIDATES
    for d in cands:
        if d and d.exists() and any(d.glob("*.parquet")):
            return d
    return None


def list_wind_runs() -> list[dict]:
    """Cached wind 8760 runs with parsed metadata, newest weather-year first."""
    d = wind_cache_dir()
    if d is None:
        return []
    runs = []
    for p in sorted(d.glob("*.parquet")):
        m = _NAME_RE.search(p.name)
        meta = {"path": str(p), "file": p.name, "nameplate_mw": np.nan,
                "lat": np.nan, "lon": np.nan, "weather": "", "year": ""}
        if m:
            meta.update(nameplate_mw=float(m["mw"]), lat=float(m["lat"]),
                        lon=float(m["lon"]), weather=m["weather"],
                        year=m["start"][:4])
        nm = meta["nameplate_mw"]
        meta["label"] = (f"{meta['lat']:.3f}, {meta['lon']:.3f} · {meta['weather']} "
                         f"{meta['year']} · {nm:.0f} MW" if m else p.name)
        runs.append(meta)
    runs.sort(key=lambda r: str(r.get("year", "")), reverse=True)
    return runs


def load_cf_shape(path: str, nameplate_mw: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Month × hour capacity-factor shape from a cached wind run.

    Returns (shape_df[month, hour, cf], meta{nameplate_mw, annual_cf}).
    """
    df = pd.read_parquet(path)
    col = "net_mw" if "net_mw" in df.columns else (
        "gross_mw" if "gross_mw" in df.columns else df.columns[0])
    idx = pd.DatetimeIndex(df.index)
    np_mw = nameplate_mw
    if np_mw is None:
        m = _NAME_RE.search(Path(path).name)
        np_mw = float(m["mw"]) if m else float(df[col].max())
    cf = (df[col] / np_mw).clip(0, 1).to_numpy()
    g = pd.DataFrame({"month": idx.month, "hour": idx.hour, "cf": cf})
    shape = g.groupby(["month", "hour"], as_index=False)["cf"].mean()
    return shape, {"nameplate_mw": float(np_mw), "annual_cf": float(np.mean(cf))}


def list_wind_sites() -> list[dict]:
    """Group cached runs by location → one entry per site, with all weather years.

    Returns dicts: key (lat,lon), label, lat, lon, nameplate_mw, years (list),
    paths (list). Sites are the natural pick in the UI; their weather years get
    blended into one capacity-factor shape.
    """
    by_site: dict = {}
    for r in list_wind_runs():
        key = (round(r["lat"], 3), round(r["lon"], 3))
        by_site.setdefault(key, []).append(r)
    sites = []
    for (lat, lon), runs in by_site.items():
        years = sorted({str(r.get("year", "")) for r in runs if r.get("year")})
        nm = next((r["nameplate_mw"] for r in runs if not np.isnan(r["nameplate_mw"])), np.nan)
        yr_txt = ", ".join(years) if years else "?"
        sites.append({
            "key": (lat, lon), "lat": lat, "lon": lon, "nameplate_mw": nm,
            "years": years, "paths": [r["path"] for r in runs],
            "label": f"{lat:.3f}, {lon:.3f} · {len(years)} yr ({yr_txt}) · {nm:.0f} MW",
        })
    sites.sort(key=lambda s: s["label"])
    return sites


def load_cf_shape_blended(paths: list[str], nameplate_mw: float | None = None
                          ) -> tuple[pd.DataFrame, dict]:
    """Average the month × hour CF shape across several runs (e.g. weather years).

    Each run's CF is computed against its own parsed nameplate, so blending is
    valid regardless of fleet size. ``nameplate_mw`` only scales generation later.
    """
    shapes, cfs = [], []
    parsed_nm = None
    for p in paths:
        shp, meta = load_cf_shape(p)            # cf vs each run's own nameplate
        shapes.append(shp)
        cfs.append(meta["annual_cf"])
        parsed_nm = parsed_nm or meta["nameplate_mw"]
    blended = (pd.concat(shapes, ignore_index=True)
               .groupby(["month", "hour"], as_index=False)["cf"].mean())
    return blended, {"nameplate_mw": float(nameplate_mw or parsed_nm or 100.0),
                     "annual_cf": float(np.mean(cfs)), "n_years": len(paths)}


def capture(price_8760: pd.DataFrame, cf_shape: pd.DataFrame, nameplate_mw: float,
            bands=("p10", "p50", "p90")) -> pd.DataFrame:
    """Monthly capture price, generation, revenue and cannibalization.

    ``price_8760`` is the price-forecast hourly output (ts, month, is_peak, p*).
    Returns one row per forecast month with gen_mwh, atc_<band>, capture_<band>,
    revenue_<band>, cannib_pct (on P50).
    """
    h = price_8760.copy()
    h["hour"] = pd.to_datetime(h["ts"]).dt.hour
    h["ym"] = pd.to_datetime(h["ts"]).dt.strftime("%Y-%m")
    h = h.merge(cf_shape, on=["month", "hour"], how="left")
    h["cf"] = h["cf"].fillna(0.0)
    h["gen"] = h["cf"] * nameplate_mw   # MWh per hour

    rows = []
    for ym, g in h.groupby("ym"):
        gen = g["gen"].sum()
        rec = {"month": ym, "gen_mwh": gen, "cf": g["cf"].mean()}
        for b in bands:
            if b not in g.columns:
                continue
            rev = float((g[b] * g["gen"]).sum())
            rec[f"revenue_{b}"] = rev
            rec[f"capture_{b}"] = rev / gen if gen else np.nan
            rec[f"atc_{b}"] = float(g[b].mean())
        if "capture_p50" in rec and rec.get("atc_p50"):
            rec["cannib_pct"] = (rec["capture_p50"] / rec["atc_p50"] - 1) * 100
        rows.append(rec)
    return pd.DataFrame(rows)


def annual(monthly: pd.DataFrame, bands=("p10", "p50", "p90")) -> pd.DataFrame:
    """Calendar-year roll-up: generation, capture, ATC, revenue, cannibalization."""
    m = monthly.copy()
    m["year"] = m["month"].str[:4]
    rows = []
    for yr, g in m.groupby("year"):
        gen = g["gen_mwh"].sum()
        rec = {"year": yr, "gen_gwh": gen / 1000.0,
               "cf": (g["cf"] * g["gen_mwh"]).sum() / gen if gen else np.nan}
        for b in bands:
            rcol, acol = f"revenue_{b}", f"atc_{b}"
            if rcol in g:
                rev = g[rcol].sum()
                rec[f"revenue_{b}_m"] = rev / 1e6           # $M
                rec[f"capture_{b}"] = rev / gen if gen else np.nan
            if acol in g:
                rec[f"atc_{b}"] = (g[acol] * g["gen_mwh"]).sum() / gen if gen else np.nan
        if "capture_p50" in rec and rec.get("atc_p50"):
            rec["cannib_pct"] = (rec["capture_p50"] / rec["atc_p50"] - 1) * 100
        rows.append(rec)
    return pd.DataFrame(rows)


def hourly_profile(cf_shape: pd.DataFrame, price_8760: pd.DataFrame) -> pd.DataFrame:
    """Average wind CF and average price by hour-of-day — shows why capture lags."""
    h = price_8760.copy()
    h["hour"] = pd.to_datetime(h["ts"]).dt.hour
    pr = h.groupby("hour")["p50"].mean().rename("price_p50")
    cf = cf_shape.groupby("hour")["cf"].mean().rename("wind_cf")
    return pd.concat([pr, cf], axis=1).reset_index()


def settlement(monthly: pd.DataFrame, strike: float) -> pd.DataFrame:
    """VPPA settlement to the offtaker: (market − strike) × generation, by month.

    Positive = project pays the buyer (market above strike); negative = buyer
    tops up. Uses the P50 capture price as the realized market price.
    """
    out = monthly[["month", "gen_mwh", "capture_p50"]].copy()
    out["strike"] = strike
    out["settle_p50_$"] = (out["capture_p50"] - strike) * out["gen_mwh"]
    if "capture_p10" in monthly:
        out["settle_p10_$"] = (monthly["capture_p10"] - strike) * out["gen_mwh"]
    if "capture_p90" in monthly:
        out["settle_p90_$"] = (monthly["capture_p90"] - strike) * out["gen_mwh"]
    return out
