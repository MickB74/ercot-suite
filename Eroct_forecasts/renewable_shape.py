"""Renewable-aware reshaping of the hourly price profile.

The 8760 shaping in ``shape.py`` borrows the *historical* hour-of-day x month
price shape. As ERCOT keeps adding solar and wind, that shape bends in ways the
training history hasn't fully shown yet:

* **Solar** collapses midday prices and pushes the net-load peak into the
  evening ramp — the "duck curve". A solar asset's capture price falls.
* **Wind** suppresses overnight and shoulder hours, worst in the windy spring.

This module applies a transparent, parametric overlay to the historical shape,
scaled by how much *incremental* solar/wind capacity is expected on the system
by each forecast year. It is a reduced-form shape adjustment, not a
production-cost model: it redistributes price *within the day* (build_8760
renormalizes within each block afterward, so the monthly level is unchanged) —
which is exactly what moves capture prices and the intraday curve.

Defaults are set to reproduce the duck-curve deepening ERCOT's own hour-of-day
shape already shows; ``observed_duck_trend()`` measures that trailing shift from
the price history so the overlay can be sanity-checked rather than hand-waved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ERCOT installed nameplate around the calibration baseline (~end-2024), GW.
# The knobs are GW *added on top* of this, so "today" reshapes ~0.
BASE_SOLAR_GW = 22.0
BASE_WIND_GW = 39.0

# Sensitivities per incremental GW (see module docstring / observed_duck_trend).
K_SOLAR_MIDDAY = 0.010   # midday shape suppression per GW of added solar
K_SOLAR_RAMP = 0.006     # evening-ramp premium per GW of added solar
K_WIND_NIGHT = 0.004     # overnight/shoulder suppression per GW of added wind
MIN_MULT = 0.05          # floor so a per-hour factor never goes <= 0

_HOURS = np.arange(24)


def solar_profile(moy: int, hour: float) -> float:
    """Normalized solar output 0..1 (peak 1 at solar noon), wider in summer."""
    daylen = 12.0 + 2.0 * np.cos(2 * np.pi * (moy - 6) / 12.0)  # ~14h Jun, ~10h Dec
    sunrise, sunset = 12.0 - daylen / 2.0, 12.0 + daylen / 2.0
    if hour < sunrise or hour > sunset:
        return 0.0
    return float(np.sin(np.pi * (hour - sunrise) / (sunset - sunrise)))


def ramp_profile(hour: float) -> float:
    """Evening net-load ramp weight — peaks ~19:30 (sun gone, load still high)."""
    return float(np.exp(-((hour - 19.5) ** 2) / (2 * 1.6 ** 2)))


def wind_profile(moy: int, hour: float) -> float:
    """Normalized wind output 0..1: higher overnight, mild spring boost."""
    diurnal = 0.5 + 0.5 * np.cos(2 * np.pi * (hour - 3) / 24.0)   # peak ~3am
    seasonal = 1.0 + 0.25 * np.cos(2 * np.pi * (moy - 4) / 12.0)  # ~Apr max
    return float(np.clip(diurnal * seasonal, 0.0, 1.0))


def hour_multipliers(moy: int, solar_add_gw: float, wind_add_gw: float, *,
                     k_solar: float = K_SOLAR_MIDDAY,
                     k_ramp: float = K_SOLAR_RAMP,
                     k_wind: float = K_WIND_NIGHT) -> np.ndarray:
    """24-vector of per-hour multipliers on the historical shape for one month.

    ``solar_add_gw`` / ``wind_add_gw`` are GW *above the baseline*. The factors
    only need to be right *relative* to each other — build_8760 renormalizes the
    shape to mean 1 within each block afterward.
    """
    s = np.array([solar_profile(moy, h) for h in _HOURS])
    r = np.array([ramp_profile(h) for h in _HOURS])
    w = np.array([wind_profile(moy, h) for h in _HOURS])
    w_centered = w - w.mean()
    a_s = max(0.0, k_solar * solar_add_gw)
    a_r = max(0.0, k_ramp * solar_add_gw)
    a_w = max(0.0, k_wind * wind_add_gw)
    mult = (1.0 - a_s * s) * (1.0 + a_r * r) * (1.0 - a_w * w_centered)
    return np.clip(mult, MIN_MULT, None)


def add_by_year(asof_year: int, per_year_gw: float, years: range) -> dict:
    """Cumulative incremental GW vs baseline by calendar year (linear ramp)."""
    return {y: per_year_gw * max(0, y - asof_year) for y in years}


def reshape_8760(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Apply the renewable overlay to an in-progress 8760 frame.

    ``df`` must carry ``ts`` (hourly timestamps), ``moy`` (month-of-year) and
    ``hour``, plus the ``shape`` column produced by build_8760. ``cfg`` keys:
    ``solar_add_by_year`` / ``wind_add_by_year`` (dict year->GW above baseline)
    and optional ``k_solar`` / ``k_ramp`` / ``k_wind`` overrides. Returns ``df``
    with ``shape`` multiplied by the per-(year, month, hour) factor.
    """
    solar = cfg.get("solar_add_by_year", {})
    wind = cfg.get("wind_add_by_year", {})
    kw = {k: cfg[k] for k in ("k_solar", "k_ramp", "k_wind") if k in cfg}

    years = df["ts"].dt.year.to_numpy()
    moys = df["moy"].to_numpy()
    hours = df["hour"].to_numpy()

    def _last(d):  # flat-extrapolate past the last provided year
        return max(d.values()) if d else 0.0

    cache: dict[tuple[int, int], np.ndarray] = {}
    mult = np.ones(len(df))
    for yr, moy in {(int(y), int(m)) for y, m in zip(years, moys)}:
        s_add = solar.get(yr, _last(solar))
        w_add = wind.get(yr, _last(wind))
        cache[(yr, moy)] = hour_multipliers(moy, s_add, w_add, **kw)
    for i in range(len(df)):
        mult[i] = cache[(int(years[i]), int(moys[i]))][int(hours[i])]

    out = df.copy()
    out["shape"] = out["shape"].to_numpy() * mult
    return out


