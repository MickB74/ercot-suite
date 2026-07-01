"""EIA-923 long-history anchor for wind/solar typical-year profiles.

The 60-Day SCED Disclosure ERCOT retains only goes back ~2 years, and at some
sites (notably the Rio Grande Valley) the recent window sits in a low-bias zone of
the ERA5 reanalysis, so a recent-only calibration is unstable and over-corrects.
EIA-923 monthly net generation goes back to each plant's COD, giving a much longer,
authoritative truth signal. This module turns that history into a cached *anchor*:

  * ``overall_factor``      — EIA energy / raw-ERA5-model energy over the full span.
  * ``ws_speed_correction`` — the multiplicative ERA5 hub-wind bias behind it
    (more transportable to a forecast than a flat energy factor).
  * ``monthly_cf_p10/p50/p90`` — per-calendar-month capacity-factor distribution
    across years (the EIA-based typical year + interannual bands).
  * ``annual_energy_p10/p50/p90`` — typical-year full-capacity energy bands.

Consumers (``plant_value``, ``near_term_bill``) call :func:`load` and, when an
anchor exists for the node, prefer it over the partial/noisy SCED anchor. Nodes
with no anchor file are completely unaffected, so this is opt-in per site.

Build one with :func:`build` (or run this module as a script for the bundled
Mirasole spec). Caches one JSON per resource node under ``data/eia_anchor/``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ercot_core import paths

ANCHOR_DIR = paths.DATA / "eia_anchor"


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #

@dataclass
class AnchorSpec:
    """Everything needed to build a node's EIA anchor."""

    node: str
    eia_plant_ids: list[int]
    lat: float
    lon: float
    # (capacity_mw, online "YYYY-MM-DD") per phase — handles staged build-out.
    phases: list[tuple[float, str]]
    hub_height_m: float = 95.0
    rotor_m: float = 110.0
    curve_key: str = "GENERIC_IEC2"
    start_year: int = 2016
    label: str = ""

    @property
    def capacity_full(self) -> float:
        return sum(c for c, _ in self.phases)

    def online_capacity(self, ts: pd.Timestamp) -> float:
        cap = 0.0
        for c, on in self.phases:
            if ts >= pd.Timestamp(on):
                cap += c
        return cap or self.phases[0][0]


# Bundled specs by node. Add a site here, then ``build`` it.
SPECS: dict[str, AnchorSpec] = {
    "MIRASOLE_GEN": AnchorSpec(
        node="MIRASOLE_GEN", eia_plant_ids=[57617, 62618],
        lat=26.465556, lon=-98.411111,
        phases=[(250.0, "2016-12-01"), (50.4, "2020-02-01")],
        hub_height_m=95.0, rotor_m=110.0,
        label="Hidalgo Mirasole Wind (Los Mirasoles)"),
}


def spec_from_eia(node: str, eia_plant_ids: list[int], *, label: str = "",
                  hub_height_m: float = 95.0, rotor_m: float = 110.0,
                  curve_key: str = "GENERIC_IEC2", start_year: int | None = None,
                  e860_year: int = 2024) -> AnchorSpec:
    """Build an :class:`AnchorSpec` from EIA-860, auto-detecting build phases.

    Each distinct generator online month at the plant(s) becomes a phase
    (capacity = summed nameplate of generators online that month), so staged
    build-outs are handled without hand-coding. Coordinates and the start year
    (= COD year) are read straight from EIA-860.
    """
    e = pd.read_parquet(paths.EIA_DIR / f"eia860_ercot_{e860_year}.parquet")
    sub = e[e["plant_id"].isin(eia_plant_ids) & (e["prime_mover"] == "WT")].copy()
    if sub.empty:
        raise RuntimeError(f"No EIA-860 wind generators for {eia_plant_ids}.")
    sub = sub.dropna(subset=["online_date"])
    phases_g = sub.groupby(sub["online_date"].dt.strftime("%Y-%m-01"))["nameplate_mw"].sum()
    phases = [(round(float(c), 1), on) for on, c in sorted(phases_g.items())]
    cod_year = int(min(p[1] for p in phases)[:4])
    return AnchorSpec(
        node=node, eia_plant_ids=eia_plant_ids,
        lat=round(float(sub["latitude"].iloc[0]), 4),
        lon=round(float(sub["longitude"].iloc[0]), 4),
        phases=phases, hub_height_m=hub_height_m, rotor_m=rotor_m,
        curve_key=curve_key, start_year=start_year or cod_year,
        label=label or str(sub["plant_name"].iloc[0]))


