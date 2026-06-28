"""ERCOT Settlement Points List & Electrical Buses Mapping (NP4-160-SG).

The authoritative crosswalk: resource node (settlement point) -> electrical bus,
substation, settlement load zone, hub bus, voltage. ~1,024 resource nodes. We
cache it as a parquet (refreshed weekly with ERCOT's Model DB Load) so the app
doesn't hit ERCOT every run. Needs no API key — gridstatus reads the public MIS
report (RTID 10008).

This gives node -> substation / load zone, NOT lat/lon. We still place pins from
plant POI coordinates (coords.NODE_COORDS); this layer adds authoritative
attributes and lets us validate those placements.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from ercot import NODE_DATA_DIR

MAP_STORE = NODE_DATA_DIR.parent / "settlement_point_bus_map.parquet"
KEEP = ["Resource Node", "Substation", "Settlement Load Zone", "Hub",
        "Electrical Bus", "PSSE Bus Number", "Voltage Level", "Publish Date"]
_RENAME = {
    "Resource Node": "node", "Substation": "substation",
    "Settlement Load Zone": "load_zone", "Hub": "hub_bus",
    "Electrical Bus": "electrical_bus", "PSSE Bus Number": "psse_bus",
    "Voltage Level": "kv", "Publish Date": "publish_date",
}


def _fetch() -> pd.DataFrame:
    import gridstatus
    df = gridstatus.Ercot().get_settlement_points_electrical_bus_mapping(date="latest")
    df = df.dropna(subset=["Resource Node"]).copy()
    df = df[[c for c in KEEP if c in df.columns]].rename(columns=_RENAME)
    # one row per resource node (a node can span several electrical buses)
    return df.drop_duplicates(subset=["node"]).reset_index(drop=True)


def load_mapping(max_age_days: int = 7, refresh: bool = False) -> pd.DataFrame:
    """Cached NP4-160 node mapping (one row per resource node). Refreshes when the
    cache is missing, stale, or refresh=True; falls back to the stale cache if the
    live fetch fails."""
    fresh = False
    if MAP_STORE.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            MAP_STORE.stat().st_mtime, tz=timezone.utc)
        fresh = age < timedelta(days=max_age_days)
    if not refresh and fresh:
        return pd.read_parquet(MAP_STORE)
    try:
        df = _fetch()
        df.to_parquet(MAP_STORE, index=False)
        return df
    except Exception:
        if MAP_STORE.exists():
            return pd.read_parquet(MAP_STORE)
        return pd.DataFrame(columns=list(_RENAME.values()))


def node_attrs() -> dict[str, dict]:
    """{node -> {substation, load_zone, kv, ...}} for quick per-node lookup."""
    df = load_mapping()
    return {r["node"]: r for r in df.to_dict("records")}
