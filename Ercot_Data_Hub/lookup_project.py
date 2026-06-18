#!/usr/bin/env python3
"""Find the ERCOT resource node for an interconnection project.

    python lookup_project.py "Azure Sky"          # by name
    python lookup_project.py 21INR0477            # by queue id (if still listed)
    python lookup_project.py ercot-21inr0477      # interconnection.fyi id form
    python lookup_project.py "Inertia Solar" --no-fetch   # skip queue download

Bridges: the interconnection queue (queue id -> name, for active projects) and
the resource-node catalog + plant-name crosswalk (name -> node, for anything in
the SCED data). Build the catalog first if you haven't:
    python datasets/system_gen_by_fuel/resource_catalog.py --build
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ercot_core import project_lookup  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="project name or queue id")
    ap.add_argument("--no-fetch", action="store_true",
                    help="don't download the queue; use cached catalog/crosswalk only")
    ap.add_argument("--save", action="store_true",
                    help="save the top candidate's units -> resolved name into the "
                         "crosswalk (name_source 'ifyi')")
    args = ap.parse_args(argv)

    res = project_lookup.lookup(args.query, allow_fetch=not args.no_fetch)

    qm = res.get("queue_matches", [])
    if qm:
        print(f"\nQueue match(es) for {args.query!r}:")
        for r in qm[:5]:
            print(f"  {r.get('Queue ID','?')}  {r.get('Project Name','?')}  "
                  f"[{r.get('Fuel','?')}/{r.get('Technology','?')}, "
                  f"{r.get('Capacity (MW)','?')} MW, {r.get('County','?')} Co, {r.get('Status','?')}]")
            print(f"      POI: {r.get('Interconnection Location','?')}")
    if res.get("ifyi"):
        r = res["ifyi"]
        print(f"\ninterconnection.fyi: {r.get('name')}  "
              f"[{r.get('fuel')}, {r.get('capacity_mw')} MW, {r.get('county')} Co, {r.get('status')}]")
        print(f"      POI: {r.get('poi')}")
        print(f"      {r.get('url')}")
    if res.get("queue_note"):
        print(f"\n  note: {res['queue_note']}")

    print(f"\nResource-node candidates (name used: {res['name_used']!r}):")
    cands = res.get("candidates", [])
    if not cands:
        print("  none found. Try a different name token, or build the catalog:")
        print("    python datasets/system_gen_by_fuel/resource_catalog.py --build")
        return 1
    for c in cands:
        av = c["availability"]
        print(f"\n  ● {c['resource_node']}   (match: {c['match']})")
        print(f"      units: {', '.join(c['units'])}")
        if c["types"]:
            print(f"      types: {', '.join(c['types'])}")
        print(f"      cached: price {av['price_rows_cached']:,} rows · "
              f"gen {av['gen_rows_cached']:,} rows · "
              f"SCED files {av['plant_sced_files']} · "
              f"in registry: {', '.join(av['units_in_registry']) or 'no'}")
    best = cands[0]
    if args.save:
        rec = res.get("ifyi") or {}
        n = project_lookup.persist_to_crosswalk(
            best["units"], res["name_used"],
            queue_id=rec.get("queue_id"), url=rec.get("url"),
            county=rec.get("county"), capacity_mw=rec.get("capacity_mw"))
        print(f"\n💾 saved {n} unit name(s) -> crosswalk (source 'ifyi'): "
              f"{', '.join(best['units'])} = {res['name_used']!r}")

    print(f"\nPull data for the top candidate:")
    print(f"  python datasets/system_gen_by_fuel/pull_nodes.py pull --node {best['resource_node']} "
          f"--start 2026-01-01 --end 2026-03-31")
    if not args.save:
        print("  (add --save to record these unit names into the crosswalk)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
