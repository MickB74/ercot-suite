"""Reverse lookup: interconnection project (queue # or name) -> ERCOT resource node.

There is no official crosswalk from an ERCOT queue ID (e.g. 21INR0477) to a
market resource node (e.g. AZURE_RN). Two bridges, used together:

  1. The interconnection queue (gridstatus) — maps a Queue ID to the project
     NAME, POI, county, capacity. BUT ERCOT's GIS report drops long-operational
     projects, so older completed projects (like Azure Sky) won't be in it.
  2. The resource-node catalog + the plant-name crosswalk — map a project NAME
     to its resource node(s)/unit(s). This works for anything already producing
     in the SCED data, including operational legacy projects.

So: a queue # gets you the name (if still listed); the name gets you the node.
For operational projects missing from the queue, search by name directly.
"""

from __future__ import annotations

import glob
import json
import os
import re

import pandas as pd

from ercot_core import paths

# Generic words to ignore when tokenising a project name.
_GENERIC = {
    "SOLAR", "WIND", "STORAGE", "ENERGY", "PROJECT", "LLC", "INC", "LP", "LTD",
    "BESS", "BATTERY", "POWER", "GENERATION", "FARM", "PV", "PHOTOVOLTAIC",
    "THE", "OF", "AND", "CENTER", "STATION", "PLANT", "HOLDINGS", "I", "II",
    "III", "IV", "UNIT", "UNITS", "PHASE",
}


# --------------------------------------------------------------------------
# Interconnection queue (full, with Queue ID)
# --------------------------------------------------------------------------
_QUEUE_COLS = [
    "Queue ID", "Project Name", "Interconnecting Entity", "County",
    "Interconnection Location", "Generation Type", "Capacity (MW)",
    "Fuel", "Technology", "Status",
]


def load_full_queue(allow_fetch: bool = True, refresh: bool = False) -> pd.DataFrame:
    """Full ERCOT interconnection queue (clean subset, incl. Queue ID), cached."""
    path = paths.INTERCONNECTION_QUEUE_FULL_PARQUET
    if path.exists() and not refresh:
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    if not allow_fetch:
        return pd.DataFrame(columns=_QUEUE_COLS)

    from ercot_core.gridstatus_client import ercot
    q = ercot().get_interconnection_queue()
    cols = [c for c in _QUEUE_COLS if c in q.columns]
    out = q[cols].copy()
    # Coerce everything except capacity to string (the raw frame mixes
    # datetimes/strings in some columns and won't serialise otherwise).
    for c in out.columns:
        if c == "Capacity (MW)":
            out[c] = pd.to_numeric(out[c], errors="coerce")
        else:
            out[c] = out[c].astype(str)
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def normalize_queue_id(raw: str) -> str:
    """'ercot-21inr0477', '21inr0477', '21INR0477' -> '21INR0477'."""
    s = str(raw).strip().upper()
    s = re.sub(r"^ERCOT[-_ ]", "", s)
    return s.strip()


def find_in_queue(query: str, allow_fetch: bool = True) -> pd.DataFrame:
    """Rows whose Queue ID or Project Name matches `query` (substring)."""
    q = load_full_queue(allow_fetch=allow_fetch)
    if q.empty:
        return q
    qid = normalize_queue_id(query)
    by_id = q[q["Queue ID"].astype(str).str.upper().str.contains(re.escape(qid), na=False)]
    if not by_id.empty:
        return by_id.reset_index(drop=True)
    return q[q["Project Name"].astype(str).str.contains(re.escape(query), case=False, na=False)].reset_index(drop=True)


# --------------------------------------------------------------------------
# Name -> resource node(s)
# --------------------------------------------------------------------------
def tokens(name: str) -> list[str]:
    """Meaningful upper-case tokens of a project name (len>=3, non-generic)."""
    words = re.split(r"[^A-Za-z0-9]+", str(name).upper())
    return [w for w in words if len(w) >= 3 and w not in _GENERIC and not w.isdigit()]


def _load_catalog() -> pd.DataFrame:
    if paths.CATALOG_PATH.exists():
        return pd.read_parquet(paths.CATALOG_PATH)
    return pd.DataFrame(columns=["resource_node", "unit_substation", "unit_name",
                                 "sced_resource_name", "resource_type"])


