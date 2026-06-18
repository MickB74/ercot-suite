"""Data-lake layout for the ERCOT price forecast engine.

Reads ERCOT historical hub prices from the shared Data Hub lake (or the older
Ercot_Price_Data repo) and writes its own forecast artifacts under ``data/``.

    data/
      inputs/      henry_hub_monthly_seed.csv   (bootstrap gas history, git-tracked)
                   gas_curve.csv                 (manual Henry Hub forward strip)
                   ercot_power_strip.csv         (manual ERCOT power futures strip)
      gas/         henry_hub_daily.parquet       (EIA-refreshed gas history cache)
      forecasts/   forecast_<HUB>_<ASOF>.parquet         (monthly strip + bands)
                   forecast_<HUB>_<ASOF>_8760.parquet    (hourly shaped scenarios)
    config.json    eia_api_key + optional hub_lake_dir override (git-ignored)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Generated artifacts (forecasts, EIA cache) follow PF_DATA; the input templates
# (seed history, manual strips) ship repo-local and stay there unless the user
# drops an override into <PF_DATA>/inputs/.
DATA = Path(os.environ.get("PF_DATA", ROOT / "data"))
REPO_INPUTS_DIR = ROOT / "data" / "inputs"   # tracked defaults, always present
INPUTS_DIR = DATA / "inputs"                  # optional user overrides
GAS_DIR = DATA / "gas"
FORECASTS_DIR = DATA / "forecasts"

CONFIG_PATH = ROOT / "config.json"


def _input(name: str) -> Path:
    """Prefer a user override in <PF_DATA>/inputs/, else the repo default."""
    override = INPUTS_DIR / name
    return override if override.exists() else REPO_INPUTS_DIR / name


# input files (resolved with override-then-repo fallback)
HENRY_HUB_SEED_CSV = _input("henry_hub_monthly_seed.csv")
GAS_CURVE_CSV = _input("gas_curve.csv")            # manual forward strip
POWER_STRIP_CSV = _input("ercot_power_strip.csv")  # manual power futures
HENRY_HUB_DAILY_PARQUET = GAS_DIR / "henry_hub_daily.parquet"
GAS_FORWARD_PARQUET = GAS_DIR / "eia_gas_forward.parquet"  # cached EIA fwd strip

_ALL_DIRS = [DATA, INPUTS_DIR, GAS_DIR, FORECASTS_DIR]

# Candidate locations for the ERCOT hub-price parquet, in preference order.
_HUB_LAKE_CANDIDATES = [
    Path.home() / "Documents" / "Github" / "Ercot_Data_Hub" / "data" / "hub_prices",
    Path.home() / "Documents" / "Github" / "Ercot_Price_Data" / "data" / "hub_prices",
    Path.home() / "Documents" / "Github" / "Ercot_Price_Data" / "data",
]
_HUB_PARQUET_NAME = "ercot_hub_prices_15min.parquet"
_DAM_PARQUET_NAME = "ercot_hub_dam_hourly.parquet"


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _hub_lake_dir() -> Path | None:
    """Resolve the hub_prices directory: config override, then candidates."""
    override = load_config().get("hub_lake_dir") or os.environ.get("PF_HUB_LAKE_DIR")
    if override:
        p = Path(override).expanduser()
        if p.exists():
            return p
    for c in _HUB_LAKE_CANDIDATES:
        if (c / _HUB_PARQUET_NAME).exists():
            return c
    return None


def hub_prices_parquet() -> Path | None:
    d = _hub_lake_dir()
    return (d / _HUB_PARQUET_NAME) if d else None


def dam_prices_parquet() -> Path | None:
    d = _hub_lake_dir()
    if d and (d / _DAM_PARQUET_NAME).exists():
        return d / _DAM_PARQUET_NAME
    return None


def eia_api_key() -> str:
    return load_config().get("eia_api_key", "") or os.environ.get("EIA_API_KEY", "")


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def set_eia_api_key(key: str) -> None:
    cfg = load_config()
    cfg["eia_api_key"] = key.strip()
    save_config(cfg)
    os.environ["EIA_API_KEY"] = key.strip()  # live for this session too
