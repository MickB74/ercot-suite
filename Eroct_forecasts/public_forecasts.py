"""Public forecast sources beyond the near gas strip — all free, all optional.

Everything here is auxiliary to the core ``gas_curve`` (which owns the realized
Henry Hub history + the NYMEX near strip). This module adds the *longer-dated*
and *cross-checking* public signals the forecast factors in:

  EIA STEO   — Henry Hub spot forecast (monthly, ~2 yrs). Gas mid-curve.
  EIA AEO    — Annual Energy Outlook Henry Hub (annual → 2050). Long-term gas
               anchor that the far tail mean-reverts toward (replaces a guess).
  EIA daily  — realized Henry Hub history → data-driven Monte Carlo gas vol.
  EIA STEO   — U.S. retail electricity price (¢/kWh → $/MWh). Cross-check line
               ONLY; never blended into the ERCOT P50 (it is not an ERCOT hub).
  ERCOT CDR  — Capacity, Demand & Reserves planning reserve margins (manual CSV;
               no free JSON API) → forward-scarcity tail boost on the heat rate.

Every fetch degrades gracefully: no key / offline / missing file → ``None`` (or
a documented fallback), so the engine always runs. Network results are cached
under ``GAS_DIR`` with the same staleness policy as the gas forward.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import gas_curve
import pf_paths

EIA_BASE = gas_curve.EIA_BASE

# --- EIA AEO (long-term Henry Hub) -----------------------------------------
# AEO vintage to pull and the Henry Hub spot-price series within it (nominal
# $/MMBtu). Overridable via config.json ("aeo_year", "aeo_scenario").
AEO_YEAR_DEFAULT = "2026"
AEO_HH_SERIES = "prce_hhp_NA_NA_ng_NA_usa_ndlrpmbtu"  # Henry Hub spot, nominal $/MMBtu
# AEO 2026 is an early release with no plain "Reference" case, so when no
# scenario is given we average the two macro-growth cases for a neutral path.
AEO_MACRO_CASES = ["lm2026", "hm2026"]  # Low / High Economic Growth
AEO_REF_CANDIDATES = ["ref2026", "aeo2026ref", "reference"]

# --- EIA STEO electricity (cross-check only) --------------------------------
# Retail price of electricity, U.S. average (¢/kWh). Industrial is the closest
# free proxy to a wholesale level; this is a comparison line, NOT a model input.
STEO_POWER_CANDIDATES = ["STEO.ESICUUS.M", "STEO.ESRCUUS.M"]

CACHE_DAYS = gas_curve.FORWARD_CACHE_DAYS


def _cfg(key: str, default):
    return pf_paths.load_config().get(key, default)


# ===========================================================================
# EIA STEO Henry Hub (gas mid-curve) — thin wrapper over the existing fetcher
# ===========================================================================
def eia_steo_gas(api_key: str | None = None) -> pd.DataFrame | None:
    """Monthly STEO Henry Hub spot forecast ($/MMBtu). df[month, gas] or None."""
    key = api_key or pf_paths.eia_api_key()
    if not key:
        return None
    return gas_curve._steo_forecast(key)


# ===========================================================================
# EIA AEO long-term Henry Hub  → far-tail anchor
# ===========================================================================
def _aeo_fetch(api_key: str, year: str, scenario: str) -> pd.DataFrame | None:
    """One AEO scenario's annual Henry Hub series. df[year(int), gas] or None."""
    try:
        import requests

        url = f"{EIA_BASE}/aeo/{year}/data"
        r = requests.get(url, params={
            "api_key": api_key, "data[0]": "value",
            "facets[scenario][]": scenario, "facets[seriesId][]": AEO_HH_SERIES,
            "sort[0][column]": "period", "sort[0][direction]": "asc", "length": 60,
        }, timeout=60)
        r.raise_for_status()
        data = r.json().get("response", {}).get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data)
        df["gas"] = pd.to_numeric(df["value"], errors="coerce")
        df["year"] = pd.to_numeric(df["period"], errors="coerce").astype("Int64")
        return df.dropna(subset=["gas", "year"])[["year", "gas"]].astype(
            {"year": int}).sort_values("year").reset_index(drop=True)
    except Exception:
        return None


