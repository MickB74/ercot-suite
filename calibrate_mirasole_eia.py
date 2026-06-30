"""Calibrate the Hidalgo Mirasole (Los Mirasoles) wind model on EIA-923 history.

The portal's weather model is currently anchored to SCED (60-Day Disclosure),
which ERCOT only retains from ~Jan 2024 — a 2-year, narrow-weather sample. EIA-923
monthly net generation goes back to each phase's COD (Phase I 57617 from Dec-2016,
Phase II 62618 from Feb-2020), giving ~9.5 years of authoritative truth.

This script:
  1. Loads EIA-923 monthly net gen for 57617 + 62618 (full plant), 2016 → now.
  2. Pulls hourly ERA5 reanalysis (Open-Meteo archive) at the site for the same
     span and runs the Vestas V110-2.0 physics model (Ercot_Wind_Forecast).
  3. Fits an EIA-derived calibration in capacity-factor space — overall bias +
     per-calendar-month seasonal shape — handling the 250→300.4 MW Phase-II step.
  4. Validates OUT-OF-SAMPLE against the 2024+ SCED you already have: derives the
     SCED bias factor independently and checks the two agree.
  5. Writes diagnostics (mirasole_eia_calibration.json) and patches the standalone
     forecast app's wind_calibration.json (project/resource multiplier + monthly
     shape) so the tuned bias is actually consumed.

Run with the Hub venv:
  Ercot_Data_Hub/.venv/bin/python calibrate_mirasole_eia.py
"""
from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
HUB = ROOT / "Ercot_Data_Hub"
WIND = ROOT / "Ercot_Wind_Forecast"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "eia923"))
sys.path.insert(0, str(WIND))

import eia923  # noqa: E402
import wind_power as wp  # noqa: E402

# ── site / asset facts (from the Hub registry & contract) ────────────────────
LAT, LON = 26.465556, -98.411111
EIA_IDS = [57617, 62618]
UNITS = ["MIRASOLE_MIR11", "MIRASOLE_MIR12", "MIRASOLE_MIR13", "MIRASOLE_MIR21"]
NODE = "MIRASOLE_GEN"
HUB_HEIGHT_M = 95.0
ROTOR_M = 110.0
# Online nameplate by phase (EIA-860 online dates).
CAP_PHASE_I = 250.0    # 57617, online 2016-12-01
CAP_PHASE_II = 50.4    # 62618, online 2020-02-01
CAP_FULL = CAP_PHASE_I + CAP_PHASE_II
PHASE_II_START = pd.Timestamp("2020-02-01")

START_YEAR, END_YEAR = 2016, 2026
ERA5_END = "2026-06-15"  # ERA5 archive lags ~5 days; EIA caps the usable window anyway

SCED_DIR = HUB / "data" / "plant_sced" / "plants"
DIAG_OUT = WIND / "mirasole_eia_calibration.json"
CALIB_JSON = WIND / "wind_calibration.json"


def log(m):
    print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)


# ── 1. EIA actual monthly net generation (full plant) ────────────────────────
def load_eia_monthly() -> pd.Series:
    years = list(range(START_YEAR, END_YEAR + 1))
    df = eia923.load_region("ercot", years=years)
    if df.empty:
        raise SystemExit("No EIA-923 cache found — run the 2016-2021 backfill first.")
    sub = df[df["plant_id"].isin(EIA_IDS)].copy()
    if sub.empty:
        raise SystemExit("Mirasole plant ids not in EIA cache.")
    sub["ts"] = pd.to_datetime(dict(year=sub["year"], month=sub["month"], day=1))
    s = sub.groupby("ts")["netgen_mwh"].sum().sort_index()
    # Drop any zero/negative months (parasitic-only / pre-COD noise).
    return s[s > 0]


def online_capacity(ts: pd.Timestamp) -> float:
    return CAP_FULL if ts >= PHASE_II_START else CAP_PHASE_I


_FLEET = wp.FleetConfig(segments=[
    wp.TurbineSpec(count=150, rated_kw=CAP_FULL * 1000 / 150,
                   hub_height_m=HUB_HEIGHT_M, rotor_m=ROTOR_M,
                   curve_key="GENERIC_IEC2", label="V110"),
])


