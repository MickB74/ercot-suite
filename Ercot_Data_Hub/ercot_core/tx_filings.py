"""Texas regulatory / county-filing deep-links and due-diligence checklists.

There is **no single API** that returns "all county filings" for an ERCOT
interconnection project. Project due diligence in Texas is spread across a
handful of authoritative public systems, each searchable by project name,
developer entity, or county:

  - **PUC Interchange** — CCN, transmission, and interconnection dockets
  - **Texas Comptroller** — Ch. 313 (expired 2022) / Ch. 403 JETI school-tax
    limitation agreements (the headline state incentive for big renewables)
  - **County Appraisal District (CAD)** — parcel ownership, valuations,
    abatement (Ch. 312) records
  - **County Clerk** — recorded leases/easements, road-use & decommissioning
    agreements, lien filings
  - **FAA OE/AAA** — obstruction evaluation (every wind turbine; tall solar/met
    towers) — a hard gate for wind
  - **TCEQ** — air (gen sets), stormwater (construction), water rights
  - **SOS / Comptroller entity search** — who the developer LLC really is

So rather than pretend a filings feed exists, this module turns a project into a
**labeled set of authoritative deep-links** (real query URLs where the portal
supports them, a pre-filled search elsewhere) plus a **tech-specific DD
checklist**. Everything here is deterministic and offline — no network calls.
"""

from __future__ import annotations

import urllib.parse as _url

# --------------------------------------------------------------------------
# Project-record portals (ERCOT / EIA)
# --------------------------------------------------------------------------
EIA_PLANT_BROWSER = "https://www.eia.gov/electricity/data/browser/#/plant/"  # + plant id
ERCOT_GIS_REPORT = "https://www.ercot.com/gridinfo/generation"  # GIS report landing


def eia_link(eia_plant_id=None, project_name: str | None = None) -> dict:
    """Direct EIA Electricity Data Browser plant page when an EIA plant id is
    known, else a search for the plant on EIA."""
    if eia_plant_id is not None and str(eia_plant_id).strip() not in ("", "None", "nan"):
        try:
            pid = str(int(float(eia_plant_id)))
            return {"label": f"EIA Electricity Data Browser — plant {pid}",
                    "url": EIA_PLANT_BROWSER + pid,
                    "note": "monthly net generation, fuel consumption, capacity (EIA-923/860)",
                    "kind": "direct"}
        except (ValueError, TypeError):
            pass
    return {"label": "EIA Electricity Data Browser",
            "url": _g(f"EIA electricity data browser plant {project_name or ''}".strip()),
            "note": "find the plant on EIA (no EIA plant id crosswalked yet)",
            "kind": "search"}


def ercot_links(queue_id: str | None = None, ifyi_url: str | None = None) -> list[dict]:
    """ERCOT-side project records: the interconnection.fyi project page (direct,
    when known — it mirrors ERCOT's queue) plus the official ERCOT GIS report."""
    out: list[dict] = []
    if ifyi_url:
        out.append({"label": "interconnection.fyi — project page (ERCOT queue mirror)",
                    "url": ifyi_url,
                    "note": "POI, capacity, status, queue & completion dates", "kind": "direct"})
    out.append({"label": "ERCOT GIS Report (Generator Interconnection Status)",
                "url": _g(f"ERCOT Generator Interconnection Status GIS report {queue_id or ''}".strip()),
                "note": (f"find Queue ID {queue_id} in ERCOT's monthly GIS report"
                         if queue_id else "ERCOT's monthly interconnection-queue report"),
                "kind": "search"})
    return out


# --------------------------------------------------------------------------
# Statewide regulatory portals (stable URLs)
# --------------------------------------------------------------------------
PUC_INTERCHANGE = "https://interchange.puc.texas.gov/search/filings/"
COMPTROLLER_313 = "https://comptroller.texas.gov/economy/local/ch313/agreement-documents.php"
COMPTROLLER_JETI = "https://comptroller.texas.gov/economy/local/ch403-jeti/"
COMPTROLLER_ENTITY = "https://mycpa.cpa.state.tx.us/coa/"          # taxable-entity search
SOS_DIRECT = "https://www.sos.state.tx.us/corp/sosda/index.shtml"  # entity / registered agent
FAA_OEAAA = "https://oeaaa.faa.gov/oeaaa/external/searchAction.jsp?action=showSearchCriteriaForm"
TCEQ_RECORDS = "https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_SEARCH"
TX_GLO = "https://www.glo.texas.gov/"                              # state lands / coastal
USFWS_IPAC = "https://ipac.ecosphere.fws.gov/"                     # protected-species screen