@dataclass
class SolarSpec:
    """Everything needed to build a solar plant's EIA anchor (PVWatts/ERA5)."""

    node: str
    eia_plant_ids: list[int]
    lat: float
    lon: float
    phases: list[tuple[float, str]]      # (AC MW, online "YYYY-MM-DD")
    dc_ac_ratio: float = 1.3
    array_type: str = "1-Axis Tracker"   # ERCOT utility-solar default
    tilt_deg: float = 25.0               # fixed-tilt only; trackers ignore
    gcr: float = 0.35
    start_year: int = 2016
    label: str = ""
    tech: str = "solar"

    @property
    def capacity_full(self) -> float:
        return sum(c for c, _ in self.phases)

    def online_capacity(self, ts: pd.Timestamp) -> float:
        cap = sum(c for c, on in self.phases if ts >= pd.Timestamp(on))
        return cap or self.phases[0][0]


def _solar_config(eia_plant_ids: list[int], e860_year: int = 2024) -> dict:
    """Per-plant array_type / dc_ac_ratio / tilt from EIA-860 Schedule 3.3.

    Capacity-weighted across the plant's generators; falls back to the ERCOT
    utility-solar defaults (single-axis tracker, 1.3 DC:AC) when not listed.
    """
    cfg = {"array_type": "1-Axis Tracker", "dc_ac_ratio": 1.3, "tilt_deg": 25.0}
    p = paths.EIA_DIR / f"eia860_solar_config_{e860_year}.parquet"
    if not p.exists():
        return cfg
    try:
        g = pd.read_parquet(p)
        sub = g[g.index.isin(eia_plant_ids)]
        if sub.empty:
            return cfg
        ac = sub["ac"].sum()
        trk = (sub["dc"] * (sub["array"] == "1-Axis Tracker")).sum() / sub["dc"].sum()
        cfg["array_type"] = "1-Axis Tracker" if trk >= 0.5 else "Fixed - Open Rack"
        cfg["dc_ac_ratio"] = round(float((sub["dc"].sum() / ac) if ac else 1.3), 3)
        cfg["dc_ac_ratio"] = min(1.7, max(1.0, cfg["dc_ac_ratio"]))
        cfg["tilt_deg"] = round(float((sub["tilt"] * sub["ac"]).sum() / ac), 1) if ac else 25.0
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _first_gen_month(eia_plant_ids: list[int]) -> str | None:
    """First EIA-923 month with positive net generation → "YYYY-MM-01", or None."""
    import sys
    sys.path.insert(0, str(paths.ROOT / "datasets" / "eia923"))
    import eia923  # noqa: E402
    df = eia923.load_region("ercot", years=list(range(2016, 2027)))
    sub = df[df["plant_id"].isin(eia_plant_ids)]
    sub = sub[sub["netgen_mwh"] > 0]
    if sub.empty:
        return None
    ts = pd.to_datetime(dict(year=sub["year"], month=sub["month"], day=1)).min()
    return ts.strftime("%Y-%m-01")


def spec_from_eia_solar(node: str, eia_plant_ids: list[int], *, label: str = "",
                        dc_ac_ratio: float | None = None, array_type: str | None = None,
                        start_year: int | None = None, e860_year: int = 2024) -> SolarSpec:
    """Build a :class:`SolarSpec` from EIA-860 (PV generators), auto-phasing.

    Robust to new plants: falls back across EIA-860 vintages to find the plant,
    and when its 860 record lacks an online date (brand-new), infers COD from the
    first EIA-923 generating month. Tracking / tilt / DC:AC default to the plant's
    actual Schedule 3.3 config, overridable via the keyword args.
    """
    sub = None
    for yr in [e860_year, 2025, 2024, 2023]:
        p = paths.EIA_DIR / f"eia860_ercot_{yr}.parquet"
        if not p.exists():
            continue
        s = pd.read_parquet(p)
        s = s[s["plant_id"].isin(eia_plant_ids) & (s["prime_mover"] == "PV")].copy()
        if not s.empty:
            sub, e860_year = s, yr
            break
    if sub is None or sub.empty:
        raise RuntimeError(f"No EIA-860 PV generators for {eia_plant_ids}.")
    cfg = _solar_config(eia_plant_ids, e860_year)
    ratio = dc_ac_ratio or cfg["dc_ac_ratio"]
    # EIA nameplate is DC; convert to AC for the capacity-factor basis.
    dated = sub.dropna(subset=["online_date"])
    if not dated.empty:
        phases_g = (dated.groupby(dated["online_date"].dt.strftime("%Y-%m-01"))["nameplate_mw"].sum()
                    / ratio)
        phases = [(round(float(c), 1), on) for on, c in sorted(phases_g.items())]
    else:
        # brand-new plant, no 860 online date — infer COD from EIA-923.
        cod = _first_gen_month(eia_plant_ids)
        if not cod:
            raise RuntimeError(f"No online date or EIA-923 generation for {eia_plant_ids}.")
        phases = [(round(float(sub["nameplate_mw"].sum()) / ratio, 1), cod)]
    cod_year = int(min(p[1] for p in phases)[:4])
    return SolarSpec(
        node=node, eia_plant_ids=eia_plant_ids,
        lat=round(float(sub["latitude"].iloc[0]), 4),
        lon=round(float(sub["longitude"].iloc[0]), 4),
        phases=phases, dc_ac_ratio=ratio,
        array_type=array_type or cfg["array_type"], tilt_deg=cfg["tilt_deg"],
        start_year=start_year or cod_year, label=label or str(sub["plant_name"].iloc[0]))


