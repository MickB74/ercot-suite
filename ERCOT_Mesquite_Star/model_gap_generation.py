"""Model the metered-generation gap for Mesquite Star (WH_WIND) from ERA5 weather.

ERCOT publishes metered SCED only on a 60-day lag, so node_generation stops at
~Apr 21, 2026 while prices run to the present. This fills the gap (Apr 22 →
latest ERA5) with the SAME ERA5/USWTDB wind model the Hub uses for plant_value:
nearest real turbine fleet → region priors → anchored to this plant's own
metered SCED by calendar month. The result is the best available estimate of
realized output, written into node_generation tagged source='era5_model' so it
is distinguishable from metered 'sced_60day' rows.

Run with the Hub venv:
  source ~/Documents/Github/ercot-suite/Ercot_Data_Hub/.venv/bin/activate
  python model_gap_generation.py
"""

import sys
import datetime
from pathlib import Path

import pandas as pd

HUB = Path(__file__).parents[1] / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "wind_forecast"))

from ercot_core import paths, plant_value, project_lookup

GAP_START = pd.Timestamp("2026-04-22 00:00:00")   # first missing day (inclusive)

# ── 1. Load the asset record from the curated registry ───────────────────────
reg = project_lookup.load_registry()
asset = None
for name, rec in reg.items():
    if rec.get("resource_name") == "WH_WIND_ALL" or name == "Mesquite Star":
        asset = dict(rec)
        asset.setdefault("project_name", name)
        break
if asset is None:
    raise SystemExit("Mesquite Star / WH_WIND_ALL not found in registry")

print(f"Asset: {asset['project_name']}  node={asset['resource_name']}  "
      f"cap={asset['capacity_mw']} MW  ({asset['lat']:.4f},{asset['lon']:.4f})")
nameplate = float(asset["capacity_mw"])
units = asset.get("sced_units") or [asset["resource_name"]]

# ── 2. Build the real turbine fleet + fetch gap-period ERA5 weather ──────────
import wind_calibration as cal
import wind_power as wp

fc, fcap, fname, fdist = plant_value._build_wind_fleet(asset)
print(f"Fleet: {fname or 'generic'}  cap={fcap:.1f} MW  segments={len(fc.segments)}")

# ERA5 lags real time by a few days; ask through yesterday and take what we get.
gap_end_req = datetime.date(2026, 6, 20)
print(f"\nFetching ERA5 {GAP_START.date()} → {gap_end_req} ...")
wx = wp.fetch_weather_era5(float(asset["lat"]), float(asset["lon"]),
                           str(GAP_START.date()), str(gap_end_req))
raw = wp.run_wind(wx, fc)
print(f"  ERA5 returned {len(raw)} hours, {raw.index.min()} → {raw.index.max()}")

# ── 3. Same calibration chain as plant_value._load_or_run_wind ───────────────
net = cal.apply_region_priors(raw["net_mw"], capacity_mw=fcap,
                              lat=float(asset["lat"]), lon=float(asset["lon"]),
                              hub_name=asset.get("hub"))
cf = (net / fcap).clip(lower=0.0) if fcap else net * 0.0

# Anchor to THIS plant's metered SCED by calendar month (realized availability /
# curtailment), exactly as the cached typical-year profile is anchored.
meta = {}
actual = plant_value._sced_actuals_hourly(asset)
if actual is not None and not actual.empty:
    act_cf = (actual / nameplate).clip(lower=0.0)
    hrs, mean_cf = int(len(act_cf)), float(act_cf.mean())
    print(f"  metered SCED for anchor: {hrs} h, mean CF {mean_cf:.3f}")
    if hrs >= 2000 and 0.15 <= mean_cf <= 0.60:
        cf = plant_value._calendar_anchor(cf, act_cf, meta)
        print(f"  anchored. monthly factors: {meta.get('sced_anchor', {}).get('monthly_factors')}")
    else:
        print("  anchor skipped (implausible/too-few) — region priors only")
else:
    print("  no metered SCED found — region priors only")

