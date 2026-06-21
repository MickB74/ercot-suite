"""Pull missing SCED and node-generation data for Mesquite Star (WH_WIND).

Run with the Hub venv:
  source ~/Documents/Github/ercot-suite/Ercot_Data_Hub/.venv/bin/activate
  python pull_missing_data.py
"""

import sys
import datetime
from pathlib import Path

HUB = Path(__file__).parents[1] / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "system_gen_by_fuel"))

from ercot_core import paths

print("=== Mesquite Star missing-data pull ===")
print(f"NODE_DATA_DIR: {paths.NODE_DATA_DIR}")
print(f"PLANT_DATA_DIR: {paths.PLANT_DATA_DIR}")

# ── 1. Check current coverage ─────────────────────────────────────────────
import pandas as pd

def node_gen_max(node: str) -> str:
    nd = paths.NODE_DATA_DIR
    hi = None
    for p in sorted(nd.glob("node_generation_*.parquet")):
        try:
            df = pd.read_parquet(p, columns=["interval_start", "resource_node"])
            df = df[df["resource_node"] == node]
            if not df.empty:
                mx = df["interval_start"].max()
                hi = mx if hi is None else max(hi, mx)
        except Exception:
            pass
    return str(hi)

def sced_max(unit: str) -> str:
    pd_dir = paths.PLANT_DATA_DIR
    hi = None
    for p in sorted(pd_dir.glob(f"{unit}_*.parquet")):
        try:
            df = pd.read_parquet(p, columns=["interval_start"])
            mx = df["interval_start"].max()
            hi = mx if hi is None else max(hi, mx)
        except Exception:
            pass
    return str(hi)

print(f"\nCurrent node_generation WH_WIND_ALL max: {node_gen_max('WH_WIND_ALL')}")
print(f"Current SCED WH_WIND_UNIT1 max: {sced_max('WH_WIND_UNIT1')}")
print(f"Current SCED WH_WIND_UNIT2 max: {sced_max('WH_WIND_UNIT2')}")

# ── 2. Pull node generation for WH_WIND_ALL ──────────────────────────────
import node_generation as ng

START_GEN = datetime.date(2026, 4, 21)
END_GEN   = datetime.date(2026, 6, 21)  # exclusive

print(f"\n--- Pulling node generation WH_WIND_ALL {START_GEN} → {END_GEN} ---")
try:
    added = ng.fetch_generation(
        resource_nodes=["WH_WIND_ALL"],
        start=START_GEN,
        end=END_GEN,
    )
    print(f"  node_generation: {added} new rows added")
except Exception as e:
    print(f"  ERROR node_generation: {e}")

# ── 3. Pull SCED for WH_WIND_UNIT1 and WH_WIND_UNIT2 ────────────────────
import sced_plants

START_SCED = datetime.date(2026, 4, 21)
END_SCED   = datetime.date(2026, 6, 21)

for unit in ["WH_WIND_UNIT1", "WH_WIND_UNIT2"]:
    print(f"\n--- Pulling SCED {unit} {START_SCED} → {END_SCED} ---")
    try:
        result = sced_plants.fetch_plants(
            resource_names=[unit],
            start=START_SCED,
            end=END_SCED,
        )
        if isinstance(result, int):
            print(f"  SCED {unit}: {result} new rows added")
        else:
            print(f"  SCED {unit}: done — {result}")
    except Exception as e:
        print(f"  ERROR SCED {unit}: {e}")

# ── 4. Post-pull coverage check ──────────────────────────────────────────
print(f"\nPost-pull node_generation WH_WIND_ALL max: {node_gen_max('WH_WIND_ALL')}")
print(f"Post-pull SCED WH_WIND_UNIT1 max: {sced_max('WH_WIND_UNIT1')}")
print(f"Post-pull SCED WH_WIND_UNIT2 max: {sced_max('WH_WIND_UNIT2')}")
print("\nDone.")