def _g(query: str) -> str:
    """A Google search deep-link — the robust fallback for per-county portals
    whose URL schemes are not stable enough to hard-code."""
    return "https://www.google.com/search?q=" + _url.quote_plus(query)


# --------------------------------------------------------------------------
# County Appraisal District (CAD) — curated high-confidence direct URLs for the
# counties with the most ERCOT renewable/storage activity; everything else
# falls back to a reliable search. (A wrong specific URL is worse than a search.)
# --------------------------------------------------------------------------
_CAD_URLS = {
    "Brazoria": "https://www.brazoriacad.org/",
    "Pecos": "https://www.pecoscad.org/",
    "Wharton": "https://www.whartoncad.net/",
    "Milam": "https://www.milamad.org/",
    "Harris": "https://hcad.org/",
    "Navarro": "https://www.navarrocad.com/",
    "Zapata": "https://www.zapatacad.org/",
    "Cameron": "https://www.cameroncad.org/",
    "Crane": "https://www.cranecad.org/",
    "Reeves": "https://www.reevescad.org/",
    "Ward": "https://www.wardcad.org/",
    "Upton": "https://www.uptoncad.org/",
    "Andrews": "https://www.andrewscad.org/",
    "Ector": "https://www.ectorcad.org/",
    "Nolan": "https://www.nolancad.org/",
    "Taylor": "https://www.taylor-cad.org/",
    "Haskell": "https://www.haskellcad.com/",
    "Throckmorton": "https://www.throckmortoncad.org/",
    "Bosque": "https://www.bosquecad.com/",
    "Falls": "https://www.fallscad.net/",
    "Bell": "https://www.bellcad.org/",
    "Williamson": "https://www.wcad.org/",
    "Travis": "https://traviscad.org/",
    "Webb": "https://webbcad.org/",
    "Starr": "https://www.starrcad.org/",
    "Hidalgo": "https://www.hidalgoad.org/",
    "Castro": "https://www.castrocad.org/",
    "Crockett": "https://www.crockettcad.org/",
    "Schleicher": "https://www.schleichercad.org/",
}


def cad_link(county: str) -> dict:
    """Appraisal-district entry point for a county (direct if known, else search)."""
    c = (county or "").strip().title()
    url = _CAD_URLS.get(c)
    if url:
        return {"label": f"{c} County Appraisal District (CAD)", "url": url,
                "note": "parcel ownership, valuations, Ch. 312 abatements", "kind": "direct"}
    return {"label": f"{c or 'County'} Appraisal District (CAD)",
            "url": _g(f"{c} county Texas appraisal district property search"),
            "note": "parcel ownership & valuations (search — CAD URL not curated)",
            "kind": "search"}


def county_clerk_link(county: str) -> dict:
    c = (county or "").strip().title()
    return {"label": f"{c} County Clerk — official public records",
            "url": _g(f"{c} county Texas county clerk official public records search"),
            "note": "recorded leases, easements, road-use & decommissioning agreements, liens",
            "kind": "search"}


def commissioners_court_link(county: str) -> dict:
    c = (county or "").strip().title()
    return {"label": f"{c} County Commissioners Court — agendas/minutes",
            "url": _g(f"{c} county Texas commissioners court agenda minutes tax abatement road use"),
            "note": "Ch. 312 county tax abatements, road-use agreements, permits",
            "kind": "search"}