def anchor_path(node: str) -> Path:
    return ANCHOR_DIR / f"{node}.json"


def load(node: str) -> dict | None:
    """Return the cached anchor dict for ``node``, or None if not built."""
    p = anchor_path(node)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001 — a corrupt cache should not break callers
        return None


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def _eia_monthly(spec: AnchorSpec, end_year: int) -> pd.Series:
    import sys
    sys.path.insert(0, str(paths.ROOT / "datasets" / "eia923"))
    import eia923  # noqa: E402
    df = eia923.load_region("ercot", years=list(range(spec.start_year, end_year + 1)))
    if df.empty:
        raise RuntimeError("No EIA-923 cache — build the yearly parquets first.")
    sub = df[df["plant_id"].isin(spec.eia_plant_ids)].copy()
    if sub.empty:
        raise RuntimeError(f"EIA plant ids {spec.eia_plant_ids} not in cache.")
    sub["ts"] = pd.to_datetime(dict(year=sub["year"], month=sub["month"], day=1))
    s = sub.groupby("ts")["netgen_mwh"].sum().sort_index()
    return s[s > 0]


def _retry(fn, tries: int = 4, base: float = 3.0):
    """Call ``fn`` with exponential backoff — Open-Meteo throttles bulk fetches."""
    import time
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            last = ex
            if i < tries - 1:
                time.sleep(base * (2 ** i))   # 3s, 6s, 12s
    raise last


def _fetch_weather(spec: AnchorSpec, end_year: int, era5_end: str) -> list:
    """Fetch hourly ERA5 once per year (reused across wind-speed trials)."""
    import sys
    sys.path.insert(0, str(paths.ROOT / ".." / "Ercot_Wind_Forecast"))
    import wind_power as wp  # noqa: E402
    out = []
    for yr in range(spec.start_year, end_year + 1):
        s = f"{yr}-01-01"
        e = f"{yr}-12-31" if yr < end_year else era5_end
        try:
            out.append(_retry(lambda: wp.fetch_weather_era5(
                spec.lat, spec.lon, s, e, tz="US/Central")))
        except Exception:  # noqa: BLE001 — skip a year that fails all retries
            continue
    if not out:
        raise RuntimeError("ERA5 fetch failed for all years.")
    return out


def _model_monthly(weathers: list, spec: AnchorSpec, ws_scale: float = 1.0) -> pd.Series:
    import sys
    sys.path.insert(0, str(paths.ROOT / ".." / "Ercot_Wind_Forecast"))
    import wind_power as wp  # noqa: E402

    fleet = wp.FleetConfig(segments=[wp.TurbineSpec(
        count=150, rated_kw=spec.capacity_full * 1000 / 150,
        hub_height_m=spec.hub_height_m, rotor_m=spec.rotor_m,
        curve_key=spec.curve_key, label="seg")])
    frames = []
    for wr in weathers:
        if ws_scale != 1.0:
            d = wr.data.copy()
            d["ws10"] *= ws_scale
            d["ws100"] *= ws_scale
            wr = wp.WeatherResult(data=d, metadata=wr.metadata, label=wr.label,
                                  latitude=wr.latitude, longitude=wr.longitude,
                                  sources=wr.sources)
        frames.append(wp.run_wind(wr, fleet)[["net_mw"]])
    hourly = pd.concat(frames).sort_index()
    hourly = hourly[~hourly.index.duplicated(keep="first")]
    m = hourly["net_mw"]
    m.index = m.index.tz_localize(None) if m.index.tz is not None else m.index
    monthly_full = m.resample("MS").sum()
    return monthly_full * monthly_full.index.map(
        lambda ts: spec.online_capacity(ts) / spec.capacity_full)


