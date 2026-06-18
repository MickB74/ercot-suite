"""Solar production forecast — NREL PVWatts model on NSRDB weather, by lat/long.

TMY (typical year) or an actual historical weather year. The engine + UI live in
``datasets/solar_forecast`` and are shared verbatim with the standalone
Ercot_Solar_Forecast repo; this page just wires in the Hub's shared config.json
credential store and data-lake cache directory.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()  # puts datasets/solar_forecast on sys.path
import _common  # noqa: F401,E402  (path bootstrap)

import streamlit as st  # noqa: E402

from ercot_core import credentials, paths  # noqa: E402

import solar_app_ui as ui  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
paths.ensure_dirs()


@st.cache_data(show_spinner=False)
def _solar_projects() -> list[dict]:
    """ERCOT solar plants (lat/long + capacity) from EIA-860, for the picker."""
    import eia860

    import pandas as pd

    g = eia860.solar_plants(region="ercot")
    return [
        {"label": f"{r.plant_name} — {r.county} ({r.nameplate_mw:,.0f} MW)",
         "plant_id": int(r.plant_id), "lat": float(r.latitude), "lon": float(r.longitude),
         "capacity_mw": float(r.nameplate_mw), "array_type": r.array_type,
         "module_type": r.module_type,
         "tilt": None if pd.isna(r.tilt) else float(r.tilt),
         "azimuth": None if pd.isna(r.azimuth) else float(r.azimuth)}
        for r in g.itertuples()
    ]


@st.cache_data(show_spinner=False)
def _sced_for_plant(plant_id: int, year: int) -> dict:
    """Actual ERCOT SCED generation for an EIA plant, via the SCED↔EIA crosswalk."""
    import pandas as pd

    from ercot_core import reconcile as R

    resources = R.mapped_resources(plant_id)
    if not resources:
        return {"resources": [], "monthly": None}
    # Stored per-plant parquets only (no slow on-demand ERCOT fetch in the UI).
    monthly = R.sced_monthly(resources, pd.Timestamp(year, 1, 1),
                             pd.Timestamp(year, 12, 31), allow_fetch=False)
    return {"resources": resources, "monthly": None if monthly.empty else monthly}


wiring = ui.Wiring(
    get_api_key=credentials.get_nrel_api_key,
    get_email=credentials.get_nrel_email,
    save_creds=credentials.save_nrel_credentials,
    cache_dir=paths.SOLAR_FORECAST_DIR,
    project_loader=_solar_projects,
    sced_loader=_sced_for_plant,
)

ui.render(st, wiring)
