"""ONE 60-day SCED disclosure download, with a shared daily cache.

Before the merge, both ``plant_sced/sced_plants.py`` and
``system_gen/node_generation.py`` called ``get_60_day_sced_disclosure()`` and
cached the daily files in *separate* directories — so the same large daily
download happened twice. They now both go through here, caching to the single
``data/sced_cache/`` directory.

Each cached day is the *operating-set* table (resource id + what the unit was
doing / dispatched to / its limits / ancillary awards), combining the
conventional-generator frame (``sced_gen_resource``) and the battery frame
(``sced_esr``, separate since the 2026 single-model go-live). That superset
covers everything plant_sced needs *and* the telemetered-net-output / base-point
columns system_gen's node generation needs.

ERCOT publishes the 60-day SCED disclosure with roughly a 60-day lag.
"""

from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta

import pandas as pd

from ercot_core import fuels, paths

DISCLOSURE_LAG_DAYS = 60

# Optionally reuse already-downloaded daily disclosures from another location
# (read-only) — purely a speed optimization; anything not found is re-downloaded,
# so the Hub has no hard dependency here. Through 2025 that frame still carried
# batteries (PWRSTR); from 2026 batteries moved to a separate ESR frame, so only
# trust reused files for years <= 2025. Set ERCOT_SCED_REUSE_DIR to point at a
# cache; otherwise the sibling price_settlements/sced_cache is used iff present.
def _default_reuse_dirs() -> list:
    env = os.environ.get("ERCOT_SCED_REUSE_DIR")
    if env:
        return [env]
    sibling = paths.ROOT.parent / "price_settlements" / "sced_cache"
    return [str(sibling)] if sibling.exists() else []


_REUSE_DIRS = _default_reuse_dirs()

# --- operating-set columns we keep: raw name -> clean name -----------------
OPERATING_COLUMNS = {
    "Telemetered Resource Status": "status",
    "Output Schedule": "output_schedule",
    "HSL": "hsl",
    "HASL": "hasl",
    "HDL": "hdl",
    "LSL": "lsl",
    "LASL": "lasl",
    "LDL": "ldl",
    "Base Point": "base_point",
    "Telemetered Net Output": "telemetered_net_output",
    "Ancillary Service REGUP": "as_regup",
    "Ancillary Service REGDN": "as_regdn",
    "Ancillary Service RRS": "as_rrs",
    "Ancillary Service RRSFFR": "as_rrsffr",
    "Ancillary Service NSRS": "as_nsrs",
    "Ancillary Service ECRS": "as_ecrs",
    "State of Charge": "state_of_charge",  # ESR-only; NA for conventional gen
}

NUMERIC_COLS = [
    "output_schedule", "hsl", "hasl", "hdl", "lsl", "lasl", "ldl",
    "base_point", "telemetered_net_output", "state_of_charge",
    "as_regup", "as_regdn", "as_rrs", "as_rrsffr", "as_nsrs", "as_ecrs",
]

FINAL_COLUMN_ORDER = (
    ["resource_name", "resource_type", "fuel_group",
     "sced_timestamp", "repeated_hour_flag", "status"]
    + ["output_schedule", "hsl", "hasl", "hdl", "lsl", "lasl", "ldl",
       "base_point", "telemetered_net_output", "state_of_charge",
       "as_regup", "as_regdn", "as_rrs", "as_rrsffr", "as_nsrs", "as_ecrs"]
)


def fuel_group_for(resource_type):
    return fuels.sced_fuel_group(resource_type)


def latest_available_date():
    """Most recent operating day the 60-day disclosure should cover.

    Anchored on *Central* "today" (the ERCOT operating day), not the machine's
    local clock, so the lag is right regardless of where this runs.
    """
    from ercot_core import tz
    return (tz.now_central() - timedelta(days=DISCLOSURE_LAG_DAYS)).date()


def _cache_dir() -> str:
    paths.SCED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return str(paths.SCED_CACHE_DIR)


def _read_existing_disclosure(date):
    """Path to a cached full disclosure for `date` from any known cache dir."""
    local = os.path.join(_cache_dir(), f"disclosure_{date}.parquet")
    if os.path.exists(local):
        return local
    if date.year <= 2025:
        fname = f"full_disclosure_{date}.parquet"
        for d in _REUSE_DIRS:
            p = os.path.join(d, fname)
            if os.path.exists(p):
                return p
    return None


def _trim_to_operating(df):
    """Reduce a raw disclosure frame to resource id + operating columns."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Timestamp column changed name: pre-2025 'Interval Start', 2025+ 'SCED Timestamp'.
    if "SCED Timestamp" in df.columns:
        tcol = "SCED Timestamp"
    elif "Interval Start" in df.columns:
        tcol = "Interval Start"
    else:
        raise KeyError(f"No SCED timestamp column found in disclosure: {list(df.columns)[:10]}")

    out = pd.DataFrame()
    out["resource_name"] = df["Resource Name"].astype(str).str.strip()
    out["resource_type"] = df.get("Resource Type")
    out["sced_timestamp"] = pd.to_datetime(df[tcol])
    out["repeated_hour_flag"] = df.get("Repeated Hour Flag")

    for raw, clean in OPERATING_COLUMNS.items():
        out[clean] = df[raw] if raw in df.columns else float("nan")

    for c in NUMERIC_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def get_daily_disclosure(date, allow_fetch=True):
    """Trimmed operating-set disclosure (all resources) for one day.

    Reads from the shared cache if present (including the sibling price
    project's cache for <=2025); otherwise downloads via gridstatus and caches
    locally to ``data/sced_cache/``.
    """
    if isinstance(date, str):
        date = pd.Timestamp(date).date()
    elif isinstance(date, datetime):
        date = date.date()

    path = _read_existing_disclosure(date)
    if path is not None:
        try:
            raw = pd.read_parquet(path)
            # Our files are already trimmed (have resource_name); reuse-dir
            # files are full/raw and need trimming.
            if "resource_name" in raw.columns:
                return raw
            return _trim_to_operating(raw)
        except Exception as e:
            print(f"  ! cache read failed for {date} ({e}); refetching")

    if not allow_fetch:
        return pd.DataFrame()

    from ercot_core.gridstatus_client import ercot

    iso = ercot()
    print(f"  downloading SCED disclosure for {date} ...")
    try:
        data = iso.get_60_day_sced_disclosure(date=date)
    except Exception as e:
        print(f"  ! download failed for {date}: {e}")
        return pd.DataFrame()

    frames = []
    for key in ("sced_gen_resource", "sced_esr"):
        if key in data and data[key] is not None and not data[key].empty:
            try:
                frames.append(_trim_to_operating(data[key]))
            except Exception as e:
                print(f"  ! could not parse {key} for {date}: {e}")
    if not frames:
        return pd.DataFrame()
    trimmed = pd.concat(frames, ignore_index=True)
    local = os.path.join(_cache_dir(), f"disclosure_{date}.parquet")
    try:
        trimmed.to_parquet(local, index=False)
    except Exception as e:
        print(f"  ! could not cache {date}: {e}")
    return trimmed


def cached_disclosure_dates() -> list:
    """Dates we already have cached (across local + reuse dirs)."""
    dates = []
    search_dirs = [_cache_dir()] + _REUSE_DIRS
    for d in search_dirs:
        for p in glob.glob(os.path.join(d, "*disclosure_*.parquet")):
            stem = os.path.basename(p).replace(".parquet", "")
            ds = stem.split("disclosure_")[-1]
            try:
                dates.append(pd.Timestamp(ds).date())
            except Exception:
                pass
    return sorted(set(dates))
