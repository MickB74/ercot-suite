"""Wind Capture & Revenue — price forecast × wind production.

Shares the engine in the standalone ``Eroct_forecasts`` sibling repo (like pages
13/14/16). Routes the price engine's lake at the Hub's hub_prices store and the
wind cache at the Hub's wind_forecast data, then renders the shared UI.
"""

from __future__ import annotations

import os
import pathlib
import sys

HUB_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HUB_ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/

from ercot_core import paths as hub_paths  # noqa: E402

FORECAST_REPO = HUB_ROOT.parent / "Eroct_forecasts"
os.environ.setdefault("PF_DATA", str(hub_paths.DATA / "price_forecast"))
os.environ.setdefault("PF_HUB_LAKE_DIR", str(hub_paths.HUB_PRICES_DIR))
os.environ.setdefault("WIND_CACHE_DIR", str(hub_paths.WIND_FORECAST_DIR))

import streamlit as st  # noqa: E402

import _common  # noqa: F401,E402

if not FORECAST_REPO.exists():
    st.title("💨 Predicted Wind Settlement")
    st.error(f"Standalone engine not found at `{FORECAST_REPO}`. Keep "
             "`Eroct_forecasts` as a sibling of `Ercot_Data_Hub`.")
    st.stop()

sys.path.insert(0, str(FORECAST_REPO))

import capture_app_ui  # noqa: E402

from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()  # puts datasets/eia923 (eia860) on sys.path


@st.cache_data(show_spinner=False)
def _nearest_project_name(lat: float, lon: float) -> "str | None":
    """Real project name nearest a run coordinate, or None if not on a wind site.

    Authority is the USWTDB (turbine-level coordinates) — the same source the Wind
    Forecast page uses for turbine detection, so names stay consistent across the
    two pages. Cross-checked once against EIA-860: on every genuine on-site run the
    two databases agree sub-km, while ambiguous points (no turbines within the
    radius) correctly return None and show coordinates only. Tight 10 km radius so a
    hypothetical point near a preset doesn't get a misleading nearby-farm label.
    """
    import turbine_db as tdb

    f = tdb.find_project_near(lat, lon, radius_km=10.0)
    return f.name if f else None


# Universal plant (shared with Plant Value / PPA Settlement). When it's a wind
# plant, hand its coordinate to the engine so the wind-site picker defaults to the
# nearest cached run; a solar plant just leaves the picker on its own default.
_uplant = _common.universal_plant_picker(st)
_preferred = None
if _uplant and str(_uplant.get("tech", "")).lower() == "wind":
    _preferred = (float(_uplant["lat"]), float(_uplant["lon"]),
                  _uplant.get("project_name", _uplant["resource_name"]))

capture_app_ui.render(resolve_name=_nearest_project_name, preferred=_preferred)
