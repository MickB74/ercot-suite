"""Acquire and tidy EIA-923 monthly generation & fuel data, filtered to ERCOT.

EIA Form 923 publishes plant-level monthly **net generation** and **fuel
consumption** as annual Excel workbooks (the "Schedules 2/3/4/5" file, Page 1).
This module:

  1. Downloads the annual ZIP from eia.gov (current year under ``/xls/``, older
     years under ``/archive/xls/``) and caches the raw ZIP in ``raw/``.
  2. Parses "Page 1 Generation and Fuel Data".
  3. Melts the wide monthly columns into a tidy long table — one row per
     plant x prime-mover x fuel x month.
  4. Filters to a region (default ERCOT, balancing-authority code ``ERCO``).
  5. Caches one parquet per year: ``eia923_ercot_<year>.parquet``.

Source: https://www.eia.gov/electricity/data/eia923/

This is the annual-resolution, plant-level companion to the 15-minute
Fuel-Mix-Report ETL in ~/Documents/Github/Ercot_Generation_Data. EIA-923 final
data lags by ~6 months (e.g. full prior-year final ~Oct); a same-year file
holds year-to-date monthly data and is revised as the year progresses.
"""

from __future__ import annotations

from pathlib import Path
import io
import zipfile

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"

BASE_URL = "https://www.eia.gov/electricity/data/eia923"
USER_AGENT = "Mozilla/5.0 (Ercot_EIA_Generation_Data ETL)"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Wide monthly column prefix -> tidy metric name.
METRIC_PREFIXES = {
    "Netgen ": "netgen_mwh",            # net generation, MWh
    "Tot_MMBtu ": "total_mmbtu",        # total fuel consumed, MMBtu
    "Elec_MMBtu ": "elec_mmbtu",        # fuel consumed for electricity, MMBtu
    "Quantity ": "fuel_quantity",       # physical fuel quantity (units vary)
    "Elec_Quantity ": "elec_fuel_quantity",
}

# Identifier columns kept on every row (raw header -> tidy name).
ID_COLUMNS = {
    "Plant Id": "plant_id",
    "Plant Name": "plant_name",
    "Operator Name": "operator_name",
    "Operator Id": "operator_id",
    "Plant State": "state",
    "Balancing Authority Code": "ba_code",
    "NERC Region": "nerc_region",
    "Sector Name": "sector",
    "Reported Prime Mover": "prime_mover",
    "Reported Fuel Type Code": "fuel_code",
    "Physical Unit Label": "fuel_unit",
}

# EIA reported fuel-type code -> canonical category (aligned with the ERCOT
# fuel taxonomy used by the Fuel-Mix-Report ETL). Unknown codes -> "Other".
FUEL_CATEGORY = {
    # Coal
    "ANT": "Coal", "BIT": "Coal", "LIG": "Coal", "SUB": "Coal", "RC": "Coal",
    "SGC": "Coal", "WC": "Coal", "SC": "Coal", "CBL": "Coal",
    # Natural gas
    "NG": "Gas",
    # Other manufactured / waste gas
    "OG": "Other Gas", "BFG": "Other Gas", "PG": "Other Gas", "SGP": "Other Gas",
    # Petroleum / oil
    "DFO": "Oil", "RFO": "Oil", "JF": "Oil", "KER": "Oil", "PC": "Oil",
    "WO": "Oil", "RG": "Oil",
    # Nuclear
    "NUC": "Nuclear",
    # Renewables
    "WAT": "Hydro", "WND": "Wind", "SUN": "Solar", "GEO": "Geothermal",
    # Biomass / waste-derived
    "AB": "Biomass", "BLQ": "Biomass", "DG": "Biomass", "LFG": "Biomass",
    "MSB": "Biomass", "MSN": "Biomass", "OBG": "Biomass", "OBL": "Biomass",
    "OBS": "Biomass", "OBW": "Biomass", "SLW": "Biomass", "TDF": "Biomass",
    "WDL": "Biomass", "WDS": "Biomass", "MSW": "Biomass",
    # Storage / other
    "MWH": "Storage", "PUR": "Other", "WH": "Other", "OTH": "Other",
}


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

def _zip_candidates(year: int) -> list[str]:
    """Possible ZIP URLs, in priority order (current path, then archive)."""
    return [
        f"{BASE_URL}/xls/f923_{year}.zip",
        f"{BASE_URL}/archive/xls/f923_{year}.zip",
    ]


