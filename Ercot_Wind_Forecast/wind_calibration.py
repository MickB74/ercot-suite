"""Calibration layer — turn a physics forecast into a *calibrated* forecast.

Two jobs:

  1. **Priors** (``wind_calibration.json``) — region/ERCOT-hub shear exponents and
     bias multipliers, a Texas monthly capacity-factor shape, and SCED-derived
     hour-of-day / month-hour residual multipliers learned from real ERCOT
     generation. These nudge the raw physics toward what the fleet actually does.

  2. **Live calibration** (:func:`calibrate_against_actuals`) — given the model's
     own output and the project's *actual* metered/SCED generation over an
     overlapping window, fit a bias factor (and optional monthly shape) so the
     forecast is re-centred on this specific site. This is the "use the truth to
     correct the model" loop that makes a single-site forecast accurate.

Self-contained: numpy / pandas, reads the bundled JSON. Region geography mirrors
the five ERCOT trading hubs.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CALIB_PATH = HERE / "wind_calibration.json"

HUB_COORDS = {
    "NORTH": (32.3865, -96.8475), "SOUTH": (26.9070, -99.2715),
    "WEST": (32.4518, -100.5371), "HOUSTON": (29.3013, -94.7977),
    "PAN": (35.2220, -101.8313),
}
HUB_ALIASES = {f"HB_{h}": h for h in HUB_COORDS} | {h: h for h in HUB_COORDS}

DEFAULT_HUB_SHEAR_ALPHA = {"NORTH": 0.34, "SOUTH": 0.33, "WEST": 0.31, "HOUSTON": 0.24, "PAN": 0.32}
DEFAULT_HUB_MULTIPLIER = {"NORTH": 1.10, "SOUTH": 1.07, "WEST": 1.02, "HOUSTON": 1.03, "PAN": 1.04}


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_table(path: str | None = None) -> dict:
    p = Path(path) if path else CALIB_PATH
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
    return {}


def normalize_hub(name) -> str | None:
    if name is None:
        return None
    return HUB_ALIASES.get(str(name).strip().upper())


def infer_hub(lat, lon) -> str | None:
    lat, lon = _as_float(lat), _as_float(lon)
    if lat is None or lon is None:
        return None
    return min(HUB_COORDS, key=lambda h: (lat - HUB_COORDS[h][0]) ** 2 + (lon - HUB_COORDS[h][1]) ** 2)


# ---------------------------------------------------------------------------
# Priors
# ---------------------------------------------------------------------------

def hub_shear_alpha(lat=None, lon=None, hub_name=None, table=None) -> tuple[float, str]:
    """Site shear exponent prior (used only when measured shear is unreliable)."""
    table = table or load_table()
    hub = normalize_hub(hub_name) or infer_hub(lat, lon)
    shear_map = table.get("hub_shear_alpha", {}) if isinstance(table, dict) else {}
    if hub and hub in shear_map:
        return float(shear_map[hub]), f"hub:{hub}"
    if hub and hub in DEFAULT_HUB_SHEAR_ALPHA:
        return DEFAULT_HUB_SHEAR_ALPHA[hub], f"default:{hub}"
    lon_f = _as_float(lon)
    if lon_f is not None:
        return (0.22, "coastal") if lon_f > -96.0 else (0.32, "inland")
    return 0.20, "global"


def region_bias_multiplier(lat=None, lon=None, hub_name=None, table=None) -> tuple[float, str]:
    """Region/hub bias multiplier prior (modeled vs. realized fleet)."""
    table = table or load_table()
    hub = normalize_hub(hub_name) or infer_hub(lat, lon)
    hub_map = table.get("hub_multiplier", {}) if isinstance(table, dict) else {}
    if hub and hub in hub_map:
        return _clamp(float(hub_map[hub]), 0.85, 1.25), f"hub:{hub}"
    if hub and hub in DEFAULT_HUB_MULTIPLIER:
        return DEFAULT_HUB_MULTIPLIER[hub], f"default:{hub}"
    return 1.0, "none"


def monthly_cf_shape(lat=None, lon=None, hub_name=None, table=None) -> dict | None:
    """Texas monthly capacity-factor target for the site's hub, if available."""
    table = table or load_table()
    hub = normalize_hub(hub_name) or infer_hub(lat, lon)
    cf = table.get("monthly_cf_target", {}) if isinstance(table, dict) else {}
    return cf.get(hub) if hub else None


