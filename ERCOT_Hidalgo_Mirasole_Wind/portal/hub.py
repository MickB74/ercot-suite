"""Locate the shared ERCOT Data Hub and read Hidalgo Mirasole Wind's cached data.

The Data Hub (sibling repo ``Ercot_Data_Hub``) owns the engine and the data
lake. This portal does not duplicate either — it imports the Hub's
``ercot_core`` package and reads the parquet files the Hub has already pulled
and cached for the Hidalgo Mirasole Wind node. No live ERCOT fetch happens here, so the portal
loads instantly and works offline.

Resolution order for the Hub root (first that exists wins):
  1. ``$HIDALGO_MIRASOLE_WIND_HUB_ROOT`` environment variable
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
    env = os.environ.get("HIDALGO_MIRASOLE_WIND_HUB_ROOT")
    if env:
        roots.append(Path(env).expanduser())
    roots.append(_THIS.parents[2] / "Ercot_Data_Hub")          # ../Ercot_Data_Hub
    roots.append(Path.home() / "Documents" / "Github" / "Ercot_Data_Hub")
    # de-dup while preserving order
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
        "and the cached Hidalgo Mirasole Wind data). Tried:\n  " + tried +
        "\n\nKeep `Ercot_Data_Hub` as a sibling of this repo, or set the "
        "HIDALGO_MIRASOLE_WIND_HUB_ROOT environment variable to its path."
    )


@lru_cache(maxsize=1)
def _wire_engine() -> None:
    """Put the Hub's ``ercot_core`` (engine) and ``app`` (exporter) on sys.path."""
    root = hub_root()
    for p in (str(root), str(root / "app")):
        if p not in sys.path:
            sys.path.insert(0, p)


def core():
    """Return the Hub's ``ercot_core`` package with submodules attached.

    The package ``__init__`` doesn't eagerly import its submodules, so we import
    the ones the portal uses here — then ``core().settlement`` etc. resolve.
    """
    _wire_engine()
    import ercot_core  # noqa: PLC0415
    import ercot_core.settlement  # noqa: F401,PLC0415
    import ercot_core.invoice  # noqa: F401,PLC0415
    import ercot_core.prices  # noqa: F401,PLC0415
    import ercot_core.tz  # noqa: F401,PLC0415
    import ercot_core.paths  # noqa: F401,PLC0415
    return ercot_core


def datasets():
    """Wire the Hub's ``system_gen_by_fuel`` dataset dir and return its pull modules.

    Returns ``(pull_nodes, node_generation, spp_archive, sced_disclosure)`` — the
    pieces a data refresh needs. These hit the live ERCOT API, so this is only
    used by ``refresh.py`` (run in the Hub's venv), never by the display pages.
    """
    _wire_engine()
    ds = hub_root() / "datasets" / "system_gen_by_fuel"
    if str(ds) not in sys.path:
        sys.path.insert(0, str(ds))
    import pull_nodes  # noqa: PLC0415
    import node_generation  # noqa: PLC0415
    from ercot_core import spp_archive, sced_disclosure  # noqa: PLC0415
    return pull_nodes, node_generation, spp_archive, sced_disclosure


def export_block():
    """Return the Hub's polished export helper (CSV/Excel/Markdown/PDF), or None."""
    _wire_engine()
    try:
        import _export  # noqa: PLC0415
        return _export.download_block
    except Exception:  # noqa: BLE001 — exporter is a nicety, not a requirement
        return None


# --------------------------------------------------------------------------- #
# Data-lake readers — Hidalgo Mirasole Wind node generation & node prices (RT15)
# --------------------------------------------------------------------------- #

def _node_dir() -> Path:
    from ercot_core import paths  # noqa: PLC0415
    return paths.NODE_DATA_DIR


def _read_years(template: str, key_col: str, key: str,
                start: pd.Timestamp, end_excl: pd.Timestamp) -> pd.DataFrame:
    """Concatenate ``template``-named yearly parquets, filtered to one key/window."""
    core()  # ensure paths importable
    nd = _node_dir()
    frames = []
    for year in range(start.year, end_excl.year + 1):
        path = nd / template.format(year=year)
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df = df[df[key_col] == key]
        df = df[(df["interval_start"] >= start) & (df["interval_start"] < end_excl)]
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def generation(resource_node: str, start: pd.Timestamp, end_excl: pd.Timestamp) -> pd.DataFrame:
    """15-min metered generation for the node over [start, end_excl)."""
    return _read_years("node_generation_{year}.parquet", "resource_node",
                       resource_node, start, end_excl)


