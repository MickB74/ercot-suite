"""Map cryptic ERCOT SCED resource codes (FRYE_SLR_UNIT1) to human plant names
(Frye Solar). No free authoritative crosswalk exists, so we use a
confidence-flagged cascade and write an editable CSV you can correct by hand.

Precedence (best first), recorded per row in `name_source`:
  overrides   plant_names_overrides.csv (you edit; always wins)
  curated     hand-verified entries (renewables, from the price project)
  known       KNOWN_MAPPINGS prefix dictionary
  queue       fuzzy match into ERCOT's Interconnection Queue (Project Name)
  derived     readable heuristic from the code itself (last resort)

Outputs to data/plant_sced/plant_names.csv.
"""

from __future__ import annotations

import os
import re

import pandas as pd

from ercot_core import paths

CROSSWALK_CSV = str(paths.PLANT_NAMES_CSV)
OVERRIDES_CSV = str(paths.PLANT_NAMES_OVERRIDES_CSV)
IFYI_NAMES_CSV = str(paths.PLANT_NAMES_IFYI_CSV)
QUEUE_CACHE = str(paths.INTERCONNECTION_QUEUE_PARQUET)
_PRICE_ASSETS = str(paths.PRICE_SETTLEMENTS_ASSETS)

_IFYI_COLS = ["resource_name", "plant_name", "queue_id", "url", "county", "capacity_mw"]

# High-confidence prefix -> name (mostly renewables). A prefix matches if it
# appears anywhere in the resource code.
KNOWN_MAPPINGS = {
    "ADL_SOLAR": "Adlong Solar", "ANCHOR_WIND": "Anchor Wind", "ASCK_SLR": "Azalea Springs",
    "AZSP_SLR": "Azalea Springs", "AZURE_SOLAR": "Azure Sky", "BAKERSFIELD": "Bakersfield Solar",
    "BART_SLR": "Bart Solar", "BCATWIND": "Bobcat Wind", "BLVN_SLR": "Blevins Solar",
    "BRISCOE": "Briscoe Wind", "BYNM_SLR": "Bynum Solar", "CABEZON": "Rio Bravo Wind",
    "CAMWIND": "Cameron Wind", "CAPRIDG": "Capricorn Ridge", "CHAL_SLR": "Chaluane Solar",
    "CHIL_SLR": "Chillicothe Solar", "CMPD_SLR": "Compound Solar", "CORALSLR": "Coral Solar",
    "CTW_SOLAR": "CTW Solar", "DIVR_SLR": "Diver Solar", "DMA": "DMA Solar", "DORA_SLR": "Dora Solar",
    "DRCK_SLR": "Dry Creek Solar", "ELZA_SLR": "Eliza Solar", "ESTONIAN": "Estonian Solar",
    "EUNICE_PV": "2W Permian", "EUNICE": "Eunice Wind", "EXGNSND": "Exgen Sound",
    "EXGNWTL": "Exgen Whitetail", "FENCESLR": "Fence Post", "FERMI": "Fermi Wind",
    "FILESSLR": "Files Solar", "FRYE_SLR": "Frye Solar", "FTWIND": "Flat Top Wind",
    "GAIA_SLR": "Gaia Solar", "GALLOWAY": "Galloway Solar", "GOAT": "Goat Mountain",
    "GPASTURE": "Green Pastures", "GRIZZLY": "Grizzly Solar", "GRYH_SLR": "Greyhound Solar",
    "HHOLLOW": "Horse Hollow", "HOLSTEIN": "Holstein Solar", "HRFDWIND": "Hereford Wind",
    "JKLP_SLR": "Blue Jay Solar", "LILY": "Lily Solar", "LMWD_SLR": "Lakewood Solar",
    "LNP": "Long Point", "LON": "Lion Solar", "MERCURY_PV": "Mercury Solar", "MERCURY": "Mercury Solar",
    "MIDP_SLR": "Midpoint Solar", "MIDWIND": "Midway Wind", "MLB_SLR": "Millers Branch Solar",
    "MONTECR": "Monte Cristo", "MOZART": "Mozart Wind", "MRKM_SLR": "Markham Solar",
    "MROW_SLR": "Maryneal", "MUSTNGCK": "Mustang Creek", "NOBLESLR": "BT Noble",
    "NRTN_SLR": "Norton Solar", "PALMWIND": "Palmas Altas", "PHO": "Phoenix Solar",
    "PISGAH": "Pisgah Solar", "QUEEN_SL": "Queen Solar", "RATLIFF": "Ratliff Solar",
    "ROSELAND": "Roseland Solar", "ROUTE_66": "Route 66 Wind", "RRC_WIND": "Roadrunner",
    "SANROMAN": "San Roman", "SOLARA": "Solara", "SPLAIN": "South Plains", "SRWE": "South Ranch",
    "SSPUR": "Spinning Spur", "STAM_SLR": "Stamford Solar", "STLHS_SL": "Stellhaus Solar",
    "TI_SOLAR": "TI Solar", "TNG_SOLAR": "TNG Solar", "TRBT_SLR": "Trumbull Solar",
    "TREB_SLR": "Treeline Solar", "TROJ_SLR": "Trojan Solar", "TYLRWIND": "Tyler Bluff",
    "VERAWIND": "Vera Wind", "VERTIGO": "Vertigo Wind", "VORTEX": "Vortex Wind",
    "WHTTAIL": "Whitetail", "WH_WIND": "Whitehorse", "WILDWIND": "Wildwind", "ZIER_SLR": "Zier Solar",
}

