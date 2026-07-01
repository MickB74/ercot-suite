"""Near-term weather forecast from Open-Meteo (free, no API key).

Fetches hourly solar irradiance or wind speed for the past ``past_days`` days
(for calibration against SCED) and the next ``forecast_days`` days (forward
estimate). The raw JSON response is cached locally for ``cache_hours`` so a
Streamlit rerun doesn't hit the API on every widget interaction.

Returned DataFrame
------------------
Always has a tz-aware UTC DatetimeIndex named ``time``.

  solar  — shortwave_radiation (W/m²), direct_radiation, diffuse_radiation,
            temperature_2m (°C)
  wind   — wind_speed_80m (m/s), wind_speed_120m (m/s), wind_direction_80m (°)
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

try:
    from . import paths as _paths
    _CACHE_DIR: Path = _paths.DATA / "weather_forecast_cache"
except Exception:
    _CACHE_DIR = Path.home() / ".cache" / "ercot_weather_forecast"

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
_SOLAR_VARS = "shortwave_radiation,direct_radiation,diffuse_radiation,temperature_2m"
_WIND_VARS = "wind_speed_80m,wind_speed_120m,wind_direction_80m"
# Archive (ERA5) also carries the native 100 m level. The Open-Meteo archive
# leaves 80m/120m as null for recent dates (~last 2 months, ERA5T lag) but
# populates 100m, so request it too and let gen_forecast prefer the height that
# actually has data — otherwise recent-month retrocasts/calibration read 0 MW.
_WIND_VARS_ARCHIVE = "wind_speed_80m,wind_speed_100m,wind_speed_120m,wind_direction_80m"
# GEFS ensemble: 80m wind only (120m not in GEFS); solar shares same var names
_ENS_SOLAR_VARS = "shortwave_radiation"
_ENS_WIND_VARS = "wind_speed_80m,wind_direction_80m"
_ENS_MODEL = "gfs05"  # GFS ensemble: base + 30 members, 35-day horizon


def _cache_path(lat: float, lon: float, tech: str, past_days: int, forecast_days: int) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{lat:.4f}_{lon:.4f}_{tech}_p{past_days}_f{forecast_days}.json"


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def fetch(
    lat: float,
    lon: float,
    tech: str,
    *,
    past_days: int = 60,
    forecast_days: int = 16,
    cache_hours: float = 2.0,
) -> pd.DataFrame:
    """Return an hourly weather DataFrame for ``lat``/``lon``.

    Parameters
    ----------
    lat, lon:
        Plant coordinates.
    tech:
        ``"solar"`` or ``"wind"``.
    past_days:
        Days of history (for calibration against SCED history).
    forecast_days:
        Forward horizon; max 16 on the free Open-Meteo tier.
    cache_hours:
        File-cache TTL. 2 h by default — a Streamlit ``st.cache_data`` with
        ``ttl=7200`` on top gives the same protection at the app layer.

    Returns
    -------
    pd.DataFrame with UTC tz-aware DatetimeIndex ``time`` and tech-specific columns.

    Raises
    ------
    Exception (urllib/json): if the API is unreachable and no cache exists.
    """
    tech = tech.lower()
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")

    cpath = _cache_path(lat, lon, tech, past_days, forecast_days)
    if _is_fresh(cpath, cache_hours):
        raw = json.loads(cpath.read_text())
    else:
        hourly_vars = _SOLAR_VARS if tech == "solar" else _WIND_VARS
        params = (
            f"latitude={lat}&longitude={lon}"
            f"&hourly={hourly_vars}"
            f"&past_days={past_days}&forecast_days={forecast_days}"
            f"&timezone=UTC"
            f"&wind_speed_unit=ms"
        )
        url = f"{_BASE_URL}?{params}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = json.loads(resp.read())
        cpath.write_text(json.dumps(raw))

    h = raw["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    # Fill any gaps (API sometimes returns null for very recent hours)
    numeric_cols = df.select_dtypes("number").columns
    df[numeric_cols] = df[numeric_cols].fillna(0.0).clip(lower=0.0)
    return df


def fetch_archive(
    lat: float,
    lon: float,
    tech: str,
    start_date: str,
    end_date: str,
    *,
    cache_hours: float = 24.0,
) -> pd.DataFrame:
    """Fetch ERA5 reanalysis data from the Open-Meteo archive API.

    Unlike :func:`fetch`, this uses the archive endpoint which provides complete
    ERA5-backed shortwave radiation for any historical period.  The forecast
    endpoint's ``past_days`` parameter returns zeros for radiation beyond ~30 days,
    making it unusable for calibration against SCED history that lags ~60 days.

    Parameters
    ----------
    lat, lon:
        Plant coordinates.
    tech:
        ``"solar"`` or ``"wind"``.
    start_date, end_date:
        ISO date strings (``"YYYY-MM-DD"``).
    cache_hours:
        File-cache TTL.  Archive data is historical so 24 h is fine.

    Returns
    -------
    pd.DataFrame with UTC tz-aware DatetimeIndex and the same columns as
    :func:`fetch`.
    """
    tech = tech.lower()
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = _CACHE_DIR / f"{lat:.4f}_{lon:.4f}_{tech}_arch_{start_date}_{end_date}.json"
    if _is_fresh(cpath, cache_hours):
        raw = json.loads(cpath.read_text())
    else:
        hourly_vars = _SOLAR_VARS if tech == "solar" else _WIND_VARS_ARCHIVE
        params = (
            f"latitude={lat}&longitude={lon}"
            f"&hourly={hourly_vars}"
            f"&start_date={start_date}&end_date={end_date}"
            f"&timezone=UTC"
            f"&wind_speed_unit=ms"
        )
        url = f"{_ARCHIVE_URL}?{params}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = json.loads(resp.read())
        cpath.write_text(json.dumps(raw))

    h = raw["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    numeric_cols = df.select_dtypes("number").columns
    # Do NOT fill null wind-speed hours with 0 — the Open-Meteo archive returns
    # `null` for a highly variable fraction of hours at some grid points (seen up
    # to 90% for a month), and treating those as 0 m/s dead calm fabricates
    # multi-week zero-generation blocks and biases the level down. Leave wind
    # nulls as NaN so the daily aggregation can reconstruct from present hours.
    # Solar keeps the night-fill (null radiation ≈ 0 is physically fine).
    if tech == "solar":
        df[numeric_cols] = df[numeric_cols].fillna(0.0)
    df[numeric_cols] = df[numeric_cols].clip(lower=0.0)
    return df


def fetch_medium_range(
    lat: float,
    lon: float,
    tech: str,
    *,
    forecast_days: int = 35,
    cache_hours: float = 6.0,
) -> pd.DataFrame:
    """Fetch GEFS ensemble P50 forecast out to 35 days (vs 16-day standard limit).

    Uses Open-Meteo's ensemble API with the GEFS05 model (31 members).  Returns
    the ensemble mean so the DataFrame has the same column structure as
    :func:`fetch` and can be passed directly to ``gen_forecast`` functions.

    GEFS doesn't have 120 m wind, so ``wind_speed_120m`` is extrapolated from
    80 m using the 1/7 Hellmann power law so the calling code never sees a gap.

    Parameters
    ----------
    lat, lon:
        Plant coordinates.
    tech:
        ``"solar"`` or ``"wind"``.
    forecast_days:
        Forward horizon; max 35 for GEFS.
    cache_hours:
        File-cache TTL (6 h default — ensemble updates 4× daily).
    """
    tech = tech.lower()
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")

    forecast_days = min(forecast_days, 35)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = _CACHE_DIR / f"{lat:.4f}_{lon:.4f}_{tech}_ens_f{forecast_days}.json"
    if _is_fresh(cpath, cache_hours):
        raw = json.loads(cpath.read_text())
    else:
        hourly_vars = _ENS_SOLAR_VARS if tech == "solar" else _ENS_WIND_VARS
        params = (
            f"latitude={lat}&longitude={lon}"
            f"&hourly={hourly_vars}"
            f"&models={_ENS_MODEL}"
            f"&forecast_days={forecast_days}"
            f"&timezone=UTC"
            f"&wind_speed_unit=ms"
        )
        url = f"{_ENSEMBLE_URL}?{params}"
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = json.loads(resp.read())
        cpath.write_text(json.dumps(raw))

    h = raw["hourly"]
    times = pd.to_datetime(h["time"], utc=True)

    if tech == "solar":
        base_vars = ["shortwave_radiation"]
    else:
        base_vars = ["wind_speed_80m", "wind_direction_80m"]

    records: dict = {"time": times}
    for var in base_vars:
        # Collect base column (control run) + all perturbation members
        all_runs = []
        if var in h:
            all_runs.append(h[var])
        all_runs += [h[k] for k in h if k.startswith(var + "_member")]
        if all_runs:
            n = len(all_runs)
            mean_vals = [sum((r[i] or 0.0) for r in all_runs) / n for i in range(len(times))]
            records[var] = mean_vals

    df = pd.DataFrame(records).set_index("time")

    # Extrapolate 120 m wind from 80 m so gen_forecast sees the expected column
    if tech == "wind" and "wind_speed_80m" in df.columns:
        df["wind_speed_120m"] = df["wind_speed_80m"] * (120.0 / 80.0) ** (1.0 / 7.0)

    numeric_cols = df.select_dtypes("number").columns
    df[numeric_cols] = df[numeric_cols].fillna(0.0).clip(lower=0.0)

    # GEFS zeroes out the trailing partial day at the model boundary; drop it.
    # Group by Central local date so the dusk-UTC-hours don't make a zeroed
    # local day look non-zero.
    signal_col = "shortwave_radiation" if tech == "solar" else "wind_speed_80m"
    if signal_col in df.columns:
        local_dates = df.index.tz_convert("America/Chicago").date
        daily_sum = df[signal_col].groupby(local_dates).sum()
        last_good = daily_sum[daily_sum > 0].index[-1] if (daily_sum > 0).any() else None
        if last_good is not None:
            df = df[pd.Index(df.index.tz_convert("America/Chicago").date) <= last_good]

    return df
