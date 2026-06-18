"""Load ERCOT historical hub prices and tag them for heat-rate bucketing.

Source: the shared ``ercot_hub_prices_15min.parquet`` lake (RTM 15-min SPP for
the trading hubs, 2020->present). We localize to tz-aware Central using the
ERCOT ``dst_flag`` so the fall-back hour never double-counts, then derive the
calendar features the heat-rate and shaping layers need.

Peak definition: ERCOT 5x16 on-peak block = Mon-Fri, hours-ending 7..22
(interval-start hour 6..21). Everything else (nights + weekends) is off-peak.
NERC holidays are treated as on-peak in v1 (documented simplification).
"""

from __future__ import annotations

import pandas as pd

import pf_paths
import pf_tz

HUBS = ["HB_NORTH", "HB_HOUSTON", "HB_SOUTH", "HB_WEST", "HB_PAN",
        "HB_BUSAVG", "HB_HUBAVG"]

PEAK_START_HOUR = 6   # interval-start; HE 7
PEAK_END_HOUR = 22    # exclusive interval-start; through HE 22


def _peak_mask(ts_central: pd.Series) -> pd.Series:
    """Boolean on-peak mask (5x16) from tz-aware Central interval-start."""
    wd = ts_central.dt.dayofweek  # Mon=0 .. Sun=6
    hr = ts_central.dt.hour
    return (wd < 5) & (hr >= PEAK_START_HOUR) & (hr < PEAK_END_HOUR)


def load_rt15(hub: str = "HB_NORTH", start=None, end=None) -> pd.DataFrame:
    """RTM 15-min prices for one hub, tagged with calendar + peak features.

    Returns columns: ts (tz-aware Central interval-START), date, year, month,
    hour, is_peak, price.
    """
    pq = pf_paths.hub_prices_parquet()
    if pq is None:
        raise FileNotFoundError(
            "No ercot_hub_prices_15min.parquet found. Set hub_lake_dir in "
            "config.json or check that the Data Hub lake exists."
        )
    df = pd.read_parquet(pq)
    df = df[df["settlement_point"] == hub].copy()
    if df.empty:
        raise ValueError(f"No rows for hub {hub!r}. Available: {sorted(df['settlement_point'].unique())}")

    # interval_ending_central is naive Central; subtract 15 min for start.
    end_central = pf_tz.localize_central(df["interval_ending_central"],
                                         flags=df.get("dst_flag"))
    ts = end_central - pd.Timedelta(minutes=15)

    out = pd.DataFrame({
        "ts": ts.to_numpy(),
        "price": pd.to_numeric(df["price"], errors="coerce").to_numpy(),
    })
    out["ts"] = pd.to_datetime(out["ts"], utc=True).dt.tz_convert(pf_tz.CENTRAL)
    out = out.dropna(subset=["price"]).sort_values("ts").reset_index(drop=True)

    out["date"] = out["ts"].dt.tz_localize(None).dt.normalize()
    out["year"] = out["ts"].dt.year
    out["month"] = out["ts"].dt.month
    out["hour"] = out["ts"].dt.hour
    out["is_peak"] = _peak_mask(out["ts"]).to_numpy()

    if start is not None:
        out = out[out["ts"] >= pd.Timestamp(start, tz=pf_tz.CENTRAL)]
    if end is not None:
        out = out[out["ts"] < pd.Timestamp(end, tz=pf_tz.CENTRAL)]
    return out.reset_index(drop=True)


def daily_mean(rt15: pd.DataFrame) -> pd.DataFrame:
    """Daily ATC (all-hours) mean price — for aligning to daily gas spot."""
    g = rt15.groupby("date", as_index=False)["price"].mean()
    g["date"] = pd.to_datetime(g["date"])
    return g


def monthly_block_mean(rt15: pd.DataFrame) -> pd.DataFrame:
    """Mean price by (year, month, block) where block in {peak, offpeak, atc}."""
    frames = []
    for label, mask in (("peak", rt15["is_peak"]),
                        ("offpeak", ~rt15["is_peak"]),
                        ("atc", pd.Series(True, index=rt15.index))):
        sub = rt15[mask]
        g = sub.groupby(["year", "month"], as_index=False)["price"].mean()
        g["block"] = label
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def hourly_shape(rt15: pd.DataFrame) -> pd.DataFrame:
    """Normalized hour-of-day x month shape factor (mean=1.0 within each month).

    Used to spread a monthly strip price across an 8760 hourly profile. Computed
    on positive-clipped prices so a few negative intervals don't invert the
    shape; the level is restored later by rescaling to the monthly mean.
    """
    df = rt15.copy()
    df["p"] = df["price"].clip(lower=0.0)
    hourly = df.groupby(["month", "hour"], as_index=False)["p"].mean()
    mmean = hourly.groupby("month")["p"].transform("mean")
    hourly["shape"] = hourly["p"] / mmean.replace(0, pd.NA)
    hourly["shape"] = hourly["shape"].fillna(1.0)
    return hourly[["month", "hour", "shape"]]


def coverage(rt15: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    return rt15["ts"].min(), rt15["ts"].max(), len(rt15)
