"""Settlement engine DST safety — the fall-back hour must not collapse."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root

import pandas as pd  # noqa: E402

from ercot_core import settlement as S, tz  # noqa: E402


def _fallback_window():
    """The 2024-11-03 window incl the duplicated 01:00-02:00 hour (12 intervals)."""
    aware = pd.date_range("2024-11-03 00:00", periods=12, freq="15min", tz=tz.CENTRAL)
    naive = aware.tz_localize(None)
    flag = pd.Series([("Y" if (t.utcoffset() == pd.Timedelta("-6h") and t.hour == 1)
                       else "N") for t in aware]).to_numpy()
    return aware, naive, flag


def test_fallback_hour_not_collapsed():
    aware, naive, flag = _fallback_window()
    n = len(aware)
    gen = pd.DataFrame({"resource_node": ["ND"] * n, "resource_name": ["U1"] * n,
                        "interval_start": naive, "mw": [40.0] * n,
                        "repeated_hour_flag": flag})
    price = pd.DataFrame({"location": ["ND"] * n, "market": ["RT15"] * n,
                          "spp": [25.0] * n, "interval_start": naive, "dst_flag": flag})
    res = S.compute_settlement(gen, price, "ND", ppa_price=30.0, ref_location="ND",
                               market="RT15")
    d, s = res["intervals"], res["summary"]
    assert s["intervals"] == 12, "fall-back hour collapsed (should keep 12 intervals)"
    assert abs(s["total_mwh"] - 12 * 40 * 0.25) < 1e-9
    dup = d[d["interval_start"] == pd.Timestamp("2024-11-03 01:00")]
    assert len(dup) == 2, "both passes of naive 01:00 must survive"
    assert d["interval_start"].dt.tz is None, "returned frame should be naive Central"


def test_normal_rt15():
    naive = pd.date_range("2024-06-01 00:00", periods=8, freq="15min")
    gen = pd.DataFrame({"resource_node": ["ND"] * 8, "resource_name": ["U1"] * 8,
                        "interval_start": naive, "mw": [100.0] * 8})
    price = pd.DataFrame({"location": ["ND"] * 8, "market": ["RT15"] * 8,
                          "spp": [20.0] * 8, "interval_start": naive})
    s = S.compute_settlement(gen, price, "ND", 30.0, "ND", market="RT15")["summary"]
    assert s["intervals"] == 8
    assert abs(s["total_mwh"] - 200.0) < 1e-9          # 8 × 100 MW × 0.25 h
    assert abs(s["merchant_revenue"] - 4000.0) < 1e-9  # 200 MWh × $20


def test_dam_hourly_broadcast():
    naive = pd.date_range("2024-06-01 00:00", periods=8, freq="15min")  # 2 hours
    gen = pd.DataFrame({"resource_node": ["ND"] * 8, "resource_name": ["U1"] * 8,
                        "interval_start": naive, "mw": [100.0] * 8})
    dam = pd.DataFrame({"location": ["ND", "ND"], "market": ["DAM", "DAM"],
                        "spp": [18.0, 22.0],
                        "interval_start": pd.date_range("2024-06-01 00:00", periods=2, freq="h")})
    s = S.compute_settlement(gen, dam, "ND", 30.0, "ND", market="DAM")["summary"]
    assert s["intervals"] == 8
    # 4 intervals × 25 MWh × $18  +  4 × 25 × $22
    assert abs(s["merchant_revenue"] - (4 * 25 * 18 + 4 * 25 * 22)) < 1e-9


def _frame_with_one_negative():
    # 4 intervals, the 3rd at a negative price (-$10), 100 MW (=25 MWh) each.
    naive = pd.date_range("2024-06-01 00:00", periods=4, freq="15min")
    gen = pd.DataFrame({"resource_node": ["ND"] * 4, "resource_name": ["U1"] * 4,
                        "interval_start": naive, "mw": [100.0] * 4})
    price = pd.DataFrame({"location": ["ND"] * 4, "market": ["RT15"] * 4,
                          "spp": [20.0, 20.0, -10.0, 20.0], "interval_start": naive})
    return gen, price


def test_floor_default_no_settlement_below():
    gen, price = _frame_with_one_negative()
    s = S.compute_settlement(gen, price, "ND", 30.0, "ND")["summary"]   # default $0 / no-settle
    assert s["intervals"] == 3, "the negative-price interval should be dropped"
    assert s["excluded_intervals"] == 1
    assert abs(s["excluded_mwh"] - 25.0) < 1e-9
    assert abs(s["total_mwh"] - 75.0) < 1e-9                            # 3 × 25 MWh


def test_floor_settle_below_clips():
    gen, price = _frame_with_one_negative()
    s = S.compute_settlement(gen, price, "ND", 30.0, "ND",
                             price_floor=0.0, settle_below_floor=True)["summary"]
    assert s["intervals"] == 4 and s["excluded_intervals"] == 0
    assert s["floored_intervals"] == 1
    # merchant on the negative interval is floored to $0 (not negative)
    assert abs(s["merchant_revenue"] - (3 * 25 * 20 + 25 * 0)) < 1e-9


def test_no_floor_settles_negatives():
    gen, price = _frame_with_one_negative()
    s = S.compute_settlement(gen, price, "ND", 30.0, "ND", price_floor=None)["summary"]
    assert s["intervals"] == 4
    # negative interval settles at -$10: 3×25×20 + 25×(-10)
    assert abs(s["merchant_revenue"] - (3 * 25 * 20 + 25 * -10)) < 1e-9


if __name__ == "__main__":
    from _run import main
    main(globals())
