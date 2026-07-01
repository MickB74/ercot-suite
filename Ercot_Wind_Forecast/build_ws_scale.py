"""Learn per-hub, per-month wind-speed bias corrections from SCED actuals.

ERA5 reanalysis under-resolves hub-height wind (worst near the coast / RGV
low-level jet), so the raw physics under-predicts energy. For every suite wind
asset we can place (registry coords + SCED units), we fit the hub-height wind
multiplier ``k`` per calendar month that makes modelled energy match metered
SCED energy, then aggregate by ERCOT hub (hours-weighted). The result is written
into ``wind_calibration.json`` as a production prior so every forecast is
speed-corrected before the power curve — see wind_calibration.ws_scale_for().

Run: python build_ws_scale.py   (writes wind_calibration.json; --dry to preview)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import wind_backtest as b
import wind_calibration as wc

HERE = Path(__file__).resolve().parent
REGISTRY = HERE.parent / "Ercot_Data_Hub" / "ercot_core" / "registry" / "ercot_assets.json"
CROSSWALK = HERE / "reference" / "crosswalk_wind.json"
KS = [round(1.0 + 0.05 * i, 2) for i in range(21)]  # 1.00 … 2.00
REGIONS = ("NORTH", "SOUTH_COAST", "SOUTH_INLAND", "WEST", "PAN", "HOUSTON")
MIN_SITES = 3   # a region needs at least this many plants for its own prior


def _region(lat: float, lon: float) -> str:
    hub = wc.infer_hub(lat, lon) or "NORTH"
    if hub == "SOUTH":
        return "SOUTH_COAST" if lon > -98.2 else "SOUTH_INLAND"
    return hub


def _wind_sites() -> list[dict]:
    """Union of crosswalk-matched plants and registry wind assets (deduped)."""
    out, seen = [], set()

    def add(name, lat, lon, units):
        if not (lat and lon and units):
            return
        key = frozenset(units)
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name, "region": _region(float(lat), float(lon)),
                    "lat": float(lat), "lon": float(lon), "units": list(units)})

    if CROSSWALK.exists():
        for r in json.loads(CROSSWALK.read_text()):
            add(r["plant"], r["lat"], r["lon"], r["units"])
    d = json.loads(REGISTRY.read_text())
    assets = d if isinstance(d, list) else d.get("assets") or list(d.values())
    for a in assets:
        if not isinstance(a, dict):
            continue
        tech = str(a.get("tech") or a.get("type") or a.get("fuel") or "").lower()
        if tech.startswith("wind") and a.get("sced_units"):
            add(a.get("name") or "_".join(a["sced_units"][:1]), a.get("lat"), a.get("lon"),
                a["sced_units"])
    return out


def _fit_site_monthly(site: dict) -> pd.DataFrame | None:
    """Per-calendar-month best k (energy match) for one site. Rows: month, k, hours.

    Returns None (with a printed reason) when the site can't give a clean read:
    no actuals, too little overlap, or a resolved-fleet capacity that disagrees
    with observed peak generation (fleet mismatch → k would absorb the capacity
    error instead of the wind bias)."""
    actual = b.load_actuals(site["units"])
    if actual.empty:
        print(f"  skip {site['name']} ({site['region']}) — no actuals")
        return None
    # USWTDB's nearest-project fleet under-resolves multi-phase sites, so rescale
    # the model to the plant's observed nameplate (99.9th-pct generation). This
    # removes capacity error so the fitted k isolates the ERA5 WIND bias, not a
    # missing-turbines bias. Skip only if resolution is too partial to trust.
    fleet, _ = b.usw_fleet(site["lat"], site["lon"])
    model_cap = fleet.capacity_mw if fleet else float(site["cap"] or 0)
    peak = float(actual.quantile(0.999))
    if not model_cap or peak <= 0 or not (0.4 <= peak / model_cap <= 3.0):
        print(f"  skip {site['name']} ({site['region']}) — fleet unresolved "
              f"(model {model_cap:.0f} MW vs peak {peak:.0f} MW)")
        return None
    cap_scale = peak / model_cap
    ss = b.SiteSpec(site["name"], site["lat"], site["lon"], peak, site["units"])
    s, e = actual.index.min().strftime("%Y-%m-%d"), actual.index.max().strftime("%Y-%m-%d")
    # use_region_prior=False so ws_scale captures the FULL speed bias (the crude
    # energy region-multiplier would otherwise double-count it in production).
    grid = {k: b.model_hourly(ss, s, e, ws_scale=k, use_region_prior=False) * cap_scale
            for k in KS}
    df = pd.DataFrame({"a": actual}).join(pd.DataFrame({f"k{k}": v for k, v in grid.items()})).dropna()
    # Offline filter (same as scoring): drop actual≈0 while model expects wind.
    cap = peak
    df = df.loc[~((df["a"] < 0.01 * cap) & (df["k1.0"] > 0.10 * cap))]
    if len(df) < 24 * 30:
        return None
    rows = []
    for mo, g in df.groupby(df.index.month):
        if len(g) < 24 * 10 or g["a"].sum() <= 0:
            continue
        best = min(KS, key=lambda k: abs(g[f"k{k}"].sum() - g["a"].sum()))
        rows.append({"month": int(mo), "k": best, "hours": len(g)})
    return pd.DataFrame(rows)


def learn() -> dict:
    per_region_month = defaultdict(lambda: defaultdict(list))  # region -> month -> [(k,hours)]
    n_sites = defaultdict(int)
    for site in _wind_sites():
        try:
            fit = _fit_site_monthly(site)
        except Exception as e:  # noqa: BLE001 — network/data hiccup on one site
            print(f"  skip {site['name']} ({site['region']}) — error: {type(e).__name__}")
            fit = None
        if fit is None or fit.empty:
            continue
        # Drop months that saturate the grid ceiling — residual mismatch, not a
        # trustworthy wind-bias read.
        fit = fit[fit["k"] < max(KS)]
        if fit.empty:
            print(f"  skip {site['name']} ({site['region']}) — all months grid-saturated")
            continue
        n_sites[site["region"]] += 1
        print(f"  {site['name']:<22} {site['region']:<13} "
              f"months={len(fit)} mean_k={np.average(fit['k'], weights=fit['hours']):.3f}")
        for r in fit.itertuples():
            per_region_month[site["region"]][r.month].append((r.k, r.hours))

    region_month, region_overall = {}, {}
    for reg in REGIONS:
        mm = per_region_month.get(reg, {})
        if n_sites[reg] < MIN_SITES:
            if mm:
                print(f"  (region {reg}: only {n_sites[reg]} site(s) — below MIN_SITES, "
                      f"folded into fallback)")
            continue
        month_k, all_k, all_w = {}, [], []
        for mo in range(1, 13):
            vals = mm.get(mo, [])
            if vals:
                ks, ws = np.array([v[0] for v in vals]), np.array([v[1] for v in vals])
                month_k[str(mo)] = round(float(np.average(ks, weights=ws)), 3)
                all_k.extend(ks); all_w.extend(ws)
        if month_k:
            region_month[reg] = month_k
            region_overall[reg] = round(float(np.average(all_k, weights=all_w)), 3)

    # Global fallback = hours-weighted mean over every site that produced a fit.
    allk = [(k, h) for mm in per_region_month.values() for v in mm.values() for k, h in v]
    default = round(float(np.average([k for k, _ in allk], weights=[h for _, h in allk])), 3) \
        if allk else 1.0
    return {"ws_scale_default": default, "region_ws_scale": region_overall,
            "region_ws_scale_month": region_month, "n_sites": dict(n_sites)}


def main(dry: bool = False):
    print("Learning per-region monthly ws_scale from SCED actuals…")
    learned = learn()
    print("\nsites per region:", learned["n_sites"])
    print("ws_scale_default:", learned["ws_scale_default"])
    print("region_ws_scale (annual):", learned["region_ws_scale"])
    print("region_ws_scale_month:")
    for h, mm in learned["region_ws_scale_month"].items():
        print(f"  {h}: {mm}")
    if dry:
        print("\n--dry: not written")
        return
    tbl = json.loads(wc.CALIB_PATH.read_text()) if wc.CALIB_PATH.exists() else {}
    tbl.update(learned)
    wc.CALIB_PATH.write_text(json.dumps(tbl, indent=2))
    wc.load_table.cache_clear()
    print(f"\nWrote {wc.CALIB_PATH}")


if __name__ == "__main__":
    import sys
    main(dry="--dry" in sys.argv)
