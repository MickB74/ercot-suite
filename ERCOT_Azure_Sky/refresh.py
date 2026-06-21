#!/usr/bin/env python3
"""Top up Azure Sky's cached data to the latest available ERCOT date — one command.

The portal settles on two streams that both live in the shared Data Hub lake:

  * generation — the four ``VORTEX_WIND1..4`` SCED units (15-min telemetry,
    publishes on a ~60-day lag). **Asset-specific — this script advances it.**
  * hub price  — HB_NORTH RT15 settlement-point price. This is a **shared** Hub
    resource the Data Hub maintains centrally for every project, so this script
    only reports its freshness and points you at the Hub's price updater.

Run it in the **Hub's** virtualenv (it has gridstatus + the ERCOT credentials):

    /path/to/Ercot_Data_Hub/.venv/bin/python refresh.py
    # or double-click "Refresh Azure Sky Data.command"

Incremental by default: it re-pulls a short overlap before the last cached day
(to catch ERCOT revisions) through the latest available date. Use ``--full`` to
rebuild from the configured backfill start, or ``--start YYYY-MM-DD`` to force a
start date.
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

from azuresky import contract, hub  # noqa: E402

OVERLAP_DAYS = 5            # re-pull this many days before the last cached day
BACKFILL_START = dt.date(2024, 1, 1)   # earliest data the portal cares about


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
    args = ap.parse_args()

    forced = dt.date.fromisoformat(args.start) if args.start else None

    try:
        sced_plants, _hub_price_pull, sced = hub.datasets()
    except FileNotFoundError as e:
        print(e)
        return 1
    except ImportError as e:
        print(f"Missing a data-pull dependency ({e}). Run this with the Hub's venv:\n"
              f"  {hub.hub_root()}/.venv/bin/python refresh.py")
        return 1

    a = contract.ASSET
    units = a["units"]
    latest = sced.latest_available_date()
    print(f"Azure Sky ({a['resource_node']}) — latest available SCED date: {latest}\n")

    # ── generation (the four VORTEX units) ───────────────────────────────────
    gw_start, gw_end = hub._gen_span(tuple(units))
    gmax = pd.Timestamp(gw_end).date() if gw_end is not None else None
    gstart = _start_for(gmax, forced, args.full)
    print(f"[generation] {len(units)} units · cached through {gmax or '—'} · "
          f"pulling {gstart} → {latest} …")
    if gstart > latest:
        print("  already current.\n")
    else:
        results = sced_plants.fetch_plants(units, gstart, latest,
                                           allow_fetch=True, write=True)
        rows = sum(len(df) for df in results.values() if df is not None)
        print(f"  fetched {rows:,} unit-rows across {len(units)} units\n")
        hub._gen_span.cache_clear()   # so the new span shows below

    # ── hub price (shared Data Hub resource — report only) ───────────────────
    p_lo, p_hi = hub._price_span(a["hub"])
    if p_hi is not None:
        print(f"[hub price] {a['hub']} RT15 cached through {pd.Timestamp(p_hi).date()} "
              "(shared Hub store).")
        if pd.Timestamp(p_hi).date() < latest:
            print("  ↳ behind the latest SCED date. HB_NORTH prices are maintained for\n"
                  "    every project by the Data Hub — top them up there:\n"
                  f'    double-click "Update ERCOT Prices.command" in\n'
                  f"    {hub.hub_root()}/datasets/hub_prices/, then re-open this portal.\n")
        else:
            print("  current.\n")
    else:
        print(f"[hub price] no cached HB_NORTH price found in the Hub store.\n")

    ws, we = hub.settlement_window(units, a["hub"])
    if ws is None:
        print("✓ Done. (No overlapping generation + price window yet.)")
    else:
        print(f"✓ Done. Portal settlement window is now {ws} → {we}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
