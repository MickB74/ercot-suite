#!/usr/bin/env python3
"""EIA-930 — hourly net generation by balancing authority (the fast sanity check).

EIA's Hourly Electric Grid Monitor (Form EIA-930) publishes hourly net
generation per balancing authority with only a ~1-day lag — far faster than the
plant-level EIA-923 monthly file (~3 months) or even ERCOT's 60-day SCED. It's
**balancing-authority × hour**, not plant-level, so it can't replace EIA-923 for
asset settlement — but it's an excellent independent, near-real-time check on
system totals (is ERCOT's reported generation in the right ballpark?).

Source: EIA Open Data v2 API, ``electricity/rto/region-data`` with ``type=NG``
(net generation, MWh). Needs the free EIA API key in the shared config.json
(``eia_api_key``). Stored as one tidy parquet: one row per (respondent, hour).

Usage:
    python eia930.py update                 # incremental (re-pull recent overlap)
    python eia930.py update --start 2024-01-01
    python eia930.py update --full          # from backfill_start (or 2 years ago)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ercot_core import credentials, paths  # noqa: E402

API_URL = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
PAGE = 5000                  # EIA v2 max rows per request
OVERLAP_DAYS = 3            # re-pull a short overlap to catch EIA revisions
DEFAULT_BACKFILL_YEARS = 2  # how far back --full / a fresh pull goes by default


def _default_start() -> dt.date:
    cfg = credentials.load_config()
    bf = str(cfg.get("backfill_start", "") or "").strip()
    if bf:
        try:
            return dt.date.fromisoformat(bf[:10])
        except ValueError:
            pass
    return dt.date.today() - dt.timedelta(days=365 * DEFAULT_BACKFILL_YEARS)


def load_store() -> pd.DataFrame:
    if paths.EIA930_REGION_PARQUET.exists():
        return pd.read_parquet(paths.EIA930_REGION_PARQUET)
    return pd.DataFrame(columns=["period", "respondent", "respondent_name", "value_mwh"])


def save_store(df: pd.DataFrame) -> None:
    paths.EIA930_DIR.mkdir(parents=True, exist_ok=True)
    df = (df.sort_values(["respondent", "period"])
            .reset_index(drop=True))
    df.to_parquet(paths.EIA930_REGION_PARQUET, index=False)


def _fetch_window(api_key: str, start: dt.date, end: dt.date, log=print) -> pd.DataFrame:
    """All respondents' hourly net generation over [start, end] (inclusive days)."""
    base = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[type][]": "NG",
        "start": f"{start.isoformat()}T00",
        "end": f"{(end + dt.timedelta(days=1)).isoformat()}T00",
        "sort[0][column]": "period", "sort[0][direction]": "asc",
        "length": PAGE,
    }
    rows, offset, total = [], 0, None
    while True:
        params = dict(base, offset=offset)
        for attempt in range(4):
            r = requests.get(API_URL, params=params, timeout=60)
            if r.status_code == 200:
                break
            wait = 2 ** attempt
            log(f"    HTTP {r.status_code} — retry in {wait}s")
            time.sleep(wait)
        else:
            raise RuntimeError(f"EIA API failed at offset {offset}: {r.status_code} {r.text[:200]}")
        resp = r.json().get("response", {})
        if total is None:
            total = int(resp.get("total", 0))
            log(f"    {total:,} rows to fetch")
        batch = resp.get("data", [])
        rows.extend(batch)
        offset += PAGE
        if offset >= (total or 0) or not batch:
            break
        log(f"      {min(offset, total):,}/{total:,}")
    if not rows:
        return pd.DataFrame(columns=["period", "respondent", "respondent_name", "value_mwh"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "period": pd.to_datetime(df["period"], format="%Y-%m-%dT%H"),
        "respondent": df["respondent"].astype(str),
        "respondent_name": df["respondent-name"].astype(str),
        "value_mwh": pd.to_numeric(df["value"], errors="coerce"),
    })
    return out.dropna(subset=["value_mwh"])


def update(start: str | None = None, full: bool = False, log=print) -> dict:
    api_key = credentials.get_eia_api_key()
    if not api_key:
        raise RuntimeError(
            "No EIA API key. Add 'eia_api_key' to config.json "
            "(free key: https://www.eia.gov/opendata/register.php).")

    store = load_store()
    today = dt.date.today()
    if start:
        start_date = dt.date.fromisoformat(start)
    elif full or store.empty:
        start_date = _default_start()
    else:
        last = pd.to_datetime(store["period"]).max().date()
        start_date = last - dt.timedelta(days=OVERLAP_DAYS)

    log(f"EIA-930 net generation by BA: {start_date} → {today}")
    fresh = _fetch_window(api_key, start_date, today, log=log)
    log(f"  fetched {len(fresh):,} rows across {fresh['respondent'].nunique() if not fresh.empty else 0} BAs")

    merged = (pd.concat([store, fresh], ignore_index=True)
              .drop_duplicates(subset=["respondent", "period"], keep="last"))
    save_store(merged)
    credentials.export_to_env()  # harmless; keeps env in sync
    paths.EIA930_STATE.write_text(
        pd.Series({"last_success": dt.datetime.now().isoformat(timespec="seconds"),
                   "rows": len(merged),
                   "max_period": str(pd.to_datetime(merged["period"]).max())
                   if not merged.empty else None}).to_json())
    log(f"  store now {len(merged):,} rows "
        f"(through {pd.to_datetime(merged['period']).max() if not merged.empty else 'n/a'})")
    return {"rows": len(merged), "added": len(fresh)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EIA-930 hourly net generation by BA")
    sub = ap.add_subparsers(dest="cmd")
    up = sub.add_parser("update", help="incremental update")
    up.add_argument("--start", default=None, help="YYYY-MM-DD start (overrides incremental)")
    up.add_argument("--full", action="store_true", help="rebuild from backfill_start / 2y ago")
    args = ap.parse_args(argv)
    if args.cmd in (None, "update"):
        update(start=getattr(args, "start", None), full=getattr(args, "full", False))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
