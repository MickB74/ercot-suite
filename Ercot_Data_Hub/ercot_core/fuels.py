"""Shared ERCOT fuel taxonomy, schema, and provenance rules.

This unifies what used to be three separate fuel mappings:
  * system_gen/ercot_fuels.py   — 15-min Fuel Mix Report canonical fuels + the
                                   provenance merge engine (the bulk of this file)
  * eia923.py FUEL_CATEGORY     — EIA reported fuel-code -> category
  * plant_sced FUEL_GROUP       — ERCOT SCED Resource Type -> fuel group

They describe the same physical fuels at three different grains, so they live
together here with one canonical fuel list as the anchor.
"""

from __future__ import annotations

import pandas as pd

# === Canonical fuel categories (ERCOT Fuel Mix Report native set) ==========
CANONICAL_FUELS = [
    "Biomass",
    "Coal",
    "Gas",            # simple-cycle / boiler gas (report "Gas")
    "Gas-CC",         # combined-cycle gas
    "Hydro",
    "Nuclear",
    "Other",
    "Power Storage",  # report "WSL"; can be negative when charging
    "Solar",
    "Wind",
]

# Report sometimes labels storage "WSL" (Wind Storage Load) -> Power Storage.
REPORT_FUEL_RENAME = {"WSL": "Power Storage"}

# === Source provenance (15-min generation merge) ===========================
# Lower priority number == more authoritative. The merge keeps, for each
# (interval_start, fuel) pair, the row with the lowest priority; ties broken by
# most-recent fetched_at.
SOURCE_FUEL_MIX_REPORT = "fuel_mix_report"
SOURCE_DASHBOARD = "ercot_dashboard"
SOURCE_API = "ercot_api"

ST_FINAL = "FINAL"
ST_PROVISIONAL = "PROVISIONAL"


def source_priority(source: str, settlement_type: str) -> int:
    if source == SOURCE_FUEL_MIX_REPORT:
        return 0 if str(settlement_type).upper() == ST_FINAL else 1
    if source == SOURCE_API:
        return 2  # settlement-quality 5-min actuals, renewables only
    if source == SOURCE_DASHBOARD:
        return 3  # real-time telemetry, coarsest
    return 9


# Dashboard reports a coarser set; map what we can and accept the loss.
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

# === Unified tidy schema (15-min generation) ===============================
SCHEMA_COLUMNS = [
    "interval_start",   # datetime64[ns], naive CPT, start of 15-min interval
    "interval_end",     # datetime64[ns], naive CPT
    "fuel",             # one of CANONICAL_FUELS
    "mw",               # float, average MW over the interval
    "settlement_type",  # FINAL / INITIAL / PRELIM / PROVISIONAL
    "source",           # fuel_mix_report / ercot_dashboard / ercot_api
    "priority",         # int, lower == more authoritative (derived)
    "fetched_at",       # datetime64[ns, UTC]
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
    single most-authoritative row (lowest priority, then most-recent fetched_at).
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

    Thin re-export of :func:`ercot_core.tz.to_utc` (kept here for the existing
    callers that import it from ``fuels``).
    """
    from ercot_core import tz
    return tz.to_utc(series)


# === EIA-923 reported fuel-code -> canonical category ======================
# (aligned with CANONICAL_FUELS; adds Oil / Other Gas / Geothermal / Storage
# which EIA distinguishes but the 15-min report folds into "Other".)
EIA_FUEL_CATEGORY = {
    # Coal
    "ANT": "Coal", "BIT": "Coal", "LIG": "Coal", "SUB": "Coal", "RC": "Coal",
    "SGC": "Coal", "WC": "Coal", "SC": "Coal", "CBL": "Coal",
    # Natural gas
    "NG": "Gas",
    # Other manufactured / waste gas
    "OG": "Other Gas", "BFG": "Other Gas", "PG": "Other Gas", "SGP": "Other Gas",
    # Petroleum / oil
    "DFO": "Oil", "RFO": "Oil", "JF": "Oil", "KER": "Oil", "PC": "Oil",
    "WO": "Oil", "RG": "Oil",
    # Nuclear
    "NUC": "Nuclear",
    # Renewables
    "WAT": "Hydro", "WND": "Wind", "SUN": "Solar", "GEO": "Geothermal",
    # Biomass / waste-derived
    "AB": "Biomass", "BLQ": "Biomass", "DG": "Biomass", "LFG": "Biomass",
    "MSB": "Biomass", "MSN": "Biomass", "OBG": "Biomass", "OBL": "Biomass",
    "OBS": "Biomass", "OBW": "Biomass", "SLW": "Biomass", "TDF": "Biomass",
    "WDL": "Biomass", "WDS": "Biomass", "MSW": "Biomass",
    # Storage / other
    "MWH": "Storage", "PUR": "Other", "WH": "Other", "OTH": "Other",
}


def eia_fuel_category(fuel_code) -> str:
    return EIA_FUEL_CATEGORY.get(str(fuel_code).strip().upper(), "Other")


# === ERCOT SCED "Resource Type" -> friendly fuel group =====================
SCED_FUEL_GROUP = {
    "PVGR": "Solar",
    "WIND": "Wind",
    "PWRSTR": "Storage",   # batteries, pre-2026 (in gen frame)
    "ESR": "Storage",      # batteries, 2026+ (separate ESR frame)
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
}


def sced_fuel_group(resource_type) -> str:
    if resource_type is None:
        return "Other"
    return SCED_FUEL_GROUP.get(str(resource_type).strip().upper(), "Other")
