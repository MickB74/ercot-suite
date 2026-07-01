"""Wind-production engine driven by lat/long weather — the standalone core.

Self-contained (numpy / pandas / requests). Optional ``windpowerlib`` unlocks
real manufacturer power curves from the Open-Energy-Database turbine library.

Pipeline (per hour):

  1. **Weather** — wind speed at 10 m and 100 m, temperature, surface pressure,
     from one or more sources:
       * ERA5 reanalysis (Open-Meteo archive) — 1940→~5 days ago, no API key.
         The workhorse for historical / backcast and recent comparison.
       * Multi-model NWP forecast (Open-Meteo) — ICON, GFS, ECMWF, GEM ensemble
         for a genuine forward forecast with model spread (P10/P50/P90).
     Multiple sources are blended (weighted mean) to cut single-model bias.
  2. **Shear → hub height** — the wind shear exponent α is measured *per hour*
     from the 10 m and 100 m speeds (α = ln(v₁₀₀/v₁₀)/ln(10)), then used to
     extrapolate from the nearest reference height to the turbine hub. This is
     far more accurate than a fixed 1/7-power assumption, and falls back to a
     site/region α (from calibration) when the measured shear is unreliable.
  3. **Air density** — ρ from temperature and pressure at hub height; the IEC
     density correction maps wind speed to the curve's reference density.
  4. **Power curve** — per turbine segment, real (windpowerlib) or parametric
     (``power_curves``). Fleet output is the MW-weighted sum across segments.
  5. **Losses** — wake + electrical + availability, as a net derate.

The engine returns hourly gross/net MW; ``wind_calibration`` then applies
region/SCED bias corrections on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import power_curves

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Loss buckets (fraction lost). Defaults are typical onshore values; the wake
# loss is the dominant, site-specific one. Combined multiplicatively.
DEFAULT_LOSSES = {
    "wake": 0.07,          # array/wake interference (5–12% typical)
    "availability": 0.03,  # turbine/grid downtime
    "electrical": 0.02,    # collector + transformer + line
    "other": 0.02,         # soiling/blade degradation/icing/hysteresis/curtailment headroom
}


def net_loss_factor(losses: dict | None = None) -> float:
    """Combine loss buckets multiplicatively → surviving fraction (0–1)."""
    losses = losses or DEFAULT_LOSSES
    f = 1.0
    for v in losses.values():
        f *= (1.0 - max(0.0, min(0.95, float(v))))
    return f


@dataclass
class TurbineSpec:
    """One turbine segment for the model.

    ``curve_key`` selects a parametric curve (``power_curves.PARAMETRIC_CURVES``).
    ``turbine_type`` is an optional real OEDB type (e.g. ``"E-126/4200"``) used
    only when ``use_windpowerlib=True`` is passed to :func:`run_wind`.
    """

    count: int = 1
    rated_kw: float = 2500.0
    hub_height_m: float = 90.0
    rotor_m: float = 120.0
    curve_key: str = "GENERIC_IEC2"
    turbine_type: str | None = None
    label: str = "turbine"

    @property
    def capacity_mw(self) -> float:
        return self.count * self.rated_kw / 1000.0


@dataclass
class FleetConfig:
    """A project: one or more turbine segments plus loss assumptions."""

    segments: list[TurbineSpec] = field(default_factory=lambda: [TurbineSpec()])
    losses: dict = field(default_factory=lambda: dict(DEFAULT_LOSSES))

    @property
    def capacity_mw(self) -> float:
        return sum(s.capacity_mw for s in self.segments)

    @property
    def mean_hub_height_m(self) -> float:
        n = sum(s.count for s in self.segments)
        return sum(s.hub_height_m * s.count for s in self.segments) / n if n else 90.0


@dataclass
class WeatherResult:
    data: pd.DataFrame          # hourly: ws10, ws100, temp_c, pressure_pa  (+ optional ws_spread)
    metadata: dict
    label: str
    latitude: float = 0.0
    longitude: float = 0.0
    sources: tuple = ()         # which weather models contributed


# ---------------------------------------------------------------------------
# Weather sources (Open-Meteo)
# ---------------------------------------------------------------------------

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_HOURLY_VARS = ("wind_speed_10m,wind_speed_100m,temperature_2m,surface_pressure")

# Forward-forecast NWP models to ensemble. Each is an independent source.
FORECAST_MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless")


def _frame_from_openmeteo(h: dict, tz: str) -> pd.DataFrame:
    """Build the standard hourly frame from an Open-Meteo ``hourly`` block."""
    idx = pd.to_datetime(h["time"]).tz_localize("UTC")
    if tz:
        idx = idx.tz_convert(tz)
    df = pd.DataFrame(index=idx)
    # Open-Meteo wind defaults to km/h → m/s.
    df["ws10"] = pd.to_numeric(pd.Series(h.get("wind_speed_10m"), index=idx), errors="coerce") / 3.6
    df["ws100"] = pd.to_numeric(pd.Series(h.get("wind_speed_100m"), index=idx), errors="coerce") / 3.6
    df["temp_c"] = pd.to_numeric(pd.Series(h.get("temperature_2m"), index=idx), errors="coerce")
    df["pressure_pa"] = pd.to_numeric(pd.Series(h.get("surface_pressure"), index=idx), errors="coerce") * 100.0
    return df


def fetch_weather_era5(latitude: float, longitude: float, start, end,
                       tz: str = "US/Central") -> WeatherResult:
    """Hourly ERA5 reanalysis via the Open-Meteo archive API (no API key).

    ``start``/``end`` are dates (YYYY-MM-DD). Returns 10 m + 100 m wind,
    temperature and surface pressure indexed in ``tz`` (default US/Central, so
    hours align with ERCOT market data).
    """
    import requests

    params = {
        "latitude": latitude, "longitude": longitude,
        "start_date": str(start), "end_date": str(end),
        "hourly": _HOURLY_VARS, "wind_speed_unit": "kmh", "timezone": "GMT",
    }
    r = requests.get(_ARCHIVE_URL, params=params, timeout=90)
    if r.status_code != 200:
        raise ValueError(f"Open-Meteo ERA5 request failed ({r.status_code}): {r.text[:200]}")
    h = (r.json() or {}).get("hourly") or {}
    if not h.get("time"):
        raise ValueError("Open-Meteo ERA5 returned no data for that range/coordinate.")
    df = _clean_weather(_frame_from_openmeteo(h, tz))
    meta = {"latitude": float(latitude), "longitude": float(longitude),
            "altitude": float((r.json() or {}).get("elevation", 0) or 0)}
    return WeatherResult(data=df, metadata=meta, label=f"ERA5 {start}→{end}",
                         latitude=float(latitude), longitude=float(longitude),
                         sources=("era5",))


def fetch_weather_forecast(latitude: float, longitude: float,
                           models: tuple = FORECAST_MODELS,
                           past_days: int = 2, forecast_days: int = 14,
                           tz: str = "US/Central") -> WeatherResult:
    """Multi-model NWP forward forecast via Open-Meteo (no API key).

    Fetches each NWP model independently and blends them into an ensemble mean,
    keeping the across-model standard deviation in ``ws_spread`` so callers can
    show P10/P50/P90 bands. This is the genuine *forecast* path (next ~2 weeks);
    use :func:`fetch_weather_era5` for historical/backcast.
    """
    import requests

    frames = {}
    elevation = 0.0
    for model in models:
        params = {
            "latitude": latitude, "longitude": longitude,
            "hourly": _HOURLY_VARS, "wind_speed_unit": "kmh", "timezone": "GMT",
            "models": model, "past_days": int(past_days),
            "forecast_days": int(min(16, forecast_days)),
        }
        try:
            r = requests.get(_FORECAST_URL, params=params, timeout=90)
            if r.status_code != 200:
                continue
            j = r.json() or {}
            h = j.get("hourly") or {}
            if not h.get("time"):
                continue
            elevation = float(j.get("elevation", elevation) or elevation)
            frames[model] = _frame_from_openmeteo(h, tz)
        except Exception:  # noqa: BLE001 — skip a model that fails, keep the rest
            continue

    if not frames:
        raise ValueError("No NWP model returned data for that coordinate.")

    blended, spread = _blend_frames(list(frames.values()))
    blended = _clean_weather(blended)
    blended["ws_spread"] = spread.reindex(blended.index)
    meta = {"latitude": float(latitude), "longitude": float(longitude), "altitude": elevation}
    return WeatherResult(data=blended, metadata=meta,
                         label=f"Forecast ({len(frames)}-model ensemble)",
                         latitude=float(latitude), longitude=float(longitude),
                         sources=tuple(frames.keys()))


def blend_weather(results: list[WeatherResult], weights: list[float] | None = None,
                  label: str | None = None) -> WeatherResult:
    """Blend several aligned ``WeatherResult``s into one weighted-mean source.

    Used to combine, e.g., ERA5 with a second reanalysis. Frames are aligned on
    their common timestamps; ``ws_spread`` carries the across-source std-dev.
    """
    results = [r for r in results if r is not None and not r.data.empty]
    if not results:
        raise ValueError("Nothing to blend.")
    if len(results) == 1:
        return results[0]
    weights = weights or [1.0] * len(results)
    blended, spread = _blend_frames([r.data for r in results], weights)
    blended["ws_spread"] = spread.reindex(blended.index)
    srcs = tuple(s for r in results for s in (r.sources or (r.label,)))
    return WeatherResult(data=blended, metadata=results[0].metadata,
                         label=label or " + ".join(r.label for r in results),
                         latitude=results[0].latitude, longitude=results[0].longitude,
                         sources=srcs)


def _blend_frames(frames: list[pd.DataFrame], weights: list[float] | None = None):
    """Weighted-mean blend on the common index; returns (mean_df, ws100_spread)."""
    common = frames[0].index
    for f in frames[1:]:
        common = common.intersection(f.index)
    aligned = [f.reindex(common) for f in frames]
    weights = np.asarray(weights or [1.0] * len(frames), dtype=float)
    weights = weights / weights.sum()

    out = pd.DataFrame(index=common)
    for col in ("ws10", "ws100", "temp_c", "pressure_pa"):
        stack = np.vstack([f[col].to_numpy(dtype=float) for f in aligned])
        out[col] = np.nansum(stack * weights[:, None], axis=0)
    ws100_stack = np.vstack([f["ws100"].to_numpy(dtype=float) for f in aligned])
    spread = pd.Series(np.nanstd(ws100_stack, axis=0), index=common)
    return out, spread


def _clean_weather(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce/fill the standard columns so downstream math never sees NaN."""
    df = df.copy()
    for c in ("ws10", "ws100"):
        df[c] = pd.to_numeric(df[c], errors="coerce").clip(lower=0).interpolate().ffill().bfill()
    df["temp_c"] = pd.to_numeric(df["temp_c"], errors="coerce").interpolate().ffill().bfill()
    # Surface pressure: fill from a standard-atmosphere default if entirely missing.
    p = pd.to_numeric(df["pressure_pa"], errors="coerce")
    df["pressure_pa"] = p.interpolate().ffill().bfill().fillna(101325.0)
    return df.dropna(how="all")


