"""Build EIA-923 calibration anchors for a diverse set of ERCOT wind plants and
compare. Tests the hypothesis behind the Mirasole finding: is the large ERA5
under-prediction unique to the Rio Grande Valley low-level jet, or systemic?

8 plants — 4 coastal/South (same LLJ regime as Mirasole) + 4 West/Panhandle.
Reuses ercot_core.eia_anchor (EIA-923 already cached for all ERCOT plants).

Run (Hub venv):
  Ercot_Data_Hub/.venv/bin/python build_wind_anchors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HUB = Path(__file__).resolve().parent / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "eia923"))
sys.path.insert(0, str(HUB / ".." / "Ercot_Wind_Forecast"))

from ercot_core import eia_anchor as ea  # noqa: E402

# (cache key/node, EIA plant id(s), label, region) — coords/phases auto from EIA-860.
CANDIDATES = [
    # ── coastal / South (Rio Grande Valley & Gulf — LLJ regime) ──
    ("REDFISH_ALL",  [57802], "Magic Valley Wind",      "Coastal-RGV"),
    ("PENA_ALL",     [56795], "Penascal Wind",          "Coastal"),
    ("COTTON_PAP2",  [57212], "Papalote Creek II",       "Coastal"),
    ("CEDROHI_CHW1", [57260], "Cedro Hill Wind",         "South-inland"),
    # ── West / Panhandle (different wind regime) ──
    ("HHOLLW2_WND1", [56291], "Horse Hollow",            "West"),
    ("CAPRIDG4_CR4", [56763], "Capricorn Ridge",         "West"),
    ("SRWE1_UNIT1",  [57983], "Stephens Ranch",          "West"),
    ("SANTACRU_ALL", [60987], "Santa Rita Wind",         "West"),
]


def main():
    rows = []
    for node, eia_ids, label, region in CANDIDATES:
        try:
            print(f"\n=== {label} ({node}, EIA {eia_ids}, {region}) ===", flush=True)
            spec = ea.spec_from_eia(node, eia_ids, label=label)
            print(f"  cap={spec.capacity_full:.0f}MW phases={spec.phases} "
                  f"coords=({spec.lat},{spec.lon}) start={spec.start_year}", flush=True)
            a = ea.build(spec)
            raw_cf = round(a["mean_cf"] / a["overall_factor"], 3) if a["overall_factor"] else None
            rows.append({
                "project": label, "region": region, "node": node,
                "cap_mw": spec.capacity_full, "cod": spec.phases[0][1][:7],
                "n_mo": a["n_months"],
                "actual_cf": a["mean_cf"], "raw_model_cf": raw_cf,
                "energy_factor": a["overall_factor"], "ws_corr": a["ws_speed_correction"],
                "p50_gwh": round(a["annual_energy_p50_mwh"] / 1000, 0),
            })
        except Exception as ex:  # noqa: BLE001
            print(f"  FAILED: {str(ex)[:160]}", flush=True)
            rows.append({"project": label, "region": region, "node": node, "FAILED": str(ex)[:80]})

    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220, "display.max_columns", 30)
    print("\n\n================  COMPARISON  ================")
    cols = ["project", "region", "cap_mw", "cod", "n_mo", "actual_cf",
            "raw_model_cf", "energy_factor", "ws_corr", "p50_gwh"]
    ok = df[df.get("FAILED").isna()] if "FAILED" in df.columns else df
    print(ok[[c for c in cols if c in ok.columns]].to_string(index=False))

    # Hypothesis test: coastal/South vs West ERA5 under-prediction.
    if "energy_factor" in ok.columns and len(ok):
        ok2 = ok.copy()
        ok2["grp"] = ok2["region"].apply(lambda r: "Coastal/South" if "Coast" in r or "South" in r else "West")
        g = ok2.groupby("grp").agg(
            n=("project", "count"),
            mean_energy_factor=("energy_factor", "mean"),
            mean_ws_corr=("ws_corr", "mean"),
            mean_actual_cf=("actual_cf", "mean")).round(3)
        print("\n--- ERA5 under-prediction by regime (incl. Mirasole context) ---")
        print(g.to_string())
        print("\nMirasole (RGV) reference: energy_factor 2.53, ws_corr 1.34, actual_cf 0.354")


if __name__ == "__main__":
    main()
