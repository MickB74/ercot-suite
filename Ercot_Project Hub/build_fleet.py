#!/usr/bin/env python3
"""
Build the full ERCOT operating wind/solar fleet roster from EIA-860.

Produces ``ercot_fleet.json`` (next to this script): one entry per operating
EIA-860 wind or solar plant in ERCOT, enriched with an ERCOT SCED resource
crosswalk, turbine/array specs (USWTDB / USPVDB), a derived hub, and the EIA
plant id. The Project Hub merges this with the curated ``ercot_assets.json``
(curated entries win) so the data-quality index covers the whole fleet, not just
the hand-picked settlement assets.

Calibration (plant_value gen/value parquets) is NOT run here — that's the slow,
API-bound step; run it separately as a batch.
"""
from __future__ import annotations

import json
import math
import os
import sys
import pathlib

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.dirname(HUB_DIR)
HUB = os.path.join(SUITE_ROOT, "Ercot_Data_Hub")
sys.path.insert(0, HUB)

OUT = os.path.join(HUB_DIR, "ercot_fleet.json")
USWTDB = os.path.join(HUB, "datasets", "wind_forecast", "reference", "uswtdb_tx.json")
USPVDB = os.path.join(HUB, "datasets", "solar_forecast", "reference", "uspvdb_tx.json")


def hav(a, b, c, d):
    p = math.pi / 180
    x = 0.5 - math.cos((c - a) * p) / 2 + math.cos(a * p) * math.cos(c * p) * (1 - math.cos((d - b) * p)) / 2
    return 12742 * math.asin(math.sqrt(x))


def derive_hub(lat, lon, county):
    """Approximate ERCOT hub from location. Heuristic — flagged hub_source."""
    if lat is None or lon is None:
        return "North"
    if lat >= 34.3:
        return "Pan"            # Panhandle
    if lon <= -100.0:
        return "West"          # far west TX
    if lat <= 29.6:
        return "South"         # south TX
    if lon >= -96.3 and lat <= 31.0:
        return "Houston"       # SE TX / Houston metro
    return "North"


def conf_from_km(d):
    return "high" if d < 2 else "medium" if d < 8 else "low"


def main():
    from ercot_core.bootstrap import setup_path
    setup_path()
    import eia860
    from ercot_core import reconcile as R

    df = eia860.load(years=[2024], region="ercot")
    # Wind = onshore wind; Solar = solar PV (exclude the battery rows of hybrids).
    is_wind = df["technology"].astype(str).str.contains("Wind", case=False, na=False)
    is_solar = df["technology"].astype(str).str.contains("Solar", case=False, na=False)
    gen = df[is_wind | is_solar].copy()
    gen["tech"] = ["Wind" if w else "Solar" for w in gen["technology"].astype(str).str.contains("Wind", case=False, na=False)]

    # Aggregate generator rows -> one record per (plant, tech).
    plants = {}
    for _, r in gen.iterrows():
        pid = int(r["plant_id"]); tech = r["tech"]
        key = (pid, tech)
        p = plants.setdefault(key, {"eia_plant_id": pid, "project_name": str(r["plant_name"]),
                                    "tech": tech, "capacity_mw": 0.0,
                                    "county": str(r["county"]), "lat": None, "lon": None,
                                    "online": None})
        p["capacity_mw"] += float(r["nameplate_mw"] or 0)
        if r.get("latitude") is not None and p["lat"] is None:
            p["lat"] = float(r["latitude"]); p["lon"] = float(r["longitude"])
        od = r.get("online_date")
        if od is not None and str(od) != "NaT":
            s = str(od)[:10]
            p["online"] = min(p["online"], s) if p["online"] else s

    # EIA plant id -> ERCOT SCED resource names (invert the auto-crosswalk).
    xwalk = {}
    try:
        ax = R.auto_crosswalk((2024,), cap_tol=0.20)
        for _, r in ax.iterrows():
            if str(r.get("confidence")) in ("high", "medium"):
                xwalk.setdefault(int(r["eia_plant_id"]), []).append(str(r["resource_name"]))
    except Exception as e:  # noqa: BLE001
        print(f"  (auto_crosswalk unavailable: {e})")

    uswtdb = json.load(open(USWTDB)) if os.path.exists(USWTDB) else []
    uspvdb = json.load(open(USPVDB)) if os.path.exists(USPVDB) else []
    AX = {"single-axis": "single_axis", "fixed-tilt": "fixed_tilt", "dual-axis": "dual_axis"}

    fleet = []
    for (pid, tech), p in plants.items():
        p["capacity_mw"] = round(p["capacity_mw"], 1)
        p["hub"] = derive_hub(p["lat"], p["lon"], p["county"])
        p["hub_source"] = "derived from lat/lon (heuristic)"
        p["status"] = "operating"
        p["source"] = "EIA-860 2024"
        res = xwalk.get(pid)
        if res:
            p["resource_name"] = res[0]
            p["sced_units"] = res
        else:
            p["resource_name"] = f"EIA_{pid}"   # synthetic key; no SCED match found
        # spec + location-confidence backfill from the matching reference DB
        if p["lat"] is not None:
            if tech == "Wind" and uswtdb:
                best = min(((hav(p["lat"], p["lon"], float(x["ylat"]), float(x["xlong"])), x)
                            for x in uswtdb if x.get("ylat")), key=lambda t: t[0], default=(999, None))
                d, x = best
                if x and d <= 8 and x.get("segments"):
                    s = max(x["segments"], key=lambda s: (s.get("count") or 0) * (s.get("rated_kw") or 0))
                    if s.get("manufacturer") and s["manufacturer"] != "Unknown":
                        p["turbine_manuf"] = s["manufacturer"]
                    if s.get("model") and s["model"] != "Unknown":
                        p["turbine_model"] = s["model"]
                    if s.get("hub_height_m"):
                        p["hub_height_m"] = round(float(s["hub_height_m"]), 1)
                    if s.get("rotor_m"):
                        p["rotor_diameter_m"] = round(float(s["rotor_m"]), 1)
                    p["location_confidence"] = conf_from_km(d)
            elif tech == "Solar" and uspvdb:
                best = min(((hav(p["lat"], p["lon"], float(x["ylat"]), float(x["xlong"])), x)
                            for x in uspvdb if x.get("ylat")), key=lambda t: t[0], default=(999, None))
                d, x = best
                if x and d <= 8:
                    ax_t = AX.get(str(x.get("p_axis")))
                    if ax_t:
                        p["tracking_type"] = ax_t
                    try:
                        if x.get("p_cap_ac") and x.get("p_cap_dc"):
                            ratio = float(x["p_cap_dc"]) / float(x["p_cap_ac"])
                            if 1.0 <= ratio <= 1.8:
                                p["dc_ac_ratio"] = round(ratio, 3)
                    except Exception:  # noqa: BLE001
                        pass
                    p["location_confidence"] = conf_from_km(d)
        p.pop("online", None)
        fleet.append(p)

    json.dump(fleet, open(OUT, "w"), indent=1)
    nw = sum(1 for p in fleet if p["tech"] == "Wind")
    ns = sum(1 for p in fleet if p["tech"] == "Solar")
    matched = sum(1 for p in fleet if not p["resource_name"].startswith("EIA_"))
    print(f"Wrote {len(fleet)} fleet entries ({nw} wind, {ns} solar) -> ercot_fleet.json")
    print(f"  {matched} crosswalked to an ERCOT SCED resource; {len(fleet)-matched} synthetic keys")


if __name__ == "__main__":
    main()
