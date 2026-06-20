"""PPA settlement math: actual 15-min generation × market price vs a PPA price.

Combines this hub's two interval datasets for a single asset (resource node):
  * actual generation  — SCED telemetered net output, 15-min MW (node_generation)
  * market price        — Settlement Point Price, $/MWh, at the node OR a hub,
                          RT 15-min or Day-Ahead hourly (node_prices)

It reports the three standard PPA structures side by side so you can read off
whichever matches your contract:
  * Merchant      Σ gen_MWh × price                  (sell everything at market)
  * PPA           Σ gen_MWh × ppa                     (paid the strike per MWh)
  * CfD / swap    Σ gen_MWh × (price − ppa)           (offtaker frame: positive =>
                  offtaker receives [market above strike], negative => offtaker pays)

Plus volume, generation-weighted capture price, and (when both node and hub
prices are supplied) the basis settlement = Σ gen_MWh × (node − hub).

MWh per 15-min interval = average MW × 0.25 h. DAM (hourly) prices are matched to
each 15-min generation interval by the hour it falls in.
"""

from __future__ import annotations

import re

import pandas as pd

from ercot_core import fuels, paths, tz

# Each 15-min RTM interval is 0.25 h of *real elapsed time* — this is correct on
# DST days too (the spring-forward day has 23 h / 92 intervals, the fall-back day
# 25 h / 100 intervals; each interval is still 0.25 h). Do NOT "fix" this to vary
# by DST. The DST hazard is in the join *keys*, handled by lifting interval_start
# to tz-aware Central (see _aware) before any groupby / dedupe / merge so the two
# passes of the duplicated 01:00–02:00 fall-back hour stay distinct.
INTERVAL_HOURS = 0.25  # 15 minutes

# ERCOT repeated-hour / DST flag columns, in order of preference, that disambiguate
# the fall-back hour exactly when a frame carries one.
_FLAG_COLS = ("repeated_hour_flag", "dst_flag")


def _aware(df: pd.DataFrame, col: str = "interval_start") -> pd.Series:
    """interval timestamps as tz-aware Central (DST-correct join key)."""
    flags = next((df[c] for c in _FLAG_COLS if c in df.columns), None)
    return tz.localize_central(df[col], flags=flags)

# Battery / storage detection (so it can be excluded from a generation PPA).
_STORAGE_TYPES = {"ESR", "PWRSTR"}
_STORAGE_NAME = re.compile(r"(_ESR|BESS|_ESS\b|_BES\b|_STOR|BATT)", re.I)


def is_storage(resource_name, resource_type=None, fuel_group=None) -> bool:
    if fuel_group and str(fuel_group).strip().lower() == "storage":
        return True
    if resource_type and str(resource_type).strip().upper() in _STORAGE_TYPES:
        return True
    if resource_type and fuels.sced_fuel_group(resource_type) == "Storage":
        return True
    return bool(_STORAGE_NAME.search(str(resource_name)))


def node_units(resource_node: str) -> pd.DataFrame:
    """Units (SCED resource names) at a node, flagged storage vs generation.

    Columns: resource_name, resource_type, fuel_group, is_storage.
    Uses the resource-node catalog for the unit list and the plant registry
    (when present) for the authoritative type/fuel group.
    """
    cols = ["resource_name", "resource_type", "fuel_group", "is_storage"]
    if not paths.CATALOG_PATH.exists():
        return pd.DataFrame(columns=cols)
    cat = pd.read_parquet(paths.CATALOG_PATH)
    sub = cat[cat["resource_node"] == resource_node][["sced_resource_name", "resource_type"]] \
        .rename(columns={"sced_resource_name": "resource_name"}).drop_duplicates("resource_name")
    if paths.PLANT_REGISTRY_PARQUET.exists():
        reg = pd.read_parquet(paths.PLANT_REGISTRY_PARQUET)[["resource_name", "resource_type", "fuel_group"]]
        sub = sub.merge(reg, on="resource_name", how="left", suffixes=("_cat", ""))
        sub["resource_type"] = sub["resource_type"].fillna(sub["resource_type_cat"])
        sub = sub.drop(columns=[c for c in ("resource_type_cat",) if c in sub.columns])
    if "fuel_group" not in sub.columns:
        sub["fuel_group"] = sub["resource_type"].map(fuels.sced_fuel_group)
    sub["is_storage"] = [is_storage(n, t, fg) for n, t, fg in
                         zip(sub["resource_name"], sub.get("resource_type"), sub.get("fuel_group"))]
    return sub[cols].reset_index(drop=True)


