"""Plant capture-price valuation tests — pure math, no network/pvlib."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from ercot_core import plant_value as PV  # noqa: E402


# --- helpers ---------------------------------------------------------------

def _price_year(year: int, p50_by_hour) -> pd.DataFrame:
    """A synthetic build_8760-shaped frame: one year, hourly, price = f(hour)."""
    idx = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left")
    p50 = np.array([p50_by_hour[h] for h in idx.hour], dtype=float)
    return pd.DataFrame({
        "ts": idx,
        "month": idx.month,
        "is_peak": (idx.dayofweek < 5) & (idx.hour >= 6) & (idx.hour < 22),
        "p10": p50 * 0.5,
        "p50": p50,
        "p90": p50 * 1.8,
    })


def _gen_year(ac_by_hour, year: int = 2027) -> pd.DataFrame:
    """PVWatts-shaped frame: DatetimeIndex + ac_kw = f(hour)."""
    idx = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left")
    ac = np.array([ac_by_hour[h] for h in idx.hour], dtype=float)
    return pd.DataFrame({"ac_kw": ac}, index=idx)


# --- hub mapping -----------------------------------------------------------

def test_to_hub_code():
    assert PV.to_hub_code("North") == "HB_NORTH"
    assert PV.to_hub_code("houston") == "HB_HOUSTON"
    assert PV.to_hub_code("WEST") == "HB_WEST"
    assert PV.to_hub_code("HB_SOUTH") == "HB_SOUTH"  # already a code
    try:
        PV.to_hub_code("Panhandle")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown hub")


# --- system config ---------------------------------------------------------

def test_system_config_from_asset():
    cfg = PV.system_config_from_asset({
        "resource_name": "X_SLR", "capacity_mw": 161.0, "hub": "North",
        "lat": 33.1, "lon": -99.2, "tracking_type": "single_axis",
        "dc_ac_ratio": 1.45, "solar_gcr": 0.28,
    })
    assert cfg.capacity_kw_dc == 161000.0
    assert cfg.array_type == "1-Axis Tracker"
    assert abs(cfg.dc_ac_ratio - 1.45) < 1e-9
    assert abs(cfg.gcr - 0.28) < 1e-9
    # No tracking field → fixed tilt, default ratio/gcr.
    fixed = PV.system_config_from_asset({"resource_name": "Y", "capacity_mw": 10.0,
                                         "hub": "South", "lat": 30, "lon": -98})
    assert fixed.array_type == "Fixed - Open Rack"


# --- capture math ----------------------------------------------------------

def test_flat_generation_captures_atc():
    # Price varies by hour (cheap midday, dear evening); generation is flat.
    price_curve = {h: (20.0 if 9 <= h <= 16 else 55.0) for h in range(24)}
    price = _price_year(2027, price_curve)
    gen = _gen_year({h: 100.0 for h in range(24)})  # flat 100 kW every hour
    out = PV.capture_by_year(gen, price)
    row = out[out["year"] == 2027].iloc[0]
    # Flat output earns exactly the all-hours (ATC) average.
    assert abs(row["capture_p50"] - row["atc_p50"]) < 1e-6
    assert abs(row["capture_ratio"] - 1.0) < 1e-6


def test_midday_solar_captures_below_atc():
    # Cheap midday, dear nights; generation only midday → capture < ATC.
    price_curve = {h: (20.0 if 9 <= h <= 16 else 60.0) for h in range(24)}
    price = _price_year(2027, price_curve)
    gen = _gen_year({h: (500.0 if 9 <= h <= 16 else 0.0) for h in range(24)})
    out = PV.capture_by_year(gen, price)
    row = out[out["year"] == 2027].iloc[0]
    assert row["capture_p50"] < row["atc_p50"]      # solar capture discount
    assert abs(row["capture_p50"] - 20.0) < 1e-6     # earns only the cheap hours
    assert row["capture_ratio"] < 1.0
    # Revenue is internally consistent: capture × generation.
    assert abs(row["revenue_p50"] - row["capture_p50"] * row["gen_mwh"]) < 1e-3
    # Bands ordered p10 < p50 < p90.
    assert row["capture_p10"] < row["capture_p50"] < row["capture_p90"]


def test_multiple_years_each_summarized():
    price = pd.concat([_price_year(2027, {h: 40.0 for h in range(24)}),
                       _price_year(2028, {h: 50.0 for h in range(24)})],
                      ignore_index=True)
    gen = _gen_year({h: (300.0 if 8 <= h <= 17 else 0.0) for h in range(24)})
    out = PV.capture_by_year(gen, price)
    assert list(out["year"]) == [2027, 2028]
    # Flat-price years → capture equals that year's price level.
    assert abs(out.loc[out.year == 2027, "capture_p50"].iloc[0] - 40.0) < 1e-6
    assert abs(out.loc[out.year == 2028, "capture_p50"].iloc[0] - 50.0) < 1e-6


def test_capture_by_month_rows_and_consistency():
    # Two months of flat-by-hour price at different levels; midday-only gen.
    jan = _price_year(2027, {h: 40.0 for h in range(24)})
    jan = jan[jan["month"] == 1]
    price = jan.copy()
    gen = _gen_year({h: (300.0 if 8 <= h <= 17 else 0.0) for h in range(24)})
    out = PV.capture_by_month(gen, price)
    assert list(out.columns[:2]) == ["year", "month"]
    row = out.iloc[0]
    assert row["year"] == 2027 and row["month"] == 1
    assert abs(row["capture_p50"] - 40.0) < 1e-6           # flat price → capture == level
    # Monthly generation totals reconcile with the yearly roll-up.
    full = _price_year(2027, {h: 40.0 for h in range(24)})
    bym = PV.capture_by_month(gen, full)
    byy = PV.capture_by_year(gen, full)
    assert abs(bym["gen_mwh"].sum() - byy["gen_mwh"].iloc[0]) < 1e-3


def test_add_net_settlement_sign_and_value():
    price = _price_year(2027, {h: 40.0 for h in range(24)})
    gen = _gen_year({h: (300.0 if 8 <= h <= 17 else 0.0) for h in range(24)})
    by = PV.capture_by_year(gen, price)              # capture_p50 == 40
    # Offtaker perspective: net = gen × (capture − strike).
    # Strike above capture → offtaker pays (negative).
    s = PV.add_net_settlement(by, strike=50.0).iloc[0]
    assert s["net_settlement"] < 0
    assert abs(s["net_settlement"] - s["gen_mwh"] * (40.0 - 50.0)) < 1e-3
    assert abs(s["contracted_mwh"] - by["gen_mwh"].iloc[0]) < 1e-3   # full offtake
    # Strike below capture → offtaker receives (positive).
    s2 = PV.add_net_settlement(by, strike=30.0).iloc[0]
    assert s2["net_settlement"] > 0


def test_partial_offtake_scales_settlement():
    price = _price_year(2027, {h: 40.0 for h in range(24)})
    gen = _gen_year({h: (300.0 if 8 <= h <= 17 else 0.0) for h in range(24)})
    by = PV.capture_by_year(gen, price)
    full = PV.add_net_settlement(by, strike=55.0, share=1.0).iloc[0]
    half = PV.add_net_settlement(by, strike=55.0, share=0.5).iloc[0]
    assert abs(half["contracted_mwh"] - 0.5 * full["contracted_mwh"]) < 1e-6
    assert abs(half["net_settlement"] - 0.5 * full["net_settlement"]) < 1e-3


if __name__ == "__main__":
    from _run import main
    main(globals())
