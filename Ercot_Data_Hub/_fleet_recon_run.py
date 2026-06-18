import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import warnings; warnings.filterwarnings("ignore")
from ercot_core.bootstrap import setup_path; setup_path()
import pandas as pd
from ercot_core import reconcile as R

def prog(i, n, name):
    if i % 10 == 0 or i == n:
        print(f"  {i}/{n} … {name}", flush=True)

print("Fleet reconciliation 2025 — all mapped plants", flush=True)
df = R.batch_reconcile((2025,), tolerance=0.10, allow_fetch=True, progress=prog)
df.to_csv("data/fleet_reconcile_2025.csv", index=False)
print("\nstatus counts:", df['status'].value_counts().to_dict(), flush=True)
off = df[df['status']=="⚠ off"].copy()
print(f"\n=== {len(off)} plant(s) where SCED is OFF (>10%) ===", flush=True)
if not off.empty:
    off['overall_pct']=(off['overall_pct']*100).round(1)
    print(off[['plant','months','eia_mwh','sced_mwh','overall_pct','months_off']].to_string(index=False), flush=True)
no_ov = df[df['status']=="no overlap"]
print(f"\nno-overlap plants: {len(no_ov)} | errors: {len(df[df['status'].str.startswith('error',na=False)])}", flush=True)