def fetch_zip(year: int, raw_dir: Path = RAW_DIR, force: bool = False) -> Path:
    """Download the EIA-923 annual ZIP for ``year`` (cached in ``raw_dir``)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"f923_{year}.zip"
    if dest.exists() and not force:
        return dest

    last_err: Exception | None = None
    for url in _zip_candidates(year):
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=120,
                allow_redirects=True,
            )
            # eia.gov 301-redirects retired paths to an HTML landing page;
            # only accept a genuine zip payload.
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and "zip" in ctype.lower():
                dest.write_bytes(resp.content)
                return dest
            last_err = RuntimeError(
                f"{url} -> HTTP {resp.status_code}, Content-Type {ctype!r}")
        except requests.RequestException as exc:  # pragma: no cover - network
            last_err = exc
    raise RuntimeError(f"Could not download EIA-923 for {year}: {last_err}")


def _find_member(zf: zipfile.ZipFile) -> str:
    """Locate the 'Schedules 2_3_4_5' workbook inside the ZIP."""
    members = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls"))]
    for n in members:
        if "2_3_4_5" in n:
            return n
    if members:  # fall back to the single / first workbook
        return members[0]
    raise FileNotFoundError("No Excel workbook found inside ZIP")


# --------------------------------------------------------------------------- #
# Parse & tidy
# --------------------------------------------------------------------------- #

def read_page1(zip_path: Path) -> pd.DataFrame:
    """Read the raw 'Page 1 Generation and Fuel Data' sheet from the ZIP."""
    with zipfile.ZipFile(zip_path) as zf:
        member = _find_member(zf)
        data = zf.read(member)

    xls = pd.ExcelFile(io.BytesIO(data))
    sheet = next(
        (s for s in xls.sheet_names if s.lower().startswith("page 1 generation")),
        xls.sheet_names[0],
    )
    raw = xls.parse(sheet, header=None)

    # Header row is the one whose first cell is "Plant Id" (preamble length
    # varies by vintage, so don't hard-code skiprows).
    first = raw.iloc[:, 0].astype(str).str.strip()
    matches = first.index[first == "Plant Id"]
    if len(matches) == 0:
        raise ValueError(f"Could not find 'Plant Id' header row in {sheet}")
    hdr = matches[0]

    cols = [str(c).replace("\n", " ").strip() for c in raw.iloc[hdr]]
    out = raw.iloc[hdr + 1:].copy()
    out.columns = cols
    return out


def _to_numeric(series: pd.Series) -> pd.Series:
    """Coerce EIA numeric cells ('.', blanks, commas) to float."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip()
        .replace({".": None, "": None, "nan": None, "None": None}),
        errors="coerce",
    )