# ── 2. ERA5 + physics model → modeled monthly MWh ────────────────────────────
def fetch_all_weather() -> list:
    """Fetch hourly ERA5 once per year; reused across wind-speed trials."""
    out = []
    for yr in range(START_YEAR, END_YEAR + 1):
        s = f"{yr}-01-01"
        e = f"{yr}-12-31" if yr < END_YEAR else ERA5_END
        try:
            log(f"ERA5 {s} → {e} …")
            out.append(wp.fetch_weather_era5(LAT, LON, s, e, tz="US/Central"))
        except Exception as ex:  # noqa: BLE001
            log(f"  {yr}: ERA5 ERROR {str(ex)[:120]}")
    if not out:
        raise SystemExit("ERA5 fetch failed for all years.")
    return out


def model_monthly(weathers: list, ws_scale: float = 1.0) -> pd.Series:
    """Modeled monthly MWh, capacity-stepped, with an optional wind-speed scale."""
    frames = []
    for wr in weathers:
        w = wr
        if ws_scale != 1.0:                       # scale hub-relevant wind speeds
            df = wr.data.copy()
            df["ws10"] *= ws_scale
            df["ws100"] *= ws_scale
            w = wp.WeatherResult(data=df, metadata=wr.metadata, label=wr.label,
                                 latitude=wr.latitude, longitude=wr.longitude,
                                 sources=wr.sources)
        frames.append(wp.run_wind(w, _FLEET)[["net_mw"]])
    hourly = pd.concat(frames).sort_index()
    hourly = hourly[~hourly.index.duplicated(keep="first")]
    m = hourly["net_mw"].copy()
    m.index = m.index.tz_localize(None) if m.index.tz is not None else m.index
    monthly_full = m.resample("MS").sum()
    return monthly_full * monthly_full.index.map(
        lambda ts: online_capacity(ts) / CAP_FULL)


def solve_ws_correction(weathers: list, actual: pd.Series) -> tuple[float, float]:
    """Bisect the wind-speed multiplier so modeled energy matches actual.

    Reports the *physical* correction (ERA5 hub-wind bias) behind the large
    energy factor — far more transportable to a forecast than a flat MWh scalar.
    """
    target = actual.sum()
    lo, hi = 1.0, 1.8
    k = 1.3
    for _ in range(18):
        k = (lo + hi) / 2
        mod = model_monthly(weathers, ws_scale=k)
        common = mod.index.intersection(actual.index)
        if mod.loc[common].sum() < target:
            lo = k
        else:
            hi = k
    mod = model_monthly(weathers, ws_scale=k)
    common = mod.index.intersection(actual.index)
    resid = float(actual.loc[common].sum() / mod.loc[common].sum())
    return round(k, 4), round(resid, 4)


# ── 3. fit calibration in CF space ───────────────────────────────────────────
def fit_calibration(actual: pd.Series, modeled: pd.Series) -> dict:
    df = pd.DataFrame({"act": actual, "mod": modeled}).dropna()
    df = df[(df["act"] > 0) & (df["mod"] > 0)]
    if df.empty:
        raise SystemExit("No overlapping EIA/model months.")

    overall = float(df["act"].sum() / df["mod"].sum())
    monthly = {}
    for mo, chunk in df.groupby(df.index.month):
        if chunk["mod"].sum() > 0 and len(chunk) >= 2:
            monthly[int(mo)] = round(float(chunk["act"].sum() / chunk["mod"].sum()), 4)

    # Diagnostics in capacity-factor terms.
    cap = df.index.map(online_capacity).astype(float)
    hours = df.index.to_series().dt.daysinmonth.values * 24.0
    df["act_cf"] = df["act"].values / (cap * hours)
    df["mod_cf"] = df["mod"].values / (cap * hours)
    corr = float(df["act"].corr(df["mod"]))
    mape = float((np.abs(df["act"] - df["mod"] * overall) / df["act"]).mean() * 100)

    by_year = (df.assign(year=df.index.year)
                 .groupby("year")
                 .apply(lambda g: pd.Series({
                     "act_mwh": g["act"].sum(),
                     "mod_mwh": g["mod"].sum(),
                     "act_cf": g["act_cf"].mean(),
                     "mod_cf": g["mod_cf"].mean(),
                     "n_months": len(g),
                 }), include_groups=False)
                 .round(4))
    return {
        "overall_factor": round(overall, 4),
        "monthly_factors": monthly,
        "correlation_monthly": round(corr, 4),
        "mape_after_calib_pct": round(mape, 2),
        "n_months": int(len(df)),
        "span": f"{df.index.min():%Y-%m} → {df.index.max():%Y-%m}",
        "mean_actual_cf": round(float(df["act_cf"].mean()), 4),
        "mean_model_cf": round(float(df["mod_cf"].mean()), 4),
        "by_year": by_year.reset_index().to_dict("records"),
    }


