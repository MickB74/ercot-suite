"""Unified data-lake layout for the ERCOT Data Hub.

Every dataset writes under one ``data/`` root instead of each repo scattering
parquets in its own working directory. The 60-day SCED disclosure cache is
*shared* (``data/sced_cache/``) so plant_sced and system_gen stop downloading
the same daily files twice.

    data/
      system_gen/        ercot_gen_by_fuel_<year>.parquet
                         resource_node_catalog.parquet
                         node_data/  node_generation_<year>.parquet, node_price_<year>.parquet
      eia923/            eia923_<region>_<year>.parquet, raw/  (cached zips)
      plant_sced/        plants.csv/.parquet, plant_names.csv, interconnection_queue.parquet
                         plants/  <RESOURCE>_<YEAR>.parquet
      hub_prices/        ercot_hub_prices_15min.parquet, .last_update.json
      sced_cache/        disclosure_<date>.parquet   (SHARED 60-day SCED)
      csv_exports/       on-demand CSV slices
    config.json          ONE ERCOT Public API credential store (git-ignored)
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of the ercot_core package directory.
ROOT = Path(__file__).resolve().parents[1]

# Allow an override (e.g. point at a shared drive) via env var.
DATA = Path(os.environ.get("ERCOT_HUB_DATA", ROOT / "data"))

# --- per-dataset directories ----------------------------------------------
SYSTEM_GEN_DIR = DATA / "system_gen"
NODE_DATA_DIR = SYSTEM_GEN_DIR / "node_data"
EIA_DIR = DATA / "eia923"
EIA_RAW_DIR = EIA_DIR / "raw"
PLANT_SCED_DIR = DATA / "plant_sced"
PLANT_DATA_DIR = PLANT_SCED_DIR / "plants"
HUB_PRICES_DIR = DATA / "hub_prices"
SOLAR_FORECAST_DIR = DATA / "solar_forecast"  # cached PVWatts forecasts (parquet)
WIND_FORECAST_DIR = DATA / "wind_forecast"    # cached wind forecasts (parquet)
PLANT_VALUE_DIR = DATA / "plant_value"        # cached plant capture-price valuations
EIA930_DIR = DATA / "eia930"                  # EIA-930 hourly net generation by BA

# --- shared / cross-dataset -------------------------------------------------
SCED_CACHE_DIR = DATA / "sced_cache"          # shared 60-day SCED disclosures
CSV_EXPORTS_DIR = DATA / "csv_exports"
LOGS_DIR = ROOT / "logs"

# --- canonical file paths ---------------------------------------------------
CONFIG_PATH = ROOT / "config.json"            # single credential store
CATALOG_PATH = SYSTEM_GEN_DIR / "resource_node_catalog.parquet"

# plant_sced registry + name crosswalk
PLANT_REGISTRY_CSV = PLANT_SCED_DIR / "plants.csv"
PLANT_REGISTRY_PARQUET = PLANT_SCED_DIR / "plants.parquet"
PLANT_NAMES_CSV = PLANT_SCED_DIR / "plant_names.csv"
PLANT_NAMES_OVERRIDES_CSV = PLANT_SCED_DIR / "plant_names_overrides.csv"
PLANT_NAMES_IFYI_CSV = PLANT_SCED_DIR / "plant_names_ifyi.csv"  # learned from interconnection.fyi lookups
PLANT_NAMES_RESOLVED_CSV = PLANT_SCED_DIR / "plant_names_resolved.csv"  # code->ifyi-name resolver
INTERCONNECTION_QUEUE_PARQUET = PLANT_SCED_DIR / "interconnection_queue.parquet"
INTERCONNECTION_QUEUE_FULL_PARQUET = PLANT_SCED_DIR / "interconnection_queue_full.parquet"
IFYI_ERCOT_PARQUET = PLANT_SCED_DIR / "ifyi_ercot_projects.parquet"  # bulk interconnection.fyi crawl

# hub_prices store
HUB_PRICES_PARQUET = HUB_PRICES_DIR / "ercot_hub_prices_15min.parquet"
HUB_PRICES_CSV = HUB_PRICES_DIR / "ercot_hub_prices_15min.csv"
HUB_PRICES_STATE = HUB_PRICES_DIR / ".last_update.json"

# eia930 store — hourly net generation (MWh) per balancing authority
EIA930_REGION_PARQUET = EIA930_DIR / "eia930_region_ng_hourly.parquet"
EIA930_STATE = EIA930_DIR / ".last_update.json"

# Curated renewable asset registry. Source of truth lives in the separate
# price_settlements repo; an 18 KB copy is vendored under ercot_core/registry/
# so the Hub stands alone (no hard dependency on a sibling checkout). Resolution
# order: explicit env override -> vendored copy -> sibling repo (for refresh).
# Refresh the vendored copy with scripts/sync_registry.py when both are local.
_VENDORED_ASSETS = ROOT / "ercot_core" / "registry" / "ercot_assets.json"
_SIBLING_ASSETS = ROOT.parent / "price_settlements" / "ercot_assets.json"


def _resolve_asset_registry() -> Path:
    env = os.environ.get("ERCOT_ASSETS_PATH")
    if env:
        return Path(env)
    if _VENDORED_ASSETS.exists():
        return _VENDORED_ASSETS
    return _SIBLING_ASSETS  # fallback for older layouts; may not exist


PRICE_SETTLEMENTS_ASSETS = _resolve_asset_registry()

# Hand-curated owner/offtaker overlay for the interconnection queue (the queue
# itself only names the project LLC). See ercot_core/queue_ownership.py.
QUEUE_OWNERSHIP_JSON = ROOT / "ercot_core" / "registry" / "queue_ownership.json"

_ALL_DIRS = [
    DATA, SYSTEM_GEN_DIR, NODE_DATA_DIR, EIA_DIR, EIA_RAW_DIR,
    PLANT_SCED_DIR, PLANT_DATA_DIR, HUB_PRICES_DIR, SCED_CACHE_DIR,
    CSV_EXPORTS_DIR, LOGS_DIR, SOLAR_FORECAST_DIR, WIND_FORECAST_DIR,
    PLANT_VALUE_DIR, EIA930_DIR,
]


def ensure_dirs() -> None:
    """Create the full data-lake directory tree (idempotent)."""
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# Datasets known to the orchestrator: key -> (dataset dir name, label).
DATASETS = {
    "system_gen": "System generation by fuel (15-min)",
    "hub_prices": "Hub settlement-point prices (RTM 15-min)",
    "plant_sced": "Plant-level SCED operating data",
    "eia923": "EIA-923 plant monthly generation & fuel",
}
