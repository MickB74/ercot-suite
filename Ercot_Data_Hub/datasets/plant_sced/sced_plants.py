"""
Core library for fetching ERCOT plant-level SCED data.

Source: ERCOT 60-Day SCED Disclosure (Generation Resource Data). The daily
disclosure download + cache now lives in ``ercot_core.sced_disclosure`` (shared
with system_gen's node generation — no more downloading the same day twice).
This module keeps the plant-centric registry, per-plant extraction, and storage.

Layout (unified data lake — see ercot_core.paths):
  data/sced_cache/    shared daily disclosure parquets (all resources, one/day)
  data/plant_sced/plants/   canonical per-plant-per-year parquets <RESOURCE>_<YEAR>.parquet
  data/plant_sced/plants.csv         registry of available resources
  data/plant_sced/plants.parquet     same, machine-readable

Times are stored tz-aware US/Central, exactly as ERCOT publishes them.
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from ercot_core import paths, sced_disclosure

# Re-export operating-set constants from the shared module (back-compat).
OPERATING_COLUMNS = sced_disclosure.OPERATING_COLUMNS
NUMERIC_COLS = sced_disclosure.NUMERIC_COLS
FINAL_COLUMN_ORDER = sced_disclosure.FINAL_COLUMN_ORDER
DISCLOSURE_LAG_DAYS = sced_disclosure.DISCLOSURE_LAG_DAYS

DATA_DIR = str(paths.PLANT_DATA_DIR)
REGISTRY_CSV = str(paths.PLANT_REGISTRY_CSV)
REGISTRY_PARQUET = str(paths.PLANT_REGISTRY_PARQUET)

paths.PLANT_DATA_DIR.mkdir(parents=True, exist_ok=True)
paths.SCED_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Back-compat: ERCOT "Resource Type" -> friendly fuel group (now in fuels).
from ercot_core.fuels import SCED_FUEL_GROUP as FUEL_GROUP  # noqa: E402


def fuel_group_for(resource_type):
    return sced_disclosure.fuel_group_for(resource_type)


def latest_available_date():
    return sced_disclosure.latest_available_date()


def get_daily_disclosure(date, allow_fetch=True):
    """Trimmed operating-set disclosure for one day (shared cache)."""
    return sced_disclosure.get_daily_disclosure(date, allow_fetch=allow_fetch)


# --- Registry of available plants ------------------------------------------
def build_registry(date=None, allow_fetch=True):
    """Build/refresh the list of available resources from a disclosure day.

    Defaults to the most recent cached day, else the latest available.
    Writes plants.csv and plants.parquet.
    """
    if date is None:
        cached = sced_disclosure.cached_disclosure_dates()
        date = max(cached) if cached else latest_available_date()

    df = get_daily_disclosure(date, allow_fetch=allow_fetch)
    if df.empty:
        raise RuntimeError(f"No disclosure data available to build registry (tried {date}).")

    reg = (
        df[["resource_name", "resource_type"]]
        .drop_duplicates("resource_name")
        .sort_values("resource_name")
        .reset_index(drop=True)
    )
    reg["fuel_group"] = reg["resource_type"].map(fuel_group_for)
    reg = reg[["resource_name", "resource_type", "fuel_group"]]
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    reg.to_csv(REGISTRY_CSV, index=False)
    reg.to_parquet(REGISTRY_PARQUET, index=False)
    print(f"Registry built from {date}: {len(reg)} resources -> {REGISTRY_CSV}")
    return reg


def load_registry():
    """Load the plant registry (merging human plant names if available)."""
    if os.path.exists(REGISTRY_PARQUET):
        reg = pd.read_parquet(REGISTRY_PARQUET)
    elif os.path.exists(REGISTRY_CSV):
        reg = pd.read_csv(REGISTRY_CSV)
    else:
        reg = build_registry()

    try:
        from ercot_core import plant_names
        xwalk = plant_names.load_crosswalk()
        if not xwalk.empty:
            reg = reg.merge(xwalk[["resource_name", "plant_name", "name_source"]],
                            on="resource_name", how="left")
    except Exception:
        pass
    if "plant_name" not in reg.columns:
        reg["plant_name"] = reg["resource_name"]
    reg["plant_name"] = reg["plant_name"].fillna(reg["resource_name"])
    return reg


# --- Per-plant extraction ---------------------------------------------------
def _finalize(df):
    """Order columns, coerce numerics, sort, dedup."""
    if df.empty:
        return df
    df = df.copy()
    df["fuel_group"] = df["resource_type"].map(fuel_group_for)
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = [c for c in FINAL_COLUMN_ORDER if c in df.columns]
    df = df[cols]
    df = df.sort_values(["resource_name", "sced_timestamp"])
    df = df.drop_duplicates(["resource_name", "sced_timestamp"])
    return df.reset_index(drop=True)


def fetch_plants(resources, start_date, end_date, allow_fetch=True, write=True):
    """Fetch native SCED operating data for one or more resources over a range.

    Each day's disclosure is loaded once (from the shared cache) and shared
    across all requested resources. Returns {resource_name: DataFrame}. When
    write=True, merges into canonical per-plant-per-year parquets under data/.
    """
    if isinstance(resources, str):
        resources = [resources]
    resources = list(dict.fromkeys(resources))
    wanted = set(resources)

    if isinstance(start_date, str):
        start_date = pd.Timestamp(start_date).date()
    if isinstance(end_date, str):
        end_date = pd.Timestamp(end_date).date()

    latest = latest_available_date()
    if end_date > latest:
        print(f"Note: disclosure only available through ~{latest}; clamping end date.")
        end_date = latest
    if start_date > end_date:
        print("Nothing to fetch: requested range is entirely within the 60-day lag window.")
        return {r: pd.DataFrame() for r in resources}

    per_resource = {r: [] for r in resources}
    days = pd.date_range(start_date, end_date, freq="D")
    print(f"Scanning {len(days)} day(s) {start_date}..{end_date} for {len(resources)} resource(s)")
    for d in days:
        day = d.date()
        disc = get_daily_disclosure(day, allow_fetch=allow_fetch)
        if disc.empty:
            continue
        hit = disc[disc["resource_name"].isin(wanted)]
        for rname, g in hit.groupby("resource_name"):
            per_resource[rname].append(g)

    results = {}
    for r in resources:
        parts = per_resource[r]
        if not parts:
            print(f"  {r}: no data found in range")
            results[r] = pd.DataFrame()
            continue
        df = _finalize(pd.concat(parts, ignore_index=True))
        results[r] = df
        print(f"  {r}: {len(df):,} intervals  ({df['sced_timestamp'].min()} .. {df['sced_timestamp'].max()})")
        if write:
            _write_plant_years(r, df)
    return results


def _write_plant_years(resource_name, df):
    """Merge df into per-year parquet files (idempotent)."""
    if df.empty:
        return
    paths.PLANT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    years = df["sced_timestamp"].dt.year
    for year, g in df.groupby(years):
        out = os.path.join(DATA_DIR, f"{resource_name}_{year}.parquet")
        if os.path.exists(out):
            try:
                prev = pd.read_parquet(out)
                g = _finalize(pd.concat([prev, g], ignore_index=True))
            except Exception:
                pass
        g.to_parquet(out, index=False)
        print(f"      wrote {out}  ({len(g):,} rows)")


def load_plant(resource_name, year=None):
    """Read stored data for a plant from data/ (all years, or one year)."""
    if year is not None:
        p = os.path.join(DATA_DIR, f"{resource_name}_{year}.parquet")
        return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()
    parts = [pd.read_parquet(p) for p in
             sorted(glob.glob(os.path.join(DATA_DIR, f"{resource_name}_*.parquet")))]
    return _finalize(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame()


if __name__ == "__main__":
    reg = load_registry()
    print(reg.groupby("fuel_group")["resource_name"].count())
