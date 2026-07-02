#!/usr/bin/env python
"""Build the per-asset SCED→EIA uplift registry (data/sced_uplift.json).

SCED telemetry under-reads WIND net generation vs EIA-923 revenue meters; the
real invoice settles on the meter, so settlement built on raw SCED under-states
wind. This computes, per asset, the energy-weighted uplift = Σ EIA / Σ SCED over
full-coverage overlap months (ramp/partial months dropped), clamps to [1.0, 1.5],
and writes the registry that ercot_core.settlement applies. Solar ≈ 1.0 (no-op).

Re-run after each data refresh; the factor self-updates as more history lands.
Reads only the cached lake (EIA-923 + node_generation) plus each portal's own
contract (for node/units/share/tech) and anchor (for EIA plant ids).
"""
import warnings; warnings.filterwarnings("ignore")
import json, subprocess, sys
from pathlib import Path
import pandas as pd

SUITE = Path(__file__).resolve().parent
HUB = SUITE / "Ercot_Data_Hub"
PY = str(HUB / ".venv/bin/python")
GENDIR = HUB / "data/system_gen/node_data"
ANCHDIR = HUB / "data/eia_anchor"
EIA923DIR = HUB / "data/eia923"
OUT = HUB / "ercot_core/registry/sced_uplift.json"   # version-controlled config

MIN_MONTHS = 3
RAMP_FRAC = 0.20          # drop months where SCED < 20% of the asset's max month
CLAMP = (1.0, 1.5)

PORTALS = [
    ("ERCOT_Markum", "markum"), ("ERCOT_Azure_Sky", "azuresky"),
    ("ERCOT_Hidalgo_Mirasole_Wind", "portal"), ("ERCOT_Hornet_Solar", "portal"),
    ("ERCOT_Miller", "portal"), ("ERCOT_Mesquite_Star", "portal"),
    ("ERCOT_Stafford_Solar", "portal"), ("ERCOT_Heart_of_Texas", "hotwind"),
    ("ERCOT_Aguayo_Wind", "portal"),
]


def portal_asset(d, pkg):
    code = (f"import sys; sys.path.insert(0,'.'); sys.path.insert(0,r'{HUB}');"
            f"from {pkg} import contract as c; import json;"
            "a=c.ASSET; t=c.load_contract();"
            "print('J'+json.dumps({'node':a.get('resource_node'),"
            "'units':a.get('sced_units') or a.get('units'),'tech':a.get('tech'),"
            "'name':a.get('project_name'),"
            "'share':float(t.get('volume_share_pct',100.0))/100.0}))")
    try:
        r = subprocess.run([PY, "-c", code], cwd=str(SUITE / d),
                           capture_output=True, text=True, timeout=120)
        for l in r.stdout.splitlines():
            if l.startswith("J"):
                return json.loads(l[1:])
    except Exception as e:  # noqa: BLE001
        print(f"  ! {d}: {str(e)[:60]}")
    return None


def _load_eia():
    fr = []
    for yr in (2024, 2025, 2026):
        f = EIA923DIR / f"eia923_all_{yr}.parquet"
        if f.exists():
            fr.append(pd.read_parquet(
                f, columns=["year", "month", "plant_id", "prime_mover", "netgen_mwh"]))
    if not fr:
        return pd.DataFrame()
    e = pd.concat(fr, ignore_index=True)
    e["M"] = e["year"].astype(str) + "-" + e["month"].astype(int).astype(str).str.zfill(2)
    return e


def sced_monthly(node, units):
    fr = []
    for yr in (2024, 2025, 2026):
        f = GENDIR / f"node_generation_{yr}.parquet"
        if not f.exists():
            continue
        g = pd.read_parquet(f)
        g = g[g["resource_node"] == node]
        if units and "resource_name" in g.columns:
            gu = g[g["resource_name"].isin(units)]
            if not gu.empty:
                g = gu
        if not g.empty:
            fr.append(g)
    if not fr:
        return pd.Series(dtype=float)
    g = pd.concat(fr)
    hrs = (pd.to_datetime(g["interval_end"]) - pd.to_datetime(g["interval_start"])
           ).dt.total_seconds() / 3600.0
    g = g.assign(mwh=g["mw"] * hrs,
                 M=pd.to_datetime(g["interval_start"]).dt.to_period("M").astype(str))
    return g.groupby("M")["mwh"].sum()


EIA = _load_eia()
registry = {}
print(f"{'asset':22}{'tech':6}{'months':>7}{'factor':>8}  span")
for d, pkg in PORTALS:
    a = portal_asset(d, pkg)
    if not a or not a.get("node"):
        continue
    node = a["node"]; tech = "wind" if "wind" in (a.get("tech") or "").lower() else "solar"
    ap = ANCHDIR / f"{node}.json"
    eids = (json.loads(ap.read_text()).get("eia_plant_ids") or []) if ap.exists() else []
    pm = "WT" if tech == "wind" else "PV"
    ee = (EIA[(EIA["plant_id"].isin(eids)) & (EIA["prime_mover"] == pm)]
          .groupby("M")["netgen_mwh"].sum()) if eids and not EIA.empty else pd.Series(dtype=float)
    sced = sced_monthly(node, a.get("units"))
    # full-coverage overlap: both present, and SCED above the ramp threshold
    if not sced.empty:
        smax = sced.max()
        full = [m for m in sced.index
                if m in ee.index and ee[m] > 0 and sced[m] > RAMP_FRAC * smax]
    else:
        full = []
    if len(full) >= MIN_MONTHS:
        s = float(sum(sced[m] for m in full)); e = float(sum(ee[m] for m in full))
        raw = e / s if s else 1.0
        fac = max(CLAMP[0], min(CLAMP[1], raw))
        registry[node] = {"factor": round(fac, 4), "tech": tech,
                          "n_months": len(full), "span": f"{min(full)}..{max(full)}",
                          "raw_ratio": round(raw, 4), "method": "sum(EIA)/sum(SCED)"}
        print(f"{a['name'][:22]:22}{tech:6}{len(full):>7}{fac:>8.3f}  {min(full)}..{max(full)}")
    else:
        # not enough overlap to measure → explicit no-op so the asset is on record
        registry[node] = {"factor": 1.0, "tech": tech, "n_months": len(full),
                          "span": "", "method": "insufficient overlap (default 1.0)"}
        print(f"{a['name'][:22]:22}{tech:6}{len(full):>7}{1.0:>8.3f}  (insufficient — default 1.0)")

OUT.write_text(json.dumps(registry, indent=2))
print(f"\nwrote {len(registry)} assets -> {OUT}")
wind = {k: v for k, v in registry.items() if v["tech"] == "wind" and v["factor"] > 1.0}
print(f"wind assets with uplift >1.0: {len(wind)} "
      f"(mean {sum(v['factor'] for v in wind.values())/len(wind):.3f})" if wind else "")
