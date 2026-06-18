"""Wind production forecast — physics by lat/long on real turbine fleets.

Builds an hourly forecast from the USWTDB turbine fleet at a coordinate,
measured wind shear, air-density-corrected power curves, multi-source weather
(ERA5 reanalysis + NWP ensemble), and ERCOT-region calibration. The engine + UI
live in ``datasets/wind_forecast`` and are shared verbatim with the standalone
Ercot_Wind_Forecast repo; this page just wires in the Hub's shared config.json
credential store and data-lake cache directory.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()  # puts datasets/wind_forecast on sys.path
import _common  # noqa: F401,E402  (path bootstrap)

import streamlit as st  # noqa: E402

from ercot_core import credentials, paths  # noqa: E402

import wind_app_ui as ui  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
paths.ensure_dirs()


@st.cache_data(show_spinner=False)
def _ercot_wind_plants() -> "list[dict]":
    """ERCOT wind plants (lat/long + capacity) from EIA-860, for coordinate→plant matching."""
    import eia860

    g = eia860.wind_plants(region="ercot")
    return [
        {"plant_id": int(r.plant_id), "plant_name": r.plant_name, "county": r.county,
         "lat": float(r.latitude), "lon": float(r.longitude),
         "capacity_mw": float(r.nameplate_mw)}
        for r in g.itertuples()
    ]


def _sced_near(lat: float, lon: float, year: int) -> dict:
    """Bridge a forecast coordinate to actual ERCOT SCED generation.

    Finds the EIA-860 wind plant nearest (lat, lon), resolves its mapped ERCOT
    resource(s) via the SCED↔EIA crosswalk, and sums their stored monthly SCED
    energy for the given calendar year. Stored per-plant parquets only — no slow
    on-demand ERCOT fetch from the UI.
    """
    import math

    import pandas as pd

    from ercot_core import reconcile as R

    plants = _ercot_wind_plants()
    if not plants:
        return {}

    def _haversine_km(a_lat, a_lon, b_lat, b_lon):
        r = 6371.0
        p1, p2 = math.radians(a_lat), math.radians(b_lat)
        dphi = math.radians(b_lat - a_lat)
        dlmb = math.radians(b_lon - a_lon)
        h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

    nearest = min(plants, key=lambda p: _haversine_km(lat, lon, p["lat"], p["lon"]))
    dist = _haversine_km(lat, lon, nearest["lat"], nearest["lon"])
    # Guard against matching a far-away plant when the point isn't on a wind site.
    if dist > 25.0:
        return {"distance_km": round(dist, 1)}

    resources = R.mapped_resources(nearest["plant_id"])
    monthly = None
    if resources:
        m = R.sced_monthly(resources, pd.Timestamp(year, 1, 1),
                           pd.Timestamp(year, 12, 31), allow_fetch=False)
        monthly = None if m.empty else m
    return {
        "plant_name": nearest["plant_name"], "plant_id": nearest["plant_id"],
        "county": nearest["county"], "capacity_mw": nearest["capacity_mw"],
        "distance_km": round(dist, 1), "resources": resources, "monthly": monthly,
    }


def _resolve_project(query: str) -> "list[dict]":
    """Resolve an ERCOT wind project / plant name (or queue ID) to coordinates.

    Primary path: substring match against EIA-860 ERCOT wind plants, which carry
    lat/long directly. Fallback for queue IDs and names that don't match an EIA
    plant name: ``project_lookup`` resolves the query to ERCOT resource node(s),
    which the SCED↔EIA crosswalk maps back to an EIA plant_id — and thence to that
    plant's coordinates. Returns up to a dozen candidates, best (direct) first.
    """
    plants = _ercot_wind_plants()
    if not plants:
        return []

    by_id = {p["plant_id"]: p for p in plants}
    hits: list[dict] = []
    seen: set[int] = set()

    def _add(p: dict, note: str = "") -> None:
        if p["plant_id"] in seen:
            return
        seen.add(p["plant_id"])
        label = f"{p['plant_name']} — {p['county']} Co · {p['capacity_mw']:.0f} MW"
        if note:
            label += f" · {note}"
        hits.append({"label": label, "lat": p["lat"], "lon": p["lon"],
                     "plant_id": p["plant_id"], "capacity_mw": p["capacity_mw"],
                     "county": p["county"]})

    # 1) Direct EIA-860 plant-name substring match.
    q = query.strip().lower()
    for p in sorted(plants, key=lambda r: r["plant_name"]):
        if q in p["plant_name"].lower():
            _add(p)

    # 2) Fallback: queue ID / market name -> resource node(s) -> plant_id -> coords.
    if not hits:
        try:
            from ercot_core import project_lookup, reconcile as R

            res = project_lookup.lookup(query, allow_fetch=False)
            resources = {u for c in res.get("candidates", []) for u in c.get("units", [])}
            if resources:
                xwalk = R.load_crosswalk()
                for _, row in xwalk.iterrows():
                    names = set(str(row["resource_names"]).split(";"))
                    if names & resources:
                        pid = int(row["eia_plant_id"])
                        if pid in by_id:
                            _add(by_id[pid], note="via resource match")
        except Exception:
            pass

    return hits[:12]


# The wind engine is keyless (Open-Meteo weather, bundled USWTDB extract); the
# get_api_key/save_creds hooks are reserved for an optional NREL WIND Toolkit
# cross-check, so we point them at the shared NREL credential store for parity
# with the Solar Forecast page.
wiring = ui.Wiring(
    get_api_key=credentials.get_nrel_api_key,
    save_creds=lambda api_key: credentials.save_nrel_credentials(
        api_key, credentials.get_nrel_email()),
    cache_dir=paths.WIND_FORECAST_DIR,
    sced_loader=_sced_near,
    resolve_project=_resolve_project,
)

ui.render(st, wiring)
