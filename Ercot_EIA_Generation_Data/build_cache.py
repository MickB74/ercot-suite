"""CLI to build/refresh the EIA-923 parquet cache.

Examples
--------
    python build_cache.py                # ERCOT, 2018..current year
    python build_cache.py 2024           # single year
    python build_cache.py 2020 2024      # inclusive range
    python build_cache.py --region tx 2024
    python build_cache.py --force 2025   # re-download (current year is revised)
"""

from __future__ import annotations

import argparse
import datetime as _dt

import eia923


def _default_years() -> list[int]:
    import tzutil
    this_year = tzutil.now_central().year
    return list(range(2018, this_year + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the EIA-923 parquet cache.")
    ap.add_argument("years", nargs="*", type=int,
                    help="single year, or start end for an inclusive range")
    ap.add_argument("--region", default="ercot", choices=["ercot", "tx", "all"])
    ap.add_argument("--force", action="store_true",
                    help="re-download the source ZIP even if cached")
    args = ap.parse_args()

    if not args.years:
        years = _default_years()
    elif len(args.years) == 1:
        years = args.years
    elif len(args.years) == 2:
        years = list(range(min(args.years), max(args.years) + 1))
    else:
        years = sorted(set(args.years))

    for year in years:
        try:
            df = eia923.build_year(year, region=args.region,
                                   force_download=args.force)
            print(f"[{year}] {len(df):>7,} rows | "
                  f"{df['plant_id'].nunique():>4} plants | "
                  f"{df['netgen_mwh'].sum():>16,.0f} MWh -> "
                  f"{eia923.parquet_path(year, args.region).name}")
        except Exception as exc:  # keep going on a bad year
            print(f"[{year}] SKIPPED: {exc}")


if __name__ == "__main__":
    main()