def candidate_nodes(name: str) -> pd.DataFrame:
    """Resource nodes likely matching a project name, with how they matched.

    Columns: resource_node, units (list), types (list), match (str), score (int).
    """
    cols = ["resource_node", "units", "types", "match", "score"]
    cat = _load_catalog()
    if cat.empty:
        return pd.DataFrame(columns=cols)

    toks = tokens(name)
    primary = max(toks, key=len) if toks else re.sub(r"[^A-Z0-9]", "", name.upper())

    norm = (cat["resource_node"].astype(str) + " " + cat["sced_resource_name"].astype(str)) \
        .str.upper().str.replace("_", "", regex=False)
    try:
        from ercot_core import plant_names
        xwalk = plant_names.load_crosswalk()
        ifyi_store = plant_names.load_ifyi_names()  # learned aliases (resource -> plant_name)
    except Exception:
        xwalk = pd.DataFrame()
        ifyi_store = {}
    pn = (xwalk["plant_name"].astype(str).str.upper()
          if (not xwalk.empty and "plant_name" in xwalk.columns) else None)

    def _collect(search_tokens: list[str]) -> dict:
        hits: dict[str, set] = {}  # node -> match methods
        for t in search_tokens:
            # Method A — catalog: normalized node / sced name contains the token.
            for node in cat.loc[norm.str.contains(re.escape(t), na=False), "resource_node"].unique():
                hits.setdefault(node, set()).add(f"catalog:{t}")
            # Method B — plant-name crosswalk: plant_name contains the token.
            if pn is not None:
                for rn in xwalk.loc[pn.str.contains(re.escape(t), na=False), "resource_name"].astype(str):
                    for nval in cat.loc[cat["sced_resource_name"].astype(str) == rn, "resource_node"].unique():
                        hits.setdefault(nval, set()).add(f"crosswalk:{t}")
            # Method C — ifyi alias store: catches projects whose queue name differs
            # from their ERCOT resource token (e.g. "Whitehorse Wind" -> WH_WIND).
            for rn, rec in ifyi_store.items():
                alias = str(rec.get("plant_name", "")).upper()
                if alias and re.search(re.escape(t), alias):
                    for nval in cat.loc[cat["sced_resource_name"].astype(str) == rn, "resource_node"].unique():
                        hits.setdefault(nval, set()).add(f"alias:{t}")
        # Whole-name crosswalk match (strongest signal).
        if pn is not None:
            for rn in xwalk.loc[pn.str.contains(re.escape(name.upper()), na=False), "resource_name"].astype(str):
                for nval in cat.loc[cat["sced_resource_name"].astype(str) == rn, "resource_node"].unique():
                    hits.setdefault(nval, set()).add("crosswalk:name")
        return hits

    # Match on the primary (most distinctive) token first; only widen to all
    # tokens if that finds nothing, so short generic words don't add noise.
    hits = _collect([primary])
    if not hits and len(toks) > 1:
        hits = _collect(toks)

    rows = []
    for node, methods in hits.items():
        sub = cat[cat["resource_node"] == node]
        rows.append({
            "resource_node": node,
            "units": sorted(sub["sced_resource_name"].astype(str).unique().tolist()),
            "types": sorted([t for t in sub["resource_type"].dropna().astype(str).unique()]),
            "match": ", ".join(sorted(methods)),
            "score": len(methods) + (2 if any(m == "crosswalk:name" for m in methods) else 0),
        })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# Cached-data availability for a node / its units
# --------------------------------------------------------------------------
def _node_data_has(template_glob: str, col: str, value: str) -> int:
    n = 0
    for p in glob.glob(str(paths.NODE_DATA_DIR / template_glob)):
        try:
            df = pd.read_parquet(p, columns=[col])
            n += int((df[col] == value).sum())
        except Exception:
            pass
    return n


def data_availability(node: str, units: list[str]) -> dict:
    """What we already have cached for a node / its units."""
    plant_files = []
    for u in units:
        plant_files += glob.glob(str(paths.PLANT_DATA_DIR / f"{u}_*.parquet"))
    in_registry = []
    if paths.PLANT_REGISTRY_PARQUET.exists():
        try:
            reg = pd.read_parquet(paths.PLANT_REGISTRY_PARQUET, columns=["resource_name"])
            known = set(reg["resource_name"].astype(str))
            in_registry = [u for u in units if u in known]
        except Exception:
            pass
    return {
        "price_rows_cached": _node_data_has("node_price_*.parquet", "location", node),
        "gen_rows_cached": _node_data_has("node_generation_*.parquet", "resource_node", node),
        "plant_sced_files": len(plant_files),
        "units_in_registry": in_registry,
    }


