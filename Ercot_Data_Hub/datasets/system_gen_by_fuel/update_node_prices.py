#!/usr/bin/env python3
"""Incrementally update the resource-node RT15 SPP lake for all tracked nodes.

The node-price lake (``node_data/node_price_<year>.parquet``) is the RT15
settlement-point price at each portal's resource node — it feeds the settlement
portals and the forecast scorecard. This tops it up: it takes the set of nodes
already in the lake, finds the latest cached interval, and pulls the recent
overlap + any gap through yesterday via the ERCOT **archive** API
(``ercot_core.spp_archive``) — the same path the portal refreshes use. (ERCOT's
MIS only retains RT15 ~7 days, so historical/older months must come from the
archive, not the live document feed.)

Incremental by default (re-pulls a short overlap to catch ERCOT revisions).
``--full`` rebuilds from the backfill start; ``--start YYYY-MM-DD`` forces a start.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import pull_nodes
from ercot_core import paths, spp_archive

OVERLAP_DAYS = 3
BACKFILL_START = dt.date(2024, 1, 1)   # ERCOT archive retains RT15 to ~2024-01


def _tracked_nodes() -> list[str]:
    """Every node already in the price lake (the set we maintain)."""
    nodes: set[str] = set()
    for f in sorted(paths.NODE_DATA_DIR.glob("node_price_*.parquet")):
        try:
            nodes |= set(pd.read_parquet(f, columns=["location"])["location"].astype(str))
        except Exception:  # noqa: BLE001
            continue
    return sorted(nodes)


def _latest_cached() -> dt.date | None:
    latest = None
    for f in sorted(paths.NODE_DATA_DIR.glob("node_price_*.parquet")):
        try:
            mx = pd.to_datetime(pd.read_parquet(f, columns=["interval_start"])["interval_start"]).max()
        except Exception:  # noqa: BLE001
            continue
        d = mx.date()
        latest = d if latest is None else max(latest, d)
    return latest


def _month_windows(start: dt.date, end: dt.date):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur + pd.offsets.MonthBegin(1)).date()
        yield max(cur, start), min(nxt - dt.timedelta(days=1), end)
        cur = nxt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true",
                    help=f"rebuild from {BACKFILL_START} instead of the last cached day")
    ap.add_argument("--start", help="force a start date (YYYY-MM-DD)")
    args = ap.parse_args()

    nodes = _tracked_nodes()
    if not nodes:
        print("No nodes in the price lake yet — nothing to update. "
              "Pull some first (pull_nodes.py pull …).")
        return 0

    cached = _latest_cached()
    end = dt.date.today() - dt.timedelta(days=1)
    if args.start:
        start = dt.date.fromisoformat(args.start)
    elif args.full or cached is None:
        start = BACKFILL_START
    else:
        start = max(BACKFILL_START, cached - dt.timedelta(days=OVERLAP_DAYS))

    print(f"[node prices] {len(nodes)} tracked nodes · cached through {cached or '—'} · "
          f"pulling {start} → {end} (archive; monthly bundles) …", flush=True)
    if start > end:
        print("  already current.")
        return 0

    grand = 0
    windows = list(_month_windows(start, end))
    for i, (m0, m1) in enumerate(windows, 1):
        df = spp_archive.fetch_rtm_spp(nodes, str(m0), str(m1), "Resource Node",
                                       log=lambda _m: None)
        n = len(df)
        if n:
            pull_nodes._merge_save(df, pull_nodes.PRICE_TEMPLATE, pull_nodes.PRICE_KEY)
            grand += n
        print(f"  [{i}/{len(windows)}] {m0:%Y-%m}: {n:,} rows "
              f"({df['location'].nunique() if n else 0} nodes)", flush=True)

    print(f"\n✓ Done. Merged {grand:,} rows across {len(windows)} month(s) for {len(nodes)} nodes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
