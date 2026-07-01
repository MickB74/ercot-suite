"""Smoke tests for the price forecast engine. Run: python -m pytest tests/ -q
(or plain `python tests/test_engine.py`). Requires the ERCOT hub_prices lake."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import forecast
import gas_curve
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
    # Feb 2021 (Uri) carries $9000+ raw prices, but the *implied* heat rate
    # normalizes by gas — which co-spiked during Uri — so the Feb median stays
    # sane and the distribution is not blown out. That stability is the point
    # of a median-anchored model.
    assert feb["ihr_p50"] < 20
    assert feb["ihr_p10"] < feb["ihr_p50"] < feb["ihr_p90"]
    assert feb["ihr_p90"] < feb["ihr_p50"] * 2


def test_backtest_gas_modes():
    import backtest
    # A small walk-forward: perfect vs live-like (persistence) gas.
    kw = dict(asof_start="2024-01-01", asof_step_months=6, horizon_months=6, n_sims=300)
    perfect = backtest.run_backtest("HB_NORTH", gas_mode="perfect", **kw)
    live = backtest.run_backtest("HB_NORTH", gas_mode="persistence", **kw)
    assert not perfect.empty and not live.empty
    # Perfect mode uses realized gas; persistence uses a naive forward.
    assert (perfect["gas_hat"] == perfect["gas_real"]).all()
    assert (live["gas_hat"] != live["gas_real"]).any()
    mp, ml = backtest._metrics(perfect), backtest._metrics(live)
    # Both produce ordered bands and finite error; gas error only adds noise, so
    # live MAE should be >= perfect MAE (never better than knowing gas exactly).
    assert mp["mae_$"] > 0 and ml["mae_$"] >= mp["mae_$"] - 1e-6
    assert 0.0 <= mp["coverage80"] <= 1.0


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


def test_realized_gas_vol_sane():
    import public_forecasts as pf
    v = pf.realized_gas_vol()
    # Always a finite float in the clamped band, even with no daily cache.
    assert 0.2 <= v <= 1.2


def test_scarcity_multiplier_monotonic():
    import public_forecasts as pf
    # At/above target → no boost; tighter margin → strictly larger boost.
    assert pf.scarcity_multiplier(20) == 1.0
    assert pf.scarcity_multiplier(15) == 1.0
    assert pf.scarcity_multiplier(None) == 1.0
    boosts = [pf.scarcity_multiplier(m) for m in (14, 12, 10, 6)]
    assert all(b >= 1.0 for b in boosts)
    assert boosts[0] < boosts[1] < boosts[2]   # ramps up as margin falls
    assert boosts[-1] >= boosts[-2]            # saturates below the knee


def test_tail_boost_preserves_median_widens_p90():
    rng = np.random.default_rng(0)
    samples = np.array([8, 9, 10, 11, 12, 40.0])
    base = scenarios._lognorm_from_samples(samples, np.random.default_rng(1), 20000)
    boosted = scenarios._lognorm_from_samples(samples, np.random.default_rng(1),
                                              20000, tail_boost=1.6)
    # Median essentially unchanged; far-upper tail strictly fatter.
    assert abs(np.median(boosted) - np.median(base)) / np.median(base) < 0.03
    assert np.percentile(boosted, 95) > np.percentile(base, 95)


def test_aeo_anchor_or_graceful():
    import public_forecasts as pf
    a = pf.aeo_anchor_for(pd.Timestamp("2032-01-01"))
    # Either a cached/fetched (level, label) or None offline — never raises.
    assert a is None or (a[0] > 0 and isinstance(a[1], str))


def test_gas_blend_far_tail_reverts():
    # 60-month strip should always span the horizon and stay positive.
    strip, src = gas_curve.forward_strip(pd.Timestamp("2026-06-01"), 60, aeo_weight=0.25)
    assert len(strip) == 60
    assert (strip["gas"] > 0).all()
    assert "mean-reversion" in src


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
