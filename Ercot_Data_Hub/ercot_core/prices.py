"""Settlement-point price access that prefers the right source per location.

Key timing fact: ERCOT's free gridstatus get_spp only serves *recent* dates,
but SCED generation lags ~60 days — so for the windows where generation exists,
gridstatus prices are gone. The hub_prices dataset (direct ERCOT API archive)
keeps the *full* 15-min RTM history for the trading hubs, so hub prices should
come from that store.

This module exposes the hub store in the node_prices tidy schema so the
settlement page can use it directly. Node/zone prices and DAM still come from
gridstatus (node_prices) where available.
"""

from __future__ import annotations

import pandas as pd

from ercot_core import paths

PRICE_COLUMNS = [
    "interval_start", "interval_end", "location", "location_type", "market",
    "spp", "source", "fetched_at",
]


def hub_store_prices(locations: list[str], start, end_excl) -> pd.DataFrame:
    """RT15 hub prices from the hub_prices store, in node_prices tidy schema.

    The store keeps interval-ENDING timestamps; we convert to interval-START to
    match generation and the node_prices schema.
    """
    if not paths.HUB_PRICES_PARQUET.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    df = pd.read_parquet(paths.HUB_PRICES_PARQUET)
    df = df[df["settlement_point"].isin(locations)]
    if df.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    ie = pd.to_datetime(df["interval_ending_central"])
    out = pd.DataFrame()
    out["interval_start"] = ie - pd.Timedelta(minutes=15)
    out["interval_end"] = ie
    out["location"] = df["settlement_point"].astype(str)
    out["location_type"] = "Trading Hub"
    out["market"] = "RT15"
    out["spp"] = pd.to_numeric(df["price"], errors="coerce")
    out["source"] = "ercot_hub_store"
    out["fetched_at"] = pd.Timestamp.now(tz="UTC")
    # Carry the ERCOT DST flag so settlement can disambiguate the duplicated
    # fall-back hour exactly (naive interval_start repeats there).
    cols = list(PRICE_COLUMNS)
    if "dst_flag" in df.columns:
        out["dst_flag"] = df["dst_flag"].to_numpy()
        cols = cols + ["dst_flag"]

    start = pd.Timestamp(start)
    end_excl = pd.Timestamp(end_excl)
    out = out[(out["interval_start"] >= start) & (out["interval_start"] < end_excl)]
    return out[cols].sort_values(["location", "interval_start"]).reset_index(drop=True)


def hub_store_coverage() -> tuple | None:
    """(min_interval_start, max_interval_start) of the hub store, or None."""
    if not paths.HUB_PRICES_PARQUET.exists():
        return None
    df = pd.read_parquet(paths.HUB_PRICES_PARQUET, columns=["interval_ending_central"])
    if df.empty:
        return None
    ie = pd.to_datetime(df["interval_ending_central"])
    return (ie.min() - pd.Timedelta(minutes=15), ie.max() - pd.Timedelta(minutes=15))
