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


def _power_curve(
    ws: pd.Series,
    capacity_mw: float,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
) -> pd.Series:
    """Simplified cubic ramp cut_in→rated, flat above, zero outside."""
    arr = ws.to_numpy(dtype=float)
    cf = np.where(
        arr < cut_in, 0.0,
        np.where(
            arr >= cut_out, 0.0,
            np.where(
                arr >= rated, 1.0,
                ((arr - cut_in) / (rated - cut_in)) ** 3,
            ),
        ),
    )
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
) -> pd.Series:
    if "wind_speed_120m" in weather_df.columns and weather_df["wind_speed_120m"].sum() > 0:
        ws_raw = weather_df["wind_speed_120m"].fillna(0.0)
        meas_h = 120.0
    elif "wind_speed_80m" in weather_df.columns:
        ws_raw = weather_df["wind_speed_80m"].fillna(0.0)
        meas_h = 80.0
    else:
        return pd.Series(0.0, index=weather_df.index)

    ws_hub = _extrapolate_wind(ws_raw, meas_h, hub_height_m)
    raw = _power_curve(ws_hub, capacity_mw, cut_in=cut_in, rated=rated, cut_out=cut_out)
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
                                     cut_in=cut_in, rated=rated, cut_out=cut_out)

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
                                    cut_in=cut_in, rated=rated, cut_out=cut_out)

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
