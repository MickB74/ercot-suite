"""Generalized metered-generation gap-fill for ERCOT settlement portals.

Fills the 60-day SCED-lag gap (node_generation stops ~Apr 21 while prices run to
now) with a weather-driven estimate, anchored to the plant's own node_generation
prior-year monthly CF (the basis settlement consumes — avoids the plant_sced
telemetry mismatch). Wind uses the ERA5/USWTDB fleet model; solar uses Open-Meteo
archive shortwave radiation. Rows are written tagged source='era5_model'
(idempotent re-runs). Usage:

  python gap_fill_generic.py NODE TECH CAP LAT LON
e.g. python gap_fill_generic.py HRNT_SLR_RN solar 600 34.5 -101.7
"""
import sys
from pathlib import Path
import pandas as pd

NODE, TECH, CAP, LAT, LON = sys.argv[1], sys.argv[2].lower(), float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
GAP_START = pd.Timestamp("2026-04-22 00:00:00")
GAP_END = "2026-06-20"

HUB = Path(__file__).parent / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB)); sys.path.insert(0, str(HUB / "datasets" / "wind_forecast"))
from ercot_core import paths, gen_forecast as gf, weather_forecast as wf, project_lookup

# asset (for hub-height / turbine on wind, and to confirm units)
reg = project_lookup.load_registry()
asset = next((dict(r, project_name=n) for n, r in reg.items() if r.get("resource_name") == NODE), {})
hub_h = float(asset.get("hub_height_m") or 95.0)
tt = asset.get("turbine_model")

# units actually reporting under the node (from metered data)
g25 = pd.read_parquet(paths.NODE_DATA_DIR / "node_generation_2025.parquet")
g25 = g25[(g25["resource_node"] == NODE) & (g25["source"] == "sced_60day")]
g26 = pd.read_parquet(paths.NODE_DATA_DIR / "node_generation_2026.parquet")
g26 = g26[(g26["resource_node"] == NODE) & (g26["source"] == "sced_60day")]
units = sorted(set(g25["resource_name"]) | set(g26["resource_name"]))
print(f"{NODE} {TECH} {CAP}MW units={units}")

# ── modeled hourly CF over the gap ───────────────────────────────────────────
if TECH == "solar":
    wx = wf.fetch_archive(LAT, LON, "solar", str(GAP_START.date()), GAP_END)
    hourly = gf._solar_hourly_mw(wx["shortwave_radiation"].fillna(0.0), CAP, 1.0)
else:
    wx = wf.fetch_archive(LAT, LON, "wind", str(GAP_START.date()), GAP_END)
    hourly = gf._wind_hourly_mw(wx, CAP, hub_h, 1.0, turbine_type=tt)
cf = (hourly / CAP).clip(lower=0.0)
cf.index = pd.to_datetime(cf.index)
if getattr(cf.index, "tz", None) is not None:
    cf.index = cf.index.tz_convert("America/Chicago").tz_localize(None)

# The ERA5 archive nulls some hours (wind model now returns NaN rather than
# fabricating 0 m/s calm). Fill those with the month's mean modeled CF so a
# missing hour is treated as AVERAGE — never 0 (fake calm) and never NaN (which
# would write null MW settlement rows into the lake). Consistent with the
# coverage-aware generation model.
if cf.isna().any():
    n_missing = int(cf.isna().sum())
    cf = cf.fillna(cf.groupby(cf.index.month).transform("mean"))
    cf = cf.fillna(cf.mean())          # a fully-null month → overall mean
    print(f"  filled {n_missing} ERA5-null hours with month-mean modeled CF")

# ── anchor each gap month to 2025 node_generation monthly CF ─────────────────
piv25 = g25.groupby("interval_start")["mw"].sum()
tgt_cf = (piv25.groupby(pd.to_datetime(piv25.index).month).mean() / CAP)
cf_idx = cf.index; factors = {}; out_cf = cf.astype(float).copy()
for mo in sorted(set(cf_idx.month)):
    mask = cf_idx.month == mo
    mdl = float(cf[mask].mean()); tgt = float(tgt_cf.get(mo, tgt_cf.mean()))
    f = max(0.3, min(6.0, (tgt / mdl) if mdl > 0 else 1.0)); factors[int(mo)] = round(f, 2)
    out_cf[mask] = cf[mask] * f
cf = out_cf.clip(lower=0.0, upper=1.0)
print(f"  2025 target CF {{m:cf}}: { {int(k): round(v,3) for k,v in tgt_cf.items()} }")
print(f"  anchor factors: {factors}")

plant_mw = (cf * CAP)
plant_mw = plant_mw[plant_mw.index >= GAP_START]
print(f"  modeled gap: mean {plant_mw.mean():.1f} MW  CF {plant_mw.mean()/CAP:.3f}")

# ── hourly → 15min, split by metered unit share, write ───────────────────────
qi = pd.date_range(plant_mw.index.min(), plant_mw.index.max() + pd.Timedelta(minutes=45), freq="15min")
qm = plant_mw.reindex(qi, method="ffill")
um = g26.groupby("resource_name")["mw"].mean()
shares = {u: (um.get(u, 0) / um.sum() if um.sum() else 1.0 / len(units)) for u in units}
now_utc = pd.Timestamp(GAP_START).tz_localize("UTC")
rows = [{"interval_start": ts, "interval_end": ts + pd.Timedelta(minutes=15), "resource_node": NODE,
         "resource_name": u, "mw": float(mw) * shares[u], "base_point_mw": float(mw) * shares[u],
         "source": "era5_model", "fetched_at": now_utc} for u in units for ts, mw in qm.items()]
new = pd.DataFrame(rows); new["mw"] = new["mw"].astype("float32"); new["base_point_mw"] = new["base_point_mw"].astype("float32")
new = new.dropna(subset=["mw", "base_point_mw"])   # never write NaN settlement MW

path = paths.NODE_DATA_DIR / "node_generation_2026.parquet"
ex = pd.read_parquet(path); before = len(ex)
ex = ex[~((ex["resource_node"] == NODE) & (ex.get("source") == "era5_model"))]
mmax = ex[(ex["resource_node"] == NODE) & (ex["source"] == "sced_60day")]["interval_start"].max()
new = new[new["interval_start"] > mmax]
out = pd.concat([ex, new], ignore_index=True).sort_values(["interval_start", "resource_name"]).reset_index(drop=True)
out.to_parquet(path, index=False)
chk = out[out["resource_node"] == NODE]; pq = chk.groupby("interval_start")["mw"].sum()
print(f"  wrote +{len(new)} rows, coverage → {chk['interval_start'].max()}, full-2026 plant CF {pq.mean()/CAP:.3f}")
