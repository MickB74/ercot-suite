"""
Core library for fetching ERCOT plant-level SCED data.

Source: ERCOT 60-Day SCED Disclosure (Generation Resource Data), pulled via
gridstatus. Published with a ~60-day lag. SCED runs every ~5 minutes (older
years are ~15-min); we keep every interval ERCOT publishes ("native"), no
resampling.

We store the *operating set* of fields per plant: what the unit was actually
doing (telemetered net output, status), what it was dispatched to (base point),
its limits (HSL/LSL etc.), and its ancillary-service awards.

Layout
------
  disclosure_cache/   shared daily disclosure parquets (all resources, one/day)
  data/               canonical per-plant-per-year parquets  <RESOURCE>_<YEAR>.parquet
  plants.csv          registry of available resources (name, type, fuel group)
  plants.parquet      same, machine-readable

Times are stored tz-aware US/Central, exactly as ERCOT publishes them.
  - naive Central (to join the generation dataset):  s.dt.tz_localize(None)
  - absolute UTC (to join RTM prices):                s.dt.tz_convert("UTC")
"""

import os
import glob
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DISCLOSURE_DIR = os.path.join(HERE, "disclosure_cache")
DATA_DIR = os.path.join(HERE, "data")
REGISTRY_CSV = os.path.join(HERE, "plants.csv")
REGISTRY_PARQUET = os.path.join(HERE, "plants.parquet")

# Optionally reuse already-downloaded daily disclosures (read-only, speed only;
# anything missing is re-downloaded to our own DISCLOSURE_DIR). Set
# ERCOT_SCED_REUSE_DIR to a cache, else use a sibling price_settlements checkout
# iff present — no hard dependency on it.
def _default_reuse_dirs():
    env = os.environ.get("ERCOT_SCED_REUSE_DIR")
    if env:
        return [env]
    sibling = os.path.join(HERE, "..", "price_settlements", "sced_cache")
    return [sibling] if os.path.isdir(sibling) else []


_REUSE_DIRS = _default_reuse_dirs()

# ERCOT publishes the 60-day SCED disclosure with roughly this lag.
DISCLOSURE_LAG_DAYS = 60

for _d in (DISCLOSURE_DIR, DATA_DIR):
    os.makedirs(_d, exist_ok=True)


# --- The operating-set columns we keep, raw name -> clean name -------------
# Order here is the stored column order (after resource_name/timestamp).
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
    # ESR-only: the defining battery state. NA for conventional gen resources.
    "State of Charge": "state_of_charge",
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

# ERCOT "Resource Type" -> friendly fuel group used for filtering/listing.
FUEL_GROUP = {
    "PVGR": "Solar",
    "WIND": "Wind",
    "PWRSTR": "Storage",
    "NUC": "Nuclear",
    "HYDRO": "Hydro",
    "CCGT90": "Gas-CC",
    "CCLE90": "Gas-CC",
    "SCGT90": "Gas",
    "SCLE90": "Gas",
    "GSREH": "Gas",
    "GSSUP": "Gas",
    "GSNONR": "Gas",
    "CLLIG": "Coal/Lignite",
    "DSL": "Diesel",
    "RENEW": "Renewable",
    "PWRSTR": "Storage",  # batteries, pre-2026 (in gen frame)
    "ESR": "Storage",     # batteries, 2026+ (separate ESR frame)
}


def fuel_group_for(resource_type):
    if resource_type is None:
        return "Other"
    return FUEL_GROUP.get(str(resource_type).strip().upper(), "Other")


def latest_available_date():
    """Most recent operating day the 60-day disclosure should cover.

    Anchored on Central "today" (the ERCOT operating day), not the local clock.
    """
    import tzutil
    return (tzutil.now_central() - timedelta(days=DISCLOSURE_LAG_DAYS)).date()