# --------------------------------------------------------------------------
# Top-level
# --------------------------------------------------------------------------
def persist_to_crosswalk(units: list[str], plant_name: str, queue_id=None,
                         url=None, county=None, capacity_mw=None) -> int:
    """Save resource_name -> name mappings into the learned 'ifyi' crosswalk tier."""
    from ercot_core import plant_names
    rows = [{"resource_name": u, "plant_name": plant_name, "queue_id": queue_id,
             "url": url, "county": county, "capacity_mw": capacity_mw} for u in units]
    return plant_names.record_ifyi_names(rows)


# --------------------------------------------------------------------------
# Curated asset registry (ercot_assets.json)
# --------------------------------------------------------------------------
# This is the source of truth that powers every analysis page (Plant Value,
# Wind Capture, PPA Settlement, the forecasts) and the standalone project
# portals (Markham, Azure Sky). Registering a project here is what turns a bare
# ERCOT node into something the whole Hub can see and a portal can be built on.

def load_registry() -> dict:
    """The curated asset registry as a name -> record dict ({} if not present)."""
    p = paths.PRICE_SETTLEMENTS_ASSETS
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def registered_projects() -> list[dict]:
    """Every registered project, each record carrying its ``project_name`` key."""
    out = []
    for name, rec in load_registry().items():
        r = dict(rec)
        r.setdefault("project_name", name)
        out.append(r)
    out.sort(key=lambda r: str(r.get("project_name") or r.get("resource_name", "")))
    return out


def upsert_asset(project_name: str, record: dict) -> str:
    """Insert or update one project in the curated registry, keyed by project_name.

    Empty/None fields are dropped; existing fields are preserved unless the new
    record overwrites them. Returns the registry file path that was written.
    """
    p = paths.PRICE_SETTLEMENTS_ASSETS
    reg = load_registry()
    clean = {k: v for k, v in record.items() if v not in (None, "", [])}
    clean.setdefault("project_name", project_name)
    reg[project_name] = {**reg.get(project_name, {}), **clean}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2))
    return str(p)


def lookup(query: str, allow_fetch: bool = True) -> dict:
    """Resolve a queue # or project name to resource node(s) + data availability."""
    result: dict = {"query": query, "queue_matches": [], "name_used": query, "candidates": []}

    looks_like_id = bool(re.search(r"\d{2}[A-Za-z]{2,4}\d", normalize_queue_id(query)))
    if looks_like_id:
        qm = find_in_queue(query, allow_fetch=allow_fetch)
        if not qm.empty:
            result["queue_matches"] = qm.to_dict("records")
            # Use the first match's project name to drive the node search.
            result["name_used"] = str(qm.iloc[0]["Project Name"])
        else:
            # Not in ERCOT's live queue (operational legacy projects drop out).
            # Fall back to interconnection.fyi, which keeps operational projects.
            # (Cache-first: returns a cached record even when offline.)
            from ercot_core import ifyi
            rec = ifyi.fetch_project(query, allow_fetch=allow_fetch)
            if rec and rec.get("name"):
                result["ifyi"] = rec
                result["name_used"] = str(rec["name"])
                result["queue_note"] = (
                    f"Not in ERCOT's live queue; resolved via interconnection.fyi "
                    f"to “{rec['name']}” ({rec.get('status')}, {rec.get('county')} Co, "
                    f"{rec.get('capacity_mw')} MW).")
            else:
                result["queue_note"] = (
                    "Queue ID not found in the current ERCOT queue (operational legacy "
                    "projects are dropped) and interconnection.fyi lookup found nothing"
                    + ("" if allow_fetch else " — re-run online to try interconnection.fyi")
                    + ". Search by project NAME instead.")
    else:
        qm = find_in_queue(query, allow_fetch=allow_fetch)
        if not qm.empty:
            result["queue_matches"] = qm.to_dict("records")

    cand = candidate_nodes(result["name_used"])
    # If the project name yields nothing (e.g. queue calls it "Whitehorse Wind" but the
    # ERCOT resource is WH_WIND which only the crosswalk knows as "Mesquite Star"), also
    # try the Interconnecting Entity name — it often matches the commercial/resource name.
    if cand.empty and result.get("queue_matches"):
        entity = str(result["queue_matches"][0].get("Interconnecting Entity", "")).strip()
        if entity:
            cand = candidate_nodes(entity)
    recs = []
    for _, r in cand.iterrows():
        rec = r.to_dict()
        rec["availability"] = data_availability(r["resource_node"], r["units"])
        recs.append(rec)
    result["candidates"] = recs
    return result
