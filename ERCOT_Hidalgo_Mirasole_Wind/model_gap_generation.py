"""Model the metered-generation gap for Hidalgo Mirasole Wind from ERA5 weather.

ERCOT publishes metered SCED on a 60-day lag, so node_generation stops ~Apr 21,
2026 while prices run to the present. This fills the gap (Apr 22 → latest ERA5)
with the same ERA5/USWTDB wind model the Hub uses for plant_value — nearest real
turbine fleet → region priors → anchored to this plant's own metered SCED by
month. Written into node_generation tagged source='era5_model'.

Run with the Hub venv:
  source ~/Documents/Github/ercot-suite/Ercot_Data_Hub/.venv/bin/activate
  python model_gap_generation.py
"""

import sys
from pathlib import Path

import pandas as pd

HUB = Path(__file__).parents[1] / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "wind_forecast"))

from ercot_core import paths, plant_value, project_lookup

NODE = "MIRASOLE_GEN"
GAP_START = pd.Timestamp("2026-04-22 00:00:00")

# ── 1. Asset from registry ───────────────────────────────────────────────────
reg = project_lookup.load_registry()
asset = None
for name, rec in reg.items():
    if rec.get("resource_name") == NODE or name == "Hidalgo Mirasole Wind":
        asset = dict(rec); asset.setdefault("project_name", name); break
if asset is None:
    raise SystemExit(f"{NODE} not found in registry")
nameplate = float(asset["capacity_mw"])
units = asset.get("sced_units") or [asset["resource_name"]]
print(f"Asset: {asset['project_name']} node={NODE} cap={nameplate} MW units={units}")

# ── 2. Real fleet + gap-period ERA5 ──────────────────────────────────────────
import wind_calibration as cal
import wind_power as wp

fc, fcap, fname, fdist = plant_value._build_wind_fleet(asset)
print(f"Fleet: {fname or 'generic'} cap={fcap:.1f} MW segments={len(fc.segments)}")

gap_end_req = "2026-06-20"
print(f"Fetching ERA5 {GAP_START.date()} → {gap_end_req} ...")
wx = wp.fetch_weather_era5(float(asset["lat"]), float(asset["lon"]),
                           str(GAP_START.date()), gap_end_req)
raw = wp.run_wind(wx, fc)
print(f"  ERA5 {len(raw)} hours, {raw.index.min()} → {raw.index.max()}")

# ── 3. ERA5 shape, anchored to node_generation prior-year monthly CF ─────────
# The raw ERA5 physics underpredicts this coastal site, and plant_value's default
# anchor reads the raw plant_sced telemetry (here 0.25 CF) which disagrees with
# node_generation — the basis settlement actually consumes (2025 ran 0.36–0.42
# CF in May/June). So anchor each gap month's mean CF to the prior-year (2025)
# node_generation CF for that calendar month, preserving ERA5's day-to-day shape.
net = cal.apply_region_priors(raw["net_mw"], capacity_mw=fcap,
                              lat=float(asset["lat"]), lon=float(asset["lon"]),
                              hub_name=asset.get("hub"))
cf = (net / fcap).clip(lower=0.0) if fcap else net * 0.0

g2025 = pd.read_parquet(paths.NODE_DATA_DIR / "node_generation_2025.parquet")
g2025 = g2025[(g2025["resource_node"] == NODE) & (g2025["source"] == "sced_60day")]
piv25 = g2025.groupby("interval_start")["mw"].sum()
piv25_m = pd.to_datetime(piv25.index).month
tgt_cf = (piv25.groupby(piv25_m).mean() / nameplate)   # 2025 monthly mean CF
print(f"  target (2025 node_gen) monthly CF: { {int(k): round(v,3) for k,v in tgt_cf.items()} }")

