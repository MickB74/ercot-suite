"""Locate the shared ERCOT Data Hub and read Azure Sky Wind's cached data.

The Data Hub (sibling repo ``Ercot_Data_Hub``) owns the engine and the data
lake. This portal does not duplicate either — it imports the Hub's
``ercot_core`` package and reads the parquet files the Hub has already pulled
and cached:

  * HB_NORTH RT15 hub prices  — ``ercot_core.prices.hub_store_prices`` over the
    Hub's ``ercot_hub_prices_15min`` store.
  * Azure generation          — the four ``VORTEX_WIND1..4`` SCED unit files in
    the Hub's ``plant_sced`` lake, aggregated here into clean 15-minute MW.

No live ERCOT fetch happens here, so the portal loads instantly and works
offline.

Resolution order for the Hub root (first that exists wins):
  1. ``$AZURE_HUB_ROOT`` environment variable
  2. a sibling directory ``../Ercot_Data_Hub``
  3. ``~/Documents/Github/Ercot_Data_Hub``
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

_THIS = Path(__file__).resolve()


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("AZURE_HUB_ROOT")
    if env:
        roots.append(Path(env).expanduser())
    roots.append(_THIS.parents[2] / "Ercot_Data_Hub")          # ../Ercot_Data_Hub
    roots.append(Path.home() / "Documents" / "Github" / "Ercot_Data_Hub")
    seen, out = set(), []
    for r in roots:
        rp = r.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


@lru_cache(maxsize=1)
def hub_root() -> Path:
    """Absolute path to the Ercot_Data_Hub repo, or raise a clear error."""
    for r in _candidate_roots():
        if (r / "ercot_core").is_dir():
            return r
    tried = "\n  ".join(str(r) for r in _candidate_roots())
    raise FileNotFoundError(
        "Could not find the shared ERCOT Data Hub (the repo that owns the engine "
        "and the cached Azure Sky data). Tried:\n  " + tried +
        "\n\nKeep `Ercot_Data_Hub` as a sibling of this repo, or set the "
        "AZURE_HUB_ROOT environment variable to its path."
    )


@lru_cache(maxsize=1)
def _wire_engine() -> None:
    """Put the Hub's ``ercot_core`` (engine) and ``app`` (exporter) on sys.path."""
    root = hub_root()
    for p in (str(root), str(root / "app")):
        if p not in sys.path:
            sys.path.insert(0, p)


def core():
    """Return the Hub's ``ercot_core`` package with the submodules the portal uses.

    The package ``__init__`` doesn't eagerly import its submodules, so we import
    them here — then ``core().settlement`` etc. resolve.
    """
    _wire_engine()
    import ercot_core  # noqa: PLC0415
    import ercot_core.settlement  # noqa: F401,PLC0415
    import ercot_core.prices  # noqa: F401,PLC0415
    import ercot_core.invoice  # noqa: F401,PLC0415
    import ercot_core.tz  # noqa: F401,PLC0415
    import ercot_core.paths  # noqa: F401,PLC0415
    return ercot_core


