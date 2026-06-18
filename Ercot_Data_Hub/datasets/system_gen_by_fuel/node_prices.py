"""Pull Settlement Point Prices at any ERCOT settlement point.

Uses gridstatus `get_spp` for the real-time 15-minute and day-ahead hourly
markets. Works for `location_type` of 'Resource Node', 'Trading Hub', or
'Load Zone' — the location name is the settlement point itself.

Tidy long schema:
    interval_start (naive CPT), interval_end, location, location_type, market,
    spp ($/MWh), source, fetched_at
"""

from __future__ import annotations

import logging

import pandas as pd

logging.getLogger("gridstatus").setLevel(logging.WARNING)

SOURCE = "ercot_spp"
PRICE_COLUMNS = [
    "interval_start", "interval_end", "location", "location_type", "market", "spp",
    "source", "fetched_at",
]

# market label -> gridstatus Markets enum name
MARKETS = {
    "RT15": "REAL_TIME_15_MIN",
    "DAM": "DAY_AHEAD_HOURLY",
}


def _to_naive_cpt(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("US/Central").dt.tz_localize(None)
    return s


def fetch_prices(
    locations: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    location_type: str = "Resource Node",
    markets: list[str] | None = None,
    fetched_at: pd.Timestamp | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Settlement Point Prices for `locations` of `location_type` over [start, end]."""
    import gridstatus

    fetched_at = fetched_at or pd.Timestamp.now(tz="UTC")
    markets = markets or ["RT15", "DAM"]
    start = pd.Timestamp(start).normalize()
    end_day = pd.Timestamp(end).normalize()  # inclusive delivery day requested
    iso = gridstatus.Ercot()

    frames = []
    for label in markets:
        if label not in MARKETS:
            raise ValueError(f"Unknown market '{label}'. Choose from {list(MARKETS)}.")
        market = getattr(gridstatus.Markets, MARKETS[label])
        # get_spp date conventions differ by market (verified against ERCOT):
        #   RT markets: Interval Start in [date, end)  -> [start, end_day+1)
        #   DAM:        delivery days in (date, end]    -> (start-1, end_day]
        if label == "DAM":
            q_date, q_end = start - pd.Timedelta(days=1), end_day
        else:
            q_date, q_end = start, end_day + pd.Timedelta(days=1)
        if verbose:
            print(f"  [price] {label} ({MARKETS[label]}) delivery {start.date()} -> {end_day.date()} ...")
        try:
            df = iso.get_spp(
                date=q_date, end=q_end, market=market,
                locations=locations, location_type=location_type,
            )
        except Exception as e:
            if verbose:
                print(f"    {label} fetch failed: {e}")
            continue
        if df is None or df.empty:
            continue

        df.columns = [c.strip() for c in df.columns]
        out = pd.DataFrame()
        out["interval_start"] = _to_naive_cpt(df["Interval Start"])
        out["interval_end"] = _to_naive_cpt(df["Interval End"])
        out["location"] = df["Location"].astype(str)
        out["location_type"] = location_type
        out["market"] = label
        out["spp"] = pd.to_numeric(df["SPP"], errors="coerce")
        frames.append(out)

    if not frames:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    out = pd.concat(frames, ignore_index=True)
    out["source"] = SOURCE
    out["fetched_at"] = pd.to_datetime(fetched_at, utc=True)
    out["spp"] = pd.to_numeric(out["spp"], downcast="float")
    return out[PRICE_COLUMNS].sort_values(["location", "market", "interval_start"])


if __name__ == "__main__":
    import sys
    node = sys.argv[1] if len(sys.argv) > 1 else "7RNCHSLR_ALL"
    from ercot_core import tz
    s = (tz.now_central().tz_localize(None) - pd.Timedelta(days=3)).normalize()
    e = s + pd.Timedelta(days=1)
    lt = sys.argv[2] if len(sys.argv) > 2 else "Resource Node"
    df = fetch_prices([node], s, e, location_type=lt)
    print(f"\n{node} ({lt}): {len(df):,} rows" if not df.empty else f"\n{node}: no data")
    if not df.empty:
        print(df.groupby("market").agg(n=("spp", "size"), mean_spp=("spp", "mean")).to_string())
        print(df.head(4).to_string(index=False))
