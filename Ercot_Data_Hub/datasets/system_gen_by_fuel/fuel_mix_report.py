"""Backbone source: ERCOT Interval Generation by Fuel Report.

Direct from ERCOT (no credentials): yearly Excel workbooks with monthly tabs,
each row = Date x Fuel x Settlement Type, plus 96 columns of average MW for the
15-minute settlement intervals (interval-ending clock labels 0:15 .. 0:00).

This is the authoritative all-fuel 15-minute series. It lags real time by days
to weeks and is revised (INITIAL -> FINAL), which the provenance merge handles.

Download index: https://www.ercot.com/gridinfo/generation
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

import ercot_fuels as F

# Current single-year workbooks. ERCOT keeps the URL stable per year and
# overwrites the file in place as new days/revisions land.
CURRENT_YEAR_URLS = {
    2025: "https://www.ercot.com/files/docs/2025/02/07/IntGenbyFuel2025.xlsx",
    2026: "https://www.ercot.com/files/docs/2026/02/09/IntGenbyFuel2026.xlsx",
}
# 2007-2024 are bundled in one zip of per-year workbooks.
PREVIOUS_YEARS_ZIP = "https://www.ercot.com/files/docs/2021/03/10/FuelMixReport_PreviousYears.zip"

MONTH_TABS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# The 96 interval columns are interval-ENDING labels but always appear in
# chronological order, so we map by position to interval-START offsets from
# local midnight. This is robust to label quirks; DST days carry the standard
# caveat documented in ercot_fuels.to_utc.
_INTERVAL_COL_COUNT = 96

_USER_AGENT = {"User-Agent": "Mozilla/5.0 (ercot-generation-data pipeline)"}


def _download(url: str) -> bytes:
    resp = requests.get(url, headers=_USER_AGENT, timeout=300)
    resp.raise_for_status()
    return resp.content


# The 2007-2024 archive is ~50 MB; cache it per-process so a multi-year backfill
# downloads it once instead of once per year.
_archive_cache: bytes | None = None


def _get_archive_zip() -> bytes:
    global _archive_cache
    if _archive_cache is None:
        _archive_cache = _download(PREVIOUS_YEARS_ZIP)
    return _archive_cache


def _parse_month_sheet(df_raw: pd.DataFrame, fetched_at: pd.Timestamp) -> pd.DataFrame:
    """Turn one wide month tab into tidy long rows in the canonical schema."""
    df = df_raw.dropna(subset=["Date"]).copy()
    if df.empty:
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)

    # The interval columns are every column after the first four
    # (Date, Fuel, Settlement Type, Total).
    meta_cols = ["Date", "Fuel", "Settlement Type", "Total"]
    interval_cols = [c for c in df.columns if c not in meta_cols]
    if len(interval_cols) != _INTERVAL_COL_COUNT:
        # Defensive: keep only the first 96 in order if ERCOT pads extra cols.
        interval_cols = interval_cols[:_INTERVAL_COL_COUNT]

    long = df.melt(
        id_vars=["Date", "Fuel", "Settlement Type"],
        value_vars=interval_cols,
        var_name="interval_label",
        value_name="mw",
    )
    # Position of each interval column => 15-min offset from midnight.
    pos = {col: i for i, col in enumerate(interval_cols)}
    long["_idx"] = long["interval_label"].map(pos)
    long = long.dropna(subset=["_idx", "mw"])

    long["interval_start"] = pd.to_datetime(long["Date"]) + pd.to_timedelta(
        long["_idx"].astype(int) * 15, unit="m"
    )
    long["interval_end"] = long["interval_start"] + pd.Timedelta(minutes=15)
    long["fuel"] = long["Fuel"].astype(str).str.strip().replace(F.REPORT_FUEL_RENAME)
    long["settlement_type"] = long["Settlement Type"].astype(str).str.strip().str.upper()
    long["source"] = F.SOURCE_FUEL_MIX_REPORT
    long["fetched_at"] = fetched_at

    long = long[long["fuel"].isin(F.CANONICAL_FUELS)]
    return F.finalize(long)


def _parse_workbook(content: bytes, fetched_at: pd.Timestamp) -> pd.DataFrame:
    xl = pd.ExcelFile(io.BytesIO(content))
    sheets = [s for s in xl.sheet_names if s in MONTH_TABS]
    frames = []
    for sheet in sheets:
        raw = pd.read_excel(xl, sheet_name=sheet)
        if "Date" not in raw.columns:
            continue
        parsed = _parse_month_sheet(raw, fetched_at)
        if not parsed.empty:
            frames.append(parsed)
    if not frames:
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def fetch_year(year: int, fetched_at: pd.Timestamp | None = None) -> pd.DataFrame:
    """Fetch + parse the full Fuel Mix Report for one year as tidy long rows.

    Uses the single-year workbook when available, otherwise pulls the year's
    file out of the 2007-2024 archive zip.
    """
    fetched_at = fetched_at or pd.Timestamp.now(tz="UTC")

    if year in CURRENT_YEAR_URLS:
        content = _download(CURRENT_YEAR_URLS[year])
        return _parse_workbook(content, fetched_at)

    # Historical: pull the matching workbook out of the archive zip.
    zip_bytes = _get_archive_zip()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        candidates = [n for n in zf.namelist() if str(year) in n and n.lower().endswith(".xlsx")]
        if not candidates:
            raise FileNotFoundError(
                f"No workbook for {year} in archive. Members: {zf.namelist()[:5]}..."
            )
        content = zf.read(candidates[0])
    return _parse_workbook(content, fetched_at)


if __name__ == "__main__":
    import sys

    yr = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    out = fetch_year(yr)
    print(f"{yr}: {len(out):,} rows | "
          f"{out['interval_start'].min()} -> {out['interval_start'].max()}")
    print("fuels:", sorted(out["fuel"].unique()))
    print("settlement types:", sorted(out["settlement_type"].unique()))