# ---------------------------------------------------------------------------
# Physics: shear, density
# ---------------------------------------------------------------------------

def hub_height_wind(df: pd.DataFrame, hub_height_m: float,
                    fallback_alpha: float = 0.20):
    """Extrapolate wind speed to hub height using the *measured* hourly shear.

    α is derived per hour from the 10 m / 100 m pair, clamped to a physical
    range, and applied from the nearest reference height (100 m for typical
    80–140 m hubs). Hours with too-low wind for a reliable α use ``fallback_alpha``.
    Returns ``(ws_hub_series, alpha_series)``.
    """
    v10 = df["ws10"].to_numpy(dtype=float)
    v100 = df["ws100"].to_numpy(dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.log(np.where(v100 > 0, v100, np.nan) / np.where(v10 > 0, v10, np.nan)) / np.log(100.0 / 10.0)
    reliable = (v10 >= 1.5) & (v100 >= 1.5) & np.isfinite(alpha)
    alpha = np.where(reliable, alpha, fallback_alpha)
    alpha = np.clip(alpha, 0.0, 0.55)

    # Extrapolate from the nearer reference height to reduce error.
    ref_h, ref_v = (100.0, v100) if hub_height_m >= 55.0 else (10.0, v10)
    ws_hub = ref_v * (float(hub_height_m) / ref_h) ** alpha
    idx = df.index
    return pd.Series(ws_hub, index=idx, name="ws_hub"), pd.Series(alpha, index=idx, name="alpha")


def air_density(temp_c, pressure_pa, hub_height_m=0.0, ref_height_m=2.0):
    """Moist-air-free air density ρ = P/(R_d·T) at hub height (kg/m³).

    Surface pressure is reduced to hub height with the barometric formula and a
    standard lapse rate; temperature uses a −6.5 K/km lapse. Good enough for the
    ±a-few-percent density effect on the power curve.
    """
    t = np.asarray(temp_c, dtype=float)
    p = np.asarray(pressure_pa, dtype=float)
    dz = max(0.0, float(hub_height_m) - float(ref_height_m))
    t_hub_k = t + 273.15 - 0.0065 * dz
    g, r_d, lapse = 9.80665, 287.05, 0.0065
    t_surf_k = t + 273.15
    p_hub = p * (1.0 - lapse * dz / t_surf_k) ** (g / (r_d * lapse))
    rho = p_hub / (r_d * t_hub_k)
    return np.clip(rho, 0.8, 1.5)


# ---------------------------------------------------------------------------
# Power conversion
# ---------------------------------------------------------------------------

def _segment_power_fraction(ws_corrected, segment: TurbineSpec, use_windpowerlib: bool):
    """Normalized (0–1) output for one segment at density-corrected hub speed."""
    if use_windpowerlib and segment.turbine_type:
        try:
            return _windpowerlib_fraction(ws_corrected, segment)
        except Exception:  # noqa: BLE001 — fall back to parametric on any error
            pass
    return power_curves.get_normalized_power(ws_corrected, segment.curve_key)


def _windpowerlib_fraction(ws_corrected, segment: TurbineSpec):
    """Real OEDB power curve via windpowerlib, normalized to nameplate."""
    import numpy as _np
    from windpowerlib import WindTurbine
    from windpowerlib.power_output import power_curve as wpl_power_curve

    wt = WindTurbine(turbine_type=segment.turbine_type, hub_height=segment.hub_height_m)
    pc = wt.power_curve  # columns: wind_speed (m/s), value (W)
    out_w = wpl_power_curve(
        wind_speed=_np.asarray(ws_corrected, dtype=float),
        power_curve_wind_speeds=pc["wind_speed"].to_numpy(),
        power_curve_values=pc["value"].to_numpy(),
    )
    rated_w = float(pc["value"].max())
    return _np.clip(_np.asarray(out_w, dtype=float) / rated_w, 0.0, 1.0)


def run_wind(weather: WeatherResult, fleet: FleetConfig,
             use_windpowerlib: bool = False,
             fallback_alpha: float = 0.20,
             ws_scale=1.0) -> pd.DataFrame:
    """Run the wind model → hourly DataFrame indexed by local time.

    Columns: ``ws_hub`` (mean across segments, m/s), ``alpha`` (shear exponent),
    ``air_density`` (kg/m³), ``gross_mw`` (before losses), ``net_mw`` (after
    losses). One column ``mw__<label>`` per segment is also returned (net).

    ``ws_scale`` multiplies the hub-height wind speed before the power curve — a
    physically-correct correction for reanalysis wind that under-resolves
    hub-height speed. May be a scalar or a ``{month: factor}`` mapping for a
    seasonal correction (see ``wind_calibration.ws_scale_for``).
    """
    df = weather.data
    idx = df.index
    elevation = float(weather.metadata.get("altitude", 0) or 0)

    loss_factor = net_loss_factor(fleet.losses)
    out = pd.DataFrame(index=idx)
    gross = pd.Series(0.0, index=idx)
    ws_hub_w = pd.Series(0.0, index=idx)   # capacity-weighted mean hub speed
    alpha_keep = None
    rho_keep = None
    total_cap = fleet.capacity_mw or 1.0

    if isinstance(ws_scale, dict):
        _mult = pd.Series(
            [float(ws_scale.get(m, ws_scale.get(str(m), 1.0))) for m in idx.month],
            index=idx)
    else:
        _mult = float(ws_scale)

    for seg in fleet.segments:
        ws_hub, alpha = hub_height_wind(df, seg.hub_height_m, fallback_alpha=fallback_alpha)
        if isinstance(_mult, pd.Series) or _mult != 1.0:
            ws_hub = ws_hub * _mult
        rho = air_density(df["temp_c"].to_numpy(), df["pressure_pa"].to_numpy(),
                          hub_height_m=seg.hub_height_m)
        ws_corr = power_curves.density_correct_speed(ws_hub.to_numpy(), rho, seg.curve_key)
        frac = _segment_power_fraction(ws_corr, seg, use_windpowerlib)
        seg_mw = pd.Series(frac, index=idx) * seg.capacity_mw
        out[f"mw__{seg.label}"] = (seg_mw * loss_factor).clip(lower=0.0)
        gross = gross.add(seg_mw, fill_value=0.0)
        ws_hub_w = ws_hub_w.add(ws_hub * (seg.capacity_mw / total_cap), fill_value=0.0)
        alpha_keep = alpha if alpha_keep is None else alpha_keep
        rho_keep = pd.Series(rho, index=idx) if rho_keep is None else rho_keep

    net = (gross * loss_factor).clip(lower=0.0, upper=fleet.capacity_mw)
    out["ws_hub"] = ws_hub_w
    out["alpha"] = alpha_keep
    out["air_density"] = rho_keep
    out["gross_mw"] = gross.clip(lower=0.0)
    out["net_mw"] = net
    if "ws_spread" in df.columns:
        out["ws_spread"] = df["ws_spread"]
    out.index.name = "timestamp"
    return out


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def _interval_hours(index) -> float:
    if len(index) < 2:
        return 1.0
    # Resolution-independent (pandas 3.0 defaults to µs, not ns): use Timedeltas.
    secs = pd.Series(index).diff().dropna().dt.total_seconds()
    secs = secs[secs > 0]
    return float(secs.median() / 3600.0) if len(secs) else 1.0


def summarize(result: pd.DataFrame, fleet: FleetConfig, col: str = "net_mw") -> dict:
    """Headline production metrics from an hourly MW result."""
    mw = result[col]
    h = _interval_hours(result.index)
    energy_mwh = float(mw.sum() * h)
    cap = fleet.capacity_mw
    hours = len(mw) * h
    cf = energy_mwh / (cap * hours) if cap and hours else 0.0
    return {
        "annual_mwh": energy_mwh,
        "capacity_factor": cf,
        "net_capacity_factor": cf,
        "specific_yield_mwh_per_mw": energy_mwh / cap if cap else 0.0,
        "peak_mw": float(mw.max()),
        "mean_hub_wind_ms": float(result["ws_hub"].mean()),
        "capacity_mw": cap,
        "hours": len(mw),
        "interval_hours": h,
    }


def monthly_energy(result: pd.DataFrame, col: str = "net_mw") -> pd.DataFrame:
    """Monthly energy (MWh) and mean hub wind speed."""
    h = _interval_hours(result.index)
    m = result.copy()
    m["month"] = m.index.month
    agg = m.groupby("month").agg(
        energy_mwh=(col, lambda s: s.sum() * h),
        mean_wind_ms=("ws_hub", "mean"),
    )
    agg.index = [pd.Timestamp(2000, mo, 1).strftime("%b") for mo in agg.index]
    agg.index.name = "month"
    return agg.round(2)


def probabilistic_bands(result: pd.DataFrame, fleet: FleetConfig) -> dict | None:
    """If a forecast carried model spread, derive simple P10/P50/P90 energy.

    Uses the across-model wind-speed std-dev (``ws_spread``) propagated through
    the dominant segment's power curve as a first-order uncertainty band.
    """
    if "ws_spread" not in result.columns or result["ws_spread"].isna().all():
        return None
    seg = max(fleet.segments, key=lambda s: s.capacity_mw)
    h = _interval_hours(result.index)
    base_ws = result["ws_hub"].to_numpy()
    spread = result["ws_spread"].fillna(0.0).to_numpy()
    bands = {}
    for name, z in (("p10", -1.2816), ("p50", 0.0), ("p90", 1.2816)):
        frac = power_curves.get_normalized_power(base_ws + z * spread, seg.curve_key)
        mw = np.clip(frac * fleet.capacity_mw * net_loss_factor(fleet.losses), 0, fleet.capacity_mw)
        bands[name] = float(mw.sum() * h)
    return bands