def datasets():
    """Return the Hub pull modules a data refresh needs.

    ``(sced_plants, hub_price_pull, sced_disclosure)`` — the pieces to top up the
    VORTEX SCED units and the HB_NORTH hub-price store. These hit the live ERCOT
    API, so this is only used by ``refresh.py`` (run in the Hub's venv), never by
    the display pages.
    """
    _wire_engine()
    root = hub_root()
    for sub in ("datasets/plant_sced", "datasets/hub_prices"):
        p = str(root / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import sced_plants  # noqa: PLC0415
    from ercot_core import sced_disclosure  # noqa: PLC0415
    try:
        import hub_prices as hub_price_pull  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — name varies; refresh handles a None
        hub_price_pull = None
    return sced_plants, hub_price_pull, sced_disclosure


def export_block():
    """Return the Hub's polished export helper (CSV/Excel/Markdown/PDF), or None."""
    _wire_engine()
    try:
        import _export  # noqa: PLC0415
        return _export.download_block
    except Exception:  # noqa: BLE001 — exporter is a nicety, not a requirement
        return None


# --------------------------------------------------------------------------- #
# Generation — aggregate the four VORTEX SCED units into 15-minute MW
# --------------------------------------------------------------------------- #

def _plant_dir() -> Path:
    _wire_engine()
    from ercot_core import paths  # noqa: PLC0415
    return paths.PLANT_DATA_DIR


def _unit_15min(resource_name: str, year: int) -> pd.DataFrame:
    """One unit's telemetered output as clean 15-min MW (naive-Central interval).

    SCED disclosure publishes at irregular sub-15-minute timestamps. We floor to
    the 15-minute interval **in UTC** (DST-safe — no ambiguous fall-back hour),
    average the telemetry within each bucket (≈ time-weighted MW), then store the
    interval start as naive Central to match the Hub's ``node_generation`` schema.
    """
    path = _plant_dir() / f"{resource_name}_{year}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["resource_node", "resource_name", "interval_start", "mw"])
    df = pd.read_parquet(path, columns=["resource_name", "sced_timestamp",
                                        "telemetered_net_output"])
    if df.empty:
        return pd.DataFrame(columns=["resource_node", "resource_name", "interval_start", "mw"])
    ts = pd.to_datetime(df["sced_timestamp"], utc=True).dt.floor("15min")
    df = df.assign(interval_start=ts.dt.tz_convert("America/Chicago").dt.tz_localize(None))
    g = (df.groupby(["resource_name", "interval_start"], as_index=False)
           ["telemetered_net_output"].mean()
           .rename(columns={"telemetered_net_output": "mw"}))
    g["resource_node"] = "AZURE_SKY_WIND_AGG"
    return g[["resource_node", "resource_name", "interval_start", "mw"]]


def generation(resource_node: str, units: list[str],
               start: pd.Timestamp, end_excl: pd.Timestamp) -> pd.DataFrame:
    """15-min generation for the Azure Sky aggregate over [start, end_excl).

    Returns the engine's ``node_generation`` schema (``resource_node``,
    ``resource_name``, ``interval_start``, ``mw``) with one row per unit per
    interval, so :func:`ercot_core.settlement.compute_settlement` sums the units.
    """
    core()  # ensure paths importable
    frames = []
    for year in range(start.year, end_excl.year + 1):
        for unit in units:
            u = _unit_15min(unit, year)
            if not u.empty:
                frames.append(u)
    if not frames:
        return pd.DataFrame(columns=["resource_node", "resource_name", "interval_start", "mw"])
    out = pd.concat(frames, ignore_index=True)
    return out[(out["interval_start"] >= start) & (out["interval_start"] < end_excl)]


# --------------------------------------------------------------------------- #
# Prices — HB_NORTH RT15 from the Hub's hub-price store
# --------------------------------------------------------------------------- #

def hub_prices(location: str, start: pd.Timestamp, end_excl: pd.Timestamp,
               market: str = "RT15") -> pd.DataFrame:
    """RT15 settlement-point prices at a trading hub over [start, end_excl)."""
    c = core()
    return c.prices.hub_store_prices([location], start, end_excl)


@lru_cache(maxsize=1)
def available_locations() -> tuple[str, ...]:
    """Settlement points the portal can settle at — those cached in the hub-price store.

    These are the only locations with real RT15 prices on hand, so they're the
    only ones the Contract page offers as a settlement reference. Trading hubs
    (``HB_*``) are returned; the named averages (``HB_HUBAVG``/``HB_BUSAVG``)
    sort last. Empty tuple if the store hasn't been pulled yet.

    Note: the four VORTEX units aggregate to ``AZURE_SKY_WIND_AGG``, which has no
    settlement-point (resource-node) price of its own — only hub prices exist —
    so node-level settlement isn't an option here.
    """
    core()
    from ercot_core import paths  # noqa: PLC0415
    if not paths.HUB_PRICES_PARQUET.exists():
        return ()
    df = pd.read_parquet(paths.HUB_PRICES_PARQUET, columns=["settlement_point"])
    pts = sorted(df["settlement_point"].dropna().unique().tolist())
    avg = [p for p in pts if "AVG" in p.upper()]
    hubs = [p for p in pts if p not in avg]
    return tuple(hubs + avg)


# --------------------------------------------------------------------------- #
# Typical-year backbone (modelled) + EIA cross-check
# --------------------------------------------------------------------------- #

def wind_typical_hourly() -> pd.DataFrame | None:
    """Read the cached modelled **typical-year** hourly profile, or None.

    The Hub's ``plant_value`` step has already run the wind model for Azure Sky's
    coordinates / turbine fleet and cached an 8,760-hour AC profile at
    ``data/plant_value/windgen_AZURE_SKY_WIND_AGG_{year}_{cap}mw.parquet``. This
    is the forward estimate's physical backbone — read it offline, no live fetch.
    Returns an hourly frame with an ``ac_kw`` column (tz-aware index), or None.
    """
    core()
    from ercot_core import paths  # noqa: PLC0415
    matches = sorted(paths.PLANT_VALUE_DIR.glob("windgen_AZURE_SKY_WIND_AGG_*mw.parquet"))
    if not matches:
        return None
    df = pd.read_parquet(matches[-1])
    return df if "ac_kw" in df.columns else None


def eia_monthly_netgen(plant_id: int, start_year: int, end_year: int,
                       prime_mover: str | None = None) -> pd.DataFrame:
    """EIA-923 monthly net generation (MWh) for one plant, per (year, month).

    Reads the Hub's cached ``eia923_all_{year}.parquet`` files and sums
    ``netgen_mwh`` across the plant's rows. ``prime_mover`` (e.g. ``"WT"`` for
    wind) restricts to one prime mover — needed for co-located plants (Azure Sky
    is wind + battery under one EIA id). Columns ``year, month, eia_mwh``; empty
    if the plant isn't in the cached EIA data.
    """
    core()
    from ercot_core import paths  # noqa: PLC0415
    eia_dir = paths.EIA_DIR
    frames = []
    for year in range(start_year, end_year + 1):
        path = eia_dir / f"eia923_all_{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["year", "month", "plant_id",
                                            "prime_mover", "netgen_mwh"])
        df = df[df["plant_id"] == int(plant_id)]
        if prime_mover:
            df = df[df["prime_mover"].astype(str).str.upper().str.startswith(prime_mover.upper())]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["year", "month", "eia_mwh"])
    out = pd.concat(frames, ignore_index=True)
    g = out.groupby(["year", "month"], as_index=False)["netgen_mwh"].sum()
    return g.rename(columns={"netgen_mwh": "eia_mwh"})


