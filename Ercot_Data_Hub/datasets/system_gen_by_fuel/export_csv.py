#!/usr/bin/env python3
"""On-demand CSV export of the ERCOT 15-minute generation-by-source data.

Parquet stays the canonical store (compact, typed); this writes a CSV slice for
Excel / sharing into csv_exports/ (git-ignored).

Usage:
    python export_csv.py 2025                     # whole year
    python export_csv.py 2026 --month 5           # just May
    python export_csv.py 2025 --fuel Solar Wind   # only those fuels
    python export_csv.py 2025 --wide              # pivot: one column per fuel
    python export_csv.py 2024 --month 7 --fuel Gas Gas-CC --wide -o july_gas.csv
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import ercot_fuels as F
from ercot_core import paths

EXPORT_DIR = str(paths.CSV_EXPORTS_DIR)
PARQUET_TEMPLATE = str(paths.SYSTEM_GEN_DIR / "ercot_gen_by_fuel_{year}.parquet")


def export(
    year: int,
    month: int | None = None,
    fuels: list[str] | None = None,
    wide: bool = False,
    out: str | None = None,
) -> str:
    path = PARQUET_TEMPLATE.format(year=year)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — build it first: python update_generation.py {year}"
        )

    df = pd.read_parquet(path)

    if month is not None:
        df = df[df["interval_start"].dt.month == month]
    if fuels:
        unknown = [f for f in fuels if f not in F.CANONICAL_FUELS]
        if unknown:
            raise ValueError(f"Unknown fuel(s): {unknown}. Valid: {F.CANONICAL_FUELS}")
        df = df[df["fuel"].isin(fuels)]

    if df.empty:
        raise ValueError("No rows match those filters — nothing to export.")

    df = df.sort_values(["interval_start", "fuel"])

    if wide:
        # One row per interval, one column per fuel (MW). Handy for charts/Excel.
        df = (
            df.pivot_table(index="interval_start", columns="fuel", values="mw")
            .reset_index()
            .rename_axis(columns=None)
        )

    os.makedirs(EXPORT_DIR, exist_ok=True)
    if out is None:
        parts = [f"ercot_gen_by_fuel_{year}"]
        if month is not None:
            parts.append(f"m{month:02d}")
        if fuels:
            parts.append("_".join(f.replace("-", "").replace(" ", "") for f in fuels))
        if wide:
            parts.append("wide")
        out = "-".join(parts) + ".csv"
    out_path = os.path.join(EXPORT_DIR, os.path.basename(out))

    df.to_csv(out_path, index=False)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("year", type=int)
    ap.add_argument("--month", type=int, choices=range(1, 13), metavar="1-12",
                    help="filter to a single month")
    ap.add_argument("--fuel", nargs="+", dest="fuels", metavar="FUEL",
                    help=f"filter to fuels (any of: {', '.join(F.CANONICAL_FUELS)})")
    ap.add_argument("--wide", action="store_true",
                    help="pivot to one column per fuel (MW)")
    ap.add_argument("-o", "--out", help="output filename (lands in csv_exports/)")
    args = ap.parse_args()

    try:
        out_path = export(args.year, args.month, args.fuels, args.wide, args.out)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        return 1

    size_mb = os.path.getsize(out_path) / 1e6
    n = sum(1 for _ in open(out_path)) - 1
    print(f"✓ wrote {n:,} rows ({size_mb:.1f} MB) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