def refresh_aeo(api_key: str | None = None) -> pd.DataFrame:
    """Fetch + cache the AEO long-term Henry Hub path. Returns df[year, gas].

    Scenario precedence: config ``aeo_scenario`` → a real "reference" case if the
    vintage has one → the average of the macro-growth cases (neutral path).
    """
    key = api_key or pf_paths.eia_api_key()
    if not key:
        raise RuntimeError("No EIA API key. Set eia_api_key in config.json.")
    year = str(_cfg("aeo_year", AEO_YEAR_DEFAULT))
    scenario = _cfg("aeo_scenario", None)

    if scenario:
        df = _aeo_fetch(key, year, scenario)
        label = scenario
    else:
        df = None
        for cand in AEO_REF_CANDIDATES:
            df = _aeo_fetch(key, year, cand)
            if df is not None:
                label = cand
                break
        if df is None:  # no reference case (e.g. AEO 2026) → average macro cases
            parts = [d for d in (_aeo_fetch(key, year, c) for c in AEO_MACRO_CASES)
                     if d is not None]
            if not parts:
                raise RuntimeError(f"EIA AEO {year} returned no Henry Hub data.")
            df = (pd.concat(parts).groupby("year", as_index=False)["gas"].mean())
            label = "macro-avg(" + "+".join(AEO_MACRO_CASES) + ")"

    df = df.sort_values("year").reset_index(drop=True)
    df.attrs["scenario"] = label
    df.attrs["aeo_year"] = year
    pf_paths.ensure_dirs()
    out = df.copy()
    out["_scenario"] = label
    out["_aeo_year"] = year
    out.to_parquet(pf_paths.AEO_GAS_PARQUET, index=False)
    return df


def _cached_aeo(max_age_days: int = CACHE_DAYS * 60) -> pd.DataFrame | None:
    """Cached AEO path. AEO updates yearly, so the cache is allowed to be stale."""
    p = pf_paths.AEO_GAS_PARQUET
    if not p.exists():
        return None
    age = (pd.Timestamp.now() - pd.Timestamp(p.stat().st_mtime, unit="s")).days
    if age > max_age_days:
        return None
    df = pd.read_parquet(p)
    if "_scenario" in df.columns:
        df.attrs["scenario"] = str(df["_scenario"].iloc[0])
        df.attrs["aeo_year"] = str(df["_aeo_year"].iloc[0])
        df = df.drop(columns=[c for c in ("_scenario", "_aeo_year") if c in df.columns])
    return df


def aeo_annual(api_key: str | None = None, *, auto_fetch: bool = True) -> pd.DataFrame | None:
    """AEO annual Henry Hub path (cached → fetched). df[year, gas] or None."""
    df = _cached_aeo()
    if df is not None and not df.empty:
        return df
    if auto_fetch and pf_paths.eia_api_key():
        try:
            return refresh_aeo(api_key)
        except Exception:
            return None
    return None


def aeo_anchor_for(month: pd.Timestamp, api_key: str | None = None) -> tuple[float, str] | None:
    """De-seasonalized AEO Henry Hub level for a given month. (level, label) or None.

    The far tail of the gas curve mean-reverts toward this instead of a fixed
    constant, so the long-run level tracks EIA's own published outlook and even
    drifts year-over-year along the AEO path.
    """
    df = aeo_annual(api_key)
    if df is None or df.empty:
        return None
    yr = int(pd.Timestamp(month).year)
    years = df["year"].to_numpy()
    gas = df["gas"].to_numpy()
    level = float(np.interp(yr, years, gas))  # flat-extrapolates beyond the path
    label = f"AEO {df.attrs.get('aeo_year', '')} [{df.attrs.get('scenario', 'ref')}]"
    return level, label


# ===========================================================================
# Data-driven gas volatility  → Monte Carlo default
# ===========================================================================
def realized_gas_vol(window_years: float = 5.0, *, floor: float = 0.20,
                     cap: float = 1.20, default: float = 0.5) -> float:
    """Annualized log-volatility of Henry Hub from cached EIA daily history.

    Computed from monthly-average returns over the trailing ``window_years`` so
    it reflects forward-price-relevant variability without intraday noise. Falls
    back to ``default`` when no daily cache is present (offline / no key yet).
    """
    daily = gas_curve.daily_history()
    if daily is None or daily.empty:
        return default
    m = (daily.set_index("date")["henry_hub"].resample("MS").mean().dropna())
    if window_years:
        cutoff = m.index.max() - pd.DateOffset(years=int(window_years))
        m = m[m.index >= cutoff]
    rets = np.log(m / m.shift(1)).dropna()
    if len(rets) < 6:
        return default
    monthly_sigma = float(rets.std(ddof=1))
    annual = monthly_sigma * math.sqrt(12.0)
    return float(min(max(annual, floor), cap))


