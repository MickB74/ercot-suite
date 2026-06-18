"""Shared taxonomy, schema, and provenance rules for ERCOT 15-minute
generation-by-source data.

The canonical fuel taxonomy is ERCOT's own Fuel Mix Report categories, because
that report is the authoritative backbone and carries the finest breakdown.
Provisional sources (the real-time dashboard, the Public API wind/solar feeds)
are mapped *into* this taxonomy as closely as they allow.

Every row in the unified dataset carries provenance so stitched-together data is
self-describing and so provisional rows can be deterministically *replaced* by
authoritative ones when ERCOT eventually publishes them.
"""

from __future__ import annotations

import pandas as pd

# --- Canonical fuel categories (ERCOT Fuel Mix Report native set) ----------
CANONICAL_FUELS = [
    "Biomass",
    "Coal",
    "Gas",        # simple-cycle / boiler gas (report "Gas")
    "Gas-CC",     # combined-cycle gas
    "Hydro",
    "Nuclear",
    "Other",
    "Power Storage",  # report "WSL"; can be negative when charging
    "Solar",
    "Wind",
]

# Report sometimes labels storage "WSL" (Wind Storage Load) -> Power Storage.
REPORT_FUEL_RENAME = {"WSL": "Power Storage"}

# --- Source provenance -----------------------------------------------------
# Lower priority number == more authoritative. The merge keeps, for each
# (interval_start, fuel) pair, the row with the lowest priority; ties broken by
# most-recent fetched_at. This is how FINAL report data REPLACES provisional
# dashboard/API rows once ERCOT posts it.
SOURCE_FUEL_MIX_REPORT = "fuel_mix_report"
SOURCE_DASHBOARD = "ercot_dashboard"
SOURCE_API = "ercot_api"

# settlement_type values: from the report (FINAL/INITIAL/PRELIM) or synthetic
# for provisional feeds.
ST_FINAL = "FINAL"
ST_PROVISIONAL = "PROVISIONAL"

# (source, is_report_final) -> priority rank
def source_priority(source: str, settlement_type: str) -> int:
    if source == SOURCE_FUEL_MIX_REPORT:
        return 0 if str(settlement_type).upper() == ST_FINAL else 1
    if source == SOURCE_API:
        return 2  # settlement-quality 5-min actuals, renewables only
    if source == SOURCE_DASHBOARD:
        return 3  # real-time telemetry, coarsest
    return 9


# --- Provisional-source category mapping into the canonical taxonomy -------
# The dashboard reports a coarser set; map what we can and accept the loss
# (these rows are provisional and will be replaced by the report).
DASHBOARD_FUEL_MAP = {
    "Coal and Lignite": "Coal",
    "Natural Gas": "Gas",          # NB: combines Gas + Gas-CC
    "Nuclear": "Nuclear",
    "Hydro": "Hydro",
    "Solar": "Solar",
    "Wind": "Wind",
    "Power Storage": "Power Storage",
    "Other": "Other",              # NB: includes biomass on the dashboard
}

# --- Unified tidy schema ---------------------------------------------------
# interval_start / interval_end are tz-naive Central Prevailing Time (CPT), as
# published by ERCOT. Storing naive CPT is lossless across DST transitions;
# use to_utc() when you need an absolute timeline.
SCHEMA_COLUMNS = [
    "interval_start",   # datetime64[ns], naive CPT, start of 15-min interval
    "interval_end",     # datetime64[ns], naive CPT
    "fuel",             # one of CANONICAL_FUELS
    "mw",               # float, average MW over the interval (negative ok for storage)
    "settlement_type",  # FINAL / INITIAL / PRELIM / PROVISIONAL
    "source",           # fuel_mix_report / ercot_dashboard / ercot_api
    "priority",         # int, lower == more authoritative (derived)
    "fetched_at",       # datetime64[ns, UTC], when this row was retrieved
]

KEY_COLUMNS = ["interval_start", "fuel"]


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a source DataFrame to the canonical schema + dtypes."""
    df = df.copy()
    df["priority"] = [
        source_priority(s, st)
        for s, st in zip(df["source"], df["settlement_type"])
    ]
    df["interval_start"] = pd.to_datetime(df["interval_start"])
    df["interval_end"] = pd.to_datetime(df["interval_end"])
    df["mw"] = pd.to_numeric(df["mw"], downcast="float")
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True)
    return df[SCHEMA_COLUMNS]


def merge_with_provenance(*frames: pd.DataFrame) -> pd.DataFrame:
    """Concatenate source frames and resolve each (interval_start, fuel) to the
    single most-authoritative row.

    Resolution order: lowest `priority`, then most-recent `fetched_at`. This is
    the engine that (a) lets the FINAL Fuel Mix Report replace provisional
    dashboard/API rows, and (b) lets a re-downloaded report replace an older
    INITIAL value with its FINAL revision.
    """
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=SCHEMA_COLUMNS)

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["interval_start", "fuel", "priority", "fetched_at"],
        ascending=[True, True, True, False],
    )
    combined = combined.drop_duplicates(subset=KEY_COLUMNS, keep="first")
    return combined.reset_index(drop=True)


def to_utc(series: pd.Series) -> pd.Series:
    """Convert naive CPT interval timestamps to tz-aware UTC.

    DST fall-back hours are genuinely ambiguous in local clock time; we infer
    the order. Spring-forward gaps are shifted forward. Good enough for joins;
    flagged here so callers know the caveat.
    """
    return (
        pd.to_datetime(series)
        .dt.tz_localize("US/Central", ambiguous="infer", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )
