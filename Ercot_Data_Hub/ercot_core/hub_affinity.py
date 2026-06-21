"""Best-hub selection for a resource node.

Given a resource node (e.g. ``HRNT_SLR_RN``), picks the ERCOT trading hub
whose RT15 price best matches the node — combining price correlation over
the available history with geographic proximity as a tiebreaker.

Usage (at portal build time or in the Contract page)::

    from ercot_core import hub_affinity
    result = hub_affinity.best_hub("HRNT_SLR_RN")
    print(result)
    # {'hub': 'HB_PAN', 'corr': 0.9992, 'basis_mean': -0.52,
    #  'basis_std': 2.38, 'method': 'correlation', 'all': [...]}

The function reads the already-cached node-price parquets and the Hub's
ercot_hub_prices_15min store — no live fetch required.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from ercot_core import paths, prices as PX

# Known hub centroids (lat, lon) — used only as a tiebreaker when two hubs
# have near-identical correlation (within CORR_TIE_BAND).
_HUB_CENTROIDS: dict[str, tuple[float, float]] = {
    "HB_NORTH":   (32.8, -97.3),   # Dallas-Fort Worth load centre
    "HB_HOUSTON": (29.8, -95.4),   # Houston Ship Channel
    "HB_SOUTH":   (29.4, -98.5),   # San Antonio
    "HB_WEST":    (31.8, -102.4),  # Midland / Abilene
    "HB_PAN":     (35.2, -101.8),  # Amarillo / Panhandle
}
CORR_TIE_BAND = 0.005   # treat correlations within this band as a tie → use distance


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _node_price_history(resource_node: str) -> pd.Series:
    """Load all cached RT15 node prices as a Series indexed by interval_start."""
    frames = []
    for year in range(2020, pd.Timestamp.now().year + 2):
        fp = paths.NODE_DATA_DIR / f"node_price_{year}.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp, columns=["interval_start", "location", "spp"])
        df = df[df["location"] == resource_node]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.Series(dtype=float)
    all_df = pd.concat(frames, ignore_index=True)
    all_df["interval_start"] = pd.to_datetime(all_df["interval_start"])
    return all_df.set_index("interval_start")["spp"].sort_index()


def best_hub_by_distance(lat: float, lon: float) -> dict[str, Any]:
    """Nearest ERCOT trading hub by straight-line distance — no price data required.

    Useful when a node's prices haven't been cached yet (e.g. just after scaffolding
    a new portal). Returns the same shape as :func:`best_hub` but ``corr`` is None
    and ``method`` is ``'distance'``.
    """
    records = [
        {"hub": h, "corr": None, "basis_mean": None, "basis_std": None,
         "dist_km": _haversine_km(lat, lon, hlat, hlon)}
        for h, (hlat, hlon) in _HUB_CENTROIDS.items()
    ]
    records.sort(key=lambda r: r["dist_km"])
    best = records[0]
    return {**best, "method": "distance", "all": records}


def best_hub(
    resource_node: str,
    lat: float | None = None,
    lon: float | None = None,
    hubs: list[str] | None = None,
    min_intervals: int = 500,
) -> dict[str, Any]:
    """Return the trading hub that best matches this resource node's RT15 price.

    Parameters
    ----------
    resource_node:
        ERCOT settlement-point name (e.g. ``"HRNT_SLR_RN"``).
    lat / lon:
        Optional plant coordinates for geographic tiebreaking. When omitted
        and two hubs tie on correlation, the result flags ``method='corr_tie'``.
    hubs:
        Subset of hubs to consider. Defaults to all five ERCOT trading hubs.
    min_intervals:
        Minimum overlapping 15-min intervals required to trust a correlation.
        Hubs below this threshold fall back to distance-only scoring.

    Returns
    -------
    dict with keys:
        ``hub``         — recommended hub name
        ``corr``        — Pearson correlation of node vs hub (RT15, all history)
        ``basis_mean``  — mean(node − hub) in $/MWh
        ``basis_std``   — std of basis in $/MWh
        ``dist_km``     — haversine distance to hub centroid (None if no coords)
        ``method``      — ``'correlation'`` | ``'distance'`` | ``'corr_tie'``
        ``all``         — list of dicts for every hub, sorted by correlation
    """
    if hubs is None:
        hubs = list(_HUB_CENTROIDS.keys())

    node_s = _node_price_history(resource_node)
    if node_s.empty:
        raise ValueError(
            f"No cached node prices found for {resource_node!r}. "
            "Run a data refresh first."
        )

    if not paths.HUB_PRICES_PARQUET.exists():
        raise ValueError("Hub price store not found — run a hub_prices refresh.")

    records = []
    for h in hubs:
        hp = PX.hub_store_prices([h],
                                  node_s.index.min(),
                                  node_s.index.max() + pd.Timedelta(minutes=15))
        if hp.empty:
            continue
        hp_s = hp.set_index("interval_start")["spp"].sort_index()
        # Deduplicate DST fall-back duplicate timestamps before merging
        node_dd = node_s[~node_s.index.duplicated(keep="first")]
        hp_dd   = hp_s[~hp_s.index.duplicated(keep="first")]
        merged = pd.concat([node_dd.rename("node"), hp_dd.rename("hub")], axis=1).dropna()
        n = len(merged)
        corr = float(merged["node"].corr(merged["hub"])) if n >= min_intervals else float("nan")
        basis = merged["node"] - merged["hub"]
        dist = (_haversine_km(lat, lon, *_HUB_CENTROIDS[h])
                if lat is not None and lon is not None and h in _HUB_CENTROIDS
                else None)
        records.append({
            "hub": h,
            "corr": corr,
            "basis_mean": float(basis.mean()),
            "basis_std": float(basis.std()),
            "n": n,
            "dist_km": dist,
        })

    if not records:
        raise ValueError("No hub price data available to compare against.")

    # Sort: correlation descending (NaN last), distance ascending as secondary
    records.sort(key=lambda r: (
        -r["corr"] if not math.isnan(r["corr"]) else float("inf"),
        r["dist_km"] if r["dist_km"] is not None else float("inf"),
    ))

    best = records[0]
    second = records[1] if len(records) > 1 else None

    # Determine method
    if math.isnan(best["corr"]):
        method = "distance"
    elif (second and not math.isnan(second["corr"])
          and abs(best["corr"] - second["corr"]) < CORR_TIE_BAND
          and best["dist_km"] is not None):
        method = "corr_tie_distance"
    else:
        method = "correlation"

    return {
        "hub": best["hub"],
        "corr": best["corr"],
        "basis_mean": best["basis_mean"],
        "basis_std": best["basis_std"],
        "dist_km": best["dist_km"],
        "method": method,
        "all": records,
    }
