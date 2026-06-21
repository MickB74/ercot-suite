"""Convert Open-Meteo weather data to near-term generation forecasts.

Two physics models:

  solar — linear DC model anchored to nameplate capacity:
          MW = capacity_mw × (GHI_W_m² / 1000) × cal_factor
          cal_factor absorbs panel efficiency, DC/AC losses, soiling, and
          any availability derating by calibrating against SCED history.

  wind  — cubic power curve with Hellmann height extrapolation:
          (1) extrapolate wind speed from measurement height to hub height
              using the 1/7 power law (open-terrain default);
          (2) apply a simplified cubic ramp from cut-in → rated speed,
              flat at capacity above rated, zero above cut-out;
          (3) multiply by cal_factor to anchor to SCED history.

The calibration factor ties model output to the plant's real behaviour and is
the single most important input — without it the solar model understates
production by ~80% (it has no panel-efficiency constant by design, so
cal_factor ≈ 0.15–0.20 for a typical PV plant).
"""

from __future__ import annotations

import calendar
import datetime as _dt

import numpy as np
import pandas as pd


# ── solar ─────────────────────────────────────────────────────────────────────

def _solar_hourly_mw(
    radiation: pd.Series,
    capacity_mw: float,
    cal_factor: float,
) -> pd.Series:
    raw = (radiation.clip(lower=0.0) / 1000.0) * capacity_mw
    return (raw * cal_factor).clip(0.0, capacity_mw)


# ── wind ──────────────────────────────────────────────────────────────────────

def _extrapolate_wind(
    ws: pd.Series,
    from_height_m: float,
    to_height_m: float,
    alpha: float = 1.0 / 7.0,
) -> pd.Series:
    """Power-law height correction (Hellmann exponent α = 1/7 for open terrain)."""
    return ws * (to_height_m / from_height_m) ** alpha


# Wind-speed spread across a utility-scale farm at any instant (m/s, 1-σ). The
# single-turbine curve assumes every turbine sees one identical wind speed, so it
# pegs the whole plant at 100% the moment that speed clears `rated`. A real
# 400+ MW farm spread over miles never does — wakes, terrain, and turbine
# availability mean the turbines are spread across a band of speeds. Convolving
# the single-turbine curve with a Gaussian of this width gives the smoother
# "farm" curve (Nørgaard–Holttinen); the mean is unaffected because `calibrate`
# re-anchors it to SCED. Two regimes (both validated vs Mesquite Star metered
# daily CF): the real per-model curve already encodes the operational rated
# approach, so it needs only light spatial smoothing (σ≈1.0); the crude cubic
# fallback needs heavy smoothing (σ≈3.0) to avoid pegging days at ~100% CF.
_FARM_WIND_SIGMA_REAL = 1.0
_FARM_WIND_SIGMA_GENERIC = 3.0
_FARM_WIND_SIGMA_MS = _FARM_WIND_SIGMA_GENERIC   # back-compat alias


def _base_cf_curve(grid: np.ndarray, turbine_type: str | None,
                   cut_in: float, rated: float, cut_out: float):
    """``(cf_over_grid, used_real)`` — single-turbine normalized CF (0–1).

    With ``turbine_type`` set, uses the real per-model parametric curve from
    ``power_curves`` (re-tuned to OEDB/manufacturer data and validated against
    ERCOT metered CF) — the same curve family ``plant_value`` uses for the
    typical-year model, so the two wind models agree. ``used_real`` says whether
    that succeeded (drives the smoothing regime). Falls back to the generic cubic
    ramp when no turbine type is given or the curve library is unavailable.
    """
    if turbine_type:
        try:
            from ercot_core import bootstrap
            bootstrap.setup_path()
            import power_curves  # noqa: PLC0415 — dataset module, on path via bootstrap
            return np.asarray(power_curves.get_normalized_power(grid, turbine_type),
                              dtype=float), True
        except Exception:  # noqa: BLE001 — fall back to the generic ramp
            pass
    cubic = np.where(
        grid < cut_in, 0.0,
        np.where(grid >= cut_out, 0.0,
                 np.where(grid >= rated, 1.0,
                          ((grid - cut_in) / (rated - cut_in)) ** 3)))
    return cubic, False


def _power_curve(
    ws: pd.Series,
    capacity_mw: float,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
    farm_sigma: float | None = None,
    turbine_type: str | None = None,
) -> pd.Series:
    """Real (or generic) single-turbine curve, Gaussian-smoothed to a farm curve.

    The base single-turbine curve is the real per-model power curve when
    ``turbine_type`` is given (else a cubic ramp); it is then convolved with a
    Gaussian to model the spread of wind speeds across a large farm. ``farm_sigma``
    defaults to the regime matching the base curve (light for the real curve,
    heavy for the cubic); pass an explicit value to override, or ``0`` to disable.
    """
    grid = np.arange(0.0, 50.0, 0.1)
    base, used_real = _base_cf_curve(grid, turbine_type, cut_in, rated, cut_out)
    if farm_sigma is None:
        farm_sigma = _FARM_WIND_SIGMA_REAL if used_real else _FARM_WIND_SIGMA_GENERIC
    if farm_sigma and farm_sigma > 0:
        k = np.arange(-4 * farm_sigma, 4 * farm_sigma + 0.1, 0.1)
        w = np.exp(-0.5 * (k / farm_sigma) ** 2)
        w /= w.sum()
        base = np.convolve(base, w, mode="same")
    arr = ws.to_numpy(dtype=float)
    cf = np.interp(arr, grid, base, left=0.0, right=0.0)
    return pd.Series(cf * capacity_mw, index=ws.index)


