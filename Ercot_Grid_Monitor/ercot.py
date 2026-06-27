"""Thin ERCOT settlement-point price access via the open-source `gridstatus`
library. SPP needs no API key — gridstatus reads ERCOT's public MIS reports.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logging.getLogger("gridstatus").setLevel(logging.WARNING)

# market label -> gridstatus Markets enum name
MARKETS = {"RT15": "REAL_TIME_15_MIN", "DAM": "DAY_AHEAD_HOURLY"}

# ercot-suite Data Hub data lake (override with ERCOT_HUB_DATA). This app lives at
# ercot-suite/Ercot_Grid_Monitor, so the lake is the sibling Ercot_Data_Hub/data.
# The lake stores RTM 15-min and DAM hourly prices for TRADING HUBS only — load
# zones aren't kept there, so those always fall back to a live pull.
DATA_LAKE = Path(os.environ.get(
    "ERCOT_HUB_DATA",
    Path(__file__).resolve().parent.parent / "Ercot_Data_Hub" / "data"))
RT_HUB_STORE = DATA_LAKE / "hub_prices" / "ercot_hub_prices_15min.parquet"
DAM_HUB_STORE = DATA_LAKE / "hub_prices" / "ercot_hub_dam_hourly.parquet"

_iso = None


def _ercot():
    global _iso
    if _iso is None:
        import gridstatus
        _iso = gridstatus.Ercot()
    return _iso


def _to_naive_central(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    try:
        if getattr(s.dt, "tz", None) is not None:
            return s.dt.tz_convert("US/Central").dt.tz_localize(None)
    except Exception:
        pass
    return s


def fetch_spp(locations, location_type: str, market: str,
              start, end) -> pd.DataFrame:
    """Settlement-point prices over [start, end] (delivery days), tidy long.

    Columns: location, interval_start (naive Central), spp ($/MWh).
    """
    import gridstatus

    iso = _ercot()
    mkt = getattr(gridstatus.Markets, MARKETS[market])
    start = pd.Timestamp(start).normalize()
    end_day = pd.Timestamp(end).normalize()
    # get_spp date conventions differ by market:
    #   RT: Interval Start in [date, end)   -> [start, end_day+1)
    #   DAM: delivery days in (date, end]    -> (start-1, end_day]
    if market == "DAM":
        q_date, q_end = start - pd.Timedelta(days=1), end_day
    else:
        q_date, q_end = start, end_day + pd.Timedelta(days=1)

    df = iso.get_spp(date=q_date, end=q_end, market=mkt,
                     locations=list(locations), location_type=location_type)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["location", "interval_start", "spp"])
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "location": df["Location"].astype(str),
        "interval_start": _to_naive_central(df["Interval Start"]),
        "spp": pd.to_numeric(df["SPP"], errors="coerce"),
    })
    return (out.dropna(subset=["spp"])
               .sort_values(["location", "interval_start"]).reset_index(drop=True))


def lake_prices(locations, location_type: str, market: str, start, end) -> pd.DataFrame:
    """Prices from the ercot-suite data lake (Trading Hub only), tidy long.

    Columns: location, interval_start (naive Central), spp. Empty frame if the
    lake has nothing for this request (missing store, no rows, or not a hub).
    """
    cols = ["location", "interval_start", "spp"]
    if location_type != "Trading Hub":
        return pd.DataFrame(columns=cols)  # lake keeps hubs only
    start = pd.Timestamp(start).normalize()
    end_excl = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)

    if market == "RT15":
        if not RT_HUB_STORE.exists():
            return pd.DataFrame(columns=cols)
        df = pd.read_parquet(RT_HUB_STORE)
        df = df[df["settlement_point"].isin(list(locations))]
        if df.empty:
            return pd.DataFrame(columns=cols)
        ie = pd.to_datetime(df["interval_ending_central"])
        out = pd.DataFrame({
            "location": df["settlement_point"].astype(str),
            "interval_start": ie - pd.Timedelta(minutes=15),  # store is interval-ending
            "spp": pd.to_numeric(df["price"], errors="coerce"),
        })
    else:  # DAM
        if not DAM_HUB_STORE.exists():
            return pd.DataFrame(columns=cols)
        df = pd.read_parquet(DAM_HUB_STORE)
        df = df[df["location"].isin(list(locations))]
        if df.empty:
            return pd.DataFrame(columns=cols)
        out = pd.DataFrame({
            "location": df["location"].astype(str),
            "interval_start": pd.to_datetime(df["interval_start"]),
            "spp": pd.to_numeric(df["spp"], errors="coerce"),
        })

    out = out[(out["interval_start"] >= start) & (out["interval_start"] < end_excl)]
    return (out.dropna(subset=["spp"])
               .sort_values(["location", "interval_start"]).reset_index(drop=True))


def get_prices(locations, location_type: str, market: str, start, end,
               prefer_lake: bool = True) -> tuple[pd.DataFrame, str]:
    """Prefer the data lake; fall back to a live gridstatus pull for anything it
    doesn't cover. Returns (tidy_df, source_label)."""
    if prefer_lake:
        df = lake_prices(locations, location_type, market, start, end)
        if not df.empty:
            return df, "data lake"
    return fetch_spp(locations, location_type, market, start, end), "live (gridstatus)"


def latest_price(location: str, location_type: str, market: str):
    """(value, interval_start) of the most recent price, or (None, None)."""
    today = pd.Timestamp.now(tz="US/Central").date()
    start = pd.Timestamp(today) - pd.Timedelta(days=1)
    df = fetch_spp([location], location_type, market, start, pd.Timestamp(today))
    if df.empty:
        return None, None
    row = df.iloc[-1]
    return float(row["spp"]), row["interval_start"]