def node_generation_mwh(gen_df: pd.DataFrame, resource_node: str,
                        units: list[str] | None = None,
                        mw_scale: float = 1.0,
                        mw_cap: float | None = None) -> pd.DataFrame:
    """Node-level MWh per 15-min interval (sum the chosen units, MW -> MWh).

    `units` limits which SCED resource names are summed (e.g. exclude the
    co-located battery). None = all units at the node.

    `mw_scale` rescales the summed node MW (1.0 = as-metered; 0.5 = a 50%
    pro-rata PPA share; 1.5 = model a 1.5x larger plant with the same shape).
    `mw_cap`, if given, then clips each interval to a contracted capacity
    (as-available up to the cap). Scaling is applied before the cap.
    """
    if gen_df is None or gen_df.empty:
        return pd.DataFrame(columns=["interval_start", "mw", "mwh"])
    g = gen_df[gen_df["resource_node"] == resource_node]
    if units is not None:
        g = g[g["resource_name"].isin(units)]
    # Sum per interval on the tz-aware key so the duplicated fall-back hour is
    # not collapsed into one bucket (naive labels repeat there).
    g = g.assign(interval_start=_aware(g))
    g = g.groupby("interval_start", as_index=False)["mw"].sum()
    if mw_scale != 1.0:
        g["mw"] = g["mw"] * mw_scale
    if mw_cap is not None:
        g["mw"] = g["mw"].clip(upper=mw_cap)
    g["mwh"] = g["mw"] * INTERVAL_HOURS
    return g


def _price_series(price_df: pd.DataFrame, location: str, market: str) -> pd.DataFrame:
    if price_df is None or price_df.empty:
        return pd.DataFrame(columns=["interval_start", "spp"])
    p = price_df[(price_df["location"] == location) & (price_df["market"] == market)]
    if p.empty:
        return pd.DataFrame(columns=["interval_start", "spp"])
    # tz-aware key first, then dedupe — on the fall-back day the two passes share
    # a naive label, so a naive drop_duplicates would silently discard one price.
    out = p[["interval_start", "spp"]].copy()
    out["interval_start"] = _aware(p)
    return out.dropna().drop_duplicates("interval_start")


def _join_price(gen: pd.DataFrame, price: pd.DataFrame, market: str, col: str) -> pd.DataFrame:
    """Attach a price column to the 15-min generation frame."""
    if gen.empty or price.empty:
        out = gen.copy()
        out[col] = pd.NA
        return out
    if market == "DAM":  # hourly price -> broadcast to the 15-min intervals in that hour
        g = gen.copy()
        g["_hr"] = pd.to_datetime(g["interval_start"]).dt.floor("h")
        pr = price.rename(columns={"interval_start": "_hr", "spp": col})
        pr["_hr"] = pd.to_datetime(pr["_hr"]).dt.floor("h")
        return g.merge(pr, on="_hr", how="left").drop(columns="_hr")
    pr = price.rename(columns={"spp": col})
    return gen.merge(pr, on="interval_start", how="left")