_TECH_WORDS = {
    "SLR": "Solar", "SOLAR": "Solar", "PV": "Solar", "SUN": "Solar",
    "WIND": "Wind", "WND": "Wind",
    "ESR": "Storage", "BESS": "Storage", "BES": "Storage", "ESS": "Storage",
    "STOR": "Storage", "BATT": "Storage",
}
_DROP_TOKENS = {"UNIT", "GEN", "G", "CC", "ST", "GT", "CT", "U", "PV", "SR", "WR", "ALL"}


def _base_token(resource):
    return re.sub(r"\d+$", "", resource.upper().split("_")[0])


def _derive(resource, fuel_group):
    parts = resource.upper().split("_")
    site, tech = [], None
    for p in parts:
        base = re.sub(r"\d+$", "", p)
        if base in _TECH_WORDS:
            tech = tech or _TECH_WORDS[base]
        elif base and base not in _DROP_TOKENS:
            site.append(base)
    site_name = " ".join(w.title() for w in site) if site else parts[0].title()
    if not tech:
        tech = fuel_group if fuel_group not in ("Other", "Renewable") else ""
    return f"{site_name} {tech}".strip()


def _load_curated_prefixes():
    import json
    out = {}
    if os.path.exists(_PRICE_ASSETS):
        try:
            data = json.load(open(_PRICE_ASSETS))
            for v in data.values():
                rn = str(v.get("resource_name", "")).upper()
                if rn and v.get("project_name"):
                    out[re.sub(r"\d+$", "", rn)] = v["project_name"]
        except Exception:
            pass
    return out


def load_queue(allow_fetch=True):
    cols = ["Project Name", "County", "Capacity (MW)", "Fuel", "Generation Type", "Status"]
    if os.path.exists(QUEUE_CACHE):
        try:
            return pd.read_parquet(QUEUE_CACHE)
        except Exception:
            pass
    if not allow_fetch:
        return pd.DataFrame(columns=cols)
    from ercot_core.gridstatus_client import ercot
    q = ercot().get_interconnection_queue()
    q = q[[c for c in cols if c in q.columns]].copy()
    q = q[q["Status"].isin(["Completed", "In Service", "Synchronized", "Active"])]
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    q.to_parquet(QUEUE_CACHE, index=False)
    return q