def _wind_hourly_mw(
    weather_df: pd.DataFrame,
    capacity_mw: float,
    hub_height_m: float,
    cal_factor: float,
    *,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
    turbine_type: str | None = None,
) -> pd.Series:
    # Prefer the highest measured level that actually carries data. The ERA5
    # archive nulls 80m/120m for recent dates (ERA5T lag) but populates the
    # native 100m, so try 120m → 100m → 80m, picking the first with real values.
    # Coerce to numeric first: a null level comes back object-dtype (all None),
    # whose .sum() is not a usable signal.
    ws_raw, meas_h = None, None
    for col, h in (("wind_speed_120m", 120.0), ("wind_speed_100m", 100.0),
                   ("wind_speed_80m", 80.0)):
        if col in weather_df.columns:
            s = pd.to_numeric(weather_df[col], errors="coerce").fillna(0.0)
            if s.sum() > 0:
                ws_raw, meas_h = s, h
                break
    if ws_raw is None:
        return pd.Series(0.0, index=weather_df.index)

    ws_hub = _extrapolate_wind(ws_raw, meas_h, hub_height_m)
    raw = _power_curve(ws_hub, capacity_mw, cut_in=cut_in, rated=rated,
                       cut_out=cut_out, turbine_type=turbine_type)
    return (raw * cal_factor).clip(0.0, capacity_mw)


# ── calibration ───────────────────────────────────────────────────────────────

def calibrate(
    weather_df: pd.DataFrame,
    sced_daily_mwh: pd.Series,
    capacity_mw: float,
    tech: str,
    *,
    hub_height_m: float = 90.0,
    min_overlap_days: int = 5,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
    turbine_type: str | None = None,
) -> float:
    """Derive a calibration factor from SCED history vs. the raw weather model.

    Parameters
    ----------
    weather_df:
        Historical hourly weather (UTC tz-aware index) from
        :func:`weather_forecast.fetch` with ``past_days`` set.
    sced_daily_mwh:
        Actual daily net generation (MWh), indexed by :class:`datetime.date`,
        already scaled to the offtaker's contracted share.
    capacity_mw:
        Plant capacity at the offtaker's contracted share.
    tech:
        ``"solar"`` or ``"wind"``.
    hub_height_m:
        Hub height (wind only).
    min_overlap_days:
        Minimum overlapping days required; returns 1.0 below this threshold.

    Returns
    -------
    float, clipped to ``[0.05, 3.0]``.
    """
    tech = tech.lower()

    if tech == "solar":
        col = "shortwave_radiation"
        if col not in weather_df.columns:
            return 1.0
        raw_hourly = _solar_hourly_mw(weather_df[col].fillna(0.0), capacity_mw, 1.0)
    else:
        raw_hourly = _wind_hourly_mw(weather_df, capacity_mw, hub_height_m, 1.0,
                                     cut_in=cut_in, rated=rated, cut_out=cut_out,
                                     turbine_type=turbine_type)

    # Convert UTC hourly MW → Central local date daily MWh (each row = 1 h)
    local_idx = raw_hourly.index.tz_convert("America/Chicago")
    raw_daily = pd.Series(raw_hourly.values, index=local_idx).resample("D").sum()
    raw_daily.index = raw_daily.index.date  # type: ignore[assignment]

    # Align with SCED
    common = [d for d in raw_daily.index if d in sced_daily_mwh.index]
    if len(common) < min_overlap_days:
        return 1.0

    model_sum = float(raw_daily.loc[common].sum())
    actual_sum = float(sced_daily_mwh.loc[common].sum())
    if model_sum <= 0:
        return 1.0
    return float(np.clip(actual_sum / model_sum, 0.05, 3.0))


# ── daily forecast ────────────────────────────────────────────────────────────

def daily_forecast_mwh(
    weather_df: pd.DataFrame,
    tech: str,
    capacity_mw: float,
    *,
    hub_height_m: float = 90.0,
    cal_factor: float = 1.0,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
    turbine_type: str | None = None,
) -> pd.Series:
    """Return daily MWh estimates indexed by :class:`datetime.date` (Central local).

    Parameters
    ----------
    weather_df:
        Hourly weather from :func:`weather_forecast.fetch` — UTC tz-aware index.
    tech:
        ``"solar"`` or ``"wind"``.
    capacity_mw:
        Contracted MW share of the plant.
    hub_height_m:
        Hub height for wind height extrapolation (ignored for solar).
    cal_factor:
        From :func:`calibrate`; defaults to 1.0 (no calibration).

    Returns
    -------
    pd.Series of MWh, indexed by ``datetime.date``.
    """
    tech = tech.lower()
    if tech == "solar":
        col = "shortwave_radiation"
        if col not in weather_df.columns:
            return pd.Series(dtype=float)
        hourly_mw = _solar_hourly_mw(weather_df[col].fillna(0.0), capacity_mw, cal_factor)
    else:
        hourly_mw = _wind_hourly_mw(weather_df, capacity_mw, hub_height_m, cal_factor,
                                    cut_in=cut_in, rated=rated, cut_out=cut_out,
                                    turbine_type=turbine_type)

    local_idx = hourly_mw.index.tz_convert("America/Chicago")
    daily = pd.Series(hourly_mw.values, index=local_idx).resample("D").sum()
    daily.index = daily.index.date  # type: ignore[assignment]
    return daily


# ── historical shape fallback ─────────────────────────────────────────────────

def hist_mwh_for_date(d: _dt.date, monthly_mwh: pd.Series) -> float:
    """Expected daily MWh from the historical monthly shape.

    Parameters
    ----------
    d:
        Target date.
    monthly_mwh:
        Mean monthly MWh indexed by calendar month (1–12).
    """
    days_in_month = calendar.monthrange(d.year, d.month)[1]
    return float(monthly_mwh.get(d.month, float(monthly_mwh.mean()))) / days_in_month
