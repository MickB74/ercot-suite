"""Walk-forward backtest for the renewable generation model (wind & solar).

WHAT THIS MEASURES
------------------
The weather input here is ERA5 *reanalysis* — the best-estimate of the weather
that actually occurred, not a forward weather forecast. So this harness grades
the **physics model + calibration**, given weather. It is the direct analog of
the price engine's perfect-foresight-gas backtest mode: a clean read = "given
the weather, the turbine/panel physics and the bias correction reproduce what
the plant actually generated." True day-ahead skill additionally carries NWP
weather-forecast error (use the forward NWP ensemble for that; only ~14 days of
it exist, so it can't be backtested over history).

THE GENUINE OUT-OF-SAMPLE TEST
------------------------------
The calibration (:mod:`wind_calibration`) is the learned part. For each *as-of*
date we fit the bias/shape correction on generation **strictly before** that
date, then apply it to the following window and score against actual SCED
telemetry. That answers "does the calibration generalise, or is it overfit to
its own window?" — which in-sample calibration cannot tell you.

METRICS (per window, raw physics vs. calibrated)
  * corr           — hourly Pearson correlation (shape skill)
  * nrmse_%        — RMSE / capacity (dispersion)
  * energy_err_%   — Σmodel/Σactual − 1 (the bias the calibration targets)
  * cf_model/cf_actual — mean capacity factor either side

Self-contained: numpy / pandas / requests + the local wind/solar engines.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import re
from functools import lru_cache

import wind_power as wp
import wind_calibration as wc
import turbine_db as tdb

# Default SCED actuals lake (Data Hub, sibling repo in the monorepo).
HERE = Path(__file__).resolve().parent
DEFAULT_SCED_DIR = HERE.parent / "Ercot_Data_Hub" / "data" / "plant_sced" / "plants"


# ---------------------------------------------------------------------------
# Site definition
# ---------------------------------------------------------------------------

@dataclass
class SiteSpec:
    name: str
    lat: float
    lon: float
    capacity_mw: float
    sced_units: list[str]                       # SCED resource names to sum
    hub: str | None = None
    fleet: wp.FleetConfig | None = None         # wind only
    kind: str = "wind"                          # "wind" | "solar"
    sced_dir: Path = field(default=DEFAULT_SCED_DIR)


# ---------------------------------------------------------------------------
# Real OEM (OEDB) power-curve matching
# ---------------------------------------------------------------------------

_MANUF_PREFIX = {  # USWTDB manufacturer → OEDB turbine_type prefixes
    "vestas": ("V",), "nordex": ("N",), "ge": ("GE", "GE1", "GE10", "GE12", "GE13"),
    "siemens": ("SWT", "S", "SG"), "siemens gamesa": ("SG", "SWT"),
    "enercon": ("E-", "E4", "E5", "E7", "E8", "E9"), "senvion": ("MM", "S1"),
    "repower": ("MM",), "acciona": ("AW",),
}


@lru_cache(maxsize=1)
def _oedb_catalog() -> tuple:
    """(turbine_type, manuf, rotor_m, rated_kw) for every OEDB type we can load."""
    from windpowerlib import get_turbine_types
    df = get_turbine_types(print_out=False)
    out = []
    for _, r in df.iterrows():
        t = str(r["turbine_type"])
        # Parse "<rotor>/<rated>" from the type string, e.g. V112/3450, GE120/2750.
        m = re.search(r"(\d{2,3})\s*/\s*(\d{3,5})", t)
        rotor = float(m.group(1)) if m else float("nan")
        rated = float(m.group(2)) if m else float("nan")
        out.append((t, str(r.get("manufacturer", "")), rotor, rated))
    return tuple(out)


def oem_match(manuf: str, rotor_m: float, rated_kw: float, *,
              rotor_tol: float = 6.0, rated_tol_pct: float = 0.10) -> str | None:
    """Nearest real OEDB turbine_type for a USWTDB segment, or None if uncovered.

    Matches within the manufacturer family on rotor diameter (±tol m) and rated
    power (±tol %). Returns the closest by combined relative distance."""
    mf = str(manuf or "").lower()
    prefixes = next((p for k, p in _MANUF_PREFIX.items() if k in mf), ())
    best, best_d = None, 1e9
    for t, m2, rot, rat in _oedb_catalog():
        if prefixes and not str(t).upper().startswith(tuple(p.upper() for p in prefixes)):
            continue
        if not (rot == rot and rat == rat):   # NaN parse
            continue
        if abs(rot - rotor_m) > rotor_tol or abs(rat - rated_kw) > rated_tol_pct * rated_kw:
            continue
        d = abs(rot - rotor_m) / max(rotor_m, 1) + abs(rat - rated_kw) / max(rated_kw, 1)
        if d < best_d:
            best, best_d = t, d
    return best


def usw_fleet(lat: float, lon: float, *, curve_mode: str = "parametric",
              radius_km: float = 12.0) -> tuple[wp.FleetConfig | None, list]:
    """Real turbine fleet at a coordinate from USWTDB → FleetConfig.

    ``curve_mode='oem'`` sets ``turbine_type`` on segments whose machine has a
    real OEDB curve (so ``run_wind(use_windpowerlib=True)`` uses it); the rest
    keep their parametric ``curve_key``. Returns (fleet, provenance rows)."""
    proj = tdb.find_project_near(lat, lon, radius_km=radius_km)
    if not proj:
        return None, []
    segs, prov = [], []
    for s in proj.segments:
        tt = None
        if curve_mode == "oem":
            tt = oem_match(s.manufacturer, s.rotor_m, s.rated_kw)
        segs.append(wp.TurbineSpec(count=s.count, rated_kw=s.rated_kw,
                    hub_height_m=s.hub_height_m, rotor_m=s.rotor_m,
                    curve_key=s.curve_key, turbine_type=tt,
                    label=f"{s.manufacturer} {s.model}"))
        prov.append({"turbine": f"{s.manufacturer} {s.model}", "count": s.count,
                     "curve": tt or f"parametric:{s.curve_key}"})
    return wp.FleetConfig(segments=segs), prov


def generic_wind_fleet(capacity_mw: float, *, hub_height_m: float = 100.0,
                       rotor_m: float = 140.0, rated_kw: float = 4000.0,
                       curve_key: str = "GENERIC_IEC2") -> wp.FleetConfig:
    """A single representative segment sized to the plant nameplate.

    Faithful enough for a *calibrated* backtest (the bias correction absorbs the
    difference from the true OEM fleet); pass an explicit ``fleet`` for the best
    raw-physics read.
    """
    count = max(1, round(capacity_mw * 1000.0 / rated_kw))
    seg = wp.TurbineSpec(count=count, rated_kw=rated_kw, hub_height_m=hub_height_m,
                         rotor_m=rotor_m, curve_key=curve_key, label="fleet")
    return wp.FleetConfig(segments=[seg])


# ---------------------------------------------------------------------------
# Actuals + model series (hourly, tz-aware Central)
# ---------------------------------------------------------------------------

def load_actuals(units, sced_dir=DEFAULT_SCED_DIR, start=None, end=None) -> pd.Series:
    """Hourly actual net MW = sum of the SCED units, resampled to the hour.

    SCED telemetry lands every ~5 min at irregular stamps; we take the hourly
    mean of ``telemetered_net_output`` (MW) so it aligns with the hourly model.
    """
    frames = []
    for u in units:
        for f in sorted(glob.glob(str(Path(sced_dir) / f"{u}_*.parquet"))):
            df = pd.read_parquet(f, columns=["sced_timestamp", "telemetered_net_output"])
            frames.append(df)
    if not frames:
        return pd.Series(dtype=float)
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.dropna(subset=["sced_timestamp"])
    s = (raw.set_index("sced_timestamp")["telemetered_net_output"]
         .sort_index().astype(float))
    # Sum across units at native cadence, then hourly-mean → MW per hour.
    hourly = s.groupby(level=0).sum() if s.index.has_duplicates else s
    hourly = hourly.resample("1h").mean()
    if start is not None:
        hourly = hourly[hourly.index >= pd.Timestamp(start, tz=hourly.index.tz)]
    if end is not None:
        hourly = hourly[hourly.index <= pd.Timestamp(end, tz=hourly.index.tz)]
    return hourly.rename("actual_mw")


def _era5_cached(lat, lon, start, end) -> wp.WeatherResult:
    """ERA5 fetch with a local parquet cache (Open-Meteo is slow + rate-limited)."""
    cache = HERE / "data" / "era5_cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = cache / f"{lat:.4f}_{lon:.4f}_{start}_{end}.parquet"
    if key.exists():
        df = pd.read_parquet(key)
        return wp.WeatherResult(data=df, metadata={"latitude": lat, "longitude": lon,
                                "altitude": 0.0}, label=f"ERA5 {start}→{end}",
                                latitude=lat, longitude=lon, sources=("era5",))
    # Open-Meteo's free archive rate-limits bursty use; retry with backoff.
    import time
    last = None
    for attempt in range(5):
        try:
            w = wp.fetch_weather_era5(lat, lon, start, end)
            w.data.to_parquet(key)
            return w
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def model_hourly(site: SiteSpec, start, end, *, use_region_prior: bool = True,
                 curve_mode: str = "parametric", ws_scale: float = 1.0) -> pd.Series:
    """Run the physics model over [start, end] with ERA5 weather → hourly net MW.

    ``use_region_prior`` applies the geographic hub bias multiplier (mirrors the
    production path) but NOT the SCED-learned month-hour multipliers, which are
    fit on this same fleet's actuals and would leak into an out-of-sample test.

    ``curve_mode``: ``parametric`` (power_curves), or ``oem`` to use the real
    OEDB curve on any segment whose machine is covered (parametric otherwise).
    """
    if site.kind == "solar":
        raise NotImplementedError("use solar_backtest.model_hourly for solar sites")
    weather = _era5_cached(site.lat, site.lon, start, end)
    # Prefer the real USWTDB fleet at the site; fall back to any explicit fleet.
    fleet, _ = usw_fleet(site.lat, site.lon, curve_mode=curve_mode)
    fleet = fleet or site.fleet or generic_wind_fleet(site.capacity_mw)
    out = wp.run_wind(weather, fleet, use_windpowerlib=(curve_mode == "oem"),
                      ws_scale=ws_scale)
    net = out["net_mw"]
    if use_region_prior:
        net = wc.apply_region_priors(net, site.capacity_mw, lat=site.lat, lon=site.lon,
                                     hub_name=site.hub, use_bias=True, use_sced=False)
    return net.rename("model_mw")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(model: pd.Series, actual: pd.Series, capacity_mw: float) -> dict:
    """Align two hourly MW series and compute skill metrics."""
    df = pd.DataFrame({"m": model, "a": actual}).dropna()
    # Drop likely offline/curtailed hours (actual≈0 while model says it's windy):
    # they punish weather skill for events the physics can't know about.
    keep = ~((df["a"] < 0.01 * capacity_mw) & (df["m"] > 0.10 * capacity_mw))
    df = df.loc[keep]
    if len(df) < 24:
        return {"n": int(len(df)), "ok": False}
    err = df["m"] - df["a"]
    cap = capacity_mw or 1.0
    return {
        "n": int(len(df)),
        "corr": float(df["m"].corr(df["a"])),
        "mbe_mw": float(err.mean()),
        "rmse_mw": float(np.sqrt((err ** 2).mean())),
        "nrmse_%": float(np.sqrt((err ** 2).mean()) / cap * 100),
        "energy_err_%": float(df["m"].sum() / df["a"].sum() * 100 - 100)
        if df["a"].sum() else float("nan"),
        "cf_model": float(df["m"].mean() / cap),
        "cf_actual": float(df["a"].mean() / cap),
        "ok": True,
    }


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward(model: pd.Series, actual: pd.Series, capacity_mw: float, *,
                 asof_start=None, asof_step_months: int = 1,
                 train_months: int = 6, test_months: int = 1) -> pd.DataFrame:
    """Roll an as-of date forward; fit calibration on the trailing ``train_months``
    and score the next ``test_months`` window, raw physics vs. calibrated.

    ``model`` / ``actual`` are full-span hourly MW series (tz-aware Central)."""
    df = pd.DataFrame({"m": model, "a": actual}).dropna()
    if df.empty:
        return pd.DataFrame()
    tz = df.index.tz
    first, last = df.index.min(), df.index.max()
    asof0 = (pd.Timestamp(asof_start, tz=tz) if asof_start
             else (first + pd.DateOffset(months=train_months)).normalize())
    rows = []
    asof = asof0.normalize().replace(day=1)
    while asof + pd.DateOffset(months=test_months) <= last + pd.Timedelta(hours=1):
        tr0, tr1 = asof - pd.DateOffset(months=train_months), asof
        te0, te1 = asof, asof + pd.DateOffset(months=test_months)
        tr = df[(df.index >= tr0) & (df.index < tr1)]
        te = df[(df.index >= te0) & (df.index < te1)]
        if len(tr) >= 24 * 20 and len(te) >= 24:
            calib = wc.calibrate_against_actuals(tr["m"], tr["a"], capacity_mw=capacity_mw)
            m_cal = wc.apply_calibration(te["m"], calib, capacity_mw=capacity_mw)
            raw = score(te["m"], te["a"], capacity_mw)
            cal = score(m_cal, te["a"], capacity_mw)
            # Uncapped energy ratio the train window actually needed — if this
            # exceeds the calibrator's clamp (0.5–1.8), the fit is saturating and
            # will under-correct the test window.
            req = float(tr["a"].sum() / tr["m"].sum()) if tr["m"].sum() else float("nan")
            if raw.get("ok") and cal.get("ok"):
                rows.append({
                    "asof": asof.strftime("%Y-%m"), "n": raw["n"],
                    "corr": raw["corr"],
                    "raw_nrmse_%": raw["nrmse_%"], "cal_nrmse_%": cal["nrmse_%"],
                    "raw_energy_%": raw["energy_err_%"], "cal_energy_%": cal["energy_err_%"],
                    "cf_actual": raw["cf_actual"], "cf_model_raw": raw["cf_model"],
                    "cf_model_cal": cal["cf_model"],
                    "req_factor": req, "calib_factor": calib.get("overall_factor"),
                })
        asof = asof + pd.DateOffset(months=asof_step_months)
    return pd.DataFrame(rows)


def summarize(bt: pd.DataFrame) -> dict:
    """n-weighted overall skill, raw vs. calibrated."""
    if bt.empty:
        return {}
    w = bt["n"]

    def wmean(col):
        return float((bt[col] * w).sum() / w.sum())
    return {
        "windows": int(len(bt)), "hours": int(w.sum()),
        "corr": wmean("corr"),
        "raw_nrmse_%": wmean("raw_nrmse_%"), "cal_nrmse_%": wmean("cal_nrmse_%"),
        "raw_abs_energy_%": float((bt["raw_energy_%"].abs() * w).sum() / w.sum()),
        "cal_abs_energy_%": float((bt["cal_energy_%"].abs() * w).sum() / w.sum()),
    }


def run_site(site: SiteSpec, *, curve_mode: str = "parametric", **kw) -> tuple[pd.DataFrame, dict]:
    """End-to-end: fetch model + actuals for the full span, then walk forward."""
    actual = load_actuals(site.sced_units, site.sced_dir)
    if actual.empty:
        raise ValueError(f"no SCED actuals found for {site.name} ({site.sced_units})")
    s = actual.index.min().strftime("%Y-%m-%d")
    e = actual.index.max().strftime("%Y-%m-%d")
    model = model_hourly(site, s, e, curve_mode=curve_mode)
    bt = walk_forward(model, actual, site.capacity_mw, **kw)
    return bt, summarize(bt)


def compare_bias(site: SiteSpec, *, ks=None, train_months: int = 6, test_months: int = 1,
                 asof_step_months: int = 1) -> pd.DataFrame:
    """Compare three ways to remove the ERA5 wind under-prediction bias:

    * ``energy_cap1.8`` — production: energy multiplier, clamped 0.5–1.8 (saturates).
    * ``energy_cap3.5`` — same, clamp raised so it can fully correct the level.
    * ``ws_scale``      — physical: fit a hub-height wind-speed multiplier on the
                          train window, apply it before the power curve.

    All are strictly out-of-sample (fit on the trailing window, scored on the
    next). ``ws_scale`` is fit from a precomputed grid of model runs."""
    actual = load_actuals(site.sced_units, site.sced_dir)
    if actual.empty:
        raise ValueError(f"no SCED actuals for {site.name}")
    s = actual.index.min().strftime("%Y-%m-%d")
    e = actual.index.max().strftime("%Y-%m-%d")
    ks = ks or [1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5]
    grid = {k: model_hourly(site, s, e, ws_scale=k) for k in ks}   # net MW per k
    base = grid[1.0]
    df = pd.DataFrame({"a": actual}).join(pd.DataFrame({f"k{k}": v for k, v in grid.items()})).dropna()
    tz, first, last = df.index.tz, df.index.min(), df.index.max()
    asof = (first + pd.DateOffset(months=train_months)).normalize().replace(day=1)

    def score_window(m, a):
        return score(pd.Series(m, index=a.index), a, site.capacity_mw)

    rows = []
    while asof + pd.DateOffset(months=test_months) <= last + pd.Timedelta(hours=1):
        tr = df[(df.index >= asof - pd.DateOffset(months=train_months)) & (df.index < asof)]
        te = df[(df.index >= asof) & (df.index < asof + pd.DateOffset(months=test_months))]
        if len(tr) >= 24 * 20 and len(te) >= 24:
            # Energy-multiplier modes (fit on train k=1 model).
            r = {"asof": asof.strftime("%Y-%m"), "n": len(te)}
            for tag, clamp in (("energy_cap1.8", (0.5, 1.8)), ("energy_cap3.5", (0.5, 3.5))):
                cal = wc.calibrate_against_actuals(tr["k1.0"], tr["a"],
                                                   capacity_mw=site.capacity_mw, clamp=clamp)
                m_cal = wc.apply_calibration(te["k1.0"], cal, capacity_mw=site.capacity_mw)
                sc = score_window(m_cal.to_numpy(), te["a"])
                r[f"{tag}_energy%"] = sc.get("energy_err_%")
                r[f"{tag}_nrmse%"] = sc.get("nrmse_%")
            # ws_scale mode: pick k matching train energy, apply that k to test.
            best_k = min(ks, key=lambda k: abs(tr[f"k{k}"].sum() - tr["a"].sum()))
            sc = score_window(te[f"k{best_k}"].to_numpy(), te["a"])
            r["ws_scale_k"] = best_k
            r["ws_scale_energy%"] = sc.get("energy_err_%")
            r["ws_scale_nrmse%"] = sc.get("nrmse_%")
            rows.append(r)
        asof = asof + pd.DateOffset(months=asof_step_months)
    bt = pd.DataFrame(rows)
    if bt.empty:
        return bt
    w = bt["n"]
    wabs = lambda c: float((bt[c].abs() * w).sum() / w.sum())  # noqa: E731
    wm = lambda c: float((bt[c] * w).sum() / w.sum())          # noqa: E731
    return pd.DataFrame({
        "abs_energy_%": {m: wabs(f"{m}_energy%") for m in ("energy_cap1.8", "energy_cap3.5", "ws_scale")},
        "nrmse_%": {m: wm(f"{m}_nrmse%") for m in ("energy_cap1.8", "energy_cap3.5", "ws_scale")},
        "mean_k": {"ws_scale": wm("ws_scale_k")},
    })


def compare_curves(site: SiteSpec, **kw) -> pd.DataFrame:
    """Overall skill under each curve source: parametric vs. real OEM (OEDB)."""
    _, prov = usw_fleet(site.lat, site.lon, curve_mode="oem")
    print(f"{site.name} — real USWTDB fleet & curve source:")
    for p in prov:
        print(f"  {p['count']:>3}x {p['turbine']:<22} → {p['curve']}")
    rows = {}
    for mode in ("parametric", "oem"):
        _, summ = run_site(site, curve_mode=mode, **kw)
        if summ:
            rows[mode] = summ
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------------------
# Example sites (real coords + SCED units)
# ---------------------------------------------------------------------------

def azure_sky() -> SiteSpec:
    """Azure Sky Wind — 350 MW, North hub, Throckmorton Co. (2024+ actuals)."""
    fleet = wp.FleetConfig(segments=[
        wp.TurbineSpec(count=65, rated_kw=4500, hub_height_m=105, rotor_m=149,
                       curve_key="NORDEX_N149", label="N149"),
        wp.TurbineSpec(count=7, rated_kw=3450, hub_height_m=82, rotor_m=163,
                       curve_key="VESTAS_V163", label="V163"),
        wp.TurbineSpec(count=7, rated_kw=2000, hub_height_m=80, rotor_m=90,
                       curve_key="GE_2X", label="gen2"),
    ])
    return SiteSpec("Azure Sky Wind", 33.1534, -99.2847, 350.0,
                    ["VORTEX_WIND1", "VORTEX_WIND2", "VORTEX_WIND3", "VORTEX_WIND4"],
                    hub="NORTH", fleet=fleet)


def los_mirasoles() -> SiteSpec:
    """Los Mirasoles Wind — ~300 MW, South hub, Hidalgo Co."""
    return SiteSpec("Los Mirasoles Wind", 26.465556, -98.411111, 300.0,
                    ["MIRASOLE_MIR11", "MIRASOLE_MIR12", "MIRASOLE_MIR13", "MIRASOLE_MIR21"],
                    hub="SOUTH", fleet=generic_wind_fleet(300.0, hub_height_m=95, rotor_m=110))


if __name__ == "__main__":
    import sys
    site = {"azure": azure_sky, "mirasole": los_mirasoles}.get(
        sys.argv[1] if len(sys.argv) > 1 else "azure", azure_sky)()
    pd.set_option("display.width", 200, "display.max_columns", 20)
    cmp = compare_curves(site, train_months=6, test_months=1, asof_step_months=1)
    print(f"\n=== {site.name}: parametric vs. real OEM curves (out-of-sample) ===")
    print(cmp.round(3).to_string())
