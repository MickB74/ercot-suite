"""Historical RTM Settlement Point Prices for ANY settlement point (node / zone
/ hub), via ERCOT's direct Public API.

gridstatus's get_spp only serves recent dates. The hub_prices dataset already
pulls NP6-905-CD (RTM SPP) straight from the ERCOT API — live endpoint for the
last ~80 days, archive for older — but keeps only the trading hubs. The same
download contains every resource node and load zone, so here we reuse those
low-level functions and just filter to the settlement point(s) you ask for.

Returns the node_prices tidy schema (interval_start naive CPT, location,
location_type, market='RT15', spp, ...). Requires ERCOT API credentials
(shared config.json). RTM only — DAM is a different report.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ercot_core import bootstrap, credentials

bootstrap.setup_path()  # make the hub_prices `ercot_api` module importable

PRICE_COLUMNS = [
    "interval_start", "interval_end", "location", "location_type", "market",
    "spp", "dst_flag", "source", "fetched_at",
]
# Include dst_flag so the two passes of the November fall-back hour (same
# delivery_date/hour/interval, opposite DSTFlag) are BOTH kept — otherwise the
# fall-back day collapses from 100 to 96 intervals. Mirrors the live hub path
# (datasets/hub_prices/ercot_api.py) and lets settlement._aware() disambiguate.
_DEDUP = ["settlement_point", "delivery_date", "delivery_hour", "delivery_interval",
          "dst_flag"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS)


def _to_tidy(df: pd.DataFrame, location_type: str, start, end) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty()
    df = df.copy()
    if "dst_flag" not in df.columns:   # older/live rows without the flag → normal-time "N"
        df["dst_flag"] = "N"
    df["dst_flag"] = df["dst_flag"].fillna("N").astype(str)
    df = df.drop_duplicates(subset=_DEDUP, keep="last")
    ie = pd.to_datetime(df["interval_ending_central"])
    out = pd.DataFrame()
    out["interval_start"] = ie - pd.Timedelta(minutes=15)
    out["interval_end"] = ie
    out["location"] = df["settlement_point"].astype(str)
    out["location_type"] = location_type
    out["market"] = "RT15"
    out["spp"] = pd.to_numeric(df["price"], errors="coerce")
    out["dst_flag"] = df["dst_flag"].values
    out["source"] = "ercot_api"
    out["fetched_at"] = pd.Timestamp.now(tz="UTC")
    s = pd.Timestamp(pd.Timestamp(start).date())
    e = pd.Timestamp(pd.Timestamp(end).date()) + pd.Timedelta(days=1)
    out = out[(out["interval_start"] >= s) & (out["interval_start"] < e)]
    return out[PRICE_COLUMNS].sort_values(["location", "interval_start"]).reset_index(drop=True)


def fetch_rtm_spp(settlement_points, start, end, location_type: str = "Resource Node",
                  log=print) -> pd.DataFrame:
    """Historical RTM SPP for the given settlement point(s) over [start, end].

    Live endpoint for the recent window, archive for older months (slow — one
    file per 15-min interval). Needs ERCOT API credentials.
    """
    import ercot_api as ea  # hub_prices dataset module (proven low-level funcs)

    cfg = credentials.load_config()
    if not credentials.have_credentials(cfg):
        raise RuntimeError("ERCOT API credentials required for historical node/zone "
                           "prices — set them on the Home page (config.json).")

    sps = list(dict.fromkeys(str(s) for s in settlement_points))
    want = set(sps)
    tokens = ea.TokenManager(cfg, log=log)
    tokens.get()  # surface credential errors immediately
    key = cfg["subscription_key"]

    from ercot_core import tz

    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    live_cutoff = tz.now_central().date() - timedelta(days=ea.LIVE_WINDOW_DAYS)

    frames = []

    # Archive (older than the live window): bulk-download monthly, filter to SPs.
    if start_d < live_cutoff:
        a_end = min(live_cutoff - timedelta(days=1), end_d)
        for m_start, m_end in ea._month_windows(start_d, a_end):
            log(f"[archive] {m_start:%Y-%m}: listing files…")
            doc_ids = ea.list_archive_docids(tokens, key, m_start, m_end, log)
            if not doc_ids:
                continue
            log(f"[archive] {m_start:%Y-%m}: {len(doc_ids):,} files (filtering to {len(sps)} SP)…")
            for i in range(0, len(doc_ids), ea.BULK_BATCH):
                resp = ea._do_request("POST", ea.ARCHIVE_DOWNLOAD, tokens, key, log=log,
                                      json={"docIds": doc_ids[i:i + ea.BULK_BATCH]})
                csvs = ea._read_csvs_from_zip(resp.content)
                if not csvs:
                    continue
                raw = pd.concat(csvs, ignore_index=True)
                ncol = "SettlementPointName" if "SettlementPointName" in raw.columns else "settlementPointName"
                if ncol not in raw.columns:
                    continue
                raw = raw[raw[ncol].isin(want)]
                if not raw.empty:
                    frames.append(ea.normalize(raw))

    # Live endpoint (recent window): one request stream per settlement point.
    live_start = max(start_d, live_cutoff)
    if live_start <= end_d:
        for sp in sps:
            log(f"[live] {sp} {live_start} → {end_d}…")
            raw = ea.fetch_settlement_point(tokens, key, sp, live_start, end_d, log)
            norm = ea.normalize(raw)
            if not norm.empty:
                frames.append(norm)

    if not frames:
        return _empty()
    return _to_tidy(pd.concat(frames, ignore_index=True), location_type, start, end)