# --------------------------------------------------------------------------- #
# Available data window
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=4)
def _gen_span(units: tuple[str, ...]):
    """(min, max) naive-Central interval over all cached VORTEX unit files."""
    pd_dir = _plant_dir()
    lo = hi = None
    for path in sorted(pd_dir.glob("VORTEX_WIND*_*.parquet")):
        if not any(path.name.startswith(u + "_") for u in units):
            continue
        df = pd.read_parquet(path, columns=["sced_timestamp"])
        if df.empty:
            continue
        ts = pd.to_datetime(df["sced_timestamp"], utc=True).dt.tz_convert(
            "America/Chicago").dt.tz_localize(None)
        mn, mx = ts.min(), ts.max()
        lo = mn if lo is None else min(lo, mn)
        hi = mx if hi is None else max(hi, mx)
    return lo, hi


@lru_cache(maxsize=4)
def _price_span(location: str):
    """(min, max) interval-start for a hub in the Hub's hub-price store."""
    core()
    from ercot_core import paths  # noqa: PLC0415
    if not paths.HUB_PRICES_PARQUET.exists():
        return None, None
    df = pd.read_parquet(paths.HUB_PRICES_PARQUET,
                         columns=["interval_ending_central", "settlement_point"])
    df = df[df["settlement_point"] == location]
    if df.empty:
        return None, None
    ie = pd.to_datetime(df["interval_ending_central"])
    return (ie.min() - pd.Timedelta(minutes=15)), ie.max()


def settlement_window(units: list[str], location: str):
    """(start_date, end_date) where BOTH metered generation and hub price exist.

    This is the span the customer can audit: every day in it settles on real
    metered output × the hub's real-time price.
    """
    g_lo, g_hi = _gen_span(tuple(units))
    p_lo, p_hi = _price_span(location)
    if None in (g_lo, g_hi, p_lo, p_hi):
        return None, None
    lo = max(g_lo, p_lo)
    hi = min(g_hi, p_hi)
    if lo > hi:
        return None, None
    return pd.Timestamp(lo).date(), pd.Timestamp(hi).date()
