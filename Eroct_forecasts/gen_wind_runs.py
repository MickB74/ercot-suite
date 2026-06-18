#!/usr/bin/env python
"""Batch-generate ERA5 wind-production runs for the capture/revenue page.

Uses the keyless Wind Forecast engine (Open-Meteo ERA5) to cache 8760 runs for
representative ERCOT wind regions and weather years. Run with a python that has
the wind deps (the Ercot_Data_Hub venv):

    .../Ercot_Data_Hub/.venv/bin/python gen_wind_runs.py

A generic 200 MW fleet is used — capture *shape* is location-driven, and the
capture page lets you override nameplate. Existing cached files are skipped.
"""

from __future__ import annotations

import pathlib
import sys

# Resolve the Hub repo-relative first (sibling in this monorepo), home as fallback.
_HUB_CANDIDATES = [
    pathlib.Path(__file__).resolve().parents[1] / "Ercot_Data_Hub",
    pathlib.Path.home() / "Documents" / "Github" / "Ercot_Data_Hub",
]
HUB = next((p for p in _HUB_CANDIDATES if p.exists()), _HUB_CANDIDATES[0])
WF_DATASET = HUB / "datasets" / "wind_forecast"
CACHE_DIR = HUB / "data" / "wind_forecast"
sys.path.insert(0, str(HUB))         # for ercot_core
sys.path.insert(0, str(WF_DATASET))  # for wind_power / wind_app_ui

import wind_app_ui as wui  # noqa: E402
import wind_power as wp  # noqa: E402

# Representative ERCOT wind regions (lat, lon, label, settlement hub).
SITES = [
    (34.90, -101.90, "Panhandle (Amarillo)", "HB_PAN"),
    (27.40, -97.70, "South/Coastal (Kenedy)", "HB_SOUTH"),
]
YEARS = [("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")]


def fleet() -> wp.FleetConfig:
    return wp.FleetConfig(segments=[wp.TurbineSpec(
        count=80, rated_kw=2500.0, hub_height_m=100.0, rotor_m=130.0,
        curve_key="GENERIC_IEC2", label="generic")])  # 200 MW


def main() -> None:
    wiring = wui.Wiring(get_api_key=lambda: "", save_creds=lambda s: None,
                        cache_dir=CACHE_DIR, sced_loader=None)
    flt = fleet()
    for lat, lon, name, hub in SITES:
        for a, b in YEARS:
            token = f"era5:{a}:{b}"
            print(f"→ {name} {lat},{lon}  {a[:4]}  (settles {hub}) …", flush=True)
            label, df, _ = wui.run_or_load(wiring, lat, lon, token, flt, use_wpl=False)
            cf = (df["net_mw"] / flt.capacity_mw).mean() if "net_mw" in df else float("nan")
            print(f"   ok: {len(df)} hrs, annual CF {cf:.1%}", flush=True)
    print(f"\nDone. Cache: {CACHE_DIR}")


if __name__ == "__main__":
    main()
