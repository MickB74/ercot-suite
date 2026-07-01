#!/usr/bin/env python3
"""Search ERCOT resource nodes and pull + store their generation and price data.

Storage (tidy long, yearly parquet, in node_data/):
    node_data/node_generation_<year>.parquet   interval x unit telemetered MW
    node_data/node_price_<year>.parquet         interval x node SPP ($/MWh)

Both are merged idempotently (dedup on key, newest fetched_at wins), so re-runs
and overlapping ranges don't duplicate rows.

Examples:
    # search the catalog (build it first if needed)
    python pull_nodes.py search RNCH
    python pull_nodes.py search --type WIND

    # pull both gen + price for matching nodes over a date range
    python pull_nodes.py pull --query RNCH --start 2026-04-01 --end 2026-04-07
    python pull_nodes.py pull --node 7RNCHSLR_ALL --price-only --start 2026-06-01 --end 2026-06-13
    python pull_nodes.py pull --type WIND --gen-only --start 2026-03-01 --end 2026-03-31
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import resource_catalog as rc
import node_generation as ng
import node_prices as npx
import settlement_points as sp
from ercot_core import paths

DATA_DIR = str(paths.NODE_DATA_DIR)
GEN_TEMPLATE = "node_generation_{year}.parquet"
PRICE_TEMPLATE = "node_price_{year}.parquet"

GEN_KEY = ["interval_start", "resource_name"]
# dst_flag distinguishes the two passes of the November fall-back hour (identical
# naive interval_start) so _merge_save keeps both instead of collapsing to one.
PRICE_KEY = ["interval_start", "location", "market", "dst_flag"]


def _path(template: str, year: int) -> str:
    return os.path.join(DATA_DIR, template.format(year=year))


def _merge_save(new: pd.DataFrame, template: str, key: list[str], time_col: str = "interval_start") -> None:
    """Split new rows by year and merge into each yearly parquet (dedup on key)."""
    if new.empty:
        print("  (nothing to save)")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    new = new.copy()
    new["_year"] = pd.to_datetime(new[time_col]).dt.year
    for year, chunk in new.groupby("_year"):
        chunk = chunk.drop(columns="_year")
        path = _path(template, int(year))
        if os.path.exists(path):
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, chunk], ignore_index=True)
        else:
            combined = chunk
        combined = (combined
                    .sort_values(key + ["fetched_at"])
                    .drop_duplicates(subset=key, keep="last")
                    .sort_values(key)
                    .reset_index(drop=True))
        combined.to_parquet(path, index=False)
        print(f"  saved {len(combined):,} rows -> {path}")


def _resolve_locations(args) -> tuple[str, list[str]]:
    """Return (location_type, [locations]) for the pull, honoring hub/zone flags.

    --hub / --zone use nargs='*': absent=None, present-empty=[] (means all).
    """
    if getattr(args, "hub", None) is not None:
        return "Trading Hub", args.hub or sp.locations("Trading Hub")
    if getattr(args, "zone", None) is not None:
        return "Load Zone", args.zone or sp.locations("Load Zone")
    # Resource Node
    if args.node:
        return "Resource Node", list(args.node)
    nodes = rc.nodes_for(query=args.query, rtype=args.rtype)
    if not nodes:
        print("No resource nodes match that search.")
        sys.exit(1)
    return "Resource Node", nodes


def cmd_search(args) -> int:
    res = rc.search(args.query, args.rtype)
    if res.empty:
        print("No matches.")
        return 0
    print(f"{len(res)} unit rows | {res['resource_node'].nunique()} nodes\n")
    with pd.option_context("display.max_rows", 80, "display.width", 120):
        print(res.to_string(index=False))
    return 0


def cmd_pull(args) -> int:
    ltype, locs = _resolve_locations(args)
    print(f"[{ltype}] resolved {len(locs)} location(s): {', '.join(locs[:8])}"
          + (" ..." if len(locs) > 8 else ""))
    fetched_at = pd.Timestamp.now(tz="UTC")

    price_only_type = ltype in sp.PRICE_ONLY_TYPES
    do_gen = not args.price_only and not price_only_type
    do_price = not args.gen_only

    if do_price:
        print("\n[prices] pulling SPP...")
        price = npx.fetch_prices(locs, args.start, args.end, location_type=ltype,
                                 fetched_at=fetched_at)
        print(f"  fetched {len(price):,} price rows")
        _merge_save(price, PRICE_TEMPLATE, PRICE_KEY)

    if do_gen:
        print("\n[generation] pulling SCED telemetered output (60-day lag)...")
        gen = ng.fetch_generation(locs, args.start, args.end, fetched_at=fetched_at)
        print(f"  fetched {len(gen):,} generation rows")
        _merge_save(gen, GEN_TEMPLATE, GEN_KEY)
    elif price_only_type and not args.gen_only:
        print(f"\n(note: {ltype} has no generation — price only)")

    print("\nDone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search the resource-node catalog")
    s.add_argument("query", nargs="?", help="name substring")
    s.add_argument("--type", dest="rtype", help="resource type filter (WIND/PVGR/PWRSTR/...)")
    s.set_defaults(func=cmd_search)

    p = sub.add_parser("pull", help="pull + store gen and/or price for nodes / hubs / zones")
    sel = p.add_argument_group("location selection (use one)")
    sel.add_argument("--node", nargs="+", help="explicit resource node name(s)")
    sel.add_argument("--query", help="name substring to match resource nodes")
    sel.add_argument("--type", dest="rtype", help="resource type to match nodes")
    sel.add_argument("--hub", nargs="*", dest="hub", metavar="HUB",
                     help="trading hub name(s); no value = all hubs (price only)")
    sel.add_argument("--zone", nargs="*", dest="zone", metavar="ZONE",
                     help="load zone name(s); no value = all zones (price only)")
    p.add_argument("--start", required=True, help="start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="end date YYYY-MM-DD (inclusive)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--gen-only", action="store_true", help="only generation")
    mode.add_argument("--price-only", action="store_true", help="only price")
    p.set_defaults(func=cmd_pull)

    args = ap.parse_args()
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
