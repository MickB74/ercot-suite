#!/usr/bin/env python3
"""Calibrate the top-N uncalibrated fleet plants by capacity (writes plant_value
gen + value parquets so build_hub credits calibration). Wind is keyless; solar
uses the NREL key from config.json."""
import sys, os, json, glob, time
HUB = "/Users/michaelbarry/Documents/Github/ercot-suite/Ercot_Data_Hub"
sys.path.insert(0, HUB); os.chdir(HUB)
from ercot_core.bootstrap import setup_path; setup_path()
from ercot_core import plant_value as pv

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
cfg = json.load(open("config.json"))
key, email = cfg.get("nrel_api_key"), cfg.get("nrel_email")

fleet = json.load(open("/Users/michaelbarry/Documents/Github/ercot-suite/Ercot_Project Hub/ercot_fleet.json"))
have = {p.split("/")[-1].split("value_")[1].rsplit("_HB_", 1)[0]
        for p in glob.glob("data/plant_value/value_*")}
todo = [a for a in fleet if a["resource_name"] not in have and a.get("lat") is not None]
todo.sort(key=lambda a: -(a.get("capacity_mw") or 0))
todo = todo[:N]
print(f"calibrating top {len(todo)} uncalibrated fleet plants by capacity", flush=True)

t0 = time.time(); ok = 0; fail = 0
for i, a in enumerate(todo, 1):
    try:
        pv.value_plant(a, year="tmy", api_key=key, email=email)
        ok += 1
        if i % 10 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] ok={ok} fail={fail} {time.time()-t0:.0f}s "
                  f"(last: {a['resource_name']} {a['tech']} {a['capacity_mw']}MW)", flush=True)
    except Exception as e:
        fail += 1
        print(f"  ✗ {a['resource_name']} ({a['tech']} {a.get('capacity_mw')}MW): {str(e)[:80]}", flush=True)
print(f"DONE: {ok} calibrated, {fail} failed, {time.time()-t0:.0f}s", flush=True)
