"""fetch_archive must NOT turn Open-Meteo `null` wind hours into 0 m/s calm
(the 2026-07 root-cause bug). Hermetic: writes a fixture into the file cache so
no network call is made.
"""
from __future__ import annotations

import json

import pandas as pd

from ercot_core import weather_forecast as wf


def _seed_cache(monkeypatch, tmp_path, lat, lon, tech, start, end, hourly):
    """Pre-write a fresh cache file so fetch_archive reads the fixture, not the API."""
    monkeypatch.setattr(wf, "_CACHE_DIR", tmp_path)
    cpath = tmp_path / f"{lat:.4f}_{lon:.4f}_{tech}_arch_{start}_{end}.json"
    cpath.write_text(json.dumps({"hourly": hourly}))
    return cpath


def test_wind_nulls_become_nan_not_zero(monkeypatch, tmp_path):
    hourly = {
        "time": ["2025-08-01T00:00", "2025-08-01T01:00", "2025-08-01T02:00"],
        "wind_speed_100m": [7.5, None, 6.0],
        "wind_speed_80m": [None, None, None],
    }
    _seed_cache(monkeypatch, tmp_path, 31.4, -98.4, "wind",
                "2025-08-01", "2025-08-01", hourly)
    df = wf.fetch_archive(31.4, -98.4, "wind", "2025-08-01", "2025-08-01")
    col = df["wind_speed_100m"]
    assert pd.isna(col.iloc[1])          # the null hour is NaN…
    assert col.iloc[1] != 0.0            # …NOT fabricated dead calm
    assert col.iloc[0] == 7.5


def test_solar_nulls_still_filled_zero(monkeypatch, tmp_path):
    """Solar keeps the night-fill: null radiation ≈ 0 is physically fine."""
    hourly = {
        "time": ["2025-08-01T00:00", "2025-08-01T01:00", "2025-08-01T02:00"],
        "shortwave_radiation": [0.0, None, 500.0],
    }
    _seed_cache(monkeypatch, tmp_path, 31.4, -98.4, "solar",
                "2025-08-01", "2025-08-01", hourly)
    df = wf.fetch_archive(31.4, -98.4, "solar", "2025-08-01", "2025-08-01")
    assert df["shortwave_radiation"].iloc[1] == 0.0
