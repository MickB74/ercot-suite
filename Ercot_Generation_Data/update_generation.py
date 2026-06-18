#!/usr/bin/env python3
"""Keep ERCOT 15-minute generation-by-source data up to date.

One tidy parquet per year: ``ercot_gen_by_fuel_<year>.parquet`` (long format,
schema in ercot_fuels.SCHEMA_COLUMNS).

For each requested year the updater:
  1. Loads the existing parquet (if any).
  2. Re-downloads the authoritative Fuel Mix Report for that year.
  3. For the *current* year, also pulls provisional supplements to fill the gap
     between the report's end and now:
        - ERCOT Public API wind/solar actuals (if credentials present)
        - ERCOT real-time dashboard (all fuels, ~last 2 days)
  4. Merges everything with provenance, so the most authoritative row wins for
     each (interval_start, fuel) — FINAL report > INITIAL report > API > dashboard.
     This is what *replaces* provisional rows once ERCOT publishes the report,
     and replaces an INITIAL report value with its FINAL revision.
  5. Writes the parquet back, with a rollback guard.

Usage:
    python update_generation.py                # current year, incremental
    python update_generation.py 2026           # one year
    python update_generation.py 2023 2024 2026 # several years
    python update_generation.py --backfill 2018-2026
    python update_generation.py --no-supplements   # report only
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

import ercot_fuels as F
import fuel_mix_report
import dashboard_source
import api_source

DATA_DIR = "."
PARQUET_TEMPLATE = "ercot_gen_by_fuel_{year}.parquet"
CURRENT_YEAR = pd.Timestamp.now(tz="US/Central").year


def _path(year: int) -> str:
    return f"{DATA_DIR}/{PARQUET_TEMPLATE.format(year=year)}"


def _load_existing(year: int) -> pd.DataFrame:
    try:
        df = pd.read_parquet(_path(year))
        print(f"  loaded existing: {len(df):,} rows "
              f"({df['interval_start'].min()} -> {df['interval_start'].max()})")
        return df
    except FileNotFoundError:
        print("  no existing parquet — fresh build")
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)


def _supplement_frames(report_df: pd.DataFrame, fetched_at: pd.Timestamp) -> list[pd.DataFrame]:
    """Provisional sources for the gap between the report's end and now."""
    frames: list[pd.DataFrame] = []
    report_end = (
        report_df["interval_start"].max()
        if not report_df.empty else None
    )
    gap_start = (
        (report_end - pd.Timedelta(days=1))
        if report_end is not None
        else pd.Timestamp.now(tz="US/Central").normalize() - pd.Timedelta(days=14)
    )

    print(f"  report ends at {report_end}; filling gap from {gap_start}")

    # API wind/solar (hourly-derived) across the whole gap (creds permitting).
    api_df = api_source.fetch_recent(start=gap_start, fetched_at=fetched_at)
    if not api_df.empty:
        print(f"  + api (wind/solar): {len(api_df):,} rows "
              f"({api_df['interval_start'].min()} -> {api_df['interval_start'].max()})")
        frames.append(api_df)

    # Dashboard all-fuel, last ~2 days.
    dash_df = dashboard_source.fetch_recent(fetched_at=fetched_at)
    if not dash_df.empty:
        print(f"  + dashboard (all fuels): {len(dash_df):,} rows "
              f"({dash_df['interval_start'].min()} -> {dash_df['interval_start'].max()})")
        frames.append(dash_df)

    return frames


def update_year(year: int, use_supplements: bool = True) -> pd.DataFrame:
    print(f"\n=== {year} ===")
    fetched_at = pd.Timestamp.now(tz="UTC")
    existing = _load_existing(year)
    prev_max = existing["interval_start"].max() if not existing.empty else pd.NaT

    print("  downloading Fuel Mix Report...")
    report = fuel_mix_report.fetch_year(year, fetched_at=fetched_at)
    if report.empty:
        raise RuntimeError(f"Fuel Mix Report returned no rows for {year}.")
    print(f"  report: {len(report):,} rows "
          f"({report['interval_start'].min()} -> {report['interval_start'].max()}, "
          f"types={sorted(report['settlement_type'].unique())})")

    frames = [existing, report]
    if use_supplements and year == CURRENT_YEAR:
        frames.extend(_supplement_frames(report, fetched_at))

    merged = F.merge_with_provenance(*frames)

    new_max = merged["interval_start"].max()
    if pd.notna(prev_max) and pd.notna(new_max) and new_max < prev_max:
        raise RuntimeError(
            f"Rollback guard: existing max {prev_max} > new max {new_max}."
        )

    merged.to_parquet(_path(year), index=False)

    by_source = merged.groupby("source").size().to_dict()
    print(f"  saved {len(merged):,} rows -> {_path(year)}")
    print(f"  provenance: {by_source}")
    return merged


def parse_years(tokens: list[str]) -> list[int]:
    years: list[int] = []
    for tok in tokens:
        if "-" in tok:
            lo, hi = tok.split("-")
            years.extend(range(int(lo), int(hi) + 1))
        else:
            years.append(int(tok))
    return sorted(set(years))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("years", nargs="*", help="years or ranges, e.g. 2026 or 2018-2026")
    ap.add_argument("--backfill", help="year range to (re)build, e.g. 2018-2026")
    ap.add_argument("--no-supplements", action="store_true",
                    help="report only; skip dashboard/API gap fill")
    args = ap.parse_args()

    if args.backfill:
        years = parse_years([args.backfill])
    elif args.years:
        years = parse_years(args.years)
    else:
        years = [CURRENT_YEAR]

    print("=" * 64)
    print(f"ERCOT 15-min generation-by-source update | years: {years}")
    print("=" * 64)

    failures = []
    for year in years:
        try:
            update_year(year, use_supplements=not args.no_supplements)
        except Exception as e:
            print(f"  !! {year} failed: {e}")
            failures.append(year)

    print("\n" + "=" * 64)
    if failures:
        print(f"Completed with failures: {failures}")
        return 1
    print("All years updated successfully. 🎉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
