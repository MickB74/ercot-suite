"""Forward price forecast — market-implied heat-rate model with MC scenarios.

The engine lives in the standalone ``Eroct_forecasts`` sibling repo and is shared
verbatim (like the solar/wind forecast pages). This page wires it into the Hub:
it points the engine's data lake at the Hub's shared ``hub_prices`` store for
history and caches forecast artifacts under the Hub's ``data/price_forecast/``.
"""

from __future__ import annotations

import os
import pathlib
import sys

# repo root (for ercot_core) + app/ (for _common), matching the other pages.
HUB_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HUB_ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/

from ercot_core import paths as hub_paths  # noqa: E402

# Route the forecast engine's lake at the Hub before importing it (pf_paths
# reads these at import time).
FORECAST_REPO = HUB_ROOT.parent / "Eroct_forecasts"
os.environ.setdefault("PF_DATA", str(hub_paths.DATA / "price_forecast"))
os.environ.setdefault("PF_HUB_LAKE_DIR", str(hub_paths.HUB_PRICES_DIR))

import streamlit as st  # noqa: E402

import _common  # noqa: F401,E402  (path bootstrap)

if not FORECAST_REPO.exists():
    st.title("⚡ ERCOT Price Forecast")
    st.error(
        f"Standalone engine not found at `{FORECAST_REPO}`.\n\n"
        "Clone/keep `Eroct_forecasts` as a sibling of `Ercot_Data_Hub`."
    )
    st.stop()

sys.path.insert(0, str(FORECAST_REPO))

import pf_app_ui  # noqa: E402

pf_app_ui.render()
