"""Portfolio-wide ERA5 wind-bias sweep: build an EIA-923 anchor for every ERCOT
wind plant with a node→EIA crosswalk, and emit one row per plant.

Resumable + incremental: each plant's result is appended to sweep_results.csv as
it finishes, and a plant whose anchor JSON already exists is reused (no refetch),
so re-running continues where it left off.

Caveat carried from the validation step: the EIA-derived factor is computed
against *metered* generation, which embeds dispatch curtailment. At congested
South-Texas nodes that understates the true ERA5 weather bias (the resource is
there but curtailed). The factor is still exactly right for revenue/settlement
modelling; for a pure meteorological bias read, treat congested nodes as a floor.

Run (Hub venv), backgrounded:
  Ercot_Data_Hub/.venv/bin/python build_full_wind_sweep.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

HUB = Path(__file__).resolve().parent / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "eia923"))
sys.path.insert(0, str(HUB / ".." / "Ercot_Wind_Forecast"))

from ercot_core import eia_anchor as ea, paths  # noqa: E402

OUT_CSV = ea.ANCHOR_DIR / "sweep_results.csv"
FIELDS = ["node", "eia", "name", "region_hub", "county", "lat", "lon", "cap_mw",
          "cod", "n_months", "actual_cf", "raw_model_cf", "energy_factor",
          "ws_corr", "p50_gwh", "status"]


def _region(lat, lon):
    """Coarse ERCOT region from coordinates (for the bias map grouping)."""
    if lat < 28.5 and lon > -99.5:
        return "Coastal/RGV"
    if lat > 34.0:
        return "Panhandle"
    if lon < -100.0:
        return "West"
    if lon > -97.5 and lat > 31:
        return "North/East"
    return "South/Central"


def candidates() -> list[dict]:
    xwalk = json.load(open(HUB / "ercot_core" / "registry" / "node_eia.json"))
    e = pd.read_parquet(paths.EIA_DIR / "eia860_ercot_2024.parquet")
    wind = e[e["prime_mover"] == "WT"]
    g = wind.groupby("plant_id").agg(
        cap=("nameplate_mw", "sum"), cod=("online_date", "min"),
        lat=("latitude", "first"), lon=("longitude", "first"),
        county=("county", "first"), name=("plant_name", "first")).reset_index()
    seen, out = set(), []
    for node, info in xwalk.items():
        eid = info.get("eia_id")
        if eid in seen:
            continue
        row = g[g["plant_id"] == eid]
        if row.empty or pd.isna(row.iloc[0]["cod"]):
            continue
        r = row.iloc[0]
        seen.add(eid)
        out.append({"node": node, "eia": int(eid), "name": info.get("eia_name"),
                    "county": r["county"], "lat": round(float(r["lat"]), 4),
                    "lon": round(float(r["lon"]), 4), "cap": round(float(r["cap"]), 1),
                    "cod": str(r["cod"])[:7]})
    return out


def done_nodes() -> set:
    if not OUT_CSV.exists():
        return set()
    try:
        d = pd.read_csv(OUT_CSV)
        return set(d[d["status"] == "ok"]["node"].astype(str))  # retry failures
    except Exception:  # noqa: BLE001
        return set()


def main():
    ea.ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    cands = candidates()
    already = done_nodes()
    new = OUT_CSV.exists()
    f = open(OUT_CSV, "a", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if not new:
        w.writeheader()
    print(f"[sweep] {len(cands)} unique wind plants; {len(already)} already done", flush=True)

    for i, c in enumerate(cands, 1):
        if c["node"] in already:
            continue
        tag = f"[{i}/{len(cands)}] {c['name'][:28]} ({c['node']})"
        try:
            spec = ea.spec_from_eia(c["node"], [c["eia"]], label=c["name"])
            a = ea.load(c["node"]) or ea.build(spec, log=lambda *_: None)
            if a["n_months"] < 24:
                raise ValueError(f"only {a['n_months']} months of EIA data")
            ef = a.get("overall_factor")
            raw = round(a["mean_cf"] / ef, 4) if ef else None
            w.writerow({
                "node": c["node"], "eia": c["eia"], "name": c["name"],
                "region_hub": _region(c["lat"], c["lon"]), "county": c["county"],
                "lat": c["lat"], "lon": c["lon"], "cap_mw": spec.capacity_full,
                "cod": spec.phases[0][1][:7], "n_months": a["n_months"],
                "actual_cf": a["mean_cf"], "raw_model_cf": raw,
                "energy_factor": ef, "ws_corr": a["ws_speed_correction"],
                "p50_gwh": round(a["annual_energy_p50_mwh"] / 1000, 0), "status": "ok"})
            f.flush()
            print(f"{tag}: factor×{ef} ws×{a['ws_speed_correction']} CF={a['mean_cf']}", flush=True)
        except Exception as ex:  # noqa: BLE001
            w.writerow({"node": c["node"], "eia": c["eia"], "name": c["name"],
                        "region_hub": _region(c["lat"], c["lon"]), "county": c["county"],
                        "lat": c["lat"], "lon": c["lon"], "cap_mw": c["cap"],
                        "cod": c["cod"], "status": f"FAIL: {str(ex)[:60]}"})
            f.flush()
            print(f"{tag}: FAIL {str(ex)[:80]}", flush=True)
    f.close()
    print(f"[sweep] DONE {dt.datetime.now():%H:%M:%S} → {OUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
