"""Timezone helper tests — the DST edge cases that make ERCOT settlement hard."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root

import pandas as pd  # noqa: E402

from ercot_core import tz  # noqa: E402


def _dst_day(day: str, periods: int = 110):
    """A real Central day as (naive labels, ERCOT-style repeated-hour flag)."""
    aware = pd.date_range(f"{day} 00:00", periods=periods, freq="15min", tz=tz.CENTRAL)
    aware = aware[[str(t.date()) == day for t in aware]]
    naive = pd.Series(aware.tz_localize(None))
    repeated = pd.Series([(t.utcoffset() == pd.Timedelta("-6h") and t.hour == 1)
                          for t in aware])              # 2nd (CST) pass of the fall-back hour
    return naive, repeated.map({True: "Y", False: "N"}), aware


def test_fall_back_day_has_100_intervals():
    naive, flag, _ = _dst_day("2024-11-03")
    aware = tz.localize_central(naive, flags=flag)
    assert aware.dt.tz_convert("UTC").nunique() == 100   # 25 h × 4
    assert str(aware.dt.tz) == "US/Central"


def test_spring_forward_day_has_92_intervals():
    naive, flag, _ = _dst_day("2024-03-10")
    aware = tz.localize_central(naive, flags=flag)
    assert aware.dt.tz_convert("UTC").nunique() == 92    # 23 h × 4


def test_nonexistent_time_shifts_forward():
    s = pd.Series(pd.to_datetime(["2024-03-10 02:30"]))  # this wall-clock never happens
    out = tz.localize_central(s)
    assert out.dt.tz is not None
    assert str(out.iloc[0]) == "2024-03-10 03:00:00-05:00"


def test_single_pass_label_does_not_raise():
    # A lone 01:30 on the fall-back day is genuinely ambiguous; must not raise.
    s = pd.Series(pd.to_datetime(["2024-11-03 00:00", "2024-11-03 01:30",
                                  "2024-11-03 03:00"]))
    out = tz.localize_central(s)
    assert out.notna().all()


def test_naive_roundtrip():
    naive = pd.Series(pd.to_datetime(["2024-06-01 12:00", "2024-01-15 03:45"]))
    back = tz.to_naive_central(tz.localize_central(naive))
    assert list(back) == list(naive)


def test_to_utc_known_value():
    s = pd.Series(pd.to_datetime(["2024-06-01 12:00"]))  # CDT = UTC-5
    assert str(tz.to_utc(s).iloc[0]) == "2024-06-01 17:00:00+00:00"


def test_flag_polarity_distinguishes_passes():
    # Same naive 01:00 label, opposite flag -> two different real instants.
    s = pd.Series(pd.to_datetime(["2024-11-03 01:00", "2024-11-03 01:00"]))
    out = tz.localize_central(s, flags=["N", "Y"]).dt.tz_convert("UTC")
    assert out.iloc[0] != out.iloc[1]
    assert (out.iloc[1] - out.iloc[0]) == pd.Timedelta(hours=1)  # CST is 1h later


def test_now_helpers_are_aware():
    assert str(tz.now_central().tz) == "US/Central"
    assert str(tz.now_utc().tz) == "UTC"


def test_assert_naive_central():
    df = pd.DataFrame({"interval_start": pd.to_datetime(["2024-06-01 00:00"])})
    tz.assert_naive_central(df, "interval_start")  # naive -> ok
    df["interval_start"] = df["interval_start"].dt.tz_localize(tz.CENTRAL)
    try:
        tz.assert_naive_central(df, "interval_start")
        raise AssertionError("expected AssertionError for tz-aware column")
    except AssertionError as e:
        assert "tz-aware" in str(e)


def test_index_input_returns_index():
    idx = pd.DatetimeIndex(pd.date_range("2024-06-01", periods=3, freq="h"))
    out = tz.localize_central(idx)
    assert isinstance(out, pd.DatetimeIndex) and out.tz is not None
    assert isinstance(tz.to_utc(idx), pd.DatetimeIndex)


if __name__ == "__main__":
    from _run import main
    main(globals())