def region_for(lat=None, lon=None, hub_name=None) -> str | None:
    """ERCOT wind region for a site, splitting South into coast vs inland.

    Coastal wind (sea-breeze / low-level jet timing) behaves very differently
    from RGV/inland, so they carry separate ws_scale priors. Coordinates are
    authoritative (they define the wind regime and match how the prior was
    learned); the trading-hub label is only a fallback when coords are absent."""
    hub = infer_hub(lat, lon) or normalize_hub(hub_name)
    if hub == "SOUTH":
        lon_f = _as_float(lon)
        if lon_f is not None:
            return "SOUTH_COAST" if lon_f > -98.2 else "SOUTH_INLAND"
    return hub


def ws_scale_for(lat=None, lon=None, hub_name=None, table=None):
    """Learned hub-height wind-speed correction for a site → {month: factor}.

    Reads the region priors written by ``build_ws_scale.py``. Falls back to the
    region annual scalar, then the global default, then 1.0 (no correction).
    Apply by passing to ``wind_power.run_wind(ws_scale=...)``."""
    table = table or load_table()
    if not isinstance(table, dict):
        return 1.0
    region = region_for(lat, lon, hub_name)
    month = table.get("region_ws_scale_month", {}) or {}
    if region and region in month:
        return {int(m): float(v) for m, v in month[region].items()}
    annual = table.get("region_ws_scale", {}) or {}
    if region and region in annual:
        return float(annual[region])
    return float(table.get("ws_scale_default", 1.0) or 1.0)


def apply_sced_bias(series: pd.Series, hub_name=None, lat=None, lon=None, table=None) -> pd.Series:
    """Apply SCED-learned month-hour (or hour-of-day) residual multipliers."""
    table = table or load_table()
    hub = normalize_hub(hub_name) or infer_hub(lat, lon)
    if not hub:
        return series
    mh = (table.get("sced_bias_month_hour_multiplier", {}) or {}).get(hub, {})
    hr = (table.get("sced_bias_hourly_multiplier", {}) or {}).get(hub, {})
    if not mh and not hr:
        return series
    mult = np.ones(len(series))
    for i, (mo, h) in enumerate(zip(series.index.month, series.index.hour)):
        v = mh.get(f"{int(mo)}-{int(h)}") if mh else None
        if v is None and hr:
            v = hr.get(str(int(h)))
        if v is not None:
            mult[i] = _clamp(float(v), 0.70, 1.30)
    return pd.Series(series.to_numpy() * mult, index=series.index, name=series.name)


def apply_region_priors(series: pd.Series, capacity_mw: float, lat=None, lon=None,
                        hub_name=None, use_bias=True, use_sced=True, table=None) -> pd.Series:
    """Apply region bias + SCED priors to a net-MW series (no actuals needed)."""
    table = table or load_table()
    out = series.astype(float).clip(lower=0.0)
    if use_bias:
        mult, _ = region_bias_multiplier(lat, lon, hub_name, table)
        out = out * mult
    if use_sced:
        out = apply_sced_bias(out, hub_name=hub_name, lat=lat, lon=lon, table=table)
    if capacity_mw and capacity_mw > 0:
        out = out.clip(lower=0.0, upper=capacity_mw)
    return out


# ---------------------------------------------------------------------------
# Live calibration against actual generation
# ---------------------------------------------------------------------------

