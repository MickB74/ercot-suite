"""Rebuild every portal's realized capture anchor and report maturity.

Run periodically (or from a scheduled routine). Each asset's anchor is recomputed
from the latest SCED + price history; the report flags which held/preliminary
assets have crossed the reliability bar (≥12 months) and are ready to enable in
their Future Bill (wire ``node=`` into the portal's ``_capture_ratios``).

  Ercot_Data_Hub/.venv/bin/python rebuild_capture_anchors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
from ercot_core import capture_anchor as ca  # noqa: E402

READY_MONTHS = 12

# (node, units, hub, settle 'NODE'|hub, price_node, label, tech)
PORTALS = [
    ("MRKM_SLR_RN", ["MRKM_SLR_PV1"], "HB_NORTH", "HB_NORTH", None, "Markum Solar", "solar"),
    ("MIRASOLE_GEN", ["MIRASOLE_MIR11", "MIRASOLE_MIR12", "MIRASOLE_MIR13", "MIRASOLE_MIR21"],
     "HB_SOUTH", "HB_SOUTH", None, "Hidalgo Mirasole", "wind"),
    ("RN_RTS1", ["RTS_U1"], "HB_WEST", "HB_WEST", None, "Heart of Texas", "wind"),
    ("BUZI_SLR_RN", ["BUZI_SLR_UNIT1", "BUZI_SLR_UNIT2", "BUZI_SLR_UNIT3", "BUZI_SLR_UNIT4"],
     "HB_WEST", "HB_WEST", None, "Stafford Solar", "solar"),
    ("HRNT_SLR_RN", ["HRNT_SLR_UNIT1", "HRNT_SLR_UNIT2", "HRNT_SLR_UNIT3"],
     "HB_PAN", "NODE", None, "Hornet Solar", "solar"),
    ("AGUAYO_UNIT1", ["AGUAYO_UNIT1"], "HB_WEST", "HB_WEST", None, "Aguayo Wind", "wind"),
    ("WH_WIND_ALL", ["WH_WIND_UNIT1", "WH_WIND_UNIT2"], "HB_WEST", "NODE", None, "Mesquite Star", "wind"),
    ("MLB_SLR_RN", ["MLB_SLR_SOLAR1", "MLB_SLR_SOLAR2", "MLB_SLR_SOLAR3"],
     "HB_NORTH", "HB_NORTH", None, "Miller Solar", "solar"),
    ("MILLERS_BRANCH_2", ["MILLERS_BRANCH_2"], "HB_NORTH", "HB_NORTH", None, "Millers Branch 2", "solar"),
    ("AZURE_SKY_WIND_AGG", ["VORTEX_WIND1", "VORTEX_WIND2", "VORTEX_WIND3", "VORTEX_WIND4"],
     "HB_NORTH", "HB_NORTH", "AZURE_RN", "Azure Sky Wind", "wind"),
]


def main():
    print(f"{'asset':18}{'months':>7}{'ratio':>7}{'basis$':>8}  status")
    newly_ready = []
    for node, units, hub, settle, pnode, label, tech in PORTALS:
        sp = node if settle == "NODE" else settle
        try:
            a = ca.build(node, settle_point=sp, units=units, hub=hub,
                         price_node=pnode, label=label, log=lambda *_: None)
        except Exception as e:  # noqa: BLE001
            print(f"{label:18}{'—':>7}{'—':>7}{'—':>8}  no data ({str(e)[:30]})")
            continue
        n = a["n_months"]
        b = a["basis"].get("basis_genweighted")
        status = "READY" if n >= READY_MONTHS else f"preliminary (<{READY_MONTHS}mo)"
        if n >= READY_MONTHS:
            newly_ready.append(label)
        print(f"{label:18}{n:>7}{a['blended_ratio']:>7.0%}{(b if b is not None else 0):>8.2f}  {status}")
    print()
    print(f"Assets with ≥{READY_MONTHS} months (eligible to wire node= into the portal): "
          + ", ".join(newly_ready))
    print("Held until mature: " + ", ".join(
        f"{l}" for n_, u_, h_, s_, p_, l, t_ in PORTALS
        if (ca.load(n_) or {}).get("n_months", 0) < READY_MONTHS))


if __name__ == "__main__":
    main()