def _solve_ws(weathers: list, spec: AnchorSpec, actual) -> float:
    target = actual.sum()
    lo, hi, k = 1.0, 1.8, 1.3
    for _ in range(16):
        k = (lo + hi) / 2
        mod = _model_monthly(weathers, spec, ws_scale=k)
        common = mod.index.intersection(actual.index)
        if mod.loc[common].sum() < target:
            lo = k
        else:
            hi = k
    return round(k, 4)


def build(spec: AnchorSpec, end_year: int = 2026, era5_end: str = "2026-06-15",
          log=print) -> dict:
    """Compute and cache the EIA anchor for ``spec``. Returns the anchor dict."""
    log(f"[eia_anchor] {spec.node}: loading EIA monthly …")
    actual = _eia_monthly(spec, end_year)
    log(f"[eia_anchor] {len(actual)} months {actual.index.min():%Y-%m}→{actual.index.max():%Y-%m}")

    weathers = _fetch_weather(spec, end_year, era5_end)
    modeled = _model_monthly(weathers, spec)
    overall = float(actual.reindex(modeled.index).dropna().sum()
                    / modeled.reindex(actual.index).dropna().sum())
    ws_k = _solve_ws(weathers, spec, actual)

    # Per-calendar-month CF distribution across years (the typical year + bands).
    hours = actual.index.to_series().dt.daysinmonth.values * 24.0
    cap = actual.index.map(spec.online_capacity).astype(float)
    cf = pd.Series(actual.values / (cap * hours), index=actual.index)
    by_mo = cf.groupby(cf.index.month)
    p = lambda q: {int(mo): round(float(np.percentile(g, q)), 4) for mo, g in by_mo}

    # Typical-year annual energy bands at full capacity (scale each calendar
    # month's CF percentile by full-cap hours, then sum the 12 months).
    full_hours = {mo: (pd.Timestamp(2001, mo, 1).daysinmonth) * 24.0 for mo in range(1, 13)}
    def annual(qfun):
        return round(sum(qfun[mo] * spec.capacity_full * full_hours[mo]
                         for mo in range(1, 13) if mo in qfun), 0)
    cf_p10, cf_p50, cf_p90 = p(10), p(50), p(90)

    out = {
        "node": spec.node, "label": spec.label,
        "eia_plant_ids": spec.eia_plant_ids,
        "capacity_full_mw": spec.capacity_full,
        "lat": spec.lat, "lon": spec.lon,
        "span": f"{actual.index.min():%Y-%m} → {actual.index.max():%Y-%m}",
        "n_months": int(len(actual)),
        "overall_factor": round(overall, 4),
        "ws_speed_correction": ws_k,
        "mean_cf": round(float(cf.mean()), 4),
        "monthly_cf_p10": cf_p10, "monthly_cf_p50": cf_p50, "monthly_cf_p90": cf_p90,
        "annual_energy_p10_mwh": annual(cf_p10),
        "annual_energy_p50_mwh": annual(cf_p50),
        "annual_energy_p90_mwh": annual(cf_p90),
        "source": "EIA-923 monthly + ERA5/physics, capacity-factor space",
    }
    ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    anchor_path(spec.node).write_text(json.dumps(out, indent=2))
    log(f"[eia_anchor] {spec.node}: factor×{out['overall_factor']} ws×{ws_k} "
        f"P50 CF={out['mean_cf']} → cached {anchor_path(spec.node).name}")
    return out


# --------------------------------------------------------------------------- #
# Solar (PVWatts / ERA5 irradiance)
# --------------------------------------------------------------------------- #

def _solar_engine():
    import sys
    sys.path.insert(0, str(paths.ROOT / "datasets" / "solar_forecast"))
    import solar_pvwatts as sf  # noqa: E402
    return sf