def _load_overrides():
    if os.path.exists(OVERRIDES_CSV):
        try:
            o = pd.read_csv(OVERRIDES_CSV)
            return dict(zip(o["resource_name"], o["plant_name"]))
        except Exception:
            pass
    return {}


# --- interconnection.fyi learned names (tier above the fuzzy queue match) ---
def load_ifyi_names() -> dict:
    """resource_name -> {plant_name, queue_id, url, county, capacity_mw}."""
    if os.path.exists(IFYI_NAMES_CSV):
        try:
            d = pd.read_csv(IFYI_NAMES_CSV)
            return {str(r["resource_name"]): {k: r.get(k) for k in _IFYI_COLS}
                    for _, r in d.iterrows()}
        except Exception:
            pass
    return {}


def record_ifyi_names(rows: list[dict]) -> int:
    """Persist resource_name -> interconnection.fyi name mappings.

    Writes the durable learned store (survives crosswalk rebuilds, consulted by
    build_crosswalk) and immediately patches the active plant_names.csv so the
    names show up without a full rebuild. `rows` need at least resource_name +
    plant_name; queue_id/url/county/capacity_mw optional.
    """
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    for c in _IFYI_COLS:
        if c not in new.columns:
            new[c] = None
    new = new[_IFYI_COLS].dropna(subset=["resource_name", "plant_name"])
    new = new.drop_duplicates("resource_name", keep="last")
    if new.empty:
        return 0

    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    if os.path.exists(IFYI_NAMES_CSV):
        old = pd.read_csv(IFYI_NAMES_CSV)
        comb = pd.concat([old, new], ignore_index=True).drop_duplicates("resource_name", keep="last")
    else:
        comb = new
    comb.to_csv(IFYI_NAMES_CSV, index=False)
    _patch_active_crosswalk(new)
    return len(new)


# Sources ifyi may overwrite (anything not more authoritative than ifyi).
_WEAKER_THAN_IFYI = {"queue", "derived", "ifyi", "none", "nan", ""}


def _patch_active_crosswalk(new: pd.DataFrame) -> None:
    """Upsert learned rows into the active plant_names.csv as name_source='ifyi',
    respecting precedence: never overwrite an override/curated/known entry."""
    x = load_crosswalk()
    if x.empty:
        x = pd.DataFrame(columns=["resource_name", "plant_name", "name_source", "county", "capacity_mw"])
    x = x.set_index("resource_name")
    for _, r in new.iterrows():
        rn = str(r["resource_name"])
        if rn in x.index and "name_source" in x.columns:
            cur = str(x.loc[rn, "name_source"]).strip().lower()
            if cur not in _WEAKER_THAN_IFYI:
                continue  # keep the stronger source (override / curated / known)
        x.loc[rn] = {
            "plant_name": r["plant_name"], "name_source": "ifyi",
            "county": r.get("county"), "capacity_mw": r.get("capacity_mw"),
        }
    x.reset_index().to_csv(CROSSWALK_CSV, index=False)


