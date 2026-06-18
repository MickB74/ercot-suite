"""Day-Ahead Market (DAM) hourly Settlement Point Prices via the ERCOT API.

Report NP4-190-CD ("DAM Settlement Point Prices"). The live endpoint has long
retention (serves multi-year history), so no archive crawl is needed. Hourly.

Returned in the node_prices tidy schema (market='DAM'); interval_start is the
hour-beginning in naive Central. Requires ERCOT API credentials.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ercot_core import bootstrap, credentials, paths
from ercot_core.prices import PRICE_COLUMNS

bootstrap.setup_path()

DAM_ENDPOINT = "https://api.ercot.com/api/public-reports/np4-190-cd/dam_stlmnt_pnt_prices"
_CHUNK_DAYS = 30
DAM_STORE = paths.HUB_PRICES_DIR / "ercot_hub_dam_hourly.parquet"


def _empty():
    return pd.DataFrame(columns=PRICE_COLUMNS)


def fetch_dam_spp(settlement_points, start, end, location_type="Trading Hub", log=lambda m: None):
    """Hourly DAM SPP for the given settlement point(s) over [start, end]."""
    import ercot_api as ea
    cfg = credentials.load_config()
    if not credentials.have_credentials(cfg):
        raise RuntimeError("ERCOT API credentials required for DAM prices (set on Home page).")
    tokens = ea.TokenManager(cfg, log=log)
    tokens.get()
    key = cfg["subscription_key"]
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()

    frames = []
    for sp in list(dict.fromkeys(settlement_points)):
        cur = start_d
        while cur <= end_d:
            chunk_end = min(cur + timedelta(days=_CHUNK_DAYS - 1), end_d)
            page = 1
            while True:
                payload = ea._do_request(
                    "GET", DAM_ENDPOINT, tokens, key, log=log,
                    params={"deliveryDateFrom": cur.isoformat(), "deliveryDateTo": chunk_end.isoformat(),
                            "settlementPoint": sp, "size": 1000, "page": page}).json()
                fields = [f["name"] for f in payload.get("fields", [])]
                rows = payload.get("data", [])
                if rows:
                    frames.append(pd.DataFrame(rows, columns=fields))
                total_pages = (payload.get("_meta") or {}).get("totalPages") or 1
                if page >= total_pages:
                    break
                page += 1
            cur = chunk_end + timedelta(days=1)

    if not frames:
        return _empty()
    raw = pd.concat(frames, ignore_index=True)
    he = raw["hourEnding"].astype(str).str.slice(0, 2).astype(int)   # "01:00".."24:00"
    base = pd.to_datetime(raw["deliveryDate"])
    out = pd.DataFrame()
    out["interval_start"] = base + pd.to_timedelta(he - 1, unit="h")  # hour-beginning
    out["interval_end"] = out["interval_start"] + pd.Timedelta(hours=1)
    out["location"] = raw["settlementPoint"].astype(str)
    out["location_type"] = location_type
    out["market"] = "DAM"
    out["spp"] = pd.to_numeric(raw["settlementPointPrice"], errors="coerce")
    out["source"] = "ercot_api_dam"
    out["fetched_at"] = pd.Timestamp.now(tz="UTC")
    # ERCOT marks the repeated fall-back hour with a DST flag; carry it so the
    # duplicated naive hour isn't dropped as a "duplicate" and settlement can
    # disambiguate it. Field name varies, so match defensively.
    dst_src = next((c for c in raw.columns
                    if "dst" in c.lower() or "repeat" in c.lower()), None)
    cols = list(PRICE_COLUMNS)
    dedupe_keys = ["location", "interval_start"]
    if dst_src is not None:
        out["dst_flag"] = (raw[dst_src].astype(str).str.strip().str.upper()
                           .map(lambda v: "Y" if v in {"Y", "TRUE", "1"} else "N"))
        cols = cols + ["dst_flag"]
        dedupe_keys = ["location", "interval_start", "dst_flag"]
    return (out[cols].dropna(subset=["spp"])
            .drop_duplicates(dedupe_keys)
            .sort_values(["location", "interval_start"]).reset_index(drop=True))


# ── DAM hub store (parallel to the RT hub store) ────────────────────────────
def build_dam_store(hubs, start, end, log=print) -> int:
    """Fetch DAM for `hubs` over [start, end] and merge into the DAM hub store."""
    new = fetch_dam_spp(hubs, start, end, location_type="Trading Hub", log=log)
    if new.empty:
        return 0
    paths.HUB_PRICES_DIR.mkdir(parents=True, exist_ok=True)
    if DAM_STORE.exists():
        old = pd.read_parquet(DAM_STORE)
        new = pd.concat([old, new], ignore_index=True).drop_duplicates(
            ["location", "interval_start"], keep="last")
    new.to_parquet(DAM_STORE, index=False)
    return len(new)


def dam_store_prices(locations, start, end_excl) -> pd.DataFrame:
    """DAM hub prices from the store over [start, end_excl), node_prices schema."""
    if not DAM_STORE.exists():
        return _empty()
    df = pd.read_parquet(DAM_STORE)
    df = df[df["location"].isin(locations)]
    if df.empty:
        return _empty()
    df = df[(df["interval_start"] >= pd.Timestamp(start)) & (df["interval_start"] < pd.Timestamp(end_excl))]
    cols = PRICE_COLUMNS + (["dst_flag"] if "dst_flag" in df.columns else [])
    return df[cols].reset_index(drop=True)
