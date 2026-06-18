"""Pull per-resource-node actual generation from ERCOT's 60-day SCED disclosure.

Each resource node maps to one or more SCED generators (see resource_catalog).
We pull the daily SCED disclosure, keep the `Telemetered Net Output` (and
`Base Point`) for the node's units, and resample SCED's ~5-minute cadence to
15-minute interval means to match the rest of this repo.

NOTE: the SCED disclosure has a ~60-day lag — data is only available for dates
roughly 60+ days in the past.

Tidy long schema (one row per interval x unit):
    interval_start (naive CPT), interval_end, resource_node, resource_name,
    mw (telemetered net output, 15-min mean), base_point_mw, source, fetched_at
"""

from __future__ import annotations

import logging
import os

import pandas as pd

import resource_catalog as rc

# gridstatus logs every MIS download at DEBUG/INFO — quiet it down.
logging.getLogger("gridstatus").setLevel(logging.WARNING)

SOURCE = "sced_60day"
GEN_COLUMNS = [
    "interval_start", "interval_end", "resource_node", "resource_name",
    "mw", "base_point_mw", "source", "fetched_at",
]

# A full daily SCED disclosure is a large download. Cache a trimmed copy per day
# so pulling more nodes (or re-pulling a range) is cheap.
CACHE_DIR = "node_data/sced_cache"
_KEEP_COLS = ["SCED Timestamp", "Resource Name", "Resource Type",
              "Telemetered Net Output", "Base Point"]


def _daily_sced_gen(iso, day: pd.Timestamp) -> pd.DataFrame | None:
    """Trimmed SCED gen-resource table for one day, cached to parquet."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"sced_gen_{day.date()}.parquet")
    if os.path.exists(cache_file):
        return pd.read_parquet(cache_file)

    data = iso.get_60_day_sced_disclosure(date=day.strftime("%Y-%m-%d"))
    if "sced_gen_resource" not in data:
        return None
    gen = data["sced_gen_resource"]
    gen.columns = [c.strip() for c in gen.columns]
    keep = [c for c in _KEEP_COLS if c in gen.columns]
    gen = gen[keep].copy()
    gen.to_parquet(cache_file, index=False)
    return gen


def fetch_generation(
    resource_nodes: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    fetched_at: pd.Timestamp | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Telemetered net output (15-min) for the units of `resource_nodes`."""
    import gridstatus

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

    iso = gridstatus.Ercot()
    frames = []
    for day in pd.date_range(start, end, freq="D"):
        if verbose:
            print(f"  [gen] SCED {day.date()} ...")
        try:
            gen = _daily_sced_gen(iso, day)
        except Exception as e:
            if verbose:
                print(f"    skip {day.date()}: {e}")
            continue
        if gen is None or gen.empty:
            continue

        sub = gen[gen["Resource Name"].isin(wanted)].copy()
        if sub.empty:
            continue

        import tzutil
        ts = tzutil.to_naive_central(sub["SCED Timestamp"])
        sub["interval_start"] = ts.dt.floor("15min")
        sub["mw"] = pd.to_numeric(sub["Telemetered Net Output"], errors="coerce")
        sub["base_point_mw"] = pd.to_numeric(sub.get("Base Point"), errors="coerce")

        agg = (sub.groupby(["interval_start", "Resource Name"], as_index=False)
                  .agg(mw=("mw", "mean"), base_point_mw=("base_point_mw", "mean")))
        frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=GEN_COLUMNS)

    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"Resource Name": "resource_name"})
    out["resource_node"] = out["resource_name"].map(name_to_node)
    out["interval_end"] = out["interval_start"] + pd.Timedelta(minutes=15)
    out["source"] = SOURCE
    out["fetched_at"] = pd.to_datetime(fetched_at, utc=True)
    out["mw"] = pd.to_numeric(out["mw"], downcast="float")
    out["base_point_mw"] = pd.to_numeric(out["base_point_mw"], downcast="float")
    return out[GEN_COLUMNS].sort_values(["resource_node", "resource_name", "interval_start"])


if __name__ == "__main__":
    import sys
    node = sys.argv[1] if len(sys.argv) > 1 else "7RNCHSLR_ALL"
    # default: a 2-day window ~65 days back (within the 60-day-lag window)
    import tzutil
    s = (tzutil.now_central().tz_localize(None) - pd.Timedelta(days=66)).normalize()
    e = s + pd.Timedelta(days=1)
    df = fetch_generation([node], s, e)
    print(f"\n{node}: {len(df):,} rows | {df['interval_start'].min()} -> {df['interval_start'].max()}"
          if not df.empty else f"\n{node}: no data")
    if not df.empty:
        print(df.head(6).to_string(index=False))
