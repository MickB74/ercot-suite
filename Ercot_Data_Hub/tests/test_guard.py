"""Invariants for the forecast-month guard (`_guard_forecast_months`) — the
EIA-P50 grounding + nameplate-cap layer. These are properties, not frozen
numbers, so they don't rot: monthly total is preserved, no day exceeds
nameplate, and a degenerate weather shape falls back to a flat spread.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ercot_core import near_term_bill as nb

CAP_SHARE = 197.0                 # MW at contracted share (Aguayo nameplate)
STRIKE = 35.0
GROUND_CF = {10: 0.30}            # EIA-923 P50 capacity factor for October
DAY_CAP = CAP_SHARE * 24.0        # 4,728 MWh/day physical ceiling
N_DAYS = 31
MONTH_TARGET = GROUND_CF[10] * CAP_SHARE * 24.0 * N_DAYS

FPX = lambda d: 40.0             # noqa: E731 — constant forward price
FWIN_START, FWIN_END = dt.date(2026, 10, 1), dt.date(2026, 10, 31)
HIST = pd.Series({10: MONTH_TARGET})


def _rows(mwh_by_day):
    return [{"date": dt.date(2026, 10, i + 1), "mwh": m,
             "net": m * (FPX(None) - STRIKE), "price": 40.0, "kind": "forecast_fourth"}
            for i, m in enumerate(mwh_by_day)]


def _guard(mwh_by_day):
    return nb._guard_forecast_months(
        _rows(mwh_by_day), hist_mwh=HIST, cap_share=CAP_SHARE, tech="wind",
        strike=STRIKE, fpx=FPX, blocked=set(),
        fwin_start=FWIN_START, fwin_end=FWIN_END, ground_cf=GROUND_CF)


def test_degenerate_shape_never_exceeds_nameplate():
    """28 dead days + 3 impossible spikes (the Oct bug) → capped & flattened."""
    out, _ = _guard([0.05] * 28 + [20000.0] * 3)
    mwh = [r["mwh"] for r in out]
    assert max(mwh) <= DAY_CAP + 1e-6                    # no supra-nameplate day
    assert abs(sum(mwh) - MONTH_TARGET) < 1.0            # month total preserved
    # degenerate → flat spread (all days ~equal to per-day P50)
    assert max(mwh) - min(mwh) < 1.0


def test_healthy_shape_is_preserved_but_capped():
    """A varied, all-live shape keeps its day-to-day variation, total intact."""
    shape = [500 + 100 * (i % 7) for i in range(N_DAYS)]   # all > dead threshold, < cap
    out, _ = _guard(shape)
    mwh = [r["mwh"] for r in out]
    assert max(mwh) <= DAY_CAP + 1e-6
    assert abs(sum(mwh) - MONTH_TARGET) < 1.0
    assert pd.Series(mwh).std() > 1.0                    # NOT flattened


def test_single_spike_in_healthy_month_is_capped():
    """One over-nameplate day inside an otherwise healthy month is clamped."""
    shape = [1500.0] * 30 + [9000.0]                     # one impossible day
    out, _ = _guard(shape)
    mwh = [r["mwh"] for r in out]
    assert max(mwh) <= DAY_CAP + 1e-6
    assert abs(sum(mwh) - MONTH_TARGET) < 1.0


def test_grounding_pins_total_regardless_of_input_shape():
    """The month total is the EIA-P50, independent of the (unreliable) shape."""
    a, _ = _guard([0.05] * 28 + [20000.0] * 3)
    b, _ = _guard([1500 + 50 * (i % 5) for i in range(N_DAYS)])
    assert abs(sum(r["mwh"] for r in a) - sum(r["mwh"] for r in b)) < 1.0


def test_money_invariant_net_equals_price_minus_strike_times_mwh():
    """Net settlement is invariant to daily reshaping (constant monthly price)."""
    out, _ = _guard([500 + 100 * (i % 7) for i in range(N_DAYS)])
    for r in out:
        assert abs(r["net"] - r["mwh"] * (FPX(None) - STRIKE)) < 1e-6
    assert abs(sum(r["net"] for r in out)
               - (FPX(None) - STRIKE) * sum(r["mwh"] for r in out)) < 1e-6