cf_idx = pd.to_datetime(cf.index)
factors = {}
cf_anchored = cf.astype(float).copy()
for mo in sorted(set(cf_idx.month)):
    mask = (cf_idx.month == mo)
    mdl_mean = float(cf[mask].mean())
    tgt = float(tgt_cf.get(mo, tgt_cf.mean()))
    f = (tgt / mdl_mean) if mdl_mean > 0 else 1.0
    f = max(0.3, min(5.0, f))
    factors[int(mo)] = round(f, 3)
    cf_anchored[mask] = (cf[mask] * f)
cf = cf_anchored.clip(lower=0.0, upper=1.0)
print(f"  node_gen anchor factors by month: {factors}")

plant_mw = (cf * nameplate).rename("mw")
plant_mw.index = pd.to_datetime(plant_mw.index)
if getattr(plant_mw.index, "tz", None) is not None:
    plant_mw.index = plant_mw.index.tz_localize(None)
plant_mw = plant_mw[plant_mw.index >= GAP_START]
print(f"Modeled plant hourly: mean {plant_mw.mean():.1f} MW  CF {plant_mw.mean()/nameplate:.3f}")

# ── 4. Hourly → 15-min, split across units by metered share ──────────────────
q_index = pd.date_range(plant_mw.index.min(),
                        plant_mw.index.max() + pd.Timedelta(minutes=45), freq="15min")
q_mw = plant_mw.reindex(q_index, method="ffill")

# Metered unit shares over the cached 2026 SCED.
g2026 = pd.read_parquet(paths.NODE_DATA_DIR / "node_generation_2026.parquet")
g2026 = g2026[(g2026["resource_node"] == NODE) & (g2026["source"] == "sced_60day")]
um = g2026.groupby("resource_name")["mw"].mean()
shares = (um / um.sum()).to_dict() if not um.empty else {u: 1.0/len(units) for u in units}
shares = {u: shares.get(u, 0.0) for u in units}
print(f"Unit shares: { {k: round(v,3) for k,v in shares.items()} }")

now_utc = pd.Timestamp(GAP_START).tz_localize("UTC")
rows = []
for u in units:
    sh = shares.get(u, 1.0/len(units))
    for ts, mw in q_mw.items():
        rows.append({"interval_start": ts, "interval_end": ts + pd.Timedelta(minutes=15),
                     "resource_node": NODE, "resource_name": u,
                     "mw": float(mw)*sh, "base_point_mw": float(mw)*sh,
                     "source": "era5_model", "fetched_at": now_utc})
new = pd.DataFrame(rows)
new["mw"] = new["mw"].astype("float32"); new["base_point_mw"] = new["base_point_mw"].astype("float32")

# ── 5. Merge into node_generation_2026 (idempotent on era5_model) ────────────
path = paths.NODE_DATA_DIR / "node_generation_2026.parquet"
ex = pd.read_parquet(path); before = len(ex)
ex = ex[~((ex["resource_node"] == NODE) & (ex.get("source") == "era5_model"))]
metered_max = ex[(ex["resource_node"] == NODE) & (ex["source"] == "sced_60day")]["interval_start"].max()
new = new[new["interval_start"] > metered_max]
out = pd.concat([ex, new], ignore_index=True).sort_values(["interval_start", "resource_name"]).reset_index(drop=True)
out.to_parquet(path, index=False)
print(f"Wrote {path.name}: {before} → {len(out)} (+{len(new)} modeled, metered_max={metered_max})")

chk = out[out["resource_node"] == NODE]
pq = chk.groupby("interval_start")["mw"].sum()
print(f"WH coverage now: {chk['interval_start'].min()} → {chk['interval_start'].max()}")
print(f"Plant mean MW (all units): {pq.mean():.1f}  CF {pq.mean()/nameplate:.3f}")
mq = chk[chk['source']=='era5_model'].groupby('interval_start')['mw'].sum()
if not mq.empty:
    print(f"Modeled gap mean MW: {mq.mean():.1f}  CF {mq.mean()/nameplate:.3f}")
print("Done.")
