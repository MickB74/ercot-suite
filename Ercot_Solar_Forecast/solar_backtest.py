"""Walk-forward backtest for the PVWatts solar generation model.

Mirror of the wind backtest (Ercot_Wind_Forecast/wind_backtest.py). ERA5 is
reanalysis, so this grades the **physics + bias correction given weather**, not
day-ahead weather-forecast error. The out-of-sample element is the calibration:
an energy-ratio bias fit on generation strictly BEFORE each as-of date, applied
to the following window and scored against actual SCED telemetry.

The solar repo ships no calibration layer, so a small overall+monthly energy
ratio is fit inline here (same shape as wind_calibration), which doubles as a
proof of concept for adding one to solar_pvwatts.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import solar_pvwatts as sp

HERE = Path(__file__).resolve().parent
DEFAULT_SCED_DIR = HERE.parent / "Ercot_Data_Hub" / "data" / "plant_sced" / "plants"
_CLAMP = (0.5, 1.8)   # same clamp as wind_calibration, for a like-for-like read


@dataclass
class SiteSpec:
    name: str
    lat: float
    lon: float
    capacity_mw_ac: float                       # inverter/AC nameplate (actuals basis)
    system: sp.SystemConfig
    sced_units: list[str]
    sced_dir: Path = field(default=DEFAULT_SCED_DIR)


# ---------------------------------------------------------------------------
# Actuals + model series (hourly MW, tz-aware Central)
# ---------------------------------------------------------------------------

def load_actuals(units, sced_dir=DEFAULT_SCED_DIR) -> pd.Series:
    frames = []
    for u in units:
        for f in sorted(glob.glob(str(Path(sced_dir) / f"{u}_*.parquet"))):
            frames.append(pd.read_parquet(f, columns=["sced_timestamp", "telemetered_net_output"]))
    if not frames:
        return pd.Series(dtype=float)
    raw = pd.concat(frames, ignore_index=True).dropna(subset=["sced_timestamp"])
    s = raw.set_index("sced_timestamp")["telemetered_net_output"].sort_index().astype(float)
    s = s.groupby(level=0).sum() if s.index.has_duplicates else s
    return s.resample("1h").mean().rename("actual_mw")


def _era5_cached(lat, lon, start, end) -> sp.WeatherResult:
    cache = HERE / "data" / "era5_cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = cache / f"{lat:.4f}_{lon:.4f}_{start}_{end}.parquet"
    if key.exists():
        df = pd.read_parquet(key)
        return sp.WeatherResult(data=df, metadata={"latitude": lat, "longitude": lon},
                                label=f"ERA5 {start}→{end}", latitude=lat, longitude=lon)
    w = sp.fetch_weather_era5(lat, lon, start, end)
    w.data.to_parquet(key)
    return w


def model_hourly(site: SiteSpec, start, end) -> pd.Series:
    """ERA5 → PVWatts → hourly AC MW (ac_kw / 1000)."""
    w = _era5_cached(site.lat, site.lon, start, end)
    out = sp.run_pvwatts(w, site.system)
    return (out["ac_kw"] / 1000.0).rename("model_mw")


# ---------------------------------------------------------------------------
# Inline calibration (overall + per-month energy ratio)
# ---------------------------------------------------------------------------

def fit_calibration(model: pd.Series, actual: pd.Series, cap_mw: float | None = None) -> dict:
    df = pd.DataFrame({"m": model, "a": actual}).dropna()
    df = df[df["m"] > 0]
    # Drop offline/curtailed/pre-COD hours (actual≈0 while the model expects sun);
    # without this the fit learns a bogus <1 factor from downtime and wrecks the
    # forward forecast. (The wind calibrator does the same — the inline solar one
    # originally didn't, which is exactly how naive calibration can hurt.)
    if cap_mw:
        df = df.loc[~((df["a"] < 0.02 * cap_mw) & (df["m"] > 0.10 * cap_mw))]
    if len(df) < 24 or df["m"].sum() <= 0:
        return {"overall": 1.0, "monthly": {}}
    clamp = lambda v: float(max(_CLAMP[0], min(_CLAMP[1], v)))  # noqa: E731
    monthly = {}
    for mo, g in df.groupby(df.index.month):
        if g["m"].sum() > 0 and len(g) >= 24:
            monthly[int(mo)] = clamp(g["a"].sum() / g["m"].sum())
    return {"overall": clamp(df["a"].sum() / df["m"].sum()), "monthly": monthly}


def apply_calibration(model: pd.Series, calib: dict) -> pd.Series:
    out = model.astype(float).copy()
    monthly = calib.get("monthly") or {}
    for i, mo in enumerate(out.index.month):
        out.iloc[i] *= monthly.get(int(mo), calib.get("overall", 1.0))
    return out


# ---------------------------------------------------------------------------
# Scoring + walk-forward  (identical metrics to the wind harness)
# ---------------------------------------------------------------------------

def score(model: pd.Series, actual: pd.Series, cap_mw: float) -> dict:
    df = pd.DataFrame({"m": model, "a": actual}).dropna()
    # Daytime only + drop offline hours (actual≈0 while model expects sun).
    df = df[df["m"] > 0.01 * cap_mw]
    df = df.loc[~((df["a"] < 0.01 * cap_mw) & (df["m"] > 0.10 * cap_mw))]
    if len(df) < 24:
        return {"n": int(len(df)), "ok": False}
    err = df["m"] - df["a"]
    cap = cap_mw or 1.0
    return {
        "n": int(len(df)), "corr": float(df["m"].corr(df["a"])),
        "rmse_mw": float(np.sqrt((err ** 2).mean())),
        "nrmse_%": float(np.sqrt((err ** 2).mean()) / cap * 100),
        "energy_err_%": float(df["m"].sum() / df["a"].sum() * 100 - 100) if df["a"].sum() else float("nan"),
        "cf_model": float(df["m"].mean() / cap), "cf_actual": float(df["a"].mean() / cap),
        "ok": True,
    }


def walk_forward(model, actual, cap_mw, *, asof_start=None, asof_step_months=1,
                 train_months=6, test_months=1) -> pd.DataFrame:
    df = pd.DataFrame({"m": model, "a": actual}).dropna()
    if df.empty:
        return pd.DataFrame()
    tz, first, last = df.index.tz, df.index.min(), df.index.max()
    asof = ((pd.Timestamp(asof_start, tz=tz) if asof_start
             else first + pd.DateOffset(months=train_months))
            .normalize().replace(day=1))
    rows = []
    while asof + pd.DateOffset(months=test_months) <= last + pd.Timedelta(hours=1):
        tr = df[(df.index >= asof - pd.DateOffset(months=train_months)) & (df.index < asof)]
        te = df[(df.index >= asof) & (df.index < asof + pd.DateOffset(months=test_months))]
        if len(tr) >= 24 * 20 and len(te) >= 24:
            calib = fit_calibration(tr["m"], tr["a"], cap_mw=cap_mw)
            raw, cal = score(te["m"], te["a"], cap_mw), score(apply_calibration(te["m"], calib), te["a"], cap_mw)
            req = float(tr["a"].sum() / tr["m"].sum()) if tr["m"].sum() else float("nan")
            if raw.get("ok") and cal.get("ok"):
                rows.append({
                    "asof": asof.strftime("%Y-%m"), "n": raw["n"], "corr": raw["corr"],
                    "raw_nrmse_%": raw["nrmse_%"], "cal_nrmse_%": cal["nrmse_%"],
                    "raw_energy_%": raw["energy_err_%"], "cal_energy_%": cal["energy_err_%"],
                    "cf_actual": raw["cf_actual"], "cf_model_raw": raw["cf_model"],
                    "cf_model_cal": cal["cf_model"], "req_factor": req,
                    "calib_factor": calib.get("overall"),
                })
        asof = asof + pd.DateOffset(months=asof_step_months)
    return pd.DataFrame(rows)


def summarize(bt: pd.DataFrame) -> dict:
    if bt.empty:
        return {}
    w = bt["n"]
    wm = lambda c: float((bt[c] * w).sum() / w.sum())  # noqa: E731
    return {"windows": int(len(bt)), "hours": int(w.sum()), "corr": wm("corr"),
            "raw_nrmse_%": wm("raw_nrmse_%"), "cal_nrmse_%": wm("cal_nrmse_%"),
            "raw_abs_energy_%": float((bt["raw_energy_%"].abs() * w).sum() / w.sum()),
            "cal_abs_energy_%": float((bt["cal_energy_%"].abs() * w).sum() / w.sum())}


def run_site(site: SiteSpec, **kw) -> tuple[pd.DataFrame, dict]:
    actual = load_actuals(site.sced_units, site.sced_dir)
    if actual.empty:
        raise ValueError(f"no SCED actuals for {site.name} ({site.sced_units})")
    model = model_hourly(site, actual.index.min().strftime("%Y-%m-%d"),
                         actual.index.max().strftime("%Y-%m-%d"))
    bt = walk_forward(model, actual, site.capacity_mw_ac, **kw)
    return bt, summarize(bt)


def stafford_solar() -> SiteSpec:
    """Stafford Solar — 250 MW-AC single-axis tracker, West hub, Motley Co."""
    cfg = sp.SystemConfig(capacity_kw_dc=250_000 * 1.27, array_type="1-Axis Tracker",
                          dc_ac_ratio=1.27)
    return SiteSpec("Stafford Solar", 33.88, -100.9, 250.0, cfg,
                    ["BUZI_SLR_UNIT1", "BUZI_SLR_UNIT2", "BUZI_SLR_UNIT3", "BUZI_SLR_UNIT4"])


if __name__ == "__main__":
    site = stafford_solar()
    bt, summ = run_site(site, train_months=6, test_months=1, asof_step_months=1)
    pd.set_option("display.width", 200, "display.max_columns", 20)
    print(f"=== {site.name}: walk-forward (ERA5 PVWatts, out-of-sample calibration) ===")
    print(bt.round(3).to_string(index=False))
    print("\nOVERALL (n-weighted):")
    for k, v in summ.items():
        print(f"  {k:18s}: {v:.3f}" if isinstance(v, float) else f"  {k:18s}: {v}")