def node_prices(resource_node: str, start: pd.Timestamp, end_excl: pd.Timestamp,
                market: str = "RT15") -> pd.DataFrame:
    """Settlement-point prices at the node over [start, end_excl) (RT15 by default)."""
    df = _read_years("node_price_{year}.parquet", "location",
                     resource_node, start, end_excl)
    if not df.empty and "market" in df.columns:
        df = df[df["market"] == market]
    return df


def hub_prices(location: str, start: pd.Timestamp, end_excl: pd.Timestamp,
               market: str = "RT15") -> pd.DataFrame:
    """RT15 settlement-point prices at a trading hub from the Hub's rich hub store.

    The node-price lake carries only sparse hub coverage; the full HB_* history
    lives in the Hub's ``ercot_hub_prices_15min`` store. Same schema as
    :func:`node_prices` (plus a harmless ``dst_flag``), so the settlement engine
    consumes either interchangeably.
    """
    c = core()
    return c.prices.hub_store_prices([location], start, end_excl)


def settlement_prices(location: str, start: pd.Timestamp, end_excl: pd.Timestamp,
                      market: str = "RT15") -> pd.DataFrame:
    """Prices at the settlement reference, routed to the right store.

    Trading hubs (``HB_*``) read the rich hub store; anything else is treated as a
    resource node and reads the node-price lake. This is the single entry point
    the pages and analytics use, so settlement follows the contract's chosen
    ``settle_point`` wherever it points.
    """
    if str(location).upper().startswith("HB_"):
        return hub_prices(location, start, end_excl, market)
    return node_prices(location, start, end_excl, market)


@lru_cache(maxsize=1)
def available_locations() -> tuple[str, ...]:
    """Settlement points the portal can settle at — the plant's node + cached hubs.

    The node (``MIRASOLE_GEN``) always leads; the trading hubs are those with
    cached RT15 prices in the Hub's hub store (so settlement always has a real
    price). Named averages (``HB_HUBAVG``/``HB_BUSAVG``) sort last.
    """
    core()
    from ercot_core import paths  # noqa: PLC0415
    node = "MIRASOLE_GEN"
    hubs: list[str] = []
    if paths.HUB_PRICES_PARQUET.exists():
        df = pd.read_parquet(paths.HUB_PRICES_PARQUET, columns=["settlement_point"])
        pts = sorted(df["settlement_point"].dropna().unique().tolist())
        avg = [p for p in pts if "AVG" in p.upper()]
        hubs = [p for p in pts if p not in avg] + avg
    return tuple([node] + [h for h in hubs if h != node])


def solar_tmy_hourly(resource_name: str, capacity_kw: float) -> pd.DataFrame | None:
    """Read the cached PVWatts **TMY** hourly AC profile for the plant, or None.

    The Data Hub's ``plant_value`` step has already run PVWatts on NSRDB Typical-
    Meteorological-Year weather for Hidalgo Mirasole Wind's coordinates/system and cached the
    8,760-hour result at ``data/plant_value/gen_{res}_tmy_{cap}kw.parquet``. This
    is the calibrated estimate's physical backbone — read it offline, no NREL key
    or live fetch needed. Returns an hourly frame with an ``ac_kw`` column
    (tz-aware Central index), or None if the Hub hasn't cached it yet.
    """
    core()  # ensure paths importable
    from ercot_core import paths  # noqa: PLC0415
    path = paths.PLANT_VALUE_DIR / f"gen_{resource_name}_tmy_{int(capacity_kw)}kw.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    return df if "ac_kw" in df.columns else None