# --------------------------------------------------------------------------
# Per-project filing link set
# --------------------------------------------------------------------------
def filing_links(project_name: str, county: str | None = None,
                 entity: str | None = None, fuel: str | None = None,
                 technology: str | None = None, *, eia_plant_id=None,
                 ifyi_url: str | None = None, queue_id: str | None = None) -> list[dict]:
    """Authoritative due-diligence deep-links for one project.

    Each item: {label, url, note, kind('direct'|'search')}. Leads with the ERCOT
    and EIA project records, then state incentive/regulatory, entity, county, and
    tech-specific sources. Tailored slightly by fuel/technology (e.g. FAA + avian
    for wind; fire code / SARA Tier II for storage).
    """
    name = (project_name or "").strip()
    tech = f"{fuel or ''} {technology or ''}".lower()
    is_wind = "wind" in tech
    is_solar = "solar" in tech or "photovoltaic" in tech
    is_storage = "stor" in tech or "battery" in tech

    links: list[dict] = []

    # Project records — ERCOT (queue) and EIA (plant)
    links += ercot_links(queue_id=queue_id, ifyi_url=ifyi_url)
    links.append(eia_link(eia_plant_id, name))

    # State incentive / regulatory (always relevant)
    links.append({"label": "PUC Interchange — dockets & CCN filings",
                  "url": PUC_INTERCHANGE,
                  "note": f"search project/entity name: “{name}”" + (f" / “{entity}”" if entity else ""),
                  "kind": "search"})
    links.append({"label": "Texas Comptroller — Ch. 313 agreement documents",
                  "url": COMPTROLLER_313,
                  "note": "expired 2022; legacy school-tax limitation agreements for older projects",
                  "kind": "direct"})
    links.append({"label": "Texas Comptroller — Ch. 403 JETI (current incentive)",
                  "url": COMPTROLLER_JETI,
                  "note": "successor to Ch. 313 for post-2023 projects", "kind": "direct"})

    # Entity diligence
    links.append({"label": "Comptroller — taxable entity / franchise search",
                  "url": COMPTROLLER_ENTITY,
                  "note": f"verify developer entity" + (f": “{entity}”" if entity else ""),
                  "kind": "search"})
    links.append({"label": "SOSDirect — entity & registered agent",
                  "url": SOS_DIRECT, "note": "ownership chain, registered agent", "kind": "search"})

    # County-level
    if county:
        links.append(cad_link(county))
        links.append(county_clerk_link(county))
        links.append(commissioners_court_link(county))

    # Tech-specific
    if is_wind:
        links.append({"label": "FAA OE/AAA — obstruction evaluation",
                      "url": FAA_OEAAA,
                      "note": "every turbine needs a Determination of No Hazard — search by name/lat-lon",
                      "kind": "search"})
        links.append({"label": "USFWS IPaC — protected-species screen",
                      "url": USFWS_IPAC, "note": "avian/bat take risk, eagle permits", "kind": "search"})
    if is_solar:
        links.append({"label": "USFWS IPaC — protected-species screen",
                      "url": USFWS_IPAC, "note": "habitat / wetlands (Section 7/404)", "kind": "search"})
    if is_storage:
        links.append({"label": "TCEQ records — air & stormwater",
                      "url": TCEQ_RECORDS,
                      "note": "SARA Tier II / fire-code & emissions for BESS", "kind": "search"})
    else:
        links.append({"label": "TCEQ records online",
                      "url": TCEQ_RECORDS,
                      "note": "air (gen sets), construction stormwater, water rights", "kind": "search"})

    return links


# --------------------------------------------------------------------------
# Due-diligence checklist (tech-aware)
# --------------------------------------------------------------------------
_BASE_CHECKLIST = [
    ("Interconnection", "Queue status & study progress (FIS/IA executed?), POI substation, GIM milestones"),
    ("Site control", "Recorded leases/easements at County Clerk; acreage vs. nameplate plausibility"),
    ("Land / parcels", "Owner-of-record & valuations at the CAD; overlap with project footprint"),
    ("Tax incentives", "Ch. 313 (legacy) or Ch. 403 JETI agreement; county Ch. 312 abatement"),
    ("Developer", "Entity standing (Comptroller/SOS), registered agent, ownership chain"),
    ("Environmental", "USFWS IPaC species screen; wetlands/waters (USACE 404); cultural/THC"),
    ("Permitting", "TCEQ air/stormwater; county road-use & permit agreements"),
]
_WIND_EXTRA = [
    ("FAA", "OE/AAA Determination of No Hazard for every turbine; lighting plan"),
    ("Wildlife", "Avian/bat use study; eagle take permit risk; setbacks"),
    ("Noise/shadow", "Sound & shadow-flicker study vs. nearby residences/setback ordinances"),
]
_SOLAR_EXTRA = [
    ("Glare", "FAA glare analysis if near an airport/flight path"),
    ("Decommissioning", "Bond/financial assurance & end-of-life plan in the lease/county agreement"),
]
_STORAGE_EXTRA = [
    ("Fire/safety", "NFPA 855 compliance, local fire-code review, emergency response plan"),
    ("Hazmat", "SARA Tier II reporting; thermal-runaway mitigation"),
]


def dd_checklist(fuel: str | None = None, technology: str | None = None) -> list[dict]:
    tech = f"{fuel or ''} {technology or ''}".lower()
    items = list(_BASE_CHECKLIST)
    if "wind" in tech:
        items += _WIND_EXTRA
    if "solar" in tech or "photovoltaic" in tech:
        items += _SOLAR_EXTRA
    if "stor" in tech or "battery" in tech:
        items += _STORAGE_EXTRA
    return [{"area": a, "item": d} for a, d in items]
