#!/usr/bin/env python3
"""ERCOT interconnection-queue search, analysis & due-diligence CLI.

A thin CLI over ``ercot_core.queue_search`` (the engine) and
``ercot_core.tx_filings`` (county/state filing links). It merges ERCOT's GIS
queue with the interconnection.fyi superset and lets you search, roll up, and
build a full due-diligence dossier for any project.

    # search ------------------------------------------------------------------
    python ercot_queue.py search "azure sky"
    python ercot_queue.py search --county Pecos --fuel Solar --status Active
    python ercot_queue.py search --min-mw 200 --technology "Battery" --limit 20
    python ercot_queue.py search "wind" --county Haskell --json

    # analyze -----------------------------------------------------------------
    python ercot_queue.py stats --by county --fuel Solar      # MW by county, solar only
    python ercot_queue.py stats --by fuel --status Active
    python ercot_queue.py stats --by entity --county Pecos

    # one project: links + info + DD ------------------------------------------
    python ercot_queue.py dossier 21INR0477
    python ercot_queue.py dossier "Azure Sky Solar"
    python ercot_queue.py links "Markham Solar" --county Bosque   # just the filing links

Data is cache-first/offline. Add --fetch to allow the GIS source to download if
its cached parquet is missing. Build/refresh the interconnection.fyi superset
with:  python ../Ercot_Data_Hub/ercot_core/ifyi.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make ercot_core importable (sibling Ercot_Data_Hub), mirroring build_fleet.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
_HUB = os.path.join(os.path.dirname(_HERE), "Ercot_Data_Hub")
sys.path.insert(0, _HUB)

from ercot_core import queue_search, tx_filings  # noqa: E402


def _fmt_mw(v):
    try:
        return f"{float(v):,.1f}"
    except (TypeError, ValueError):
        return "?"


def _print_table(df, cols=None):
    if df.empty:
        print("  (no matches)")
        return
    cols = cols or ["queue_id", "project_name", "fuel", "technology",
                    "capacity_mw", "county", "status", "in_gis"]
    cols = [c for c in cols if c in df.columns]
    widths = {c: max(len(c), *(len(str(v)[:34]) for v in df[c].fillna("")))
              for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for _, r in df.iterrows():
        print("  " + "  ".join(str(r[c] if r[c] is not None else "")[:34].ljust(widths[c])
                               for c in cols))


# --------------------------------------------------------------------------
def cmd_search(a):
    df = queue_search.search(
        a.text, county=a.county, fuel=a.fuel, technology=a.technology,
        status=a.status, entity=a.entity, min_mw=a.min_mw, max_mw=a.max_mw,
        in_gis=(True if a.in_gis else None), sort=a.sort, desc=not a.asc,
        limit=a.limit, allow_fetch=a.fetch)
    if a.json:
        print(df.to_json(orient="records", indent=1))
        return 0
    total = _fmt_mw(df["capacity_mw"].astype(float).sum()) if not df.empty else "0"
    print(f"\n{len(df)} project(s), {total} MW total\n")
    _print_table(df)
    if not df.empty:
        print(f"\n  → details:  python ercot_queue.py dossier {df.iloc[0]['queue_id']}")
    return 0


def cmd_stats(a):
    df = queue_search.stats(by=a.by, status=a.status, county=a.county,
                            fuel=a.fuel, technology=a.technology, entity=a.entity,
                            in_gis=(True if a.in_gis else None), allow_fetch=a.fetch)
    if a.json:
        print(df.to_json(orient="records", indent=1))
        return 0
    print(f"\nProjects & capacity by {a.by}"
          + (f" — status~{a.status}" if a.status else "")
          + (f", county~{a.county}" if a.county else "") + "\n")
    if df.empty:
        print("  (no data)")
        return 0
    w = max(len(a.by), *(len(str(v)) for v in df[a.by]))
    print(f"  {a.by.ljust(w)}  projects   total_mw   median_mw")
    print(f"  {'-'*w}  --------   --------   ---------")
    for _, r in df.iterrows():
        print(f"  {str(r[a.by]).ljust(w)}  {int(r['projects']):>8}  "
              f"{_fmt_mw(r['total_mw']):>9}  {_fmt_mw(r['median_mw']):>9}")
    print(f"\n  {int(df['projects'].sum())} projects, {_fmt_mw(df['total_mw'].sum())} MW total")
    return 0


def _print_links(links):
    for L in links:
        tag = "↗" if L["kind"] == "direct" else "🔎"
        print(f"  {tag} {L['label']}")
        print(f"      {L['url']}")
        if L.get("note"):
            print(f"      ({L['note']})")


def cmd_links(a):
    links = tx_filings.filing_links(a.name, county=a.county, entity=a.entity,
                                    fuel=a.fuel, technology=a.technology)
    if a.json:
        print(json.dumps(links, indent=1))
        return 0
    print(f"\nFiling / due-diligence links — {a.name}"
          + (f" ({a.county} Co)" if a.county else "") + "\n")
    _print_links(links)
    return 0


def cmd_dossier(a):
    d = queue_search.dossier(a.query, allow_fetch=a.fetch)
    if a.json:
        print(json.dumps(d, indent=1, default=str))
        return 0

    rec = d.get("record")
    print("\n" + "=" * 72)
    if not d["found"]:
        print(f"  No queue record found for {a.query!r}.")
        print("  (Showing generic filing links + checklist below.)")
    else:
        print(f"  {rec.get('project_name','?')}   [{rec.get('queue_id','?')}]")
        print("=" * 72)
        _tech = (rec.get('technology') or rec.get('gen_type')
                 or (f"{d.get('inferred_tech')} (inferred)" if d.get('inferred_tech') else '?'))
        print(f"  Fuel/Tech   : {rec.get('fuel') or d.get('inferred_tech') or '?'} / {_tech}")
        print(f"  Capacity    : {_fmt_mw(rec.get('capacity_mw'))} MW")
        print(f"  Status      : {rec.get('status','?')}"
              + (f"  (GIS: {rec.get('gis_status')})" if rec.get('gis_status') else "")
              + ("" if rec.get("in_gis") else "   ⚠ not in current GIS queue"))
        print(f"  County      : {rec.get('county','?')}")
        print(f"  Entity      : {rec.get('entity','?')}")
        print(f"  POI         : {rec.get('poi','?')}")
        print(f"  Queue date  : {rec.get('queue_date') or '?'}    "
              f"Proposed COD: {rec.get('proposed_completion') or '?'}    "
              f"Actual COD: {rec.get('actual_completion') or '?'}")
        if rec.get("url"):
            print(f"  Project page: {rec['url']}")

    reg = d.get("registry_match")
    if reg:
        print(f"\n  ✓ Curated-registry match: {reg.get('project_name')} "
              f"(resource {reg.get('resource_name')}, {reg.get('tech')}, "
              f"{_fmt_mw(reg.get('capacity_mw'))} MW) — analyzable in the Data Hub.")

    cw = d.get("crosswalk") or {}
    cands = cw.get("candidates") or []
    if cands:
        print(f"\n  Resource-node candidate(s) (name used: {cw.get('name_used')!r}):")
        for c in cands:
            av = c.get("availability", {})
            print(f"    ● {c['resource_node']}  (match: {c['match']})")
            print(f"        units: {', '.join(c['units'])}")
            print(f"        cached: price {av.get('price_rows_cached',0):,} rows · "
                  f"gen {av.get('gen_rows_cached',0):,} rows · "
                  f"SCED {av.get('plant_sced_files',0)} files · "
                  f"registry: {', '.join(av.get('units_in_registry') or []) or 'no'}")
    elif cw.get("queue_note"):
        print(f"\n  note: {cw['queue_note']}")

    print("\n  ── Filing / due-diligence links ──")
    _print_links(d["filing_links"])

    print("\n  ── Due-diligence checklist ──")
    for c in d["dd_checklist"]:
        print(f"    □ {c['area']:<14} {c['item']}")
    print()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fetch", action="store_true",
                        help="allow the GIS queue to download if not cached")
    common.add_argument("--json", action="store_true", help="emit JSON")

    s = sub.add_parser("search", parents=[common], help="search the queue")
    s.add_argument("text", nargs="?", help="free text (name/entity/county/queue-id/POI)")
    s.add_argument("--county"); s.add_argument("--fuel"); s.add_argument("--technology")
    s.add_argument("--status"); s.add_argument("--entity")
    s.add_argument("--min-mw", type=float, dest="min_mw")
    s.add_argument("--max-mw", type=float, dest="max_mw")
    s.add_argument("--in-gis", action="store_true", help="only projects in the current GIS queue")
    s.add_argument("--sort", default="capacity_mw")
    s.add_argument("--asc", action="store_true", help="ascending sort")
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", parents=[common], help="rollup analytics")
    st.add_argument("--by", default="fuel",
                    choices=["fuel", "technology", "status", "gis_status", "county", "entity"])
    st.add_argument("--status"); st.add_argument("--county")
    st.add_argument("--fuel"); st.add_argument("--technology"); st.add_argument("--entity")
    st.add_argument("--in-gis", action="store_true")
    st.set_defaults(func=cmd_stats)

    do = sub.add_parser("dossier", parents=[common],
                        help="full DD package for one project (queue id or name)")
    do.add_argument("query")
    do.set_defaults(func=cmd_dossier)

    ln = sub.add_parser("links", parents=[common], help="just the filing links for a project")
    ln.add_argument("name")
    ln.add_argument("--county"); ln.add_argument("--entity")
    ln.add_argument("--fuel"); ln.add_argument("--technology")
    ln.set_defaults(func=cmd_links)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