# --- Daily disclosure (all resources for one day) --------------------------
def _read_existing_disclosure(date):
    """Return path to a cached full disclosure for `date` from any cache dir."""
    local = os.path.join(DISCLOSURE_DIR, f"disclosure_{date}.parquet")
    if os.path.exists(local):
        return local
    # Reuse the sibling price project's downloads to skip re-downloading.
    # BUT those files hold only the gen-resource frame. Through 2025 that frame
    # still carried batteries (PWRSTR); from 2026 batteries moved to a separate
    # ESR frame, so a 2026+ sibling file would silently drop all storage. Only
    # trust sibling files for years <= 2025.
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
        if raw in df.columns:
            out[clean] = df[raw]
        else:
            # Float NaN (not object NA) so all-missing columns concat cleanly.
            out[clean] = float("nan")

    # Coerce numerics up front so frames with different missing columns
    # (gen has no State of Charge; ESR has no Ancillary Service awards) share
    # consistent dtypes and concatenate without ambiguity.
    for c in NUMERIC_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def get_daily_disclosure(date, allow_fetch=True):
    """
    Return the trimmed operating-set disclosure (all resources) for one day.
    Reads from cache if present (including the sibling price project's cache);
    otherwise downloads via gridstatus and caches locally.
    """
    if isinstance(date, str):
        date = pd.Timestamp(date).date()
    elif isinstance(date, datetime):
        date = date.date()

    path = _read_existing_disclosure(date)
    if path is not None:
        try:
            raw = pd.read_parquet(path)
            # Reuse-dir files are full/raw; our local files are already trimmed.
            if "resource_name" in raw.columns:
                return raw
            return _trim_to_operating(raw)
        except Exception as e:
            print(f"  ! cache read failed for {date} ({e}); refetching")

    if not allow_fetch:
        return pd.DataFrame()

    import gridstatus
    iso = gridstatus.Ercot()
    print(f"  downloading SCED disclosure for {date} ...")
    try:
        data = iso.get_60_day_sced_disclosure(date=date)
    except Exception as e:
        print(f"  ! download failed for {date}: {e}")
        return pd.DataFrame()

    # Conventional generators live in sced_gen_resource; batteries (ESRs) live
    # in sced_esr (separate frame since the 2026 single-model go-live). Combine
    # both into one operating-set table so storage is covered.
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
    local = os.path.join(DISCLOSURE_DIR, f"disclosure_{date}.parquet")
    try:
        trimmed.to_parquet(local, index=False)
    except Exception as e:
        print(f"  ! could not cache {date}: {e}")
    return trimmed


# --- Registry of available plants ------------------------------------------
def build_registry(date=None, allow_fetch=True):
    """
    Build/refresh the list of available resources from a disclosure day.
    Defaults to the most recent locally-cached day, else the latest available.
    Writes plants.csv and plants.parquet.
    """
    if date is None:
        # Prefer the newest day we already have cached anywhere.
        cached = []
        for d in [DISCLOSURE_DIR] + _REUSE_DIRS:
            cached += glob.glob(os.path.join(d, "*disclosure_*.parquet"))
        if cached:
            dates = []
            for p in cached:
                stem = os.path.basename(p).replace(".parquet", "")
                ds = stem.split("disclosure_")[-1]
                try:
                    dates.append(pd.Timestamp(ds).date())
                except Exception:
                    pass
            date = max(dates) if dates else latest_available_date()
        else:
            date = latest_available_date()

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
        import plant_names
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
    """
    Fetch native SCED operating data for one or more resources over a date range.

    Efficient: each day's disclosure is loaded once and shared across all
    requested resources. Returns {resource_name: DataFrame}. When write=True,
    merges into canonical per-plant-per-year parquets under data/.
    """
    if isinstance(resources, str):
        resources = [resources]
    resources = list(dict.fromkeys(resources))  # de-dup, keep order
    wanted = set(resources)

    if isinstance(start_date, str):
        start_date = pd.Timestamp(start_date).date()
    if isinstance(end_date, str):
        end_date = pd.Timestamp(end_date).date()

    # Respect the 60-day publication lag.
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