def compute_settlement(
    gen_df: pd.DataFrame,
    price_df: pd.DataFrame,
    resource_node: str,
    ppa_price: float,
    ref_location: str,
    market: str = "RT15",
    node_location: str | None = None,
    hub_location: str | None = None,
    units: list[str] | None = None,
    price_floor: float | None = 0.0,
    settle_below_floor: bool = False,
    mw_scale: float = 1.0,
    mw_cap: float | None = None,
    price_ceiling: float | None = None,
    exclude_negative: bool = False,
    rec_per_mwh: float = 0.0,
    escalation_pct: float = 0.0,
    escalation_base_year: int | None = None,
) -> dict:
    """Interval-level settlement + summary for one asset.

    ref_location is the settlement reference (the node itself, or a hub) — all
    merchant/PPA/CfD numbers settle against it. If both node_location and
    hub_location are given, the locational basis Σ mwh×(node−hub) is added too
    (the congestion exposure, regardless of which point you settle at).
    `units` selects which SCED units count as the PPA asset (default behaviour
    in the UI excludes co-located storage).

    Price-floor handling (the standard VPPA lever for negative/low prices):
      * ``price_floor=None``                       — no floor; every interval
        settles at the raw market price (negatives included).
      * ``price_floor=X, settle_below_floor=False`` (DEFAULT, X=$0) — *no
        settlement* in intervals where price < X: those MWh are treated as not
        sold and are dropped from volume / merchant / PPA / CfD entirely. This
        is how most VPPAs handle negative prices ("no electrons sold below the
        floor").
      * ``price_floor=X, settle_below_floor=True``  — the interval still settles
        but the market leg is clipped up to X, so the CfD pays PPA − X there
        (you still pay the PPA; market revenue floored at X).

    Returns {"intervals", "summary"}.
    """
    gen = node_generation_mwh(gen_df, resource_node, units=units,
                              mw_scale=mw_scale, mw_cap=mw_cap)
    df = _join_price(gen, _price_series(price_df, ref_location, market), market, "price")

    # Only intervals with both generation and a reference price settle.
    df = df.dropna(subset=["price"]).copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["price_raw"] = df["price"]

    # Negative-price exclusion (VPPA term: no settlement in intervals where the
    # real-time price is < $0, independent of any floor). Off by default.
    n_negative = int((df["price_raw"] < 0).sum())
    excluded_neg_mwh = 0.0
    if exclude_negative:
        excluded_neg_mwh = float(df.loc[df["price_raw"] < 0, "mwh"].sum())
        df = df[df["price_raw"] >= 0].copy()

    below = ((df["price_raw"] < price_floor) if price_floor is not None
             else pd.Series(False, index=df.index))
    n_below = int(below.sum())
    excluded_mwh = 0.0
    if price_floor is not None and not settle_below_floor:
        # No settlement below the floor — drop those intervals (no electrons sold).
        excluded_mwh = float(df.loc[below, "mwh"].sum())
        df = df[~below].copy()
    elif price_floor is not None and settle_below_floor:
        # Still settle, but floor the market leg (CfD pays PPA − floor there).
        df["price"] = df["price"].clip(lower=price_floor)

    # Price ceiling (the upper rail of a collar): cap the settled market price.
    if price_ceiling is not None:
        df["price"] = df["price"].clip(upper=price_ceiling)

    # Strike escalation: the contract strike steps up `escalation_pct` per year
    # from `escalation_base_year`. With pct=0 (default) the strike is flat.
    if escalation_pct and escalation_base_year:
        _yr = pd.to_datetime(df["interval_start"]).dt.year
        _exp = (_yr - int(escalation_base_year)).clip(lower=0)
        df["strike"] = float(ppa_price) * (1.0 + float(escalation_pct)) ** _exp
    else:
        df["strike"] = float(ppa_price)

    df["merchant"] = df["mwh"] * df["price"]
    df["ppa_revenue"] = df["mwh"] * df["strike"]
    # CfD signed from the OFFTAKER's perspective: market − strike, ×MWh, plus any
    # fixed REC/green-attribute value per MWh (rec_per_mwh, 0 by default).
    # Positive ⇒ offtaker receives (market above strike); negative ⇒ offtaker pays.
    df["cfd"] = df["mwh"] * (df["price"] - df["strike"]) + df["mwh"] * float(rec_per_mwh)

    # Locational basis: node price minus hub price (independent of settlement ref).
    if node_location and hub_location and node_location != hub_location:
        df = _join_price(df, _price_series(price_df, node_location, market), market, "node_price")
        df = _join_price(df, _price_series(price_df, hub_location, market), market, "hub_price")
        df["basis"] = df["mwh"] * (pd.to_numeric(df["node_price"], errors="coerce")
                                   - pd.to_numeric(df["hub_price"], errors="coerce"))

    # Restore the naive-Central storage convention for the returned/displayed
    # frame now that all DST-sensitive joins are done.
    df["interval_start"] = tz.to_naive_central(df["interval_start"])

    total_mwh = float(df["mwh"].sum())
    merchant = float(df["merchant"].sum())
    ppa_rev = float(df["ppa_revenue"].sum())
    rec_value = total_mwh * float(rec_per_mwh)   # REC/green-attribute value to offtaker
    summary = {
        "resource_node": resource_node,
        "reference": ref_location,
        "market": market,
        "ppa_price": ppa_price,
        "intervals": int(len(df)),
        "total_mwh": total_mwh,
        "capture_price": (merchant / total_mwh) if total_mwh else 0.0,  # gen-weighted market $/MWh
        "merchant_revenue": merchant,
        "ppa_revenue": ppa_rev,
        # offtaker frame: + => offtaker receives. Includes REC value (0 by default).
        "cfd_settlement": merchant - ppa_rev + rec_value,
        "ppa_vs_merchant": ppa_rev - merchant,  # seller frame: + => PPA beats merchant
        "price_ceiling": price_ceiling,
        "exclude_negative": exclude_negative,
        "negative_intervals": n_negative,
        "negative_excluded_mwh": excluded_neg_mwh,
        "rec_per_mwh": rec_per_mwh,
        "rec_value": rec_value,
        "escalation_pct": escalation_pct,
        "escalation_base_year": escalation_base_year,
        "units": list(units) if units is not None else None,
        "mw_scale": mw_scale,
        "mw_cap": mw_cap,
        "price_floor": price_floor,
        "settle_below_floor": (None if price_floor is None else settle_below_floor),
        "below_floor_intervals": n_below,            # intervals where raw price < floor
        # When settling below the floor, those intervals are clipped; otherwise
        # they're excluded (no electrons sold) and dropped from the totals above.
        "floored_intervals": (n_below if (price_floor is not None and settle_below_floor) else 0),
        "excluded_intervals": (n_below if (price_floor is not None and not settle_below_floor) else 0),
        "excluded_mwh": excluded_mwh,
    }
    if "basis" in df.columns:
        summary["basis_settlement"] = float(df["basis"].sum())
    return {"intervals": df, "summary": summary}