def build_crosswalk(registry, allow_fetch=True):
    """Resolve a plant_name for every resource in `registry`; write plant_names.csv."""
    overrides = _load_overrides()
    resolved = load_resolved_names()
    curated = _load_curated_prefixes()
    ifyi = load_ifyi_names()
    queue = load_queue(allow_fetch=allow_fetch)
    if not queue.empty:
        queue = queue.assign(PN=queue["Project Name"].astype(str).str.upper())

    rows = []
    for _, r in registry.iterrows():
        name, src, county, cap = None, None, None, None
        rn, fg, up = r["resource_name"], r["fuel_group"], r["resource_name"].upper()
        tok = _base_token(rn)

        if rn in overrides:
            name, src = overrides[rn], "override"
        if name is None and rn in resolved:
            rec = resolved[rn]
            name, src = rec.get("plant_name"), "resolved"
            county, cap = rec.get("county"), rec.get("capacity_mw")
        if name is None:
            for k, v in curated.items():
                if k in up:
                    name, src = v, "curated"
                    break
        if name is None:
            for k, v in KNOWN_MAPPINGS.items():
                if k in up:
                    name, src = v, "known"
                    break
        # interconnection.fyi learned names — above the fuzzy queue match.
        if name is None and rn in ifyi:
            rec = ifyi[rn]
            name, src = rec.get("plant_name"), "ifyi"
            county, cap = rec.get("county"), rec.get("capacity_mw")
        if name is None and not queue.empty and len(tok) >= 4:
            m = queue[queue["PN"].str.contains(rf"\b{re.escape(tok)}", regex=True, na=False)]
            if not m.empty:
                best = m.sort_values("Capacity (MW)", ascending=False).iloc[0]
                name, src = str(best["Project Name"]).title(), "queue"
                county = best.get("County")
                try:
                    cap = float(best["Capacity (MW)"])
                except (TypeError, ValueError):
                    cap = None
        if name is None:
            name, src = _derive(rn, fg), "derived"

        rows.append({"resource_name": rn, "plant_name": name, "name_source": src,
                     "county": county, "capacity_mw": cap})

    xwalk = pd.DataFrame(rows)
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    xwalk.to_csv(CROSSWALK_CSV, index=False)
    by = xwalk["name_source"].value_counts().to_dict()
    print(f"Names crosswalk -> {CROSSWALK_CSV}  ({len(xwalk)} resources)")
    print("  by source:", by)
    return xwalk


def load_crosswalk():
    if os.path.exists(CROSSWALK_CSV):
        return pd.read_csv(CROSSWALK_CSV)
    return pd.DataFrame(columns=["resource_name", "plant_name", "name_source", "county", "capacity_mw"])


# ── resolver tier: ERCOT code -> authoritative interconnection.fyi name ──────
RESOLVED_CSV = str(paths.PLANT_NAMES_RESOLVED_CSV)
_RESOLVED_COLS = ["resource_name", "plant_name", "queue_id", "county", "capacity_mw"]


def load_resolved_names() -> dict:
    if os.path.exists(RESOLVED_CSV):
        try:
            d = pd.read_csv(RESOLVED_CSV)
            return {str(r["resource_name"]): {k: r.get(k) for k in _RESOLVED_COLS}
                    for _, r in d.iterrows()}
        except Exception:
            pass
    return {}


def record_resolved_names(rows: list[dict]) -> int:
    """Persist code→ifyi-name resolutions (authoritative; overwrite all but manual
    overrides) and patch the active crosswalk with name_source='resolved'."""
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    for c in _RESOLVED_COLS:
        if c not in new.columns:
            new[c] = None
    new = new[_RESOLVED_COLS].dropna(subset=["resource_name", "plant_name"]).drop_duplicates(
        "resource_name", keep="last")
    if new.empty:
        return 0
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    if os.path.exists(RESOLVED_CSV):
        old = pd.read_csv(RESOLVED_CSV)
        comb = pd.concat([old, new], ignore_index=True).drop_duplicates("resource_name", keep="last")
    else:
        comb = new
    comb.to_csv(RESOLVED_CSV, index=False)

    x = load_crosswalk()
    if x.empty:
        x = pd.DataFrame(columns=["resource_name", "plant_name", "name_source", "county", "capacity_mw"])
    x = x.set_index("resource_name")
    for _, r in new.iterrows():
        rn = str(r["resource_name"])
        if rn in x.index and str(x.loc[rn, "name_source"]).strip().lower() == "override":
            continue  # never override a manual override
        x.loc[rn] = {"plant_name": r["plant_name"], "name_source": "resolved",
                     "county": r.get("county"), "capacity_mw": r.get("capacity_mw")}
    x.reset_index().to_csv(CROSSWALK_CSV, index=False)
    return len(new)
