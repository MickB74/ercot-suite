#!/usr/bin/env python3
"""Reconcile every ERCOT resource node to its EIA plant by 2024 generation.

The attribute matcher (fuel + county + capacity + COD + name tokens) proposes a
candidate; actual *production* confirms it: a node's 2024 SCED MWh vs the
candidate plant's EIA-923 2024 net generation (ratio ~1.0 ⇒ same plant). Where the
attribute candidate isn't confirmed, same-fuel ERCOT EIA plants whose 2024 net gen
lands within ±12% of the node's output are offered as production-magnitude
alternatives. Writes a CSV verdict table.

2024 is used because both sides are fully settled (SCED is cached locally; EIA-923
2024 is final). Fast — the SCED disclosure is read from the local cache.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402

import eia860  # noqa: E402
import node_generation as ng  # noqa: E402
from ercot_core import paths, reconcile as R  # noqa: E402

YEAR = 2024
CONFIRM_TOL = 0.15   # |SCED/EIA − 1| below this ⇒ confirmed
MAG_TOL = 0.12       # same-fuel production-magnitude window for alternatives
OUT = paths.DATA / "reconcile"
OUT.mkdir(parents=True, exist_ok=True)
RESULT = OUT / f"ercot_eia_reconciliation_{YEAR}.csv"
MONTHLY = OUT / f"sced_monthly_mwh_{YEAR}.parquet"

cat = pd.read_parquet(paths.CATALOG_PATH)
nodes = sorted(cat["resource_node"].dropna().astype(str).unique())
node_units = cat.groupby("resource_node")["sced_resource_name"].apply(list).to_dict()
print(f"{len(nodes)} resource nodes; aggregating {YEAR} SCED (cached) …", flush=True)

# Accurate per-resource fuel from the SCED disclosure "Resource Type" (WIND / PVGR /
# PWRSTR / CCGT90 / …) — the reliable signal the resource-name heuristic lacked.
import datetime as _dt  # noqa: E402

from ercot_core import sced_disclosure as SD  # noqa: E402

RESOURCE_FUEL: dict[str, str] = {}
RESOURCE_HSL: dict[str, float] = {}    # max High Sustainable Limit ≈ nameplate MW
for _d in [_dt.date(YEAR, 2, 15), _dt.date(YEAR, 6, 15), _dt.date(YEAR, 9, 15)]:
    try:
        _df = SD.get_daily_disclosure(_d, allow_fetch=True)
    except Exception:  # noqa: BLE001
        _df = None
    if _df is None or _df.empty or "resource_type" not in _df.columns:
        continue
    _g = _df.dropna(subset=["resource_name"]).groupby("resource_name")
    for rn, rt in _g["resource_type"].first().items():
        RESOURCE_FUEL.setdefault(str(rn), SD.fuel_group_for(rt))
    if "hsl" in _df.columns:
        for rn, hsl in _g["hsl"].max().items():
            if pd.notna(hsl):
                RESOURCE_HSL[str(rn)] = max(RESOURCE_HSL.get(str(rn), 0.0), float(hsl))
print(f"SCED resource-type fuel map: {len(RESOURCE_FUEL)} resources, "
      f"HSL/capacity: {len(RESOURCE_HSL)}", flush=True)

# --- per-node annual SCED MWh (monthly aggregation keeps memory low) ----------
if MONTHLY.exists():
    monthly = pd.read_parquet(MONTHLY)
    print("  reusing cached monthly SCED parquet", flush=True)
else:
    parts = []
    for m in range(1, 13):
        ms = pd.Timestamp(YEAR, m, 1)
        me = ms + pd.offsets.MonthEnd(1)
        g = ng.fetch_generation(nodes, ms, me, verbose=False)
        if g.empty:
            continue
        mm = (g.assign(mwh=g["mw"].astype(float).clip(lower=0) * 0.25)
                .groupby("resource_node")["mwh"].sum().reset_index())
        mm["month"] = m
        parts.append(mm)
        print(f"  {YEAR}-{m:02d}: {len(mm)} nodes", flush=True)
        del g
    monthly = pd.concat(parts, ignore_index=True)
    monthly.to_parquet(MONTHLY, index=False)
annual = monthly.groupby("resource_node")["mwh"].sum()
print(f"nodes with {YEAR} generation: {len(annual)}", flush=True)

# --- EIA side: 2024 net gen + fuel, restricted to ERCOT (ERCO) plants ---------
e923 = pd.read_parquet(paths.EIA_DIR / f"eia923_all_{YEAR}.parquet",
                       columns=["plant_id", "netgen_mwh"])
eia_gen = e923.groupby("plant_id")["netgen_mwh"].sum()
e860 = eia860.load([max(eia860.available_years("ercot"))], "ercot")
eia_fuel = e860.dropna(subset=["plant_id"]).groupby("plant_id")["fuel_category"].first()
erco = set(e860["plant_id"].dropna().astype(int))
eia_cap = e860.dropna(subset=["plant_id"]).groupby("plant_id")["nameplate_mw"].sum()
emag = pd.DataFrame({"pid": eia_gen.index.astype("Int64"), "gen": eia_gen.values})
emag = emag[emag["pid"].isin(erco) & (emag["gen"] > 0)].copy()
emag["fuel"] = emag["pid"].map(eia_fuel)
emag["cap"] = emag["pid"].map(eia_cap)

xw = R.auto_crosswalk([max(eia860.available_years("ercot"))], region="ercot",
                      cap_tol=0.25, use_860m=True).set_index("resource_name")


def fuel_of(node, units):
    """Fuel from the SCED Resource Type (majority across the node's units); falls
    back to an ERCOT-naming heuristic only when a unit isn't in the disclosure."""
    fg = [RESOURCE_FUEL[u] for u in units if u in RESOURCE_FUEL]
    if fg:
        return pd.Series(fg).mode().iloc[0]
    s = (str(node) + " " + " ".join(map(str, units))).upper()
    if any(k in s for k in ("WIND", "_WND", "WND_")):
        return "Wind"
    if any(k in s for k in ("SOLAR", "_SLR", "SLR_", "_PV", "PV_", "_SUN")):
        return "Solar"
    if any(k in s for k in ("BESS", "_ESS", "STOR", "BATT", "_BES", "ESR", "_ES_")):
        return "Storage"
    return "Gas"


# SCED fuel_group -> EIA fuel_category bucket(s).
FUEL_MATCH = {"Wind": {"wind"}, "Solar": {"solar"}, "Storage": {"storage", "batteries"},
              "Nuclear": {"nuclear"}, "Coal": {"coal", "coal/lignite", "lignite"},
              "Hydro": {"hydro", "conventional hydroelectric"},
              "Gas": {"gas", "other gas", "natural gas"}}

# Empirical SCED/EIA offset: telemetered SCED runs a few % below EIA-923 net gen.
# Calibrate K = median ratio over attribute-candidate matches near 1, recenter on it.
_cal = []
for node, smwh in annual.items():
    if smwh < 1000:
        continue
    c = next((xw.loc[u] for u in node_units.get(node, [node]) if u in xw.index), None)
    pid = int(c["eia_plant_id"]) if c is not None and pd.notna(c["eia_plant_id"]) else None
    if pid in eia_gen.index and eia_gen[pid] > 0:
        r = smwh / float(eia_gen[pid])
        if 0.75 < r < 1.25:
            _cal.append(r)
K = float(pd.Series(_cal).median()) if _cal else 0.93
print(f"calibrated SCED/EIA ratio K = {K:.3f} (n={len(_cal)})", flush=True)

rows = []
for node, smwh in annual.items():
    if smwh < 1000:
        continue
    units = node_units.get(node, [node])
    nf = fuel_of(node, units)
    want = FUEL_MATCH.get(nf, set())
    pool = emag[emag["fuel"].astype(str).str.lower().isin(want)] if want else emag
    expected = smwh / K                                   # offset-corrected EIA gen
    mag = pool[(pool["gen"] - expected).abs() / expected < MAG_TOL]
    # Capacity tie-break: HSL (≈ nameplate) vs EIA nameplate, when several remain.
    ncap = sum(RESOURCE_HSL.get(u, 0.0) for u in units)
    if ncap > 0 and len(mag) > 1:
        capf = mag[(mag["cap"].fillna(0) - ncap).abs() / ncap < 0.25]
        if len(capf) >= 1:
            mag = capf

    c = next((xw.loc[u] for u in units if u in xw.index), None)
    attr_pid = int(c["eia_plant_id"]) if c is not None and pd.notna(c["eia_plant_id"]) else None
    attr_ok = (attr_pid in eia_gen.index and eia_gen[attr_pid] > 0
               and abs((smwh / float(eia_gen[attr_pid])) / K - 1) < CONFIRM_TOL)

    mag_ids = set(mag["pid"].astype(int).tolist())
    if attr_ok and (attr_pid in mag_ids or not mag_ids):
        pid, method = attr_pid, "attr+production"          # both agree (strongest)
    elif len(mag_ids) == 1:
        pid, method = next(iter(mag_ids), None), "production (unique)"
    elif attr_ok:
        pid, method = attr_pid, "attr+production"
    elif len(mag_ids) > 1:
        pid, method = None, f"ambiguous ({len(mag_ids)} same-fuel cands)"
    else:
        pid, method = (attr_pid, "attr only (unconfirmed)") if attr_pid else (None, "unresolved")

    egen = float(eia_gen[pid]) if (pid is not None and pid in eia_gen.index) else None
    rows.append({
        "resource_node": node, "fuel": nf, "sced_2024_mwh": round(smwh),
        "eia_plant_id": pid, "eia_2024_mwh": round(egen) if egen else None,
        "ratio_vs_K": round((smwh / egen) / K, 3) if egen else None,
        "method": method,
        "confirmed": method in ("attr+production", "production (unique)"),
    })

res = pd.DataFrame(rows).sort_values("sced_2024_mwh", ascending=False)
res.to_csv(RESULT, index=False)
n = len(res)
conf = int(res["confirmed"].sum())
print(f"\nRECONCILED {n} generating nodes ({YEAR}):")
print(f"  ✓ CONFIRMED (production-verified): {conf}  ({conf/n*100:.0f}%)")
print(res["method"].value_counts().to_string())
print("saved:", RESULT)
