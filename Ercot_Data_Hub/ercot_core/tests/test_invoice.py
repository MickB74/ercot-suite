"""Invoice reconciliation harness tests."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root

import pandas as pd  # noqa: E402

from ercot_core import invoice as INV, tz  # noqa: E402


def test_suggest_mapping_basic():
    m = INV.suggest_mapping(["Interval Ending", "Settlement Point", "SPP $/MWh",
                             "MWh", "Amount", "DSTFlag"])
    assert m["time_col"] == "Interval Ending"
    assert m["time_basis"] == "ending"
    assert m["price_col"] == "SPP $/MWh"
    assert m["volume_col"] == "MWh"
    assert m["amount_col"] == "Amount"
    assert m["dst_flag_col"] == "DSTFlag"


def test_price_errors_and_missing_interval():
    n = 10
    istart = pd.date_range("2024-06-01 00:00", periods=n, freq="15min")
    price_df = pd.DataFrame({"location": ["HB"] * n, "market": ["RT15"] * n,
                             "spp": [25.0] * n, "interval_start": istart})
    inv_price = [25.0] * n
    for i in (2, 5, 7):
        inv_price[i] = 30.0                       # 3 seeded price errors
    raw = pd.DataFrame({
        "Interval Ending": istart + pd.Timedelta(minutes=15),
        "Settlement Point": ["HB"] * n,
        "Price $/MWh": inv_price,
        "MWh": [10.0] * n,
        "Amount": [p * 10 for p in inv_price],
    }).drop(index=8).reset_index(drop=True)        # drop a MIDDLE interval -> 1 missing
    # (a trailing gap past the last billed interval is intentionally NOT flagged;
    #  missing-interval detection is bounded to the invoice's covered window.)

    inv = INV.load_invoice(raw, INV.suggest_mapping(raw.columns))
    res = INV.reconcile(inv, price_df=price_df, location="HB", market="RT15",
                        abs_tol=0.01, rel_tol=0.005)
    c = res["summary"]["status_counts"]
    assert c.get("price_mismatch") == 3
    assert c.get("missing_in_invoice") == 1
    assert c.get("match") == 6
    assert abs(res["summary"]["variance"] - 150.0) < 1e-9   # 3 × $5 × 10 MWh


def test_fallback_day_invoice_matches_one_to_one():
    aware = pd.date_range("2024-11-03 00:00", periods=12, freq="15min", tz=tz.CENTRAL)
    naive = aware.tz_localize(None)
    flag = pd.Series([("Y" if (t.utcoffset() == pd.Timedelta("-6h") and t.hour == 1)
                       else "N") for t in aware]).to_numpy()
    n = len(aware)
    price_df = pd.DataFrame({"location": ["HB"] * n, "market": ["RT15"] * n,
                             "spp": [20.0] * n, "interval_start": naive, "dst_flag": flag})
    raw = pd.DataFrame({"Interval Start": naive, "DSTFlag": flag,
                        "SettlementPoint": ["HB"] * n, "SPP": [20.0] * n,
                        "MWh": [4.0] * n, "Amount": [80.0] * n})
    inv = INV.load_invoice(raw, INV.suggest_mapping(raw.columns))
    res = INV.reconcile(inv, price_df=price_df, location="HB", market="RT15")
    assert res["summary"]["intervals"] == 12
    assert res["summary"]["status_counts"] == {"match": 12}


def test_metered_volume_mismatch():
    n = 4
    istart = pd.date_range("2024-06-01 00:00", periods=n, freq="15min")
    price_df = pd.DataFrame({"location": ["ND"] * n, "market": ["RT15"] * n,
                             "spp": [50.0] * n, "interval_start": istart})
    gen_df = pd.DataFrame({"resource_node": ["ND"] * n, "resource_name": ["U1"] * n,
                           "interval_start": istart, "mw": [40.0] * n})  # 10 MWh/intvl
    raw = pd.DataFrame({"Interval Start": istart, "Node": ["ND"] * n,
                        "MWh": [10.0, 10.0, 12.0, 10.0],   # one wrong quantity
                        "Amount": [500.0] * n})
    m = INV.suggest_mapping(raw.columns)
    m["time_basis"] = "beginning"
    inv = INV.load_invoice(raw, m)
    res = INV.reconcile(inv, price_df=price_df, gen_df=gen_df, location="ND",
                        market="RT15", volume_source="metered", resource_node="ND",
                        abs_tol=0.01, rel_tol=0.005)
    c = res["summary"]["status_counts"]
    assert c.get("volume_mismatch") == 1
    assert c.get("match") == 3


if __name__ == "__main__":
    from _run import main
    main(globals())
