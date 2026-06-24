"""
Map cryptic ERCOT SCED resource codes (FRYE_SLR_UNIT1) to human plant names
(Frye Solar). There is no free authoritative code->plant crosswalk, so we use a
confidence-flagged cascade and write an editable CSV you can correct by hand.

Precedence (best first), recorded per row in `name_source`:
  overrides   plant_names_overrides.csv (you edit; always wins)
  curated     hand-verified entries (renewables, from the price project)
  known       KNOWN_MAPPINGS prefix dictionary
  queue       fuzzy match into ERCOT's Interconnection Queue (Project Name)
  derived     readable heuristic from the code itself (last resort)

Build/refresh:  python fetch_plants.py --build-names
Output:         plant_names.csv  (resource_name, plant_name, name_source, county, capacity_mw)
"""
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CROSSWALK_CSV = os.path.join(HERE, "plant_names.csv")
OVERRIDES_CSV = os.path.join(HERE, "plant_names_overrides.csv")
QUEUE_CACHE = os.path.join(HERE, "interconnection_queue.parquet")
# Curated renewable registry (optional). Lives in the separate price_settlements
# repo; override with ERCOT_ASSETS_PATH, else use a sibling checkout if present.
# Consumers guard on existence, so absence just drops the "curated" name source.
_PRICE_ASSETS = os.environ.get(
    "ERCOT_ASSETS_PATH",
    os.path.join(HERE, "..", "price_settlements", "ercot_assets.json"),
)

# High-confidence prefix -> name (carried over from the price project, mostly
# renewables). A prefix matches if it appears anywhere in the resource code.
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

# Technology tokens stripped/recognised when deriving a readable name.
_TECH_WORDS = {
    "SLR": "Solar", "SOLAR": "Solar", "PV": "Solar", "SUN": "Solar",
    "WIND": "Wind", "WND": "Wind",
    "ESR": "Storage", "BESS": "Storage", "BES": "Storage", "ESS": "Storage",
    "STOR": "Storage", "BATT": "Storage",
}
_DROP_TOKENS = {"UNIT", "GEN", "G", "CC", "ST", "GT", "CT", "U", "PV", "SR", "WR", "ALL"}


def _base_token(resource):
    """Leading abbreviation token, e.g. FRYE_SLR_UNIT1 -> FRYE."""
    return re.sub(r"\d+$", "", resource.upper().split("_")[0])


def _derive(resource, fuel_group):
    """Readable best-guess name from the code itself."""
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
    """resource-code prefix -> verified project name, from price project assets."""
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
    import gridstatus
    q = gridstatus.Ercot().get_interconnection_queue()
    q = q[[c for c in cols if c in q.columns]].copy()
    q = q[q["Status"].isin(["Completed", "In Service", "Synchronized", "Active"])]
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


def build_crosswalk(registry, allow_fetch=True):
    """Resolve a plant_name for every resource in `registry`; write plant_names.csv."""
    overrides = _load_overrides()
    curated = _load_curated_prefixes()
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
        if name is None and not queue.empty and len(tok) >= 4:
            # Token must start a word in the project name: \bAMAD matches
            # "Amador" but \bANSON does not match "Hanson".
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
    xwalk.to_csv(CROSSWALK_CSV, index=False)
    by = xwalk["name_source"].value_counts().to_dict()
    print(f"Names crosswalk -> {CROSSWALK_CSV}  ({len(xwalk)} resources)")
    print("  by source:", by)
    return xwalk


def load_crosswalk():
    if os.path.exists(CROSSWALK_CSV):
        return pd.read_csv(CROSSWALK_CSV)
    return pd.DataFrame(columns=["resource_name", "plant_name", "name_source", "county", "capacity_mw"])
