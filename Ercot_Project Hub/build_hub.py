#!/usr/bin/env python3
"""
ERCOT Project Hub — data-quality index generator.

Reads the shared asset registry plus the live data lake and produces, for every
project loaded into the suite:

  * what we know about it (metadata),
  * how complete and trustworthy that data is (a data-quality grade), and
  * which downstream tools actually consume it (coverage).

Outputs (all written next to this script):
  README.md            human-readable index + ranked data-quality table
  data_quality.csv     one row per project (spreadsheet-friendly)
  data_quality.json    same data, machine-readable
  projects/<slug>.md   one detail card per project

Pure standard library — no venv required. Re-run any time the registry or data
lake changes:  python3 "Ercot_Project Hub/build_hub.py"
"""
from __future__ import annotations

import csv
import json
import os
import re
import glob
from collections import Counter

# ---------------------------------------------------------------------------
# Paths — resolve the suite root from this file's location so the script is
# runnable from anywhere.
# ---------------------------------------------------------------------------
HUB_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.dirname(HUB_DIR)
HUB = os.path.join(SUITE_ROOT, "Ercot_Data_Hub")

REGISTRY = os.path.join(HUB, "ercot_core", "registry", "ercot_assets.json")
FLEET = os.path.join(HUB_DIR, "ercot_fleet.json")  # full EIA-860 roster (optional)
CROSSWALK = os.path.join(HUB, "data", "plant_sced", "eia_sced_crosswalk.csv")
PLANT_SCED_DIR = os.path.join(HUB, "data", "plant_sced", "plants")
PLANT_VALUE_DIR = os.path.join(HUB, "data", "plant_value")

PROJECTS_OUT = os.path.join(HUB_DIR, "projects")

# Projects that have a dedicated single-asset settlement portal in the suite.
# keyed by registry resource_name -> portal metadata.
PORTALS = {
    "MRKM_SLR_PV1": {
        "name": "Markum Solar",
        "folder": "ERCOT_Markum",
        "structure": "VPPA / CfD",
        "strike": 35.0,
        "eia_plant_id": 67580,
        "note": "Customer-facing portal; SCED matches EIA-923 within ±1%. "
                "Registry county may be stale (Throckmorton) vs. portal's "
                "corrected Bosque County.",
    },
    "AZURE_SKY_WIND_AGG": {
        "name": "Azure Sky Wind",
        "folder": "ERCOT_Azure_Sky",
        "structure": "VPPA / CfD",
        "strike": 17.34,
        "eia_plant_id": None,
        "note": "350 MW; settles at HB_NORTH against VORTEX SCED.",
    },
}

# Expected metadata fields by technology (drives the completeness score).
COMMON_FIELDS = ["resource_name", "project_name", "tech", "capacity_mw",
                 "hub", "lat", "lon", "county"]
WIND_FIELDS = ["hub_height_m", "turbine_model", "turbine_manuf", "rotor_diameter_m"]
SOLAR_FIELDS = ["tracking_type", "dc_ac_ratio"]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "project"


# ---------------------------------------------------------------------------
# Load enrichment sources from the data lake. Each is optional — if a file or
# directory is missing the signal degrades gracefully to "unknown / absent".
# ---------------------------------------------------------------------------
def load_crosswalk() -> dict:
    """resource_name -> (eia_plant_id, eia_plant_name)."""
    out = {}
    if not os.path.exists(CROSSWALK):
        return out
    with open(CROSSWALK, newline="") as fh:
        for row in csv.DictReader(fh):
            for rn in (row.get("resource_names") or "").split(";"):
                rn = rn.strip()
                if rn:
                    out[rn] = (row.get("eia_plant_id"), row.get("eia_plant_name"))
    return out


def load_sced_stems() -> set:
    """Set of SCED resource stems with cached actuals (year suffix stripped)."""
    stems = set()
    for p in glob.glob(os.path.join(PLANT_SCED_DIR, "*.parquet")):
        base = os.path.basename(p)
        stems.add(re.sub(r"_\d{4}\.parquet$", "", base))
    return stems


