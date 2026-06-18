"""Canonical ERCOT settlement-point reference lists (single source of truth).

Three settlement-point types carry a price (SPP):
  - Resource Node  -> an individual generator's node (also has generation)
  - Trading Hub    -> a price index (price only; no generation)
  - Load Zone      -> a zonal price (price only; no generation)

Before the merge, ``system_gen/settlement_points.py`` and
``hub_prices/ercot_api.py`` each hard-coded their own copy of the 7 trading
hubs. They now both import HUBS from here.
"""

from __future__ import annotations

import pandas as pd

LOCATION_TYPES = ["Resource Node", "Trading Hub", "Load Zone"]
PRICE_ONLY_TYPES = ["Trading Hub", "Load Zone"]  # no generation

# Canonical ERCOT settlement points (verified via get_spp). Refresh with refresh().
HUBS = [
    "HB_BUSAVG", "HB_HOUSTON", "HB_HUBAVG", "HB_NORTH", "HB_PAN", "HB_SOUTH", "HB_WEST",
]
ZONES = [
    "LZ_AEN", "LZ_CPS", "LZ_HOUSTON", "LZ_LCRA", "LZ_NORTH", "LZ_RAYBN", "LZ_SOUTH", "LZ_WEST",
]


def locations(location_type: str) -> list[str]:
    if location_type == "Trading Hub":
        return list(HUBS)
    if location_type == "Load Zone":
        return list(ZONES)
    raise ValueError(f"Use resource_catalog for Resource Node; got {location_type!r}.")


def refresh(location_type: str) -> list[str]:
    """Fetch the current settlement-point names for a type from ERCOT (DAM, 1 day)."""
    import gridstatus

    from ercot_core.gridstatus_client import ercot

    from ercot_core import tz

    iso = ercot()
    s = (tz.now_central().tz_localize(None) - pd.Timedelta(days=2)).normalize()
    df = iso.get_spp(date=s, end=s + pd.Timedelta(days=1),
                     market=gridstatus.Markets.DAY_AHEAD_HOURLY, location_type=location_type)
    df.columns = [c.strip() for c in df.columns]
    return sorted(df["Location"].astype(str).unique().tolist())


if __name__ == "__main__":
    for lt in PRICE_ONLY_TYPES:
        print(lt, "(refreshed):", refresh(lt))
