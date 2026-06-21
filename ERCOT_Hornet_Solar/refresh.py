#!/usr/bin/env python3
"""Top up Hornet Solar's cached data to the latest available ERCOT date — one command.

Advances both streams the portal settles on, into the shared Data Hub lake:

  * generation — SCED telemetered 15-min output (publishes on a ~60-day lag)
  * node price — RT15 settlement-point price at HRNT_SLR_RN

Node prices older than ERCOT's live window only exist in the **archive**, so this
uses the archive fetch (``ercot_core.spp_archive``) — the same path that worked
for the 2026 backfill — rather than the live-only ``pull_nodes`` job.

Run it in the **Hub's** virtualenv (it has gridstatus + the ERCOT credentials):

    /path/to/Ercot_Data_Hub/.venv/bin/python refresh.py
    # or double-click "Refresh Hornet Solar Data.command"

Incremental by default: it re-pulls a short overlap before each stream's last
cached day (to catch ERCOT revisions) through the latest available date. Use
``--full`` to rebuild from the configured backfill start, or ``--start YYYY-MM-DD``
to force a start date.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portal import contract, hub  # noqa: E402

OVERLAP_DAYS = 5            # re-pull this many days before the last cached day
BACKFILL_START = dt.date(2024, 10, 1)  # plant COD ~Nov 2024; Oct gives a clean quarter start


def _cached_max(read_fn, node: str) -> dt.date | None:
    """Latest cached interval date for a stream, scanning all years, or None."""
    latest = None
    for year in range(BACKFILL_START.year, dt.date.today().year + 1):
        start = pd.Timestamp(year, 1, 1)
        end_excl = pd.Timestamp(year + 1, 1, 1)
        df = read_fn(node, start, end_excl)
        if df is not None and not df.empty:
            mx = pd.to_datetime(df["interval_start"]).max().date()
            latest = mx if latest is None else max(latest, mx)
    return latest


def _start_for(cached_max: dt.date | None, forced_start: dt.date | None,
               full: bool) -> dt.date:
    if forced_start:
        return forced_start
    if full or cached_max is None:
        return BACKFILL_START
    return max(BACKFILL_START, cached_max - dt.timedelta(days=OVERLAP_DAYS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true",
                    help=f"rebuild from {BACKFILL_START} instead of the last cached day")
    ap.add_argument("--start", help="force a start date (YYYY-MM-DD)")
    ap.add_argument("--gen-only", action="store_true", help="only refresh generation")
    ap.add_argument("--price-only", action="store_true", help="only refresh node price")
    args = ap.parse_args()

    forced = dt.date.fromisoformat(args.start) if args.start else None

    try:
        pull_nodes, node_generation, spp_archive, sced = hub.datasets()
    except FileNotFoundError as e:
        print(e)
        return 1
    except ImportError as e:
        print(f"Missing a data-pull dependency ({e}). Run this with the Hub's venv:\n"
              f"  {hub.hub_root()}/.venv/bin/python refresh.py")
        return 1

    node = contract.ASSET["resource_node"]
    latest = sced.latest_available_date()
    print(f"Hornet Solar ({node}) — latest available SCED date: {latest}\n")

    do_gen = not args.price_only
    do_price = not args.gen_only

    # ── generation ──────────────────────────────────────────────────────────
    if do_gen:
        gmax = _cached_max(hub.generation, node)
        gstart = _start_for(gmax, forced, args.full)
        print(f"[generation] cached through {gmax or '—'} · pulling {gstart} → {latest} …")
        if gstart > latest:
            print("  already current.\n")
        else:
            g = node_generation.fetch_generation([node], pd.Timestamp(gstart),
                                                 pd.Timestamp(latest), verbose=False)
            print(f"  fetched {len(g):,} rows")
            if not g.empty:
                pull_nodes._merge_save(g, pull_nodes.GEN_TEMPLATE, pull_nodes.GEN_KEY)
            print()

    # ── node price (archive-aware) ───────────────────────────────────────────
    if do_price:
        pmax = _cached_max(hub.node_prices, node)
        pstart = _start_for(pmax, forced, args.full)
        print(f"[node price] cached through {pmax or '—'} · pulling {pstart} → {latest} "
              "(archive for older months — can take a few minutes) …")
        if pstart > latest:
            print("  already current.\n")
        else:
            p = spp_archive.fetch_rtm_spp([node], pstart, latest,
                                          location_type="Resource Node",
                                          log=lambda m: print("   " + m))
            print(f"  fetched {len(p):,} rows")
            if not p.empty:
                pull_nodes._merge_save(p, pull_nodes.PRICE_TEMPLATE, pull_nodes.PRICE_KEY)
            print()

    ws, we = hub.settlement_window(node)
    print(f"✓ Done. Portal settlement window is now {ws} → {we}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
