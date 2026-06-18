"""Central-time handling for the price forecast engine.

Mirrors the ERCOT Data Hub's ``ercot_core/tz.py`` convention so this repo is
DST-correct on its own: the hub_prices lake stores **naive Central** interval
labels, and the two DST break days (spring-forward gap, fall-back duplicated
hour) must be resolved with the ERCOT ``dst_flag`` before any hour/peak
bucketing — otherwise the eight duplicated 15-min intervals in the fall-back
hour silently double-count.
"""

from __future__ import annotations

import pandas as pd

CENTRAL = "US/Central"


def now_central() -> pd.Timestamp:
    return pd.Timestamp.now(tz=CENTRAL)


def localize_central(s, *, flags=None) -> pd.Series:
    """Lift naive-Central timestamps to tz-aware Central (DST-correct).

    ``flags`` — ERCOT repeated-hour / DST flag aligned to ``s`` ("Y"/"N"),
    where truthy marks the *second*, standard-time pass of the fall-back hour.
    """
    ser = pd.to_datetime(pd.Series(s).reset_index(drop=True))
    if getattr(ser.dt, "tz", None) is not None:
        return ser.dt.tz_convert(CENTRAL)
    if flags is not None:
        f = pd.Series(list(flags)).reset_index(drop=True)
        repeated = f.astype(str).str.strip().str.upper().isin({"Y", "TRUE", "1"})
        if len(repeated) == len(ser):
            return ser.dt.tz_localize(CENTRAL, ambiguous=(~repeated).to_numpy(),
                                      nonexistent="shift_forward")
    try:
        return ser.dt.tz_localize(CENTRAL, ambiguous="infer",
                                  nonexistent="shift_forward")
    except Exception:
        return ser.dt.tz_localize(CENTRAL, ambiguous=True,
                                  nonexistent="shift_forward")
