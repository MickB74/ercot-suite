"""Search, analyze, and build due-diligence dossiers over the ERCOT
interconnection queue.

This is the engine behind the ``Ercot Queue`` CLI and (optionally) a Hub page. It
unifies the two queue sources the suite already caches, then layers search,
rollup analytics, and a per-project dossier on top.

Two complementary sources (both cached in the data lake, see
``ercot_core.project_lookup`` / ``ercot_core.ifyi``):

  - **ERCOT GIS report** (``load_full_queue``) — the authoritative *current*
    snapshot: clean Fuel / Technology / Capacity / Interconnecting Entity / POI,
    Status ∈ {Active, Completed}. ~1.8k rows. Drops long-operational projects.
  - **interconnection.fyi** (``ifyi.load_ercot_projects``) — a superset (~3.3k)
    that keeps Withdrawn / Suspended / Operational projects and adds queue &
    completion **dates** and a canonical **URL** per project.

``unified_queue()`` merges them on a normalized Queue ID: GIS wins for the clean
fuel/technology/capacity fields; interconnection.fyi supplies dates, the URL, and
the richer lifecycle status. Everything is cache-first and works offline.
"""

from __future__ import annotations

import re

import pandas as pd

from ercot_core import paths, project_lookup, tx_filings

# Canonical column set of the unified view.
COLUMNS = [
    "queue_id", "project_name", "entity", "county", "poi",
    "capacity_mw", "fuel", "technology", "gen_type",
    "status", "gis_status", "queue_date", "proposed_completion",
    "actual_completion", "in_gis", "url",
]


def _norm_id(s) -> str:
    return re.sub(r"^ERCOT[-_ ]", "", str(s).strip().upper())


def _coalesce(*vals):
    for v in vals:
        if v is not None and str(v).strip() not in ("", "nan", "NaN", "None", "NaT"):
            return v
    return None


_cache: dict[str, pd.DataFrame] = {}


