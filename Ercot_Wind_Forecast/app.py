"""Standalone Streamlit app — wind production forecast by lat/long.

Run:  .venv/bin/streamlit run app.py   (or double-click "Open Wind Forecast.command")

Caches forecasts as parquet under ``data/`` and stores an optional NREL API key
(reserved for the WIND Toolkit cross-check) in a git-ignored ``config.json``.
The engine (``wind_power.py``), turbine resolver (``turbine_db.py``),
calibration (``wind_calibration.py``) and UI (``wind_app_ui.py``) are designed
to drop unchanged into an ERCOT Data Hub page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

import wind_app_ui as ui

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
CACHE_DIR = HERE / "data"


def _load_cfg() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_creds(api_key: str) -> None:
    cfg = _load_cfg()
    cfg["nrel_api_key"] = api_key
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


st.set_page_config(page_title="Wind Forecast", page_icon="🌬️", layout="wide")

wiring = ui.Wiring(
    get_api_key=lambda: _load_cfg().get("nrel_api_key", "") or os.environ.get("NREL_API_KEY", ""),
    save_creds=_save_creds,
    cache_dir=CACHE_DIR,
)
ui.render(st, wiring)