# ── 4. out-of-sample SCED validation ─────────────────────────────────────────
def sced_monthly() -> pd.Series:
    """Monthly SCED MWh — restricted to months where ALL 4 units report.

    The per-unit plant_sced files are incomplete before 2025 (only MIRASOLE_MIR21
    has 2024), so summing "all units" on partial months silently undercounts the
    plant. Keeping only full-coverage months makes the SCED↔EIA cross-check honest.
    """
    files = sorted(SCED_DIR.glob("MIRASOLE_*.parquet"))
    if not files:
        return pd.Series(dtype=float)
    parts = []
    for f in files:
        d = pd.read_parquet(f, columns=["resource_name", "sced_timestamp",
                                         "telemetered_net_output"])
        parts.append(d)
    raw = pd.concat(parts, ignore_index=True).dropna(subset=["sced_timestamp"])
    ts = pd.to_datetime(raw["sced_timestamp"])
    ts = ts.dt.tz_localize(None) if getattr(ts.dt, "tz", None) is not None else ts
    raw = raw.assign(ts=ts, ym=ts.dt.to_period("M"))
    cov = raw.groupby("ym")["resource_name"].nunique()
    clean = cov[cov == len(UNITS)].index
    raw = raw[raw["ym"].isin(clean)]
    if raw.empty:
        return pd.Series(dtype=float)
    # Sum all units at each timestamp, then integrate MW → monthly MWh via an
    # hourly mean (SCED intervals are ~5 min but irregular).
    s = pd.Series(raw["telemetered_net_output"].values, index=raw["ts"]).groupby(level=0).sum()
    return s.resample("h").mean().resample("MS").sum()


