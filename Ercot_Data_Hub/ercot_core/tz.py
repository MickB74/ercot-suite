"""Canonical timezone handling for the ERCOT Data Hub.

ERCOT settles in **Central Prevailing Time** (``US/Central``): 15-min RTM
intervals, hourly DAM. Two days a year break naive wall-clock arithmetic:

  * spring forward — 02:00–03:00 never happens (23 h / 92 intervals).
  * fall back      — 01:00–02:00 happens twice (25 h / 100 intervals). The two
                     passes share the *same* naive wall-clock label, so anything
                     that groups/joins/dedupes on a naive timestamp silently
                     collapses or double-counts those eight 15-min intervals.

**Storage convention.** Interval columns in the parquet lake are stored *naive
Central* on purpose (they open cleanly in Excel). That is fine for display and
for same-grid arithmetic, but settlement-grade joins must first lift the naive
labels into tz-aware Central, where the fall-back hour is unambiguous. This
module is the one place that conversion lives — previously it was reinvented in
``fuels.to_utc``, ``reconcile.py``, and three copies of ``_to_naive_cpt``.
"""

from __future__ import annotations

import pandas as pd

CENTRAL = "US/Central"


def now_central() -> pd.Timestamp:
    """Current instant as a tz-aware Central timestamp ("today in ERCOT")."""
    return pd.Timestamp.now(tz=CENTRAL)


def now_utc() -> pd.Timestamp:
    """Current instant as a tz-aware UTC timestamp (for ``fetched_at``)."""
    return pd.Timestamp.now(tz="UTC")


def _is_datetimelike(obj) -> bool:
    return isinstance(obj, (pd.Series, pd.DatetimeIndex, pd.Index))


def localize_central(s, *, flags=None):
    """Lift *naive Central* timestamps to tz-aware Central.

    Handles DST: ``ambiguous="infer"`` resolves the duplicated fall-back hour
    from sort order (first pass = CDT, second = CST) and ``nonexistent=
    "shift_forward"`` nudges any value that lands in the spring-forward gap.

    ``flags`` — an optional ERCOT repeated-hour / DST flag aligned to ``s``
    (``"Y"``/``"N"`` strings or booleans, where truthy marks the *second*,
    standard-time pass of the fall-back hour). When given it is used as an
    explicit boolean ``ambiguous`` mask, which is exact even when the rows are
    not perfectly sorted. Accepts a Series, DatetimeIndex, or scalar.
    """
    if not _is_datetimelike(s):
        s = pd.to_datetime(s)
    is_index = isinstance(s, (pd.DatetimeIndex, pd.Index))
    ser = pd.Series(pd.to_datetime(s)) if is_index else pd.to_datetime(s)

    if getattr(ser.dt, "tz", None) is not None:
        out = ser.dt.tz_convert(CENTRAL)
    else:
        out = _localize_naive(ser, flags)
    return pd.DatetimeIndex(out) if is_index else out


def _localize_naive(ser: pd.Series, flags) -> pd.Series:
    """Localize a naive-Central Series, resolving the fall-back hour.

    Order of preference: an explicit ERCOT flag (exact) -> ``infer`` from sorted
    duplicate labels (exact when both passes are present) -> assume the DST
    (first) pass, which never raises and is the natural reading of a lone
    wall-clock label on the fall-back day.
    """
    if flags is not None:
        ambiguous = _flags_to_ambiguous(flags, len(ser))
        return ser.dt.tz_localize(CENTRAL, ambiguous=ambiguous,
                                  nonexistent="shift_forward")
    try:
        return ser.dt.tz_localize(CENTRAL, ambiguous="infer",
                                  nonexistent="shift_forward")
    except pd.errors.OutOfBoundsDatetime:
        raise
    except Exception:  # AmbiguousTimeError (pytz/zoneinfo): single-pass data
        return ser.dt.tz_localize(CENTRAL, ambiguous=True,
                                  nonexistent="shift_forward")


def _flags_to_ambiguous(flags, n: int):
    """Coerce an ERCOT DST/repeated-hour flag into pandas ``ambiguous`` form.

    pandas wants a bool array where ``True`` marks a DST (daylight) time. The
    ERCOT repeated-hour / DST flag instead marks the *second*, standard-time
    pass of the fall-back hour, so DST = NOT flag.
    """
    f = flags if isinstance(flags, pd.Series) else pd.Series(list(flags))
    if f.dtype == bool:
        repeated = f
    else:
        repeated = f.astype(str).str.strip().str.upper().isin({"Y", "TRUE", "1"})
    repeated = repeated.reset_index(drop=True)
    if len(repeated) != n:  # length mismatch -> fall back to inference
        return "infer"
    return (~repeated).to_numpy()


def to_naive_central(s):
    """Convert any timestamps to Central wall-clock, then drop the tz.

    Accepts tz-aware (any zone) or already-naive input; the result is naive
    Central, matching the parquet storage convention. Replaces the per-module
    ``_to_naive_cpt`` helpers.
    """
    is_index = isinstance(s, (pd.DatetimeIndex, pd.Index))
    ser = pd.Series(pd.to_datetime(s)) if is_index else pd.to_datetime(s)
    if getattr(ser.dt, "tz", None) is not None:
        ser = ser.dt.tz_convert(CENTRAL).dt.tz_localize(None)
    return pd.DatetimeIndex(ser) if is_index else ser


def to_utc(s, *, flags=None):
    """Convert naive-Central interval timestamps to tz-aware UTC (DST-correct)."""
    aware = localize_central(s, flags=flags)
    if isinstance(aware, pd.DatetimeIndex):
        return aware.tz_convert("UTC")
    return aware.dt.tz_convert("UTC")


def assert_naive_central(df: pd.DataFrame, *cols: str) -> None:
    """Dev guard: raise if a column meant to be naive Central is tz-aware.

    Catches schema drift where a tz-aware column sneaks into the lake (which
    would make downstream naive arithmetic wrong).
    """
    for col in cols:
        if col not in df.columns:
            continue
        tz = getattr(df[col].dt, "tz", None)
        if tz is not None:
            raise AssertionError(
                f"column {col!r} should be naive Central but is tz-aware ({tz})"
            )
