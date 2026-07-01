"""Pure-math tests for the wind generation model — the layer where the
2026-07 ERA5 fabricated-calm bug lived. No network, no data lake.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ercot_core import gen_forecast as gf


def _central_hours(start: str, days: int, mw: float = 100.0) -> pd.Series:
    """A UTC-indexed hourly MW series whose Central-local days are fully covered."""
    idx = pd.date_range(start, periods=24 * days, freq="h",
                         tz="America/Chicago").tz_convert("UTC")
    return pd.Series([mw] * len(idx), index=idx)


# ── _daily_from_hourly: coverage-aware daily aggregation ──────────────────────

def test_full_coverage_day_equals_plain_sum():
    """A fully-covered day must equal a naive daily sum (no solar regression)."""
    s = _central_hours("2025-06-01", 4, mw=100.0)
    daily = gf._daily_from_hourly(s)
    # interior days are fully covered → 100 MW × 24 h
    full = daily.dropna()
    assert (abs(full - 2400.0) < 1e-6).all()


def test_missing_hours_are_not_treated_as_calm():
    """Null hours must NOT drag a day down to ~0 — the core of the fixed bug.

    A day with 12 present hours at 100 MW and 12 null hours should read
    ``mean(present) × 24 = 2400`` MWh, not ``sum = 1200``.
    """
    s = _central_hours("2025-06-01", 3, mw=100.0)
    # null out the second Central day's first 12 hours
    day2 = pd.Timestamp("2025-06-02", tz="America/Chicago")
    mask = (s.index.tz_convert("America/Chicago") >= day2) & \
           (s.index.tz_convert("America/Chicago") < day2 + pd.Timedelta(hours=12))
    s[mask] = np.nan
    daily = gf._daily_from_hourly(s)
    assert abs(daily[pd.Timestamp("2025-06-02").date()] - 2400.0) < 1e-6


def test_low_coverage_day_is_missing_not_zero():
    """A day with fewer than ``min_hours`` present is NaN (unknown), never 0."""
    s = _central_hours("2025-06-01", 3, mw=100.0)
    day2 = pd.Timestamp("2025-06-02", tz="America/Chicago")
    keep = (s.index.tz_convert("America/Chicago") >= day2) & \
           (s.index.tz_convert("America/Chicago") < day2 + pd.Timedelta(hours=3))
    inday2 = (s.index.tz_convert("America/Chicago") >= day2) & \
             (s.index.tz_convert("America/Chicago") < day2 + pd.Timedelta(hours=24))
    s[inday2 & ~keep] = np.nan          # only 3 present hours < min_hours(6)
    daily = gf._daily_from_hourly(s, min_hours=6)
    assert pd.isna(daily[pd.Timestamp("2025-06-02").date()])


# ── _cap_fill: water-filling with a nameplate ceiling ─────────────────────────

def test_cap_fill_conserves_total_when_feasible():
    alloc = gf._cap_fill({1: 1.0, 2: 2.0, 3: 3.0}, total=300.0, cap=200.0)
    assert abs(sum(alloc.values()) - 300.0) < 1e-6


def test_cap_fill_never_exceeds_cap():
    # weight wants everything on day 3, but the cap forces a spill
    alloc = gf._cap_fill({1: 0.0, 2: 0.0, 3: 1.0}, total=300.0, cap=200.0)
    assert all(v <= 200.0 + 1e-6 for v in alloc.values())
    assert abs(sum(alloc.values()) - 300.0) < 1e-6
    assert abs(alloc[3] - 200.0) < 1e-6          # capped
    assert abs(alloc[1] - 50.0) < 1e-6           # overflow spread equally to zero-weight days
    assert abs(alloc[2] - 50.0) < 1e-6


def test_cap_fill_infeasible_pins_all_at_cap():
    alloc = gf._cap_fill({1: 1.0, 2: 1.0}, total=1000.0, cap=200.0)
    assert all(abs(v - 200.0) < 1e-6 for v in alloc.values())


# ── _power_curve: cut-in / rated / cut-out behaviour ──────────────────────────

def test_power_curve_crisp_cubic_boundaries():
    """With smoothing off, the cubic curve has exact boundary behaviour."""
    ws = pd.Series([0.0, 2.0, 12.0, 18.0, 30.0])   # calm, <cut-in, rated, high, >cut-out
    cap = 197.0
    mw = gf._power_curve(ws, cap, cut_in=3.0, rated=12.0, cut_out=25.0, farm_sigma=0)
    assert mw.iloc[0] == 0.0                        # 0 m/s
    assert mw.iloc[1] == 0.0                        # below cut-in
    assert abs(mw.iloc[2] - cap) < 1e-6             # at rated → nameplate
    assert abs(mw.iloc[3] - cap) < 1e-6             # above rated → nameplate
    assert mw.iloc[4] == 0.0                        # above cut-out → 0


def test_power_curve_bounded_and_monotonic_below_rated():
    ws = pd.Series(np.arange(3.0, 12.0, 0.5))
    cap = 197.0
    mw = gf._power_curve(ws, cap, farm_sigma=0).to_numpy()
    assert (mw >= -1e-9).all() and (mw <= cap + 1e-9).all()
    assert np.all(np.diff(mw) >= -1e-9)             # non-decreasing cut-in → rated


# ── _wind_hourly_mw: null wind must stay NaN, not become 0 m/s calm ───────────

def test_wind_hourly_keeps_nulls_as_nan():
    idx = pd.date_range("2025-08-01", periods=6, freq="h", tz="UTC")
    df = pd.DataFrame({"wind_speed_100m": [8.0, np.nan, 8.0, np.nan, 8.0, 8.0]}, index=idx)
    hourly = gf._wind_hourly_mw(df, capacity_mw=197.0, hub_height_m=100.0, cal_factor=1.0)
    # the two null input hours must be NaN in the output, not fabricated 0-power calm
    assert hourly.isna().sum() == 2
    assert hourly.iloc[0] > 0                       # a real 8 m/s hour generates