def observed_duck_trend(rt15: pd.DataFrame, *, midday=(11, 12, 13, 14, 15),
                        recent_months: int = 18, prior_months: int = 24) -> dict | None:
    """Measure the trailing shift in ERCOT's own midday price share.

    Splits the price history into a recent window and the window before it, and
    compares the average midday (HE 12-16) share of the daily mean. A falling
    ratio is the duck curve deepening in the realized data — the empirical basis
    for the overlay's default sensitivities. Returns None if history is too thin.
    """
    if rt15 is None or rt15.empty or "date" not in rt15.columns:
        return None
    df = rt15.copy()
    df["p"] = df["price"].clip(lower=0.0)
    df["d"] = pd.to_datetime(df["date"])
    last = df["d"].max()
    rec0 = last - pd.DateOffset(months=recent_months)
    pri0 = rec0 - pd.DateOffset(months=prior_months)

    def _midday_share(g: pd.DataFrame) -> float | None:
        if g.empty:
            return None
        hourly = g.groupby("hour")["p"].mean()
        if hourly.mean() <= 0:
            return None
        mid = hourly.reindex(list(midday)).dropna()
        return float(mid.mean() / hourly.mean()) if not mid.empty else None

    recent = _midday_share(df[df["d"] >= rec0])
    prior = _midday_share(df[(df["d"] >= pri0) & (df["d"] < rec0)])
    if recent is None or prior is None:
        return None
    return {
        "recent_midday_share": round(recent, 3),
        "prior_midday_share": round(prior, 3),
        "drop_pct": round(100 * (1 - recent / prior), 1) if prior else None,
        "recent_window_months": recent_months,
    }
