"""fetch_archive must NOT turn Open-Meteo `null` wind hours into 0 m/s calm
(the 2026-07 root-cause bug). Hermetic: writes a fixture into the file cache so
no network call is made.
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

from ercot_core import weather_forecast as wf


class _FakeResp:
    def __init__(self, body: bytes):
        self._b = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _hourly(n_present: int, n_null: int) -> dict:
    """A raw archive payload with `n_present` real 100m hours + `n_null` nulls."""
    total = n_present + n_null
    times = [f"2026-06-01T{h:02d}:00" for h in range(total)]
    ws = [7.0] * n_present + [None] * n_null
    return {"hourly": {"time": times, "wind_speed_100m": ws}}


def _seed_cache(monkeypatch, tmp_path, lat, lon, tech, start, end, hourly):
    """Pre-write a fresh cache file so fetch_archive reads the fixture, not the API."""
    monkeypatch.setattr(wf, "_CACHE_DIR", tmp_path)
    cpath = tmp_path / f"{lat:.4f}_{lon:.4f}_{tech}_arch_{start}_{end}.json"
    cpath.write_text(json.dumps({"hourly": hourly}))
    return cpath


def test_wind_nulls_become_nan_not_zero(monkeypatch, tmp_path):
    # 10 hours, 1 null → 90% coverage, so the cache is trusted (no refetch) and we
    # test only the null-handling parse path.
    hourly = {
        "time": [f"2025-08-01T{h:02d}:00" for h in range(10)],
        "wind_speed_100m": [7.5, None, 6.0, 7.0, 8.0, 6.5, 7.2, 6.8, 7.1, 6.9],
        "wind_speed_80m": [None] * 10,
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


# ── coverage metric + flaky-partial cache robustness ──────────────────────────

def test_archive_coverage_metric():
    assert wf._archive_coverage(_hourly(24, 0), "wind") == 1.0
    assert wf._archive_coverage(_hourly(12, 12), "wind") == 0.5
    assert wf._archive_coverage(_hourly(0, 24), "wind") == 0.0
    assert wf._archive_coverage(_hourly(3, 21), "solar") == 1.0     # solar always OK


def test_fresh_but_partial_wind_cache_is_refetched(monkeypatch, tmp_path):
    """A fresh cache that's mostly null must NOT be trusted — refetch instead."""
    monkeypatch.setattr(wf.time, "sleep", lambda *a: None)
    # seed a fresh but 42%-covered cache (the Azure Sky June bug)
    _seed_cache(monkeypatch, tmp_path, 33.15, -99.28, "wind",
                "2026-06-01", "2026-06-30", _hourly(10, 14)["hourly"])
    # the live API now serves a complete window
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=15: _FakeResp(json.dumps(_hourly(24, 0)).encode()))
    df = wf.fetch_archive(33.15, -99.28, "wind", "2026-06-01", "2026-06-30")
    assert df["wind_speed_100m"].notna().mean() == 1.0      # refetched full coverage


def test_partial_fetch_does_not_clobber_good_cache(monkeypatch, tmp_path):
    """A transient partial response must not overwrite good cached data."""
    monkeypatch.setattr(wf.time, "sleep", lambda *a: None)
    _seed_cache(monkeypatch, tmp_path, 33.15, -99.28, "wind",
                "2026-06-01", "2026-06-30", _hourly(24, 0)["hourly"])   # good cache on disk
    # force the fetch branch (cache_hours=0), but the API returns a flaky partial
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=15: _FakeResp(json.dumps(_hourly(4, 20)).encode()))
    df = wf.fetch_archive(33.15, -99.28, "wind", "2026-06-01", "2026-06-30", cache_hours=0)
    assert df["wind_speed_100m"].notna().mean() == 1.0      # kept the good cache


# ── live forecast endpoints: same wind-null rule as the archive ───────────────

def test_fetch_forecast_wind_nulls_stay_nan(monkeypatch):
    monkeypatch.setattr(wf, "_is_fresh", lambda *a, **k: False)   # force a fetch
    payload = {"hourly": {
        "time": ["2026-07-01T00:00", "2026-07-01T01:00", "2026-07-01T02:00"],
        "wind_speed_80m": [7.0, None, 8.0],
        "wind_direction_80m": [180, None, 200],
    }}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=15: _FakeResp(json.dumps(payload).encode()))
    df = wf.fetch(33.15, -99.28, "wind", past_days=1, forecast_days=1)
    assert pd.isna(df["wind_speed_80m"].iloc[1])            # null hour NOT fabricated calm
    assert df["wind_speed_80m"].iloc[0] == 7.0


def test_fetch_forecast_solar_nulls_filled(monkeypatch):
    monkeypatch.setattr(wf, "_is_fresh", lambda *a, **k: False)
    payload = {"hourly": {
        "time": ["2026-07-01T00:00", "2026-07-01T01:00", "2026-07-01T02:00"],
        "shortwave_radiation": [0.0, None, 500.0],
    }}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=15: _FakeResp(json.dumps(payload).encode()))
    df = wf.fetch(33.15, -99.28, "solar", past_days=1, forecast_days=1)
    assert df["shortwave_radiation"].iloc[1] == 0.0         # night-fill preserved


def test_medium_range_excludes_null_members(monkeypatch):
    """An hour with no present ensemble member stays NaN, not coalesced to 0."""
    monkeypatch.setattr(wf, "_is_fresh", lambda *a, **k: False)
    monkeypatch.setattr(wf.time, "sleep", lambda *a: None)
    payload = {"hourly": {
        "time": ["2026-07-01T00:00", "2026-07-01T01:00", "2026-07-01T02:00"],
        "wind_speed_80m": [6.0, None, 8.0],
        "wind_speed_80m_member01": [8.0, None, 10.0],
        "wind_direction_80m": [180, 190, 200],
    }}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=20: _FakeResp(json.dumps(payload).encode()))
    df = wf.fetch_medium_range(33.15, -99.28, "wind", forecast_days=1)
    assert df["wind_speed_80m"].iloc[0] == 7.0              # mean of 6 and 8, present members
    assert pd.isna(df["wind_speed_80m"].iloc[1])            # all members null → NaN, not 0