def _model_monthly_solar(spec: SolarSpec, end_year: int, era5_end: str) -> pd.Series:
    """Modeled monthly AC MWh from ERA5 irradiance + PVWatts, capacity-stepped."""
    sf = _solar_engine()
    system = sf.SystemConfig(
        capacity_kw_dc=spec.capacity_full * 1000.0 * spec.dc_ac_ratio,
        array_type=spec.array_type, dc_ac_ratio=spec.dc_ac_ratio,
        tilt_deg=spec.tilt_deg, gcr=spec.gcr)
    frames = []
    for yr in range(spec.start_year, end_year + 1):
        s = f"{yr}-01-01"
        e = f"{yr}-12-31" if yr < end_year else era5_end
        try:
            wx = _retry(lambda: sf.fetch_weather_era5(spec.lat, spec.lon, s, e, tz="US/Central"))
            frames.append(sf.run_pvwatts(wx, system)[["ac_kw"]])
        except Exception:  # noqa: BLE001 — skip a year that fails all retries
            continue
    if not frames:
        raise RuntimeError("ERA5 solar fetch failed for all years.")
    hourly = pd.concat(frames).sort_index()
    hourly = hourly[~hourly.index.duplicated(keep="first")]
    m = hourly["ac_kw"] / 1000.0                      # kW hourly → MWh
    m.index = m.index.tz_localize(None) if m.index.tz is not None else m.index
    monthly_full = m.resample("MS").sum()
    return monthly_full * monthly_full.index.map(
        lambda ts: spec.online_capacity(ts) / spec.capacity_full)


def _fit_cache(spec, actual: pd.Series, modeled: pd.Series, extra: dict, log) -> dict:
    """Shared fit → percentiles → cache (wind & solar)."""
    overall = float(actual.reindex(modeled.index).dropna().sum()
                    / modeled.reindex(actual.index).dropna().sum())
    hours = actual.index.to_series().dt.daysinmonth.values * 24.0
    cap = actual.index.map(spec.online_capacity).astype(float)
    cf = pd.Series(actual.values / (cap * hours), index=actual.index)
    by_mo = cf.groupby(cf.index.month)
    pct = lambda q: {int(mo): round(float(np.percentile(g, q)), 4) for mo, g in by_mo}
    full_hours = {mo: pd.Timestamp(2001, mo, 1).daysinmonth * 24.0 for mo in range(1, 13)}
    cf_p10, cf_p50, cf_p90 = pct(10), pct(50), pct(90)
    ann = lambda qf: round(sum(qf[mo] * spec.capacity_full * full_hours[mo]
                               for mo in range(1, 13) if mo in qf), 0)
    out = {
        "node": spec.node, "label": spec.label, "tech": getattr(spec, "tech", "wind"),
        "eia_plant_ids": spec.eia_plant_ids, "capacity_full_mw": spec.capacity_full,
        "lat": spec.lat, "lon": spec.lon,
        "span": f"{actual.index.min():%Y-%m} → {actual.index.max():%Y-%m}",
        "n_months": int(len(actual)), "overall_factor": round(overall, 4),
        "mean_cf": round(float(cf.mean()), 4),
        "monthly_cf_p10": cf_p10, "monthly_cf_p50": cf_p50, "monthly_cf_p90": cf_p90,
        "annual_energy_p10_mwh": ann(cf_p10), "annual_energy_p50_mwh": ann(cf_p50),
        "annual_energy_p90_mwh": ann(cf_p90), **extra,
    }
    ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    anchor_path(spec.node).write_text(json.dumps(out, indent=2))
    log(f"[eia_anchor] {spec.node}: factor×{out['overall_factor']} "
        f"CF={out['mean_cf']} ({out['tech']}) → cached {anchor_path(spec.node).name}")
    return out


def build_solar(spec: SolarSpec, end_year: int = 2026, era5_end: str = "2026-06-15",
                log=print) -> dict:
    """Compute and cache the EIA anchor for a solar plant."""
    log(f"[eia_anchor] {spec.node} (solar): loading EIA monthly …")
    actual = _eia_monthly(spec, end_year)
    modeled = _model_monthly_solar(spec, end_year, era5_end)
    return _fit_cache(spec, actual, modeled, {
        "source": "EIA-923 monthly + ERA5/PVWatts (irradiance), capacity-factor space",
        "dc_ac_ratio": spec.dc_ac_ratio, "array_type": spec.array_type}, log)


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def monthly_cf_targets(node: str, band: str = "p50") -> dict | None:
    """Per-calendar-month CF targets for ``node`` (1–12 → CF), or None."""
    a = load(node)
    if not a:
        return None
    key = {"p10": "monthly_cf_p10", "p50": "monthly_cf_p50", "p90": "monthly_cf_p90"}[band]
    d = a.get(key) or {}
    return {int(k): float(v) for k, v in d.items()} if d else None


if __name__ == "__main__":
    import sys
    node = sys.argv[1] if len(sys.argv) > 1 else "MIRASOLE_GEN"
    build(SPECS[node])