plant_mw_hourly = (cf * nameplate).rename("mw")
plant_mw_hourly.index = pd.to_datetime(plant_mw_hourly.index)
print(f"\nModeled plant hourly: mean {plant_mw_hourly.mean():.1f} MW  "
      f"CF {plant_mw_hourly.mean()/nameplate:.3f}  "
      f"({plant_mw_hourly.index.min()} → {plant_mw_hourly.index.max()})")

# ── 4. Expand hourly → 15-min, split across the two units, build rows ────────
# Drop tz so it matches the naive-Central node_generation store.
idx = plant_mw_hourly.index
if getattr(idx, "tz", None) is not None:
    plant_mw_hourly.index = idx.tz_localize(None)

# Keep only the true gap (>= GAP_START) so we never overwrite metered rows.
plant_mw_hourly = plant_mw_hourly[plant_mw_hourly.index >= GAP_START]

# 15-min grid: each hour's mean MW repeated across its four intervals.
q_index = pd.date_range(plant_mw_hourly.index.min(),
                        plant_mw_hourly.index.max() + pd.Timedelta(minutes=45),
                        freq="15min")
q_mw = plant_mw_hourly.reindex(q_index, method="ffill")

# Metered unit shares (≈50/50); fall back to even split.
SHARES = {"WH_WIND_UNIT1": 0.4954, "WH_WIND_UNIT2": 0.5046}
if set(units) != set(SHARES):
    SHARES = {u: 1.0 / len(units) for u in units}

now_utc = pd.Timestamp(GAP_START).tz_localize("UTC")  # deterministic stamp
rows = []
for u in units:
    share = SHARES.get(u, 1.0 / len(units))
    for ts, mw in q_mw.items():
        rows.append({
            "interval_start": ts,
            "interval_end": ts + pd.Timedelta(minutes=15),
            "resource_node": "WH_WIND_ALL",
            "resource_name": u,
            "mw": float(mw) * share,
            "base_point_mw": float(mw) * share,
            "source": "era5_model",
            "fetched_at": now_utc,
        })
new = pd.DataFrame(rows)
new["mw"] = new["mw"].astype("float32")
new["base_point_mw"] = new["base_point_mw"].astype("float32")
print(f"\nBuilt {len(new)} modeled rows ({new['interval_start'].min()} → {new['interval_start'].max()})")

# ── 5. Merge into node_generation_2026.parquet (replace any prior model rows) ─
path = paths.NODE_DATA_DIR / "node_generation_2026.parquet"
existing = pd.read_parquet(path)
# Drop any earlier era5_model rows for this node so re-runs are idempotent.
before = len(existing)
existing = existing[~((existing["resource_node"] == "WH_WIND_ALL") &
                      (existing.get("source") == "era5_model"))]
# Never duplicate metered intervals: drop modeled rows that overlap real ones.
metered_max = existing[(existing["resource_node"] == "WH_WIND_ALL") &
                       (existing["source"] == "sced_60day")]["interval_start"].max()
print(f"Metered WH_WIND_ALL max (sced_60day): {metered_max}")
new = new[new["interval_start"] > metered_max]

combined = pd.concat([existing, new], ignore_index=True)
combined = combined.sort_values(["interval_start", "resource_name"]).reset_index(drop=True)
combined.to_parquet(path, index=False)
print(f"Wrote {path.name}: {before} → {len(combined)} rows (+{len(new)} modeled)")

# ── 6. Verify new coverage ───────────────────────────────────────────────────
chk = combined[combined["resource_node"] == "WH_WIND_ALL"]
print(f"\nWH_WIND_ALL generation now: {chk['interval_start'].min()} → {chk['interval_start'].max()}")
plant_q = chk.groupby("interval_start")["mw"].sum()
print(f"Plant mean MW (both units, all): {plant_q.mean():.1f}  CF {plant_q.mean()/nameplate:.3f}")
model_q = chk[chk['source']=='era5_model'].groupby('interval_start')['mw'].sum()
if not model_q.empty:
    print(f"Modeled gap mean MW: {model_q.mean():.1f}  CF {model_q.mean()/nameplate:.3f}")
print("\nDone.")