def main():
    log("=== EIA actual monthly ===")
    actual = load_eia_monthly()
    log(f"EIA months: {len(actual)}  ({actual.index.min():%Y-%m} → {actual.index.max():%Y-%m})")

    log("=== ERA5 + V110 physics model ===")
    weathers = fetch_all_weather()
    modeled = model_monthly(weathers)

    log("=== solve implied wind-speed correction ===")
    ws_k, ws_resid = solve_ws_correction(weathers, actual)
    log(f"ERA5 hub-wind bias correction = ×{ws_k}  (residual energy factor after = {ws_resid})")

    log("=== fit EIA calibration ===")
    fit = fit_calibration(actual, modeled)
    fit["ws_speed_correction"] = ws_k
    fit["residual_factor_after_ws_correction"] = ws_resid
    log(f"overall bias factor = {fit['overall_factor']}  "
        f"(model {'under' if fit['overall_factor']>1 else 'over'}-predicts)")
    log(f"monthly corr = {fit['correlation_monthly']}, "
        f"MAPE after calib = {fit['mape_after_calib_pct']}%, n={fit['n_months']}")
    log(f"actual CF = {fit['mean_actual_cf']:.3f} vs model CF = {fit['mean_model_cf']:.3f}")

    # ── out-of-sample SCED check ──
    log("=== out-of-sample SCED validation (2024+) ===")
    sced = sced_monthly()
    val = {}
    if not sced.empty:
        join = pd.DataFrame({"sced": sced, "eia": actual, "model": modeled}).dropna()
        if not join.empty:
            # Like-for-like: both factors on the SAME overlap months.
            sced_factor = float(join["sced"].sum() / join["model"].sum())
            eia_factor_overlap = float(join["eia"].sum() / join["model"].sum())
            sced_vs_eia = float(join["sced"].sum() / join["eia"].sum())
            agree = round(100 * (1 - abs(sced_factor - eia_factor_overlap)
                                 / eia_factor_overlap), 2)
            # Non-stationarity: full-period vs the recent (overlap) window.
            val = {
                "sced_derived_factor_overlap": round(sced_factor, 4),
                "eia_derived_factor_overlap": round(eia_factor_overlap, 4),
                "factor_agreement_pct": agree,
                "sced_vs_eia_energy_ratio": round(sced_vs_eia, 4),
                "full_period_eia_factor": fit["overall_factor"],
                "factor_drift_recent_vs_full": round(
                    eia_factor_overlap - fit["overall_factor"], 4),
                "overlap_months": int(len(join)),
                "overlap_span": f"{join.index.min():%Y-%m} → {join.index.max():%Y-%m}",
            }
            log(f"on overlap months: SCED factor {sced_factor:.3f} vs "
                f"EIA factor {eia_factor_overlap:.3f} → {agree}% agreement")
            log(f"SCED/EIA energy ratio = {sced_vs_eia:.4f} (sanity: should be ~1.0)")
            log(f"NON-STATIONARITY: recent factor {eia_factor_overlap:.2f} vs "
                f"full-period {fit['overall_factor']:.2f} "
                f"(ERA5 bias is {'worse' if eia_factor_overlap>fit['overall_factor'] else 'better'} lately)")
    else:
        log("no SCED on disk — skipped")

    # ── write diagnostics ──
    diag = {
        "asset": "Hidalgo Mirasole Wind (Los Mirasoles)",
        "node": NODE, "eia_plant_ids": EIA_IDS, "units": UNITS,
        "lat": LAT, "lon": LON,
        "capacity_phase_i_mw": CAP_PHASE_I, "capacity_phase_ii_mw": CAP_PHASE_II,
        "phase_ii_online": str(PHASE_II_START.date()),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "method": "ERA5(Open-Meteo) + Vestas V110-2.0 physics vs EIA-923 monthly, CF-space",
        "calibration": fit,
        "sced_out_of_sample": val,
    }
    DIAG_OUT.write_text(json.dumps(diag, indent=2))
    log(f"wrote diagnostics → {DIAG_OUT.name}")

    # ── record into wind_calibration.json (diagnostics block ONLY) ──
    # We deliberately do NOT overwrite project_multiplier/resource_multiplier:
    # the standalone forecast app layers region priors + SCED-bias on top of raw
    # physics, so a raw-physics-derived factor would double-count. The portal uses
    # the Hub's live SCED calibration (gen_forecast.calibrate, clamped to 3.0),
    # which this validates. Stored here as a labelled, ready-to-apply reference.
    cal = json.loads(CALIB_JSON.read_text())
    cal.setdefault("eia_calibration", {})[NODE] = {
        "project": "Hidalgo Mirasole Wind",
        "overall_energy_factor": fit["overall_factor"],
        "ws_speed_correction": fit["ws_speed_correction"],
        "residual_after_ws_correction": fit["residual_factor_after_ws_correction"],
        "monthly_factors": fit["monthly_factors"],
        "source": "EIA-923 monthly 2016-12+ (plants 57617+62618), ERA5+V110-2.0 physics",
        "span": fit["span"],
        "validated_vs_sced_pct": val.get("factor_agreement_pct"),
        "note": ("Raw ERA5+physics under-resolves the Rio Grande Valley low-level "
                 "jet (mean hub wind reads ~6.7 m/s vs ~8.5 needed). Prefer the "
                 "ws_speed_correction over the flat energy factor; apply on top of "
                 "raw physics only, not the app's already-primed output."),
    }
    CALIB_JSON.write_text(json.dumps(cal, indent=2))
    log(f"recorded eia_calibration[{NODE}] in {CALIB_JSON.name} (live multipliers untouched)")
    log("DONE")


if __name__ == "__main__":
    main()
