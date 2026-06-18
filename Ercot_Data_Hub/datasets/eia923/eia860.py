"""EIA-860 annual generator inventory, filtered to ERCOT.

The companion *directory* to the EIA-923 generation data: every plant and
generator with identity + siting + sizing — plant_id, name, county, lat/lon,
nameplate capacity, technology / prime mover / fuel, status, and online (or
planned) date. Covers operable, proposed, and retired units, so it's the
complete ERCOT project universe (including new builds 923 hasn't caught yet).

Source (no API key): https://www.eia.gov/electricity/data/eia860/  — annual ZIP
of Excel workbooks. We parse the Plant and Generator workbooks, join them, and
filter to balancing authority ERCO (fallback: Texas).

Output: data/eia923/eia860_ercot_<year>.parquet  (one row per generator).
"""

from __future__ import annotations

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import requests

from ercot_core import fuels, paths

BASE_URL = "https://www.eia.gov/electricity/data/eia860"
USER_AGENT = "Mozilla/5.0 (Ercot_Data_Hub EIA-860 ETL)"

PLANT_COLS = {
    "Plant Code": "plant_id", "Plant Name": "plant_name", "State": "state",
    "County": "county", "Latitude": "latitude", "Longitude": "longitude",
    "Balancing Authority Code": "ba_code", "NERC Region": "nerc_region",
    "Sector Name": "sector",
}
GEN_COLS = {
    "Plant Code": "plant_id", "Generator ID": "generator_id",
    "Technology": "technology", "Prime Mover": "prime_mover",
    "Energy Source 1": "energy_source", "Nameplate Capacity (MW)": "nameplate_mw",
    "Status": "status",
    "Operating Year": "operating_year", "Operating Month": "operating_month",
    "Planned Operation Year": "planned_year", "Planned Operation Month": "planned_month",
}


def _zip_candidates(year: int) -> list[str]:
    return [f"{BASE_URL}/xls/eia860{year}.zip",
            f"{BASE_URL}/archive/xls/eia860{year}.zip"]


def fetch_zip(year: int, force: bool = False) -> "os.PathLike":
    paths.EIA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = paths.EIA_RAW_DIR / f"eia860_{year}.zip"
    if dest.exists() and not force:
        return dest
    last = None
    for url in _zip_candidates(year):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180, allow_redirects=True)
            if r.status_code == 200 and "zip" in r.headers.get("Content-Type", "").lower():
                dest.write_bytes(r.content)
                return dest
            last = f"{url} -> HTTP {r.status_code}, {r.headers.get('Content-Type')!r}"
        except requests.RequestException as e:  # pragma: no cover
            last = str(e)
    raise RuntimeError(f"Could not download EIA-860 {year}: {last}")


def _read_sheet(zf, name_contains, must_not=None, want_sheets=None):
    """Read matching workbook(s)/sheet(s) from the zip, header row auto-detected."""
    members = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls"))]
    target = None
    for n in members:
        base = os.path.basename(n).lower()
        if name_contains in base and (must_not is None or must_not not in base):
            target = n
            break
    if target is None:
        raise FileNotFoundError(f"No workbook matching {name_contains!r} in {members}")
    data = zf.read(target)
    xls = pd.ExcelFile(io.BytesIO(data))
    sheets = want_sheets or xls.sheet_names
    frames = []
    for s in sheets:
        if s not in xls.sheet_names:
            continue
        raw = xls.parse(s, header=None)
        # header row = first row whose cells contain "Plant Code"
        hdr = None
        for i in range(min(5, len(raw))):
            if raw.iloc[i].astype(str).str.strip().eq("Plant Code").any():
                hdr = i
                break
        if hdr is None:
            continue
        cols = [str(c).replace("\n", " ").strip() for c in raw.iloc[hdr]]
        df = raw.iloc[hdr + 1:].copy()
        df.columns = cols
        df = df[pd.to_numeric(df.get("Plant Code"), errors="coerce").notna()]
        df["_sheet"] = s
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_year(year: int, region: str = "ercot", force_download: bool = False) -> pd.DataFrame:
    """Download + parse EIA-860 for `year`, ERCO-filter, cache parquet."""
    zpath = fetch_zip(year, force=force_download)
    with zipfile.ZipFile(zpath) as zf:
        plant_raw = _read_sheet(zf, "plant", must_not="generator")
        gen_raw = _read_sheet(zf, "generator",
                              want_sheets=["Operable", "Proposed", "Retired and Canceled"])

    plant = plant_raw.rename(columns={k: v for k, v in PLANT_COLS.items() if k in plant_raw.columns})
    plant = plant[[c for c in PLANT_COLS.values() if c in plant.columns]].drop_duplicates("plant_id")

    gen = gen_raw.rename(columns={k: v for k, v in GEN_COLS.items() if k in gen_raw.columns})
    keep = [c for c in GEN_COLS.values() if c in gen.columns] + ["_sheet"]
    gen = gen[keep].copy()
    gen["status_group"] = gen["_sheet"].map(
        {"Operable": "operable", "Proposed": "proposed", "Retired and Canceled": "retired"}).fillna("operable")
    gen = gen.drop(columns="_sheet")

    df = gen.merge(plant, on="plant_id", how="left")
    df["plant_id"] = pd.to_numeric(df["plant_id"], errors="coerce").astype("Int64")
    df["nameplate_mw"] = pd.to_numeric(df.get("nameplate_mw"), errors="coerce")
    df["fuel_category"] = df.get("energy_source", pd.Series(index=df.index)).map(fuels.eia_fuel_category)
    # online date: actual for operable, planned otherwise
    yr = pd.to_numeric(df.get("operating_year"), errors="coerce").fillna(
        pd.to_numeric(df.get("planned_year"), errors="coerce"))
    mo = pd.to_numeric(df.get("operating_month"), errors="coerce").fillna(
        pd.to_numeric(df.get("planned_month"), errors="coerce")).fillna(1)
    df["online_date"] = pd.to_datetime(dict(year=yr, month=mo.clip(1, 12), day=1), errors="coerce")
    df["data_year"] = int(year)

    df = _filter_region(df, region)
    order = ["data_year", "plant_id", "plant_name", "generator_id", "state", "county",
             "latitude", "longitude", "ba_code", "nerc_region", "sector", "technology",
             "prime_mover", "energy_source", "fuel_category", "nameplate_mw", "status",
             "status_group", "online_date"]
    df = df[[c for c in order if c in df.columns]].reset_index(drop=True)

    paths.EIA_DIR.mkdir(parents=True, exist_ok=True)
    out = paths.EIA_DIR / f"eia860_{region}_{year}.parquet"
    df.to_parquet(out, index=False)
    return df


