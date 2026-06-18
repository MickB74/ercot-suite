"""Locate the shared ERCOT Data Hub and read Markum Solar's cached data.

The Data Hub (sibling repo ``Ercot_Data_Hub``) owns the engine and the data
lake. This portal does not duplicate either — it imports the Hub's
``ercot_core`` package and reads the parquet files the Hub has already pulled
and cached for the Markum node. No live ERCOT fetch happens here, so the portal
loads instantly and works offline.

Resolution order for the Hub root (first that exists wins):
  1. ``$MARKUM_HUB_ROOT`` environment variable
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
    env = os.environ.get("MARKUM_HUB_ROOT")
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
        "and the cached Markum data). Tried:\n  " + tried +
        "\n\nKeep `Ercot_Data_Hub` as a sibling of this repo, or set the "
        "MARKUM_HUB_ROOT environment variable to its path."
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
# Data-lake readers — Markum node generation & node prices (RT15)
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


def solar_tmy_hourly(resource_name: str, capacity_kw: float) -> pd.DataFrame | None:
    """Read the cached PVWatts **TMY** hourly AC profile for the plant, or None.

    The Data Hub's ``plant_value`` step has already run PVWatts on NSRDB Typical-
    Meteorological-Year weather for Markum's coordinates/system and cached the
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


def eia_monthly_netgen(plant_id: int, start_year: int, end_year: int,
                       prime_mover: str | None = None) -> pd.DataFrame:
    """EIA-923 monthly **net generation** (MWh) for one plant, per (year, month).

    Reads the Hub's cached ``eia923_all_{year}.parquet`` files and sums
    ``netgen_mwh`` across the plant's rows. ``prime_mover`` (e.g. ``"PV"`` for
    solar) restricts to one prime mover — needed for co-located plants under one
    EIA id. Returns columns ``year, month, eia_mwh`` — empty if the plant isn't
    in the cached EIA data for that span.
    """
    core()  # ensure paths importable
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


def settlement_window(resource_node: str):
    """(start_date, end_date) where BOTH metered generation and node price exist.

    This is the span the customer can audit: every day in it settles on real
    metered output × the node's real-time price.
    """
    core()
    g_lo, g_hi = _available_span(resource_node, "resource_node", "node_generation_{year}.parquet")
    p_lo, p_hi = _available_span(resource_node, "location", "node_price_{year}.parquet")
    if None in (g_lo, g_hi, p_lo, p_hi):
        return None, None
    lo = max(g_lo, p_lo)
    hi = min(g_hi, p_hi)
    return pd.Timestamp(lo).date(), pd.Timestamp(hi).date()