def wind_typical_hourly() -> pd.DataFrame | None:
    """Read the cached wind-model typical-year hourly profile, or None."""
    core()
    from ercot_core import paths  # noqa: PLC0415
    import portal.contract as _c  # noqa: PLC0415
    res = _c.ASSET["resource_name"]
    matches = sorted(paths.PLANT_VALUE_DIR.glob(f"windgen_{res}_tmy_*mw.parquet"))
    if not matches:
        matches = sorted(paths.PLANT_VALUE_DIR.glob(f"windgen_{res}_*mw.parquet"))
    if not matches:
        return None
    df = pd.read_parquet(matches[-1])
    return df if "ac_kw" in df.columns else None


def eia_monthly_netgen(plant_id, start_year: int, end_year: int,
                       prime_mover: str | None = None) -> pd.DataFrame:
    """EIA-923 monthly **net generation** (MWh) per (year, month).

    ``plant_id`` may be a single id or a list/tuple of ids — multi-phase plants
    file under separate EIA ids (e.g. Hidalgo Wind Farm 57617 + Phase II 62618),
    and the ERCOT resource node spans them all, so summing the set is what
    reconciles against SCED. Reads the Hub's cached ``eia923_all_{year}.parquet``
    files and sums ``netgen_mwh``. ``prime_mover`` (e.g. ``"PV"``) restricts to one
    prime mover for co-located plants under one id. Returns ``year, month,
    eia_mwh`` — empty if none of the ids are in the cached EIA data for that span.
    """
    core()  # ensure paths importable
    from ercot_core import paths  # noqa: PLC0415
    ids = [int(p) for p in (plant_id if isinstance(plant_id, (list, tuple, set)) else [plant_id])]
    eia_dir = paths.EIA_DIR
    frames = []
    for year in range(start_year, end_year + 1):
        path = eia_dir / f"eia923_all_{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["year", "month", "plant_id",
                                            "prime_mover", "netgen_mwh"])
        df = df[df["plant_id"].isin(ids)]
        if prime_mover:
            df = df[df["prime_mover"].astype(str).str.upper().str.startswith(prime_mover.upper())]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["year", "month", "eia_mwh"])
    out = pd.concat(frames, ignore_index=True)
    g = out.groupby(["year", "month"], as_index=False)["netgen_mwh"].sum()
    return g.rename(columns={"netgen_mwh": "eia_mwh"})


@lru_cache(maxsize=8)
def _available_span(resource_node: str, key_col: str, template: str):
    nd = _node_dir()
    lo = hi = None
    for path in sorted(nd.glob(template.format(year="*"))):
        df = pd.read_parquet(path, columns=["interval_start", key_col])
        df = df[df[key_col] == resource_node]
        if df.empty:
            continue
        mn, mx = df["interval_start"].min(), df["interval_start"].max()
        lo = mn if lo is None else min(lo, mn)
        hi = mx if hi is None else max(hi, mx)
    return lo, hi


@lru_cache(maxsize=8)
def _hub_price_span(location: str):
    """(min, max) interval-start for a hub in the Hub's rich hub-price store."""
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


def settlement_window(resource_node: str, location: str | None = None):
    """(start_date, end_date) where BOTH metered generation and the price exist.

    This is the span the customer can audit: every day in it settles on real
    metered output × the settlement reference's real-time price. ``location`` is
    the settlement reference (defaults to the node); a hub location reads the
    rich hub store, the node reads the node-price lake.
    """
    core()
    loc = location or resource_node
    g_lo, g_hi = _available_span(resource_node, "resource_node", "node_generation_{year}.parquet")
    if str(loc).upper().startswith("HB_"):
        p_lo, p_hi = _hub_price_span(loc)
    else:
        p_lo, p_hi = _available_span(loc, "location", "node_price_{year}.parquet")
    if None in (g_lo, g_hi, p_lo, p_hi):
        return None, None
    lo = max(g_lo, p_lo)
    hi = min(g_hi, p_hi)
    if lo > hi:
        return None, None
    return pd.Timestamp(lo).date(), pd.Timestamp(hi).date()


def clear_data_caches() -> None:
    """Drop the cached data-span lookups after a refresh writes new parquet data.

    ``_available_span`` / ``_hub_price_span`` are ``@lru_cache``-d for speed, so a
    just-completed refresh isn't visible until they're cleared — otherwise the
    settlement window reports the pre-refresh span (e.g. a stale ``None``)."""
    _available_span.cache_clear()
    _hub_price_span.cache_clear()