def _filter_region(df, region="ercot"):
    region = region.lower()
    if region == "all":
        return df
    if region == "tx":
        return df[df["state"] == "TX"].reset_index(drop=True)
    if region == "ercot":
        if "ba_code" in df.columns and (df["ba_code"] == "ERCO").any():
            return df[df["ba_code"] == "ERCO"].reset_index(drop=True)
        return df[df["state"] == "TX"].reset_index(drop=True)
    raise ValueError(f"region must be ercot|tx|all, got {region!r}")


def parquet_path(year, region="ercot"):
    return paths.EIA_DIR / f"eia860_{region}_{year}.parquet"


def available_years(region="ercot") -> list[int]:
    ys = []
    for p in paths.EIA_DIR.glob(f"eia860_{region}_*.parquet"):
        try:
            ys.append(int(p.stem.rsplit("_", 1)[-1]))
        except ValueError:
            pass
    return sorted(ys)


def load(years=None, region="ercot") -> pd.DataFrame:
    if years is None:
        years = available_years(region)
    frames = [pd.read_parquet(parquet_path(y, region)) for y in years if parquet_path(y, region).exists()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def solar_schedule(years=None) -> pd.DataFrame:
    """Per-generator solar mounting/module attributes from EIA-860 Schedule 3_3.

    Returns columns ``plant_id``, ``generator_id``, ``array_type`` (mapped to the
    PVWatts options: "1-Axis Tracker" for single/dual-axis, else "Fixed - Open
    Rack"), ``module_type`` ("Thin film" vs "Standard"), ``tilt``, ``azimuth``.
    Cached as a parquet per vintage. National table — callers join on plant_id.
    """
    yrs = years or available_years()
    if not yrs:
        return pd.DataFrame(columns=["plant_id", "generator_id", "array_type",
                                     "module_type", "tilt", "azimuth"])
    year = max(yrs)
    cache = paths.EIA_DIR / f"eia860_solar_{year}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    with zipfile.ZipFile(fetch_zip(year)) as zf:
        raw = _read_sheet(zf, "solar")
    if raw.empty:
        return pd.DataFrame(columns=["plant_id", "generator_id", "array_type",
                                     "module_type", "tilt", "azimuth"])

    def _yn(col):
        return raw.get(col, pd.Series("", index=raw.index)).astype(str).str.strip().str.upper().eq("Y")

    tracker = _yn("Single-Axis Tracking?") | _yn("Dual-Axis Tracking?")
    thin = (_yn("Thin-Film (CdTe)?") | _yn("Thin-Film (A-Si)?")
            | _yn("Thin-Film (CIGS)?") | _yn("Thin-Film (Other)?"))
    out = pd.DataFrame({
        "plant_id": pd.to_numeric(raw.get("Plant Code"), errors="coerce").astype("Int64"),
        "generator_id": raw.get("Generator ID").astype(str).str.strip(),
        "array_type": ["1-Axis Tracker" if t else "Fixed - Open Rack" for t in tracker],
        "module_type": ["Thin film" if t else "Standard" for t in thin],
        "tilt": pd.to_numeric(raw.get("Tilt Angle"), errors="coerce"),
        "azimuth": pd.to_numeric(raw.get("Azimuth Angle"), errors="coerce"),
    }).dropna(subset=["plant_id"])
    paths.EIA_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache, index=False)
    return out