def calibrate_against_actuals(modeled: pd.Series, actual: pd.Series,
                              capacity_mw: float | None = None,
                              monthly: bool = True,
                              offline_threshold_mw: float | None = None,
                              clamp: tuple = (0.5, 1.8)) -> dict:
    """Fit a bias correction from overlapping modeled vs. actual generation.

    Aligns the two series on their common timestamps, filters likely
    offline/curtailed intervals (actual≈0 while modeled is high), then computes:

      * ``overall_factor`` — energy ratio Σactual / Σmodeled (the headline bias).
      * ``monthly_factors`` — per-calendar-month energy ratios (seasonal shape).
      * fit diagnostics — correlation, MBE, RMSE, n.

    Apply the result with :func:`apply_calibration`. This is what turns a
    region-level forecast into a site-tuned one.
    """
    mod = pd.to_numeric(modeled, errors="coerce")
    act = pd.to_numeric(actual, errors="coerce")
    # Align timezones before the index join: the modeled series is tz-aware
    # Central, but uploaded actuals may be naive or in another zone. Without this
    # the join silently yields little/no overlap (and DST hours misalign).
    import tzutil
    if isinstance(mod.index, pd.DatetimeIndex):
        mod.index = tzutil.localize_central(mod.index)
    if isinstance(act.index, pd.DatetimeIndex):
        act.index = tzutil.localize_central(act.index)
    df = pd.DataFrame({"mod": mod, "act": act}).dropna()
    if df.empty:
        return {"overall_factor": 1.0, "monthly_factors": {}, "n": 0, "ok": False}

    if offline_threshold_mw is None and capacity_mw:
        offline_threshold_mw = max(2.0, min(20.0, capacity_mw * 0.05))
    if offline_threshold_mw:
        keep = ~((df["act"] < 0.5) & (df["mod"] > offline_threshold_mw))
        keep &= df["mod"] > max(1.0, (capacity_mw or 0) * 0.02)
        df = df.loc[keep]
    if len(df) < 24:
        return {"overall_factor": 1.0, "monthly_factors": {}, "n": int(len(df)), "ok": False}

    lo, hi = clamp
    overall = _clamp(df["act"].sum() / df["mod"].sum() if df["mod"].sum() else 1.0, lo, hi)

    monthly_factors = {}
    if monthly:
        g = df.groupby(df.index.month)
        for mo, chunk in g:
            if chunk["mod"].sum() > 0 and len(chunk) >= 24:
                monthly_factors[int(mo)] = round(_clamp(chunk["act"].sum() / chunk["mod"].sum(), lo, hi), 4)

    corr = float(df["mod"].corr(df["act"])) if df["mod"].std() and df["act"].std() else float("nan")
    mbe = float((df["mod"] - df["act"]).mean())
    rmse = float(np.sqrt(((df["mod"] - df["act"]) ** 2).mean()))
    return {
        "overall_factor": round(overall, 4),
        "monthly_factors": monthly_factors,
        "correlation": round(corr, 4) if corr == corr else None,
        "mbe_mw": round(mbe, 3),
        "rmse_mw": round(rmse, 3),
        "n": int(len(df)),
        "ok": True,
    }


def apply_calibration(series: pd.Series, calib: dict, capacity_mw: float | None = None) -> pd.Series:
    """Apply a :func:`calibrate_against_actuals` result to a forecast series."""
    if not calib or not calib.get("ok"):
        return series
    out = series.astype(float).copy()
    monthly = calib.get("monthly_factors") or {}
    if monthly:
        for mo, f in monthly.items():
            mask = out.index.month == int(mo)
            out.loc[mask] = out.loc[mask] * float(f)
        # Months without a fitted factor fall back to the overall factor.
        covered = set(int(m) for m in monthly)
        miss = ~out.index.month.isin(covered)
        if miss.any():
            out.loc[miss] = out.loc[miss] * float(calib.get("overall_factor", 1.0))
    else:
        out = out * float(calib.get("overall_factor", 1.0))
    if capacity_mw and capacity_mw > 0:
        out = out.clip(lower=0.0, upper=capacity_mw)
    return out
