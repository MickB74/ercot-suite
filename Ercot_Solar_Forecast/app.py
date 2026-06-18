"""Standalone Streamlit app — PVWatts solar production forecast by lat/long.

Run:  .venv/bin/streamlit run app.py   (or: streamlit run app.py)

Stores the NREL API key/email in a git-ignored ``config.json`` beside this file
and caches forecasts as parquet under ``data/``. The forecasting engine
(``solar_pvwatts.py``) and UI (``solar_app_ui.py``) are shared verbatim with the
ERCOT Data Hub page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

import solar_app_ui as ui

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


def _save_creds(api_key: str, email: str) -> None:
    cfg = _load_cfg()
    cfg.update({"nrel_api_key": api_key, "nrel_email": email})
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


st.set_page_config(page_title="Solar Forecast — PVWatts", page_icon="☀️", layout="wide")

_cfg = _load_cfg()
wiring = ui.Wiring(
    get_api_key=lambda: _load_cfg().get("nrel_api_key", "") or os.environ.get("NREL_API_KEY", ""),
    get_email=lambda: _load_cfg().get("nrel_email", "") or os.environ.get("NREL_EMAIL", ""),
    save_creds=_save_creds,
    cache_dir=CACHE_DIR,
)
ui.render(st, wiring)