def tidy(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    """Melt the wide Page-1 frame into a tidy long monthly table."""
    df = raw.copy()
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    # Drop footnote / total rows: keep only numeric Plant Id.
    df = df[pd.to_numeric(df["Plant Id"], errors="coerce").notna()].copy()
    df = df.reset_index(drop=True)
    df["_rid"] = df.index

    present_ids = {raw_c: tidy_c for raw_c, tidy_c in ID_COLUMNS.items()
                   if raw_c in df.columns}

    # Melt each metric's 12 monthly columns, keyed on the row id.
    metric_frames = []
    for prefix, metric in METRIC_PREFIXES.items():
        cols = [f"{prefix}{m}" for m in MONTHS if f"{prefix}{m}" in df.columns]
        if not cols:
            continue
        sub = df[["_rid", *cols]].melt(
            id_vars="_rid", value_vars=cols,
            var_name="month_name", value_name=metric,
        )
        sub["month_name"] = sub["month_name"].str.replace(prefix, "", regex=False)
        sub[metric] = _to_numeric(sub[metric])
        metric_frames.append(sub.set_index(["_rid", "month_name"]))

    long = pd.concat(metric_frames, axis=1).reset_index()

    # Attach identifier columns.
    ids = df[["_rid", *present_ids.keys()]].rename(columns=present_ids)
    long = long.merge(ids, on="_rid", how="left").drop(columns="_rid")

    long["plant_id"] = pd.to_numeric(long["plant_id"], errors="coerce").astype("Int64")
    long["year"] = int(year)
    long["month"] = long["month_name"].map({m: i for i, m in enumerate(MONTHS, 1)})
    # Monthly stamp, kept tz-NAIVE on purpose: EIA-923 is monthly and a calendar
    # month has no single instant or timezone. To line these rows up against
    # interval (15-min/hourly) ERCOT data, lift to a tz-aware Central month start
    # with central_month_start() rather than comparing naive-to-aware.
    long["date"] = pd.to_datetime(
        dict(year=long["year"], month=long["month"], day=1), errors="coerce")
    long["fuel_category"] = (
        long.get("fuel_code", pd.Series(index=long.index))
        .map(FUEL_CATEGORY).fillna("Other"))

    # Drop months with no activity at all (keeps the table compact).
    metric_cols = [m for m in METRIC_PREFIXES.values() if m in long.columns]
    long = long.dropna(subset=metric_cols, how="all")

    order = [
        "year", "month", "date", "plant_id", "plant_name", "operator_name",
        "operator_id", "state", "ba_code", "nerc_region", "sector",
        "prime_mover", "fuel_code", "fuel_category", "fuel_unit",
        *metric_cols,
    ]
    return long[[c for c in order if c in long.columns]].reset_index(drop=True)


def filter_region(df: pd.DataFrame, region: str = "ercot") -> pd.DataFrame:
    """Filter tidy rows to a region.

    ``ercot`` -> balancing-authority code ERCO (falls back to Texas plants for
    older vintages that predate BA-code reporting). ``tx`` -> all Texas plants.
    ``all`` -> no filter.
    """
    region = region.lower()
    if region == "all":
        return df
    if region == "tx":
        return df[df["state"] == "TX"].reset_index(drop=True)
    if region == "ercot":
        if "ba_code" in df.columns and (df["ba_code"] == "ERCO").any():
            return df[df["ba_code"] == "ERCO"].reset_index(drop=True)
        # Pre-BA-code vintage: approximate ERCOT with Texas plants.
        return df[df["state"] == "TX"].reset_index(drop=True)
    raise ValueError(f"Unknown region {region!r} (use ercot | tx | all)")


# --------------------------------------------------------------------------- #
# Build / load cache
# --------------------------------------------------------------------------- #

def parquet_path(year: int, region: str = "ercot", out_dir: Path = ROOT) -> Path:
    return out_dir / f"eia923_{region}_{year}.parquet"


def build_year(
    year: int,
    region: str = "ercot",
    out_dir: Path = ROOT,
    raw_dir: Path = RAW_DIR,
    force_download: bool = False,
) -> pd.DataFrame:
    """Download, parse, filter, and cache one year. Returns the tidy frame."""
    zip_path = fetch_zip(year, raw_dir=raw_dir, force=force_download)
    df = filter_region(tidy(read_page1(zip_path), year), region=region)
    out = parquet_path(year, region=region, out_dir=out_dir)
    df.to_parquet(out, index=False)
    return df


def available_years(region: str = "ercot", out_dir: Path = ROOT) -> list[int]:
    years = []
    for p in out_dir.glob(f"eia923_{region}_*.parquet"):
        try:
            years.append(int(p.stem.rsplit("_", 1)[-1]))
        except ValueError:
            continue
    return sorted(years)


def central_month_start(s: pd.Series) -> pd.Series:
    """The naive monthly ``date`` lifted to a tz-aware Central month-start.

    Use this when joining EIA-923 monthly rows to interval (15-min / hourly)
    ERCOT data so both sides share a tz-aware Central key — avoids the silent
    NaT/misalignment of comparing naive months to tz-aware intervals.
    """
    import tzutil
    return tzutil.localize_central(pd.to_datetime(s))


def load(years=None, region: str = "ercot", out_dir: Path = ROOT) -> pd.DataFrame:
    """Load and concatenate cached yearly parquets."""
    if years is None:
        years = available_years(region=region, out_dir=out_dir)
    frames = []
    for y in years:
        p = parquet_path(y, region=region, out_dir=out_dir)
        if p.exists():
            frames.append(pd.read_parquet(p))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":  # quick smoke test
    import sys
    yr = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    out = build_year(yr)
    print(f"{len(out):,} rows for {yr} "
          f"| {out['plant_id'].nunique()} plants "
          f"| {out['netgen_mwh'].sum():,.0f} MWh net gen")
    print(out.groupby("fuel_category")["netgen_mwh"].sum()
          .sort_values(ascending=False).round(0))