def solar_plants(region="ercot", years=None) -> pd.DataFrame:
    """Plant-level solar PV sites with coordinates — one row per plant_id.

    EIA-860 rows are per-generator; this aggregates to the plant: summed
    ``nameplate_mw``, the plant's name/county/lat/long, and the mounting/module
    attributes of the plant's largest generator (from the Solar schedule, so the
    forecast can auto-set array/module/tilt/azimuth). Used to drive the
    solar-forecast project picker. Loads the latest available vintage only.
    """
    yrs = years or available_years(region)
    if not yrs:
        return pd.DataFrame()
    df = load(years=[max(yrs)], region=region)
    if df.empty:
        return df
    fuel = df.get("fuel_category", pd.Series("", index=df.index)).astype(str)
    tech = df.get("technology", pd.Series("", index=df.index)).astype(str)
    mask = fuel.str.lower().eq("solar") | tech.str.contains("Solar Photovolt", case=False, na=False)
    s = df[mask].dropna(subset=["latitude", "longitude"]).copy()
    if s.empty:
        return s

    # Attach mounting/module attributes per generator from the Solar schedule.
    s["generator_id"] = s["generator_id"].astype(str).str.strip()
    sched = solar_schedule(years=yrs)
    if not sched.empty:
        s = s.merge(sched, on=["plant_id", "generator_id"], how="left")
    # Generators absent from the Solar schedule (often proposed) default to a
    # fixed open-rack, crystalline-silicon system; tilt/azimuth left NaN (the
    # forecast falls back to 25°/180° at use).
    for col in ("array_type", "module_type", "tilt", "azimuth"):
        if col not in s.columns:
            s[col] = pd.NA
    s["array_type"] = s["array_type"].fillna("Fixed - Open Rack")
    s["module_type"] = s["module_type"].fillna("Standard")
    s["nameplate_mw"] = pd.to_numeric(s["nameplate_mw"], errors="coerce")

    # Plant-level: dominant (largest) generator sets the array/module/orientation.
    s = s.sort_values("nameplate_mw", ascending=False)
    g = s.groupby("plant_id", as_index=False).agg(
        plant_name=("plant_name", "first"),
        county=("county", "first"),
        latitude=("latitude", "first"),
        longitude=("longitude", "first"),
        nameplate_mw=("nameplate_mw", "sum"),
        array_type=("array_type", "first"),
        module_type=("module_type", "first"),
        tilt=("tilt", "first"),
        azimuth=("azimuth", "first"),
    )
    return g.sort_values("plant_name").reset_index(drop=True)


def wind_plants(region="ercot", years=None) -> pd.DataFrame:
    """Plant-level onshore-wind sites with coordinates — one row per plant_id.

    EIA-860 rows are per-generator; this aggregates to the plant: summed
    ``nameplate_mw`` and the plant's name/county/lat/long. Used to bridge a
    wind-forecast coordinate to its EIA plant_id (and thence, via the SCED↔EIA
    crosswalk, to actual ERCOT generation). Loads the latest available vintage.
    """
    yrs = years or available_years(region)
    if not yrs:
        return pd.DataFrame()
    df = load(years=[max(yrs)], region=region)
    if df.empty:
        return df
    fuel = df.get("fuel_category", pd.Series("", index=df.index)).astype(str)
    tech = df.get("technology", pd.Series("", index=df.index)).astype(str)
    mask = fuel.str.lower().eq("wind") | tech.str.contains("Wind", case=False, na=False)
    w = df[mask].dropna(subset=["latitude", "longitude"]).copy()
    if w.empty:
        return w
    w["nameplate_mw"] = pd.to_numeric(w["nameplate_mw"], errors="coerce")
    g = w.groupby("plant_id", as_index=False).agg(
        plant_name=("plant_name", "first"),
        county=("county", "first"),
        latitude=("latitude", "first"),
        longitude=("longitude", "first"),
        nameplate_mw=("nameplate_mw", "sum"),
    )
    return g.sort_values("plant_name").reset_index(drop=True)


if __name__ == "__main__":
    yr = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    df = build_year(yr, force_download="--force" in sys.argv)
    print(f"EIA-860 {yr} ERCOT: {len(df):,} generators | {df['plant_id'].nunique()} plants | "
          f"{df['nameplate_mw'].sum():,.0f} MW nameplate")
    print(df.groupby(["status_group", "fuel_category"])["nameplate_mw"].sum().round(0)
          .sort_values(ascending=False).head(12))