def load_plant_value() -> tuple[set, set]:
    """(resources with typical-year gen profiles, resources with value parquets).

    Solar gen profiles are cached as ``gen_<res>_<year>_…`` and wind as
    ``windgen_<res>_<year>_…`` — match both prefixes.
    """
    gen, val = set(), set()
    for p in glob.glob(os.path.join(PLANT_VALUE_DIR, "*.parquet")):
        b = os.path.basename(p)
        mg = re.match(r"(?:wind)?gen_(.+?)_(?:tmy|\d{4})_", b)
        if mg:
            gen.add(mg.group(1))
        mv = re.match(r"value_(.+?)_HB_", b)
        if mv:
            val.add(mv.group(1))
    return gen, val


def matches(resource: str, sced_units, stems: set) -> bool:
    """True if a registry resource (or any of its sced_units) has cached actuals."""
    candidates = [resource] + list(sced_units or [])
    for c in candidates:
        for s in stems:
            if s == c or s.startswith(c + "_") or c.startswith(s):
                return True
    return False


def in_value_set(resource: str, sced_units, vset: set) -> bool:
    candidates = [resource] + list(sced_units or [])
    for c in candidates:
        for s in vset:
            if s == c or s.startswith(c) or c.startswith(s):
                return True
    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def grade_letter(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def assess(name, rec, crosswalk, sced_stems, gen_set, val_set):
    tech = rec.get("tech", "")
    expected = list(COMMON_FIELDS)
    if tech == "Wind":
        expected += WIND_FIELDS
    elif tech == "Solar":
        expected += SOLAR_FIELDS

    present = [f for f in expected if rec.get(f) not in (None, "", [])]
    missing = [f for f in expected if f not in present]
    completeness = round(100 * len(present) / len(expected), 1)

    resource = rec.get("resource_name", "")
    sced_units = rec.get("sced_units", [])
    status = str(rec.get("status") or "operating").lower()
    queue_id = rec.get("queue_id")

    # --- Source verification ---
    eia_direct = rec.get("eia_plant_id")
    has_crosswalk = (bool(eia_direct) or resource in crosswalk
                     or any(u in crosswalk for u in sced_units))
    has_sced_actuals = matches(resource, sced_units, sced_stems)
    has_loc_conf = bool(rec.get("location_confidence"))
    queue_verified = bool(queue_id)
    if status == "planned":
        # A pre-operational project cannot have SCED actuals or an EIA-923
        # crosswalk (no generation history exists yet), so demanding them would
        # penalize it for the impossible. Score it on the verification available
        # to a planned asset: confirmation in the ERCOT interconnection queue
        # (authoritative identity) plus a sited location. Caps at 80 — full 100
        # stays reserved for operating plants with realized SCED + EIA match.
        ver_score = round(60 * queue_verified + 20 * has_loc_conf, 1)
    else:
        ver_score = round(
            40 * has_crosswalk + 40 * has_sced_actuals + 20 * has_loc_conf, 1
        )

    # --- Calibration / model readiness ---
    has_gen_profile = in_value_set(resource, sced_units, gen_set)
    has_value = in_value_set(resource, sced_units, val_set)
    cal_score = round(50 * has_gen_profile + 50 * has_value, 1)

    # --- Coverage (which tools consume this project) ---
    coverage = ["Registry"]
    if rec.get("lat") is not None and rec.get("lon") is not None:
        coverage.append("Wind Forecast" if tech == "Wind" else "Solar Forecast")
    if has_sced_actuals:
        coverage.append("SCED ETL")
    if has_value:
        coverage.append("Plant Value")
    portal = PORTALS.get(resource)
    if portal:
        coverage.append(f"Portal ({portal['folder']})")

    overall = round((completeness + ver_score + cal_score) / 3, 1)

    return {
        "project": name,
        "resource_name": resource,
        "tech": tech,
        "source": rec.get("source", "curated"),
        "status": status,
        "queue_id": queue_id,
        "queue_verified": queue_verified,
        "capacity_mw": rec.get("capacity_mw"),
        "hub": rec.get("hub"),
        "county": rec.get("county"),
        "completeness_pct": completeness,
        "missing_fields": missing,
        "eia_crosswalk": has_crosswalk,
        "eia_plant_id": (eia_direct or crosswalk.get(resource, (None, None))[0]
                         or (portal or {}).get("eia_plant_id")),
        "sced_actuals": has_sced_actuals,
        "location_confidence": rec.get("location_confidence"),
        "verification_score": ver_score,
        "gen_profile": has_gen_profile,
        "plant_value": has_value,
        "calibration_score": cal_score,
        "coverage": coverage,
        "portal": portal["name"] if portal else None,
        "overall_score": overall,
        "grade": grade_letter(overall),
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def write_csv(rows, path):
    cols = ["project", "resource_name", "tech", "status", "queue_id",
            "capacity_mw", "hub", "county",
            "grade", "overall_score", "completeness_pct", "verification_score",
            "calibration_score", "eia_crosswalk", "eia_plant_id", "sced_actuals",
            "gen_profile", "plant_value", "location_confidence", "portal",
            "missing_fields", "coverage"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([
                r["project"], r["resource_name"], r["tech"], r["status"],
                r.get("queue_id"), r["capacity_mw"],
                r["hub"], r["county"], r["grade"], r["overall_score"],
                r["completeness_pct"], r["verification_score"],
                r["calibration_score"], r["eia_crosswalk"], r["eia_plant_id"],
                r["sced_actuals"], r["gen_profile"], r["plant_value"],
                r["location_confidence"], r["portal"],
                "; ".join(r["missing_fields"]), "; ".join(r["coverage"]),
            ])


def yn(b):
    return "✅" if b else "—"


def write_project_card(r, path):
    planned = r.get("status") == "planned"
    status_label = ("🟡 Planned" + (f" (COD {r['cod']})" if r.get("cod") else "")
                    if planned else "🟢 Operating")
    if planned:
        verify_signal = (f"queue-verified {yn(r['queue_verified'])} "
                         f"(ERCOT {r['queue_id'] or '—'}) · location confidence: "
                         f"{r['location_confidence'] or '—'} · SCED/EIA not applicable pre-COD")
    else:
        verify_signal = (f"EIA crosswalk {yn(r['eia_crosswalk'])} · SCED actuals "
                         f"{yn(r['sced_actuals'])} · location confidence: "
                         f"{r['location_confidence'] or '—'}")
    lines = [
        f"# {r['project']}",
        "",
        f"**Grade: {r['grade']}**  ({r['overall_score']}/100 overall) · {status_label}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Resource name | `{r['resource_name']}` |",
        f"| Technology | {r['tech']} |",
        f"| Capacity | {r['capacity_mw']} MW |",
        f"| Hub | {r['hub']} |",
        f"| County | {r['county']} |",
        f"| EIA plant ID | {r['eia_plant_id'] or '—'} |",
        f"| ERCOT queue ID | {r.get('queue_id') or '—'} |",
        f"| Portal | {r['portal'] or '—'} |",
        "",
        "## Data quality",
        "",
        "| Dimension | Score | Signal |",
        "| --- | --- | --- |",
        f"| Field completeness | {r['completeness_pct']}% | "
        f"{'all expected fields present' if not r['missing_fields'] else 'missing: ' + ', '.join('`'+m+'`' for m in r['missing_fields'])} |",
        f"| Source verification | {r['verification_score']}/100 | {verify_signal} |",
        f"| Calibration / model | {r['calibration_score']}/100 | "
        f"typical-year profile {yn(r['gen_profile'])} · plant value {yn(r['plant_value'])} |",
        "",
        "## Coverage",
        "",
        "Consumed by: " + ", ".join(r["coverage"]),
        "",
        "---",
        "*Auto-generated by `build_hub.py`. Edits here are overwritten on regen — "
        "fix data at the source (registry / data lake) instead.*",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def write_readme(rows, path):
    n = len(rows)
    grades = Counter(r["grade"] for r in rows)
    avg = round(sum(r["overall_score"] for r in rows) / n, 1) if n else 0
    techs = Counter(r["tech"] for r in rows)
    n_crosswalk = sum(r["eia_crosswalk"] for r in rows)
    n_sced = sum(r["sced_actuals"] for r in rows)
    n_value = sum(r["plant_value"] for r in rows)
    n_portal = sum(bool(r["portal"]) for r in rows)

    ranked = sorted(rows, key=lambda r: (-r["overall_score"], r["project"]))

    out = [
        "# ERCOT Project Hub",
        "",
        "Single source of truth for **what projects are loaded into the suite and "
        "how good the data behind each one is.** Everything below is auto-generated "
        "from the shared registry and the live data lake.",
        "",
        f"> Regenerate: `python3 \"Ercot_Project Hub/build_hub.py\"` &nbsp;|&nbsp; "
        f"Machine-readable: [`data_quality.csv`](data_quality.csv) · "
        f"[`data_quality.json`](data_quality.json)",
        "",
        "## Summary",
        "",
        f"- **{n} projects** loaded ({', '.join(f'{c} {t}' for t, c in techs.items())})",
        f"- **Average data-quality score: {avg}/100**",
        f"- Grade distribution: " + ", ".join(f"{g}: {grades.get(g,0)}" for g in "ABCDF"),
        f"- **{n_crosswalk}/{n}** have an EIA-923 crosswalk · "
        f"**{n_sced}/{n}** have cached SCED actuals · "
        f"**{n_value}/{n}** have a computed plant value · "
        f"**{n_portal}** have a dedicated portal",
        "",
        "## Data-quality dimensions",
        "",
        "| Dimension | What it measures | Source |",
        "| --- | --- | --- |",
        "| **Completeness** | Share of expected metadata fields present (tech-aware: "
        "wind needs turbine specs, solar needs tracking/ratio) | `ercot_assets.json` |",
        "| **Verification** | Is the project trustworthy: EIA-923 crosswalk match, "
        "cached SCED actuals, stated location confidence | crosswalk CSV + `plant_sced/` |",
        "| **Calibration** | Model-readiness: typical-year generation profile + "
        "computed plant value | `plant_value/` |",
        "| **Coverage** | Which downstream tools actually consume the project | derived |",
        "",
        "## Rollup by technology",
        "",
        "| Tech | Projects | Capacity (MW) | Avg score | Avg complete | Crosswalk | SCED | Valued |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for g in rollup(rows, "tech"):
        out.append(
            f"| {g['tech']} | {g['projects']} | {g['capacity_mw']:g} | "
            f"{g['avg_score']:g} | {g['avg_completeness']:g}% | "
            f"{g['with_crosswalk']}/{g['projects']} | {g['with_sced']}/{g['projects']} | "
            f"{g['with_value']}/{g['projects']} |"
        )
    out += [
        "",
        "## Rollup by hub",
        "",
        "| Hub | Projects | Capacity (MW) | Avg score | Avg complete | Crosswalk | SCED | Valued |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for g in rollup(rows, "hub"):
        out.append(
            f"| {g['hub']} | {g['projects']} | {g['capacity_mw']:g} | "
            f"{g['avg_score']:g} | {g['avg_completeness']:g}% | "
            f"{g['with_crosswalk']}/{g['projects']} | {g['with_sced']}/{g['projects']} | "
            f"{g['with_value']}/{g['projects']} |"
        )
    out += [
        "",
        "## All projects (ranked by data quality)",
        "",
        "| Project | Tech | MW | Hub | Grade | Overall | Complete | Verify | Calib | Portal |",
        "| --- | --- | ---: | --- | :---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in ranked:
        slug = r.get("_slug") or slugify(r["project"])
        out.append(
            f"| [{r['project']}](projects/{slug}.md) | {r['tech']} | "
            f"{r['capacity_mw']:g} | {r['hub']} | **{r['grade']}** | "
            f"{r['overall_score']:g} | {r['completeness_pct']:g}% | "
            f"{r['verification_score']:g} | {r['calibration_score']:g} | "
            f"{r['portal'] or '—'} |"
        )
    out += [
        "",
        "## How to improve a project's grade",
        "",
        "- **Low completeness** → fill the `missing_fields` listed on the project "
        "card, in `ercot_assets.json`.",
        "- **Low verification** → add the project to `eia_sced_crosswalk.csv` and "
        "pull its SCED actuals into `plant_sced/plants/`.",
        "- **Low calibration** → run the plant-value pipeline to produce the "
        "typical-year gen profile and value parquet.",
        "",
        "*Generated by `build_hub.py`.*",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


# ---------------------------------------------------------------------------
def collect_rows():
    """Load every source and return the assessed rows. Importable so the
    Streamlit page renders the same data the files are built from."""
    if not os.path.exists(REGISTRY):
        raise FileNotFoundError(f"Registry not found: {REGISTRY}")
    registry = json.load(open(REGISTRY))
    crosswalk = load_crosswalk()
    sced_stems = load_sced_stems()
    gen_set, val_set = load_plant_value()

    # Curated registry first — these are authoritative and win on conflicts.
    rows = [assess(name, rec, crosswalk, sced_stems, gen_set, val_set)
            for name, rec in registry.items()]
    curated_res = {r["resource_name"] for r in rows}
    curated_eia = {r["eia_plant_id"] for r in rows if r["eia_plant_id"]}

    # Merge the full EIA-860 fleet roster for coverage, skipping any plant the
    # curated registry already represents (by EIA id or SCED resource).
    if os.path.exists(FLEET):
        for f in json.load(open(FLEET)):
            if f.get("eia_plant_id") in curated_eia:
                continue
            if f.get("resource_name") in curated_res:
                continue
            rows.append(assess(f.get("project_name", f.get("resource_name")), f,
                               crosswalk, sced_stems, gen_set, val_set))
    return rows


def rollup(rows, key):
    """Aggregate rows by a key ('hub' or 'tech') -> list of summary dicts."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key) or "—", []).append(r)
    out = []
    for g, items in sorted(groups.items()):
        n = len(items)
        out.append({
            key: g,
            "projects": n,
            "capacity_mw": round(sum(i["capacity_mw"] or 0 for i in items), 1),
            "avg_score": round(sum(i["overall_score"] for i in items) / n, 1),
            "avg_completeness": round(sum(i["completeness_pct"] for i in items) / n, 1),
            "with_crosswalk": sum(i["eia_crosswalk"] for i in items),
            "with_sced": sum(i["sced_actuals"] for i in items),
            "with_value": sum(i["plant_value"] for i in items),
        })
    return out


def main():
    rows = collect_rows()

    os.makedirs(PROJECTS_OUT, exist_ok=True)
    current = set()
    used = {}
    for r in rows:
        slug = slugify(r["project"])
        if slug in used:                       # disambiguate name collisions
            slug = f"{slug}-{r['eia_plant_id'] or used[slug]}"
        used[slug] = used.get(slug, 0) + 1
        r["_slug"] = slug
        current.add(slug + ".md")
        write_project_card(r, os.path.join(PROJECTS_OUT, slug + ".md"))
    # Prune cards for projects no longer in the registry (e.g. de-duplicated).
    for f in os.listdir(PROJECTS_OUT):
        if f.endswith(".md") and f not in current:
            os.remove(os.path.join(PROJECTS_OUT, f))

    write_csv(rows, os.path.join(HUB_DIR, "data_quality.csv"))
    with open(os.path.join(HUB_DIR, "data_quality.json"), "w") as fh:
        json.dump(rows, fh, indent=2)
    write_readme(rows, os.path.join(HUB_DIR, "README.md"))

    avg = round(sum(r["overall_score"] for r in rows) / len(rows), 1)
    print(f"Built Project Hub: {len(rows)} projects, avg score {avg}/100")
    print(f"  README.md, data_quality.csv, data_quality.json")
    print(f"  projects/ ({len(rows)} cards)")


if __name__ == "__main__":
    main()