def unified_queue(allow_fetch: bool = False, refresh: bool = False) -> pd.DataFrame:
    """The merged ERCOT queue (GIS ∪ interconnection.fyi), one row per Queue ID.

    Cache-first and offline by default (``allow_fetch=False``). Pass
    ``allow_fetch=True`` to let the GIS source download if its parquet is missing.
    """
    if not refresh and "u" in _cache:
        return _cache["u"]

    gis = project_lookup.load_full_queue(allow_fetch=allow_fetch)
    from ercot_core import ifyi
    fyi = ifyi.load_ercot_projects()

    rows: dict[str, dict] = {}

    # interconnection.fyi first (the superset) ...
    if not fyi.empty:
        for _, r in fyi.iterrows():
            qid = _norm_id(r.get("queue_id"))
            if not qid or qid == "NONE":
                continue
            rows[qid] = {
                "queue_id": qid,
                "project_name": _coalesce(r.get("name")),
                "entity": _coalesce(r.get("entity")),
                "county": _coalesce(r.get("county")),
                "poi": _coalesce(r.get("poi")),
                "capacity_mw": pd.to_numeric(r.get("capacity_mw"), errors="coerce"),
                "fuel": _coalesce(r.get("fuel")),
                "technology": None,
                "gen_type": None,
                "status": _coalesce(r.get("status")),
                "gis_status": None,
                "queue_date": _coalesce(r.get("queue_date")),
                "proposed_completion": _coalesce(r.get("proposed_completion")),
                "actual_completion": _coalesce(r.get("actual_completion")),
                "in_gis": False,
                "url": _coalesce(r.get("url")),
            }

    # ... then overlay GIS (authoritative for the current snapshot).
    if not gis.empty:
        for _, r in gis.iterrows():
            qid = _norm_id(r.get("Queue ID"))
            if not qid:
                continue
            base = rows.get(qid, {"queue_id": qid, "in_gis": False})
            base.update({
                "queue_id": qid,
                "project_name": _coalesce(r.get("Project Name"), base.get("project_name")),
                "entity": _coalesce(r.get("Interconnecting Entity"), base.get("entity")),
                "county": _coalesce(r.get("County"), base.get("county")),
                "poi": _coalesce(r.get("Interconnection Location"), base.get("poi")),
                "capacity_mw": _coalesce(pd.to_numeric(r.get("Capacity (MW)"), errors="coerce"),
                                         base.get("capacity_mw")),
                "fuel": _coalesce(r.get("Fuel"), base.get("fuel")),
                "technology": _coalesce(r.get("Technology")),
                "gen_type": _coalesce(r.get("Generation Type")),
                "gis_status": _coalesce(r.get("Status")),
                # Prefer the richer ifyi lifecycle status if present, else GIS.
                "status": _coalesce(base.get("status"), r.get("Status")),
                "in_gis": True,
            })
            base.setdefault("queue_date", None)
            base.setdefault("proposed_completion", None)
            base.setdefault("actual_completion", None)
            base.setdefault("url", None)
            rows[qid] = base

    df = pd.DataFrame(list(rows.values()))
    if df.empty:
        df = pd.DataFrame(columns=COLUMNS)
    else:
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = None
        df = df[COLUMNS]
        # collapse stray newlines/whitespace in free-text fields (POI etc.)
        for c in ("project_name", "entity", "county", "poi", "fuel",
                  "technology", "gen_type", "status", "gis_status"):
            df[c] = df[c].apply(
                lambda v: re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v)
        # tidy date columns to date-only strings
        for c in ("queue_date", "proposed_completion", "actual_completion"):
            df[c] = df[c].apply(lambda v: str(v)[:10] if v not in (None, "") else None)
    _cache["u"] = df
    return df


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def search(text: str | None = None, *, county=None, fuel=None, technology=None,
           status=None, entity=None, min_mw=None, max_mw=None, in_gis=None,
           sort="capacity_mw", desc=True, limit=None,
           allow_fetch: bool = False) -> pd.DataFrame:
    """Filter the unified queue. All filters are case-insensitive substrings
    (except numeric MW bounds and the ``in_gis`` flag) and combine with AND.

    ``text`` searches across project name, entity, county, queue id, and POI.
    """
    df = unified_queue(allow_fetch=allow_fetch).copy()
    if df.empty:
        return df

    def _contains(col, val):
        return df[col].astype(str).str.contains(re.escape(str(val)), case=False, na=False)

    if text:
        hay = (df["project_name"].astype(str) + "|" + df["entity"].astype(str) + "|"
               + df["county"].astype(str) + "|" + df["queue_id"].astype(str) + "|"
               + df["poi"].astype(str))
        df = df[hay.str.contains(re.escape(text), case=False, na=False)]
    if county:
        df = df[_contains("county", county)]
    if fuel:
        df = df[_contains("fuel", fuel)]
    if technology:
        df = df[_contains("technology", technology) | _contains("gen_type", technology)]
    if status:
        df = df[_contains("status", status) | _contains("gis_status", status)]
    if entity:
        df = df[_contains("entity", entity)]
    if min_mw is not None:
        df = df[pd.to_numeric(df["capacity_mw"], errors="coerce") >= float(min_mw)]
    if max_mw is not None:
        df = df[pd.to_numeric(df["capacity_mw"], errors="coerce") <= float(max_mw)]
    if in_gis is not None:
        df = df[df["in_gis"] == bool(in_gis)]

    if sort in df.columns:
        if sort == "capacity_mw":
            df = df.assign(_s=pd.to_numeric(df[sort], errors="coerce")).sort_values(
                "_s", ascending=not desc, na_position="last").drop(columns="_s")
        else:
            df = df.sort_values(sort, ascending=not desc, na_position="last")
    df = df.reset_index(drop=True)
    return df.head(limit) if limit else df


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
def stats(by="fuel", *, status=None, county=None, fuel=None, technology=None,
          entity=None, in_gis=None, allow_fetch: bool = False) -> pd.DataFrame:
    """Rollup: count of projects and total/median MW grouped by a column.

    ``by`` ∈ {fuel, technology, status, county, entity, gis_status}. The same
    filters as ``search`` can scope the population before grouping.
    """
    df = search(status=status, county=county, fuel=fuel, technology=technology,
                entity=entity, in_gis=in_gis, allow_fetch=allow_fetch)
    if df.empty or by not in df.columns:
        return pd.DataFrame(columns=[by, "projects", "total_mw", "median_mw"])
    g = df.assign(mw=pd.to_numeric(df["capacity_mw"], errors="coerce")).groupby(
        df[by].fillna("(unknown)").astype(str))
    out = g.agg(projects=("queue_id", "count"),
                total_mw=("mw", "sum"),
                median_mw=("mw", "median")).reset_index()
    out["total_mw"] = out["total_mw"].round(1)
    out["median_mw"] = out["median_mw"].round(1)
    return out.sort_values("total_mw", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# Per-project dossier (the due-diligence assembly)
# --------------------------------------------------------------------------
def infer_tech(name: str | None, fuel: str | None = None,
               technology: str | None = None) -> str | None:
    """Best-guess technology label ('Solar'/'Wind'/'Storage'/'Gas'), from the
    explicit fields first, else keyword-sniffed from the project name. Lets the
    DD checklist and tech-specific links still fire on ifyi-only records whose
    Fuel/Technology columns are blank."""
    blob = f"{fuel or ''} {technology or ''} {name or ''}".lower()
    if "wind" in blob:
        return "Wind"
    if "solar" in blob or "photovolt" in blob or "_slr" in blob:
        return "Solar"
    if "bess" in blob or "stor" in blob or "battery" in blob:
        return "Storage"
    if "gas" in blob or "combined" in blob or "turbine" in blob:
        return "Gas"
    return None


def _registry_match(name: str, county: str | None, tech: str | None = None) -> dict | None:
    """Find a curated-registry asset matching this project name (token overlap,
    with a county bonus and a same-/cross-tech adjustment so e.g. a Solar query
    doesn't latch onto a same-named Wind asset)."""
    reg = project_lookup.registered_projects()
    if not reg:
        return None
    toks = set(project_lookup.tokens(name))
    if not toks:
        return None
    qtech = (tech or infer_tech(name) or "").lower()
    best, best_score = None, 0.0
    for rec in reg:
        rtoks = set(project_lookup.tokens(str(rec.get("project_name", ""))))
        overlap = len(toks & rtoks)
        if overlap == 0:
            continue
        score = float(overlap)
        if county and str(rec.get("county", "")).lower() == str(county).lower():
            score += 1
        rtech = str(rec.get("tech", "")).lower()
        if qtech and rtech:
            score += 1 if qtech == rtech else -0.75   # reward agreement, dock mismatch
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= 1 else None


def dossier(query: str, *, allow_fetch: bool = False) -> dict:
    """Full due-diligence package for a single project (queue id or name).

    Assembles: the unified queue record, the interconnection.fyi link, the
    resource-node crosswalk + cached-data availability (via ``project_lookup``),
    a curated-registry match if one exists, authoritative county/state filing
    deep-links, and a tech-aware DD checklist.
    """
    df = unified_queue(allow_fetch=allow_fetch)
    qn = _norm_id(query)
    rec = None
    if not df.empty:
        hit = df[df["queue_id"] == qn]
        if hit.empty:
            hit = df[df["project_name"].astype(str).str.contains(
                re.escape(query), case=False, na=False)]
        if not hit.empty:
            # prefer the largest / most-complete match
            hit = hit.assign(_s=pd.to_numeric(hit["capacity_mw"], errors="coerce")).sort_values(
                "_s", ascending=False)
            rec = hit.drop(columns="_s").iloc[0].to_dict()

    name = (rec or {}).get("project_name") or query
    county = (rec or {}).get("county")
    fuel = (rec or {}).get("fuel")
    tech = (rec or {}).get("technology") or (rec or {}).get("gen_type")
    entity = (rec or {}).get("entity")
    # Fall back to a name-sniffed tech when the columns are blank (common on
    # ifyi-only records), so the checklist & tech-specific links still apply.
    eff_tech = tech or infer_tech(name, fuel, tech)

    reg_match = _registry_match(name, county, eff_tech)
    eia_id = (reg_match or {}).get("eia_plant_id")

    # Resource-node crosswalk + cached-data availability (best-effort; catalog
    # may be empty in a fresh checkout).
    crosswalk = {}
    try:
        lk = project_lookup.lookup(name, allow_fetch=allow_fetch)
        crosswalk = {
            "name_used": lk.get("name_used"),
            "candidates": [
                {"resource_node": c["resource_node"], "units": c["units"],
                 "match": c["match"], "availability": c["availability"]}
                for c in lk.get("candidates", [])[:5]
            ],
            "ifyi": lk.get("ifyi"),
            "queue_note": lk.get("queue_note"),
        }
    except Exception as e:  # noqa: BLE001
        crosswalk = {"error": str(e)}

    return {
        "query": query,
        "record": rec,
        "found": rec is not None,
        "inferred_tech": eff_tech,
        "registry_match": reg_match,
        "crosswalk": crosswalk,
        "filing_links": tx_filings.filing_links(
            name, county=county, entity=entity, fuel=fuel, technology=eff_tech,
            eia_plant_id=eia_id, ifyi_url=(rec or {}).get("url"),
            queue_id=(rec or {}).get("queue_id")),
        "dd_checklist": tx_filings.dd_checklist(fuel=fuel, technology=eff_tech),
    }
