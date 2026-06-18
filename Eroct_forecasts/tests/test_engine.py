"""Smoke tests for the price forecast engine. Run: python -m pytest tests/ -q
(or plain `python tests/test_engine.py`). Requires the ERCOT hub_prices lake."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import forecast
import heat_rate
import pf_history
import scenarios
import shape as shaping


def test_peak_share_matches_5x16():
    rt = pf_history.load_rt15("HB_NORTH")
    # 5x16 block = 80 of 168 weekly hours ≈ 0.476.
    assert abs(rt["is_peak"].mean() - 80 / 168) < 0.02


def test_heat_rate_median_robust_to_uri():
    rt = pf_history.load_rt15("HB_NORTH")
    b = heat_rate.buckets(rt)
    feb = b[(b["month"] == 2) & (b["block"] == "atc")].iloc[0]
    # Uri blows out the mean but not the median; tail lands in p90.
    assert feb["ihr_p50"] < 20
    assert feb["ihr_p90"] > feb["ihr_p50"] * 3


def test_scenarios_ordered():
    rng = np.random.default_rng(0)
    sims = scenarios.simulate_month(3.5, np.array([8, 9, 10, 11, 50.0]), 0.5,
                                    rng=rng, n=4000, gas_vol=0.5, price_cap=5000)
    s = scenarios.summarize(sims)
    assert s["p10"] < s["p50"] < s["p90"]
    assert s["std"] > 0


def test_full_forecast_runs():
    curve, meta = forecast.run("HB_NORTH", asof="2026-07-01", horizon_months=12, n_sims=2000)
    assert not curve.empty
    assert (curve["p10"] <= curve["p50"] + 1e-6).all()
    assert (curve["p50"] <= curve["p90"] + 1e-6).all()
    assert set(curve["block"]) == {"peak", "offpeak", "atc"}


def test_run_many_and_matrix():
    curve, metas = forecast.run_many(["HB_NORTH", "HB_HOUSTON"], asof="2026-07-01",
                                     horizon_months=12, n_sims=1000)
    assert set(curve["hub"]) == {"HB_NORTH", "HB_HOUSTON"}
    m = forecast.price_matrix(curve, block="atc", metric="p50")
    assert list(m.columns) == sorted(["HB_NORTH", "HB_HOUSTON"]) or set(m.columns) == {"HB_NORTH", "HB_HOUSTON"}
    assert m.shape[0] == 12


def test_8760_levels_match_strip():
    curve, _ = forecast.run("HB_NORTH", asof="2026-07-01", horizon_months=12, n_sims=2000)
    rt = pf_history.load_rt15("HB_NORTH")
    hourly = shaping.build_8760(curve, rt)
    assert len(hourly) > 8000
    # block hourly average should reconstruct the monthly block P50 within ~1%.
    h = hourly.copy()
    h["ms"] = h["ts"].values.astype("datetime64[M]")
    h["block"] = np.where(h["is_peak"], "peak", "offpeak")
    rebuilt = h.groupby(["ms", "block"])["p50"].mean().reset_index()
    cur = curve.assign(ms=pd.to_datetime(curve["month"]).values.astype("datetime64[M]"))
    m = rebuilt.merge(cur, on=["ms", "block"], suffixes=("_h", "_c"))
    rel = ((m["p50_h"] - m["p50_c"]).abs() / m["p50_c"]).max()
    assert rel < 0.01


def test_wind_capture_sane():
    import wind_revenue as wr

    runs = wr.list_wind_runs()
    if not runs:
        return  # no cached wind runs in this environment — skip
    shp, meta = wr.load_cf_shape(runs[0]["path"])
    assert 0 < meta["annual_cf"] < 0.7
    curve, _ = forecast.run("HB_NORTH", asof="2026-07-01", horizon_months=12, n_sims=1000)
    p8760 = shaping.build_8760(curve, pf_history.load_rt15("HB_NORTH"))
    mo = wr.capture(p8760, shp, meta["nameplate_mw"])
    ann = wr.annual(mo)
    # Capture is gen-weighted price: positive and within a sane band of ATC.
    # (Below for nocturnal West/Panhandle wind; can be ABOVE for coastal.)
    assert (ann["capture_p50"] > 0).all()
    assert ((ann["capture_p50"] / ann["atc_p50"]).between(0.5, 1.8)).all()
    assert (mo["gen_mwh"] > 0).all()


def test_backtest_runs_and_scores():
    import backtest

    df = backtest.run_backtest("HB_NORTH", asof_start="2024-01-01",
                               asof_step_months=6, horizon_months=6, n_sims=1000)
    assert not df.empty
    assert (df["p10"] <= df["p50"] + 1e-6).all() and (df["p50"] <= df["p90"] + 1e-6).all()
    s = backtest.summarize(df)
    ov = s["overall"]
    assert 0.0 <= ov["coverage80"] <= 1.0
    assert ov["mape_%"] >= 0
    assert 0.0 <= ov["pit_below50"] <= 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All tests passed.")
