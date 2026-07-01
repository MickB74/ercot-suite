"""Build a SCED-resource ↔ USWTDB-project crosswalk for wind plants.

There is no shared key between ERCOT SCED resource codes (e.g. LV1_LV1A) and
USWTDB project names (e.g. "Los Vientos I"), so we match on two signals and
require BOTH:

  1. Capacity agreement — summed SCED peak (99.9th-pct MW) ≈ USWTDB nameplate
     (the reliable anchor).
  2. Name-token overlap — after expanding common ERCOT abbreviations.

Output (crosswalk_wind.json): one row per validated plant with coords, capacity,
SCED units, and an ERCOT region bucket (coastal South split from inland). Fed to
build_ws_scale.py to learn geography-aware wind-speed priors from many plants.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

import turbine_db as tdb
import wind_calibration as wc

HERE = Path(__file__).resolve().parent
SCED_DIR = HERE.parent / "Ercot_Data_Hub" / "data" / "plant_sced" / "plants"
OUT = HERE / "reference" / "crosswalk_wind.json"

# ERCOT-code → English abbreviation expansions (token level).
ABBREV = {
    "MTN": "mountain", "MT": "mountain", "CRK": "creek", "CR": "creek",
    "WND": "wind", "WD": "wind", "HHOLLOW": "horse hollow", "HHGT": "horse hollow",
    "LV": "los vientos", "BUFF": "buffalo", "PAP": "papalote", "SPLAIN": "south plains",
    "HRFD": "hereford", "SWEETWN": "sweetwater", "PYR": "pyron", "INDL": "inadale",
    "CEDROHIL": "cedro hill", "SAGEDRAW": "sage draw", "RSNAKE": "rattlesnake",
    "GPASTURE": "green pastures", "BRAZ": "brazos", "BCAT": "bobcat", "GOAT": "goat",
    "BULLCRK": "bull creek", "MESQCRK": "mesquite creek", "CAPRIDGE": "cap ridge",
    "CAPRIDG": "cap ridge", "LNCRK": "lone", "SSPUR": "spinning spur", "STWF": "sweetwater",
    "TKWSW": "sweetwater", "MAVCRK": "maverick creek", "BLSUMMIT": "blue summit",
    "BLSUMIT": "blue summit", "FTWIND": "goldthwaite", "WHMESA": "white mesa",
    "WHTTAIL": "white tail", "RDCANYON": "red canyon", "TRENT": "trent mesa",
    "PANTH": "panther", "PC": "panther creek", "GRIF": "griffin", "SANROMAN": "san roman",
    "CAMWIND": "cameron", "PENA": "penascal", "KEECHI": "keechi", "LOCKETT": "lockett",
    "MARYNEAL": "maryneal", "CHAMPION": "champion", "FLUVANNA": "fluvanna",
    "BRISCOE": "briscoe", "SENATEWD": "senate", "ROUTE": "route", "SALTFORK": "salt fork",
    "CFLATS": "cactus flats", "ELB": "elbow creek", "NWF": "noble", "OWF": "old settler",
    "LGD": "langford", "APPALOSA": "appaloosa run", "COYOTE": "coyote", "GUNMTN": "gunsight",
    # North / north-central additions
    "HORSECRK": "horse creek", "BCATWIND": "bobcat bluff", "SHANNONW": "shannon",
    "WNDTHST2": "windthorst", "WNDTHST": "windthorst", "BARTONCH": "barton chapel",
    "WOLFRIDGE": "wolf ridge", "TYLRWIND": "tyler bluff", "COOKE": "wildcat creek",
    "BUCKTHRN": "buckthorn", "KEECHI": "keechi",
    # Panhandle / South Plains additions
    "GRANDVW1": "grandview", "GRANDVW": "grandview", "SSPURTWO": "spinning spur",
    "SS3WIND": "spinning spur", "WILDORADO": "wildorado", "LLANO": "llano estacado",
    "MAJESTIC": "majestic", "MCADOO": "mcadoo", "LORENZO": "lorenzo", "SALTFORK": "salt fork",
    "PANHANDLE": "panhandle", "BLUESMMT": "blue summit", "BLSUMMIT": "blue summit",
}
DROP = {"unit", "units", "wind", "gen", "g", "esr", "the", "farm", "energy",
        "project", "ranch", "phase", "i", "ii", "iii", "iv", "v", "wf", "creek", "1", "2"}


def _norm_tokens(s: str) -> set:
    s = re.sub(r"[^A-Za-z0-9]+", " ", str(s)).strip().lower()
    toks = set()
    for t in s.split():
        t2 = ABBREV.get(t.upper())
        (toks.update(t2.split()) if t2 else toks.add(t))
    return {t for t in toks if t not in DROP and len(t) > 1}


def _plant_key(u: str) -> str:
    k = u
    for _ in range(4):
        k = re.sub(r"_(UNIT|WIND|WND|GEN|G|ESR)\w*$", "", k)
        k = re.sub(r"_[0-9]+$", "", k)
        k = re.sub(r"_[A-Z]{1,6}[0-9]+[A-Z]?$", "", k)
    return k


def _sced_clusters() -> dict:
    """plant_key -> {units:[...], peak: MW} for WIND resources."""
    clusters = defaultdict(lambda: {"units": [], "peak": 0.0})
    files = sorted(glob.glob(str(SCED_DIR / "*_2025.parquet")))
    for f in files:
        u = os.path.basename(f).replace("_2025.parquet", "")
        try:
            df = pd.read_parquet(f, columns=["resource_type", "telemetered_net_output"])
        except Exception:
            continue
        if df.empty or not str(df["resource_type"].dropna().iloc[0]).upper().startswith("WIND"):
            continue
        peak = float(df["telemetered_net_output"].quantile(0.999))
        c = clusters[_plant_key(u)]
        c["units"].append(u)
        c["peak"] += max(peak, 0.0)
    return clusters


def _region(lat: float, lon: float) -> str:
    hub = wc.infer_hub(lat, lon) or "NORTH"
    if hub == "SOUTH":
        return "SOUTH_COAST" if lon > -98.2 else "SOUTH_INLAND"
    return hub


def _alpha(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def build(cap_tol: float = 0.22, verbose: bool = True) -> list:
    import os
    projs = [p for p in tdb.list_projects() if (p.get("capacity_mw") or 0) >= 50]
    # (project, name tokens, alpha-smooshed name) for matching.
    ptok = [(p, _norm_tokens(p["name"]), _alpha(p["name"])) for p in projs]
    rows, used = [], set()
    for key, c in sorted(_sced_clusters().items()):
        peak = c["peak"]
        if peak < 40:
            continue
        ktok = _norm_tokens(key)
        ka = _alpha(key)
        best, best_score = None, 0.0
        for p, tk, pa in ptok:
            cap = p["capacity_mw"] or 0
            if not cap:
                continue
            # A shared ≥5-char name prefix (e.g. AVIATOR↔aviatorwind, SHANNONW↔
            # shannon) is a strong ID; allow a looser cap window for it since SCED
            # clustering can split/partial a plant. Otherwise require tight cap +
            # a distinctive shared token.
            # ≥6-char shared prefix: strong enough to avoid common-prefix
            # collisions (e.g. "santa" in Santa Rita vs Santa Cruz).
            cpre = len(os.path.commonprefix([ka, pa]))
            strong = cpre >= 6
            if strong:
                if not (0.5 <= peak / cap <= 2.0):
                    continue
                score = 1.0 + cpre / 100.0        # prefer longer prefixes
            else:
                if abs(cap - peak) / max(cap, peak) > cap_tol:
                    continue
                inter = ktok & tk
                distinctive = any(len(t) >= 4 for t in inter)
                score = len(inter) / max(1, len(ktok | tk))
                if not (inter and (distinctive or score >= 0.5)):
                    continue
            if score > best_score:
                best, best_score = p, score
        if best and best_score >= 0.2 and best["name"] not in used:
            used.add(best["name"])
            rows.append({"plant": key, "uswtdb_name": best["name"],
                         "lat": round(best["lat"], 4), "lon": round(best["lon"], 4),
                         "uswtdb_cap": round(best["capacity_mw"], 0), "sced_peak": round(peak, 0),
                         "region": _region(best["lat"], best["lon"]), "units": c["units"]})
    if verbose:
        by = defaultdict(int)
        for r in rows:
            by[r["region"]] += 1
            print(f"  {r['plant']:<18} → {r['uswtdb_name']:<26} "
                  f"{r['region']:<12} cap {r['uswtdb_cap']:.0f}/{r['sced_peak']:.0f}MW  units={len(r['units'])}")
        print(f"\nMatched {len(rows)} plants. By region: {dict(by)}")
    return rows


if __name__ == "__main__":
    import sys
    rows = build()
    if "--dry" not in sys.argv:
        OUT.write_text(json.dumps(rows, indent=2))
        print(f"Wrote {OUT}")