# ===========================================================================
# EIA STEO electricity — cross-check line ONLY (never blended)
# ===========================================================================
def eia_steo_power(api_key: str | None = None, *, auto_fetch: bool = True) -> pd.DataFrame | None:
    """Monthly U.S. retail electricity price as $/MWh. df[month, price] or None.

    Cross-check overlay only — it is a U.S. retail average, not an ERCOT hub, so
    it is displayed alongside the forecast but never enters the model.
    """
    cached = _cached_steo_power()
    if cached is not None:
        return cached
    if not auto_fetch:
        return None
    key = api_key or pf_paths.eia_api_key()
    if not key:
        return None
    for sid in STEO_POWER_CANDIDATES:
        d = gas_curve._eia_series(sid, key, length=60)
        if d is not None and not d.empty:
            d = d.rename(columns={"period": "month", "value": "price"})
            d["month"] = pd.to_datetime(d["month"])
            d["price"] = d["price"] * 10.0  # ¢/kWh → $/MWh
            d["_series"] = sid
            out = d[["month", "price", "_series"]].sort_values("month").reset_index(drop=True)
            pf_paths.ensure_dirs()
            out.to_parquet(pf_paths.STEO_POWER_PARQUET, index=False)
            return out
    return None


def _cached_steo_power(max_age_days: int = CACHE_DAYS) -> pd.DataFrame | None:
    p = pf_paths.STEO_POWER_PARQUET
    if not p.exists():
        return None
    age = (pd.Timestamp.now() - pd.Timestamp(p.stat().st_mtime, unit="s")).days
    if age > max_age_days:
        return None
    df = pd.read_parquet(p)
    df["month"] = pd.to_datetime(df["month"])
    return df


# ===========================================================================
# ERCOT CDR reserve margins  → forward-scarcity tail boost
# ===========================================================================
def ercot_reserve_margin() -> pd.DataFrame | None:
    """ERCOT planning reserve margins by year. df[year(int), reserve_margin_pct].

    Sourced from a manually maintained CSV (ERCOT publishes the CDR as XLSX with
    no free JSON API). Columns required: ``year``, ``reserve_margin_pct``.
    Returns None when the file is absent or empty.
    """
    p = pf_paths.ERCOT_CDR_CSV
    if not p.exists():
        return None
    df = pd.read_csv(p, comment="#")
    if df.empty or "year" not in df.columns or "reserve_margin_pct" not in df.columns:
        return None
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["reserve_margin_pct"] = pd.to_numeric(df["reserve_margin_pct"], errors="coerce")
    df = df.dropna(subset=["year", "reserve_margin_pct"]).astype({"year": int})
    return df[["year", "reserve_margin_pct"]].sort_values("year").reset_index(drop=True) \
        if not df.empty else None


def scarcity_multiplier(reserve_margin_pct: float | None, *, target: float = 15.0,
                        max_boost: float = 0.6, knee: float = 10.0) -> float:
    """Heat-rate upper-tail boost from a forward year's reserve margin (≥ 1.0).

    At/above ``target`` margin the system is comfortable → 1.0 (no change). As
    the margin falls toward ``knee`` the boost ramps linearly up to ``1+max_boost``;
    below ``knee`` it saturates. This widens scarcity risk in tight forward years
    without touching the central case (the median is held fixed in scenarios).
    """
    if reserve_margin_pct is None or not np.isfinite(reserve_margin_pct):
        return 1.0
    rm = float(reserve_margin_pct)
    if rm >= target:
        return 1.0
    frac = (target - rm) / max(target - knee, 1e-6)
    frac = min(max(frac, 0.0), 1.0)
    return 1.0 + max_boost * frac


def scarcity_by_month(months, *, on: bool = True) -> tuple[dict, dict]:
    """Per-month tail-boost map keyed off the forward year's reserve margin.

    Returns ({Timestamp(month): boost}, meta). When ``on`` is False or no CDR
    data exists, every boost is 1.0 (no-op) and meta records why.
    """
    if not on:
        return ({pd.Timestamp(m): 1.0 for m in months}, {"scarcity": False})
    cdr = ercot_reserve_margin()
    if cdr is None or cdr.empty:
        return ({pd.Timestamp(m): 1.0 for m in months},
                {"scarcity": True, "cdr": "none (no ercot_cdr.csv)"})
    rm_by_year = dict(zip(cdr["year"], cdr["reserve_margin_pct"]))
    out = {}
    for m in months:
        m = pd.Timestamp(m)
        rm = rm_by_year.get(int(m.year))
        out[m] = scarcity_multiplier(rm)
    meta = {"scarcity": True,
            "cdr_years": f"{int(cdr['year'].min())}–{int(cdr['year'].max())}",
            "cdr_rows": int(len(cdr))}
    return out, meta
