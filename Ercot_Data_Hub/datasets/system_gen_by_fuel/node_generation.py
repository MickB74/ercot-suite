"""Pull per-resource-node actual generation from ERCOT's 60-day SCED disclosure.

Each resource node maps to one or more SCED generators (see resource_catalog).
We read the daily SCED disclosure from the *shared* ercot_core cache (the same
files plant_sced uses — no more double downloads), keep each unit's telemetered
net output and base point, and resample SCED's ~5-minute cadence to 15-minute
interval means to match the rest of this dataset.

NOTE: the SCED disclosure has a ~60-day lag — data is only available for dates
roughly 60+ days in the past.

Tidy long schema (one row per interval x unit):
    interval_start (naive CPT), interval_end, resource_node, resource_name,
    mw (telemetered net output, 15-min mean), base_point_mw, source, fetched_at
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import resource_catalog as rc
from ercot_core import sced_disclosure

SOURCE = "sced_60day"
GEN_COLUMNS = [
    "interval_start", "interval_end", "resource_node", "resource_name",
    "mw", "base_point_mw", "source", "fetched_at",
]


def fetch_generation(
    resource_nodes: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    fetched_at: pd.Timestamp | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Telemetered net output (15-min) for the units of `resource_nodes`."""
    fetched_at = fetched_at or pd.Timestamp.now(tz="UTC")
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()

    # Map each SCED resource name -> its resource node (for labeling/aggregation).
    name_to_node: dict[str, str] = {}
    for node in resource_nodes:
        for sced_name in rc.sced_names_for(node):
            name_to_node[sced_name] = node
    wanted = set(name_to_node)
    if not wanted:
        raise ValueError(f"No SCED units found for nodes {resource_nodes}. Build the catalog?")

    frames = []
    for day in pd.date_range(start, end, freq="D"):
        if verbose:
            print(f"  [gen] SCED {day.date()} ...")
        try:
            # Shared 60-day SCED disclosure cache (operating-set columns).
            disc = sced_disclosure.get_daily_disclosure(day.date())
        except Exception as e:
            if verbose:
                print(f"    skip {day.date()}: {e}")
            continue
        if disc is None or disc.empty:
            continue

        sub = disc[disc["resource_name"].isin(wanted)].copy()
        if sub.empty:
            continue

        ts = pd.to_datetime(sub["sced_timestamp"])
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_convert("US/Central").dt.tz_localize(None)
        sub["interval_start"] = ts.dt.floor("15min")
        sub["mw"] = pd.to_numeric(sub["telemetered_net_output"], errors="coerce")
        sub["base_point_mw"] = pd.to_numeric(sub.get("base_point"), errors="coerce")

        agg = (sub.groupby(["interval_start", "resource_name"], as_index=False)
                  .agg(mw=("mw", "mean"), base_point_mw=("base_point_mw", "mean")))
        frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=GEN_COLUMNS)

    out = pd.concat(frames, ignore_index=True)
    out["resource_node"] = out["resource_name"].map(name_to_node)
    out["interval_end"] = out["interval_start"] + pd.Timedelta(minutes=15)
    out["source"] = SOURCE
    out["fetched_at"] = pd.to_datetime(fetched_at, utc=True)
    out["mw"] = pd.to_numeric(out["mw"], downcast="float")
    out["base_point_mw"] = pd.to_numeric(out["base_point_mw"], downcast="float")
    return out[GEN_COLUMNS].sort_values(["resource_node", "resource_name", "interval_start"])


if __name__ == "__main__":
    node = sys.argv[1] if len(sys.argv) > 1 else "7RNCHSLR_ALL"
    # default: a 2-day window ~66 days back (within the 60-day-lag window)
    from ercot_core import tz
    s = (tz.now_central().tz_localize(None) - pd.Timedelta(days=66)).normalize()
    e = s + pd.Timedelta(days=1)
    df = fetch_generation([node], s, e)
    print(f"\n{node}: {len(df):,} rows | {df['interval_start'].min()} -> {df['interval_start'].max()}"
          if not df.empty else f"\n{node}: no data")
    if not df.empty:
        print(df.head(6).to_string(index=False))
