#!/usr/bin/env python3
"""
Select ERCOT plants and a time frame, then fetch & store their native SCED
operating data.

Browse what's available
-----------------------
  python fetch_plants.py --list                  # every resource (1,100+)
  python fetch_plants.py --list --fuel Solar      # one fuel group
  python fetch_plants.py --list --fuel Wind Storage
  python fetch_plants.py --search FRYE            # name contains FRYE
  python fetch_plants.py --fuels                  # show fuel-group counts

Fetch & store
-------------
  python fetch_plants.py FRYE_SLR_UNIT1 VORTEX_WIND1 --start 2026-01-01 --end 2026-03-31
  python fetch_plants.py FRYE_SLR_UNIT1 --year 2026
  python fetch_plants.py --fuel Wind --year 2025          # every wind unit, full year
  python fetch_plants.py FRYE_SLR_UNIT1 --year 2026 --csv # also export a CSV

Maintenance
-----------
  python fetch_plants.py --refresh-registry       # rebuild the available list

Stored as data/<RESOURCE>_<YEAR>.parquet. ERCOT publishes with a ~60-day lag,
so the most recent ~2 months aren't available yet.
"""
import argparse
import os
import sys

import pandas as pd

import sced_plants as sp


def _filter_registry(reg, fuels=None, search=None):
    out = reg
    if fuels:
        wanted = {f.lower() for f in fuels}
        out = out[out["fuel_group"].str.lower().isin(wanted)]
    if search:
        out = out[out["resource_name"].str.contains(search, case=False, na=False)]
    return out.reset_index(drop=True)


def cmd_list(args):
    reg = sp.load_registry()
    sub = _filter_registry(reg, args.fuel, args.search)
    if sub.empty:
        print("No resources match.")
        return
    width = max(sub["resource_name"].str.len().max(), 14)
    has_names = "plant_name" in sub.columns
    nw = max(sub["plant_name"].str.len().max(), 12) if has_names else 0
    header = f"{'RESOURCE':<{width}}  {'TYPE':<8}  {'FUEL':<13}"
    if has_names:
        header += f"  {'PLANT NAME':<{nw}}"
    print(header)
    print("-" * len(header))
    for _, r in sub.iterrows():
        line = f"{r['resource_name']:<{width}}  {str(r['resource_type']):<8}  {r['fuel_group']:<13}"
        if has_names:
            line += f"  {r['plant_name']}"
        print(line)
    print(f"\n{len(sub)} resource(s).")


def cmd_fuels(args):
    reg = sp.load_registry()
    counts = reg.groupby("fuel_group")["resource_name"].count().sort_values(ascending=False)
    print("Fuel groups (resource counts):")
    for fuel, n in counts.items():
        print(f"  {fuel:<14} {n}")
    print(f"  {'TOTAL':<14} {len(reg)}")


def _resolve_dates(args):
    if args.year:
        return f"{args.year}-01-01", f"{args.year}-12-31"
    if not args.start:
        sys.exit("Provide --start (and optional --end) or --year.")
    end = args.end or str(sp.latest_available_date())
    return args.start, end


def cmd_fetch(args):
    reg = sp.load_registry()
    resources = list(args.resources)
    if args.fuel:
        resources += _filter_registry(reg, args.fuel, None)["resource_name"].tolist()
    resources = list(dict.fromkeys(resources))
    if not resources:
        sys.exit("No resources selected. Pass resource names and/or --fuel.")

    # Warn about names not in the registry (typos), but still attempt them.
    known = set(reg["resource_name"])
    unknown = [r for r in resources if r not in known]
    if unknown:
        print(f"Warning: not in registry (will still try): {', '.join(unknown)}")

    start, end = _resolve_dates(args)
    print(f"Fetching {len(resources)} resource(s) {start} .. {end}\n")
    results = sp.fetch_plants(resources, start, end)

    if args.csv:
        frames = [df for df in results.values() if not df.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            os.makedirs("csv_exports", exist_ok=True)
            tag = args.year or f"{start}_{end}"
            name = resources[0] if len(resources) == 1 else f"{len(resources)}plants"
            path = os.path.join("csv_exports", f"sced_{name}_{tag}.csv")
            combined.to_csv(path, index=False)
            print(f"\nCSV: {path}  ({len(combined):,} rows)")

    total = sum(len(df) for df in results.values())
    print(f"\nDone. {total:,} total intervals across {len(results)} resource(s).")


def main():
    p = argparse.ArgumentParser(
        description="Fetch & store ERCOT plant-level native SCED operating data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("resources", nargs="*", help="Resource name(s) to fetch.")
    p.add_argument("--list", action="store_true", help="List available resources.")
    p.add_argument("--fuels", action="store_true", help="Show fuel-group counts.")
    p.add_argument("--search", metavar="TERM", help="Filter list by name substring.")
    p.add_argument("--fuel", nargs="+", metavar="FUEL",
                   help="Filter by fuel group (Solar Wind Storage Gas Gas-CC Nuclear Hydro Coal/Lignite ...).")
    p.add_argument("--start", metavar="YYYY-MM-DD", help="Start date.")
    p.add_argument("--end", metavar="YYYY-MM-DD", help="End date (default: latest available).")
    p.add_argument("--year", type=int, help="Shortcut for a whole calendar year.")
    p.add_argument("--csv", action="store_true", help="Also export a combined CSV.")
    p.add_argument("--refresh-registry", action="store_true",
                   help="Rebuild the available-resources list from a fresh disclosure.")
    p.add_argument("--build-names", action="store_true",
                   help="Build/refresh the resource-code -> plant-name crosswalk (plant_names.csv).")
    args = p.parse_args()

    if args.refresh_registry:
        sp.build_registry()
        return
    if args.build_names:
        import plant_names
        # Use the raw registry (without an existing name merge) as the input.
        reg = sp.load_registry()[["resource_name", "resource_type", "fuel_group"]]
        plant_names.build_crosswalk(reg)
        return
    if args.fuels:
        cmd_fuels(args)
        return
    # Browse mode: an explicit --list, or any query with no time frame given.
    if args.list or args.search or (not args.start and not args.year):
        cmd_list(args)
        return
    cmd_fetch(args)


if __name__ == "__main__":
    main()
