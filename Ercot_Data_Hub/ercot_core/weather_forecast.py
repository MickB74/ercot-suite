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
_SOLAR_VARS = "shortwave_radiation,direct_radiation,diffuse_radiation,temperature_2m"
_WIND_VARS = "wind_speed_80m,wind_speed_120m,wind_direction_80m"


def _cache_path(lat: float, lon: float, tech: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{lat:.4f}_{lon:.4f}_{tech}.json"


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

    cpath = _cache_path(lat, lon, tech)
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
