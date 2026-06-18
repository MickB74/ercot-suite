"""Optional provisional supplement: ERCOT Public API (api.ercot.com).

Requires credentials (set as environment variables, read by gridstatus):
    ERCOT_API_USERNAME
    ERCOT_API_PASSWORD
    ERCOT_PUBLIC_API_SUBSCRIPTION_KEY

The Public API has NO all-fuel generation product. It does expose system-wide
WIND and SOLAR actual production. gridstatus wraps these at HOURLY resolution
(`GEN SYSTEM WIDE`), so we expand each hour across its four 15-minute intervals
(an hourly-derived approximation) to fill the current-month gap for renewables
between the report's end and the ~2-day dashboard window.

All rows are tagged PROVISIONAL and are replaced by the Fuel Mix Report.
If credentials are absent, fetch_recent() returns empty and logs why.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import ercot_fuels as F
from ercot_core import credentials

REQUIRED_ENV = ["ERCOT_API_USERNAME", "ERCOT_API_PASSWORD", "ERCOT_PUBLIC_API_SUBSCRIPTION_KEY"]


def credentials_present() -> bool:
    # Mirror the shared config.json into the env vars gridstatus reads, so
    # configuring credentials once (anywhere in the hub) lights this up too.
    credentials.export_to_env()
    return all(os.getenv(k) for k in REQUIRED_ENV)


def _expand_hourly_to_15min(df: pd.DataFrame, fuel: str, fetched_at: pd.Timestamp) -> pd.DataFrame:
    """df has 'Interval Start' (tz-aware) and 'GEN SYSTEM WIDE' (hourly MW)."""
    if df is None or df.empty or "GEN SYSTEM WIDE" not in df.columns:
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)

    df = df[["Interval Start", "GEN SYSTEM WIDE"]].dropna()
    df = df.rename(columns={"GEN SYSTEM WIDE": "mw"})
    start_local = pd.to_datetime(df["Interval Start"])
    if start_local.dt.tz is not None:
        start_local = start_local.dt.tz_convert("US/Central").dt.tz_localize(None)
    df = df.assign(hour_start=start_local)

    # Replicate each hourly MW across the 4 sub-intervals.
    rows = []
    for offset in (0, 15, 30, 45):
        part = df.copy()
        part["interval_start"] = part["hour_start"] + pd.to_timedelta(offset, unit="m")
        rows.append(part[["interval_start", "mw"]])
    out = pd.concat(rows, ignore_index=True)

    out["interval_end"] = out["interval_start"] + pd.Timedelta(minutes=15)
    out["fuel"] = fuel
    out["settlement_type"] = F.ST_PROVISIONAL
    out["source"] = F.SOURCE_API
    out["fetched_at"] = fetched_at
    return F.finalize(out)


def fetch_recent(
    start: pd.Timestamp,
    end: pd.Timestamp | None = None,
    fetched_at: pd.Timestamp | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Fetch system-wide wind + solar actuals for [start, end] and expand to 15-min.

    `start`/`end` are dates or timestamps (naive CPT or tz-aware). Returns empty
    if credentials are not configured.
    """
    if not credentials_present():
        if verbose:
            missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
            print(f"[api_source] skipped — missing env: {', '.join(missing)}")
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)

    from gridstatus.ercot_api.ercot_api import ErcotAPI

    fetched_at = fetched_at or pd.Timestamp.now(tz="UTC")
    end = end or pd.Timestamp.now(tz="US/Central")
    api = ErcotAPI()

    frames = []
    try:
        wind = api.get_wind_actual_and_forecast_hourly(date=start, end=end, verbose=False)
        frames.append(_expand_hourly_to_15min(wind, "Wind", fetched_at))
    except Exception as e:  # network / availability
        if verbose:
            print(f"[api_source] wind fetch failed: {e}")
    try:
        solar = api.get_solar_actual_and_forecast_hourly(date=start, end=end, verbose=False)
        frames.append(_expand_hourly_to_15min(solar, "Solar", fetched_at))
    except Exception as e:
        if verbose:
            print(f"[api_source] solar fetch failed: {e}")

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    s = pd.Timestamp.now(tz="US/Central").normalize() - pd.Timedelta(days=14)
    out = fetch_recent(start=s)
    if out.empty:
        print("no api data (see message above)")
    else:
        print(f"{len(out):,} rows | {out['interval_start'].min()} -> {out['interval_start'].max()}")
        print("fuels:", sorted(out["fuel"].unique()))
