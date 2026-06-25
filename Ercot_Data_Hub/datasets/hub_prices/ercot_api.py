#!/usr/bin/env python3
"""
ERCOT Hub Price Downloader -- core engine.

Pulls 15-minute Real-Time Market (RTM) Settlement Point Prices for the ERCOT
trading HUBS directly from the official ERCOT Public API (api.ercot.com,
report NP6-905-CD "Settlement Point Prices at Resource Nodes, Hubs and Load
Zones").

It logs in with your own free ERCOT API account, fetches the data, and keeps a
local store up to date incrementally:

    data/ercot_hub_prices_15min.parquet   (full history, fast to load)
    data/ercot_hub_prices_15min.csv        (same data, opens in Excel)

Usage (command line):
    python ercot_api.py set-credentials      # interactive one-time setup
    python ercot_api.py test-auth            # verify login works
    python ercot_api.py update               # fetch latest and update store
    python ercot_api.py update --auto        # same, quiet, used by the weekly job

Most people never touch the command line -- they just use the button app
(ercot_gui.py). This module is what that app calls under the hood.
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import requests

from ercot_core import credentials, paths, tz
from ercot_core.settlement_points import HUBS

# --------------------------------------------------------------------------
# Paths & constants (unified data lake — see ercot_core.paths)
# --------------------------------------------------------------------------

PROJECT_DIR = paths.ROOT
CONFIG_PATH = paths.CONFIG_PATH                 # single shared credential store
DATA_DIR = paths.HUB_PRICES_DIR
PARQUET_PATH = paths.HUB_PRICES_PARQUET
CSV_PATH = paths.HUB_PRICES_CSV
STATE_PATH = paths.HUB_PRICES_STATE

# ERCOT Public API: OAuth2 (Azure AD B2C, resource-owner password flow).
# These two values are PUBLIC and identical for every ERCOT API user -- they
# identify the ERCOT public API application itself, not you.
B2C_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
TOKEN_URL = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/"
    "B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)

# RTM Settlement Point Prices at Resource Nodes, Hubs and Load Zones (NP6-905-CD)
SPP_ENDPOINT = "https://api.ercot.com/api/public-reports/np6-905-cd/spp_node_zone_hub"

# The live endpoint above only serves roughly the last 90 days. Older data lives
# in ERCOT's archive (one zipped CSV per 15-minute interval). We list archive
# documents, then bulk-download them in batches.
ARCHIVE_BASE = "https://api.ercot.com/api/public-reports/archive/np6-905-cd"
ARCHIVE_DOWNLOAD = ARCHIVE_BASE + "/download"
ARCHIVE_LIST_SIZE = 1000     # archive documents listed per page
BULK_BATCH = 1000            # max docIds per bulk-download POST (ERCOT cap)
# Use the fast live endpoint for the most recent N days; archive for older.
LIVE_WINDOW_DAYS = 80

# Every ERCOT trading hub settlement point — shared list (ercot_core).
# (imported above as HUBS from ercot_core.settlement_points)

# How far back to reach on the very first run if there is no local data yet.
# (RTM SPP history is large; default to the start of last year. Change with
#  "backfill_start" in config.json, e.g. "2019-01-01".)
DEFAULT_BACKFILL_START = date(tz.now_central().year - 1, 1, 1).isoformat()

# Re-fetch this many days of overlap each update so late ERCOT price
# corrections get picked up.
OVERLAP_DAYS = 3

# Considered "stale" (the weekly catch-up should run) after this many days.
STALE_AFTER_DAYS = 7

DATE_CHUNK_DAYS = 30   # fetch in chunks to keep each request small
PAGE_SIZE = 1000
HTTP_TIMEOUT = 60
MAX_RETRIES = 4

# ERCOT's free public-API tier is rate-limited (about 30 requests/minute). We
# proactively space requests out to stay under it, and when ERCOT does push
# back with a 429 we wait exactly as long as it tells us to.
MIN_REQUEST_INTERVAL = 2.2    # seconds between requests (~27/min)
MAX_RATE_LIMIT_WAITS = 12     # how many times we'll patiently wait out a 429
_last_request_time = 0.0      # module-level throttle clock


# --------------------------------------------------------------------------
# Small logging helper -- prints, and also forwards to a GUI callback if given
# --------------------------------------------------------------------------

def _make_logger(callback=None):
    def log(msg: str):
        line = str(msg)
        print(line, flush=True)
        if callback is not None:
            try:
                callback(line)
            except Exception:
                pass
    return log


# --------------------------------------------------------------------------
# Config / credentials
# --------------------------------------------------------------------------

# Credentials are managed by the shared ercot_core.credentials module so the
# whole monorepo uses one config.json. These aliases keep the existing call
# sites in this file working unchanged.
load_config = credentials.load_config
save_config = credentials.save_config
have_credentials = credentials.have_credentials
set_credentials_interactive = credentials.set_credentials_interactive


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

class ErcotAuthError(RuntimeError):
    pass


def get_access_token(cfg: dict, log=print) -> str:
    """Log in to the ERCOT Public API and return a bearer access token."""
    if not have_credentials(cfg):
        raise ErcotAuthError(
            "Missing ERCOT API credentials. Run 'set-credentials' (or use the "
            "Set Credentials button in the app)."
        )
    params = {
        "username": cfg["username"],
        "password": cfg["password"],
        "grant_type": "password",
        # ERCOT's public API uses the OpenID **id_token** as the bearer credential
        # (not the access_token), so we must request response_type=id_token.
        "scope": f"openid {B2C_CLIENT_ID} offline_access",
        "client_id": B2C_CLIENT_ID,
        "response_type": "id_token",
    }
    log("Logging in to ERCOT Public API...")
    resp = requests.post(TOKEN_URL, data=params, timeout=HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise ErcotAuthError(
            f"Login failed (HTTP {resp.status_code}). Check your username/password.\n"
            f"{resp.text[:300]}"
        )
    token = resp.json().get("id_token")
    if not token:
        raise ErcotAuthError("Login response did not contain an id_token.")
    log("  login OK.")
    return token


class TokenManager:
    """Holds an ERCOT id_token and silently re-logs-in when it gets old.

    ERCOT tokens expire after ~1 hour, so a long first-time backfill would
    otherwise fail partway through. We refresh after 50 minutes, and callers
    can force a refresh on a 401.
    """

    REFRESH_AFTER = 50 * 60  # seconds

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self._token = None
        self._acquired = 0.0

    def get(self, force: bool = False) -> str:
        if force or self._token is None or (time.time() - self._acquired) > self.REFRESH_AFTER:
            self._token = get_access_token(self.cfg, log=self.log)
            self._acquired = time.time()
        return self._token


# --------------------------------------------------------------------------
# Data fetching
# --------------------------------------------------------------------------

def _throttle():
    """Space requests out so we stay under ERCOT's rate limit."""
    global _last_request_time
    wait = MIN_REQUEST_INTERVAL - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _rate_limit_wait_seconds(resp) -> float:
    """How long ERCOT wants us to wait after a 429."""
    # Prefer the standard Retry-After header.
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    # Otherwise parse "Try again in N seconds" from the body.
    m = re.search(r"in\s+(\d+)\s+second", resp.text or "")
    if m:
        return float(m.group(1))
    return 30.0  # sensible default


def _do_request(method, url, tokens: "TokenManager", key, log=print, **kwargs):
    """One request to the ERCOT API with throttling, 429-waiting, and 401 re-auth.

    Used by both the live endpoint and the archive endpoint. Returns the raw
    requests.Response on success (HTTP 200).
    """
    rate_waits = 0
    attempt = 0
    while True:
        headers = {
            "Authorization": f"Bearer {tokens.get()}",
            "Ocp-Apim-Subscription-Key": key,
        }
        _throttle()
        try:
            r = requests.request(method, url, headers=headers, timeout=HTTP_TIMEOUT, **kwargs)
        except requests.RequestException as e:
            attempt += 1
            if attempt > MAX_RETRIES:
                raise RuntimeError(f"ERCOT API request failed after {MAX_RETRIES} tries: {e}")
            time.sleep(2 * attempt)
            continue

        if r.status_code == 200:
            return r

        if r.status_code == 429:
            # Rate limited -- wait exactly as long as ERCOT asks, then retry.
            # These waits don't count against the normal retry budget.
            rate_waits += 1
            if rate_waits > MAX_RATE_LIMIT_WAITS:
                raise RuntimeError("Still rate-limited after many waits -- giving up for now.")
            secs = _rate_limit_wait_seconds(r) + 1.0
            log(f"      rate-limited by ERCOT; waiting {secs:.0f}s (free-tier limit)...")
            time.sleep(secs)
            continue

        if r.status_code in (401, 403):
            # token likely expired -- force a fresh login and retry
            attempt += 1
            if attempt > MAX_RETRIES:
                raise RuntimeError(f"ERCOT API error HTTP {r.status_code}: {r.text[:300]}")
            tokens.get(force=True)
            continue

        if r.status_code in (500, 502, 503, 504):
            attempt += 1
            if attempt > MAX_RETRIES:
                raise RuntimeError(f"ERCOT API error HTTP {r.status_code}: {r.text[:300]}")
            time.sleep(3 * attempt)
            continue

        raise RuntimeError(f"ERCOT API error HTTP {r.status_code}: {r.text[:300]}")


def _request_page(tokens: "TokenManager", key, settlement_point, d_from, d_to, page, log=print):
    params = {
        "deliveryDateFrom": d_from,
        "deliveryDateTo": d_to,
        "settlementPoint": settlement_point,
        "size": PAGE_SIZE,
        "page": page,
    }
    return _do_request("GET", SPP_ENDPOINT, tokens, key, log=log, params=params).json()


def _json_to_frame(payload) -> pd.DataFrame:
    fields = [f["name"] for f in payload.get("fields", [])]
    rows = payload.get("data", [])
    if not fields or not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=fields)


def fetch_settlement_point(tokens, key, settlement_point, start: date, end: date, log) -> pd.DataFrame:
    """Fetch all 15-min SPP rows for one hub between start and end (inclusive)."""
    frames = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=DATE_CHUNK_DAYS - 1), end)
        d_from, d_to = chunk_start.isoformat(), chunk_end.isoformat()
        page = 1
        while True:
            payload = _request_page(tokens, key, settlement_point, d_from, d_to, page, log=log)
            df = _json_to_frame(payload)
            if not df.empty:
                frames.append(df)
            meta = payload.get("_meta", {}) or {}
            total_pages = meta.get("totalPages") or 1
            if page >= total_pages:
                break
            page += 1
        chunk_start = chunk_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------
# Archive fetching (for data older than ~90 days)
# --------------------------------------------------------------------------

def _month_windows(start: date, end: date):
    """Yield (month_start, month_end) date pairs covering [start, end]."""
    cur = date(start.year, start.month, 1)
    while cur <= end:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def list_archive_docids(tokens, key, dt_from: date, dt_to: date, log) -> list[str]:
    """List archive document IDs whose post date falls in [dt_from, dt_to]."""
    doc_ids: list[str] = []
    page = 1
    # pad by a day so boundary intervals posted just after midnight are included
    p_from = f"{dt_from.isoformat()}T00:00:00"
    p_to = f"{(dt_to + timedelta(days=1)).isoformat()}T00:00:00"
    while True:
        params = {"postDatetimeFrom": p_from, "postDatetimeTo": p_to,
                  "size": ARCHIVE_LIST_SIZE, "page": page}
        payload = _do_request("GET", ARCHIVE_BASE, tokens, key, log=log, params=params).json()
        for a in payload.get("archives", []):
            did = a.get("docId")
            if did is not None:
                doc_ids.append(str(did))
        meta = payload.get("_meta", {}) or {}
        total_pages = meta.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1
    return doc_ids


def _read_csvs_from_zip(content: bytes) -> list[pd.DataFrame]:
    """Recursively pull every CSV out of a (possibly nested) zip blob."""
    frames = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            data = zf.read(name)
            if data[:2] == b"PK":  # nested zip
                frames.extend(_read_csvs_from_zip(data))
            elif name.lower().endswith(".csv"):
                try:
                    frames.append(pd.read_csv(io.BytesIO(data)))
                except Exception:
                    pass
    return frames


def bulk_download_hub_rows(tokens, key, doc_ids: list[str], log) -> pd.DataFrame:
    """Download a batch of archive docs and return only the HUB rows (normalised)."""
    resp = _do_request("POST", ARCHIVE_DOWNLOAD, tokens, key, log=log,
                       json={"docIds": doc_ids})
    frames = _read_csvs_from_zip(resp.content)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    # Keep only the trading hubs before doing any heavy work.
    name_col = "SettlementPointName" if "SettlementPointName" in raw.columns else "settlementPointName"
    raw = raw[raw[name_col].isin(HUBS)]
    return normalize(raw)


def _days_in(m_start: date, m_end: date) -> list[str]:
    out, d = [], m_start
    while d <= m_end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# A fully-present day has 7 hubs x 96 intervals = 672 rows; DST days have 92 or
# 100 intervals (644 / 700 rows). 630 is a safe "this day is already complete"
# floor that tolerates DST without skipping genuinely-partial days.
#
# NOTE: a flat floor is *wrong* for the November fall-back day — that day needs
# 100 intervals/hub (700 rows), but an old pull that captured only one pass of
# the duplicated 1-2 AM hour lands at 672, which clears any flat floor and so
# the day is skipped forever (the repeated-hour second pass never gets pulled).
# Use a DST-aware per-day target instead; keep the flat floor as a fallback.
_DAY_COMPLETE_ROWS = 630

_CENTRAL = ZoneInfo("America/Chicago")


def _day_intervals(d: date) -> int:
    """15-min intervals in an ERCOT delivery day: 96 normal, 100 on the November
    fall-back day (clocks repeat 1-2 AM), 92 on the March spring-forward day."""
    a = datetime(d.year, d.month, d.day, tzinfo=_CENTRAL).astimezone(timezone.utc)
    nxt = date(d.year, d.month, d.day) + timedelta(days=1)
    b = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_CENTRAL).astimezone(timezone.utc)
    return round((b - a).total_seconds() / 3600) * 4


def _day_complete_rows(dstr: str, n_points: int) -> int:
    """Rows that mark ``dstr`` fully present, given how many settlement points
    the store carries. DST-aware: the November fall-back day (100 intervals)
    must carry its extra hour, so a one-pass pull (672 rows) is NOT treated as
    complete. Other days keep the flat floor to avoid mass re-pulls."""
    try:
        iv = _day_intervals(date.fromisoformat(dstr))
    except (ValueError, TypeError):
        return _DAY_COMPLETE_ROWS
    if iv > 96 and n_points:   # fall-back day — require both passes of 1-2 AM
        return n_points * iv - n_points   # tolerate 1 missing interval/point
    return _DAY_COMPLETE_ROWS


def archive_backfill(tokens, key, start: date, end: date, store: pd.DataFrame,
                     progress=None, log=print) -> pd.DataFrame:
    """Backfill hub prices from the archive for [start, end], month by month.

    Months already fully present in the store are skipped, so this both (a)
    avoids re-downloading data you already have and (b) makes the job resumable:
    if it's interrupted, the next run continues from the first incomplete month.
    Saves a checkpoint (parquet) after each downloaded month. Returns the store.
    """
    day_counts = store.groupby("delivery_date").size().to_dict() if not store.empty else {}
    n_points = store["settlement_point"].nunique() if not store.empty else len(HUBS)
    months = list(_month_windows(start, end))
    log(f"Archive backfill: {start} -> {end}  ({len(months)} month(s) to consider).")
    log("This is the slow part (ERCOT archives one file per 15-min interval).")
    for mi, (m_start, m_end) in enumerate(months, 1):
        if all(day_counts.get(d, 0) >= _day_complete_rows(d, n_points)
               for d in _days_in(m_start, m_end)):
            log(f"  Month {mi}/{len(months)}: {m_start:%Y-%m} already complete — skipping.")
            continue
        log(f"  Month {mi}/{len(months)}: {m_start} … listing files…")
        doc_ids = list_archive_docids(tokens, key, m_start, m_end, log)
        if not doc_ids:
            log("    (no files)")
            continue
        log(f"    {len(doc_ids):,} files; downloading in batches of {BULK_BATCH}…")
        month_frames = []
        for bi in range(0, len(doc_ids), BULK_BATCH):
            batch = doc_ids[bi:bi + BULK_BATCH]
            df = bulk_download_hub_rows(tokens, key, batch, log)
            if not df.empty:
                month_frames.append(df)
            done = min(bi + BULK_BATCH, len(doc_ids))
            log(f"      {done:,}/{len(doc_ids):,} files parsed…")
            if progress:
                progress(f"archive {m_start:%Y-%m}: {done}/{len(doc_ids)} files")
        if month_frames:
            store = pd.concat([store, *month_frames], ignore_index=True)
            store = store.drop_duplicates(
                subset=_dedup_keys(store),
                keep="last")
            save_store(store, write_csv=False)  # checkpoint (parquet only; CSV at end)
            write_state({"last_success": tz.now_central().isoformat(),
                         "rows": len(store), "note": f"archive checkpoint thru {m_end}"})
            log(f"    checkpoint saved: {len(store):,} rows so far.")
    return store


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# Map field names to friendly column names. The live JSON API uses camelCase;
# the archive CSV files use PascalCase. We accept both.
_RENAME = {
    # live (JSON) endpoint
    "deliveryDate": "delivery_date",
    "deliveryHour": "delivery_hour",
    "deliveryInterval": "delivery_interval",
    "settlementPointName": "settlement_point",
    "settlementPoint": "settlement_point",
    "settlementPointType": "settlement_point_type",
    "settlementPointPrice": "price",
    "DSTFlag": "dst_flag",
    # archive (CSV) files
    "DeliveryDate": "delivery_date",
    "DeliveryHour": "delivery_hour",
    "DeliveryInterval": "delivery_interval",
    "SettlementPointName": "settlement_point",
    "SettlementPointType": "settlement_point_type",
    "SettlementPointPrice": "price",
}


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.rename(columns=_RENAME).copy()
    df["delivery_hour"] = pd.to_numeric(df["delivery_hour"], errors="coerce").astype("Int64")
    df["delivery_interval"] = pd.to_numeric(df["delivery_interval"], errors="coerce").astype("Int64")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    # Parse the delivery date. Live API gives ISO ("2026-06-02"); archive CSVs
    # give US format ("06/02/2026"). Detect which and parse accordingly, then
    # store a single canonical ISO string so both sources de-duplicate cleanly.
    sample = str(df["delivery_date"].dropna().iloc[0]) if df["delivery_date"].notna().any() else ""
    fmt = "%m/%d/%Y" if "/" in sample else None
    base = pd.to_datetime(df["delivery_date"], format=fmt, errors="coerce")
    df["delivery_date"] = base.dt.strftime("%Y-%m-%d")

    # Interval-ENDING timestamp in ERCOT (US/Central) clock time, kept naive so
    # it opens cleanly in Excel:
    #   hour 1, interval 1  -> 00:15   (covers 00:00-00:15)
    #   hour 24, interval 4 -> 24:00 -> next day 00:00
    minutes = (df["delivery_hour"].astype("float") - 1) * 60 + df["delivery_interval"].astype("float") * 15
    df["interval_ending_central"] = base + pd.to_timedelta(minutes, unit="m")

    cols = [
        "interval_ending_central", "settlement_point", "price",
        "delivery_date", "delivery_hour", "delivery_interval",
        "settlement_point_type", "dst_flag",
    ]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    df = df.dropna(subset=["interval_ending_central", "settlement_point"])
    return df


# --------------------------------------------------------------------------
# Store / state
# --------------------------------------------------------------------------

def load_store() -> pd.DataFrame:
    if PARQUET_PATH.exists():
        return pd.read_parquet(PARQUET_PATH)
    return pd.DataFrame()


def _std_dst(v) -> str:
    """Normalise the DST flag to "Y"/"N". The live JSON API returns it as a
    bool, the archive CSVs as "Y"/"N" strings -- unify so the column has one
    type (otherwise Parquet can't serialise the mixed column)."""
    return "Y" if str(v).strip().lower() in ("y", "yes", "true", "t", "1") else "N"


def _dedup_keys(df: pd.DataFrame) -> list[str]:
    """Store identity key. Includes dst_flag so the two passes of the duplicated
    fall-back hour (same delivery_hour/interval, opposite DSTFlag) are BOTH kept
    — otherwise the November DST day stores 96 intervals instead of 100."""
    keys = ["settlement_point", "delivery_date", "delivery_hour", "delivery_interval"]
    if "dst_flag" in df.columns:
        keys.append("dst_flag")
    return keys


def save_store(df: pd.DataFrame, write_csv: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = df.sort_values(["interval_ending_central", "settlement_point"]).reset_index(drop=True)
    if "dst_flag" in df.columns:
        df["dst_flag"] = df["dst_flag"].map(_std_dst)
    df.to_parquet(PARQUET_PATH, index=False)
    if write_csv:  # CSV is large; skip it during mid-backfill checkpoints
        df.to_csv(CSV_PATH, index=False)


def read_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def write_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def days_since_update() -> float | None:
    state = read_state()
    ts = state.get("last_success")
    if not ts:
        return None
    try:
        last = pd.Timestamp(ts)
    except ValueError:
        return None
    # Compare in Central. Tolerate older naive-local state files (pre-tz fix) by
    # localizing them to Central before differencing.
    now = tz.now_central()
    if last.tz is None:
        last = last.tz_localize(tz.CENTRAL, ambiguous=True, nonexistent="shift_forward")
    return (now - last).total_seconds() / 86400.0


def is_stale() -> bool:
    d = days_since_update()
    return d is None or d >= STALE_AFTER_DAYS


def store_summary() -> dict:
    """Human-friendly snapshot of the current local store, for the GUI."""
    info = {"exists": PARQUET_PATH.exists(), "rows": 0, "start": None, "end": None,
            "hubs": [], "days_since_update": days_since_update()}
    if not info["exists"]:
        return info
    try:
        df = load_store()
        info["rows"] = len(df)
        if not df.empty:
            info["start"] = str(df["interval_ending_central"].min())
            info["end"] = str(df["interval_ending_central"].max())
            info["hubs"] = sorted(df["settlement_point"].unique().tolist())
    except Exception as e:  # pragma: no cover - defensive
        info["error"] = str(e)
    return info


# --------------------------------------------------------------------------
# The main update routine
# --------------------------------------------------------------------------

def update(progress_callback=None) -> dict:
    """Fetch new data from ERCOT and merge it into the local store.

    Returns a summary dict. Raises on auth/connection failure.
    """
    log = _make_logger(progress_callback)
    cfg = load_config()
    tokens = TokenManager(cfg, log=log)
    tokens.get()  # log in now so credential errors surface immediately
    key = cfg["subscription_key"]

    existing = load_store()

    # We always aim to cover [backfill_start, today]. The archive step below
    # skips months already present, so this fills any gap (old history you don't
    # have yet) without re-downloading what you do.
    start = date.fromisoformat(cfg.get("backfill_start", DEFAULT_BACKFILL_START))
    end = tz.now_central().date()
    if start > end:
        start = end

    if existing.empty:
        log(f"No local data yet. Backfilling all hubs from {start}.")
    else:
        have_min = existing["delivery_date"].min()
        have_max = existing["delivery_date"].max()
        log(f"Existing data {have_min} → {have_max}. Ensuring coverage back to {start}; "
            f"will fetch only missing months plus the recent window.")

    # The live endpoint only serves ~90 days. Split the work:
    #   * older than LIVE_WINDOW_DAYS  -> archive (slow, checkpointed)
    #   * the recent window            -> live endpoint (fast, per hub)
    live_cutoff = end - timedelta(days=LIVE_WINDOW_DAYS)
    combined = existing

    if start < live_cutoff:
        combined = archive_backfill(tokens, key, start, live_cutoff - timedelta(days=1),
                                    combined, progress=progress_callback, log=log)
        live_start = live_cutoff
    else:
        live_start = start

    log(f"Fetching recent window from the live API ({live_start} -> {end}) ...")
    new_frames = []
    for i, hub in enumerate(HUBS, 1):
        log(f"[{i}/{len(HUBS)}] {hub} ...")
        raw = fetch_settlement_point(tokens, key, hub, live_start, end, log)
        norm = normalize(raw)
        log(f"      got {len(norm):,} intervals.")
        if not norm.empty:
            new_frames.append(norm)

    if new_frames:
        combined = pd.concat([combined, *new_frames], ignore_index=True)

    if combined.empty:
        raise RuntimeError("No data available -- nothing fetched and no existing store.")

    before = len(combined)
    combined = combined.drop_duplicates(
        subset=_dedup_keys(combined),
        keep="last",
    )
    log(f"Merged: {before:,} -> {len(combined):,} rows after de-duplication.")

    save_store(combined)

    summary = {
        "rows": len(combined),
        "new_rows": len(combined) - (len(existing) if not existing.empty else 0),
        "start": str(combined["interval_ending_central"].min()),
        "end": str(combined["interval_ending_central"].max()),
        "hubs": sorted(combined["settlement_point"].unique().tolist()),
        "parquet": str(PARQUET_PATH),
        "csv": str(CSV_PATH),
    }
    write_state({"last_success": tz.now_central().isoformat(), **summary})

    log("")
    log(f"Done. {summary['rows']:,} total intervals "
        f"({summary['start']} -> {summary['end']}).")
    log(f"Saved: {CSV_PATH.name} and {PARQUET_PATH.name} in the data/ folder.")
    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="ERCOT hub 15-min price downloader.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("set-credentials", help="Enter/Save your ERCOT API credentials.")
    sub.add_parser("test-auth", help="Verify your credentials can log in.")
    up = sub.add_parser("update", help="Fetch latest data and update the local store.")
    up.add_argument("--auto", action="store_true",
                    help="Quiet mode for the scheduled weekly job; skips if data is still fresh.")
    sub.add_parser("status", help="Show what's in the local store.")

    args = parser.parse_args(argv)

    if args.cmd == "set-credentials":
        set_credentials_interactive()
        return 0

    if args.cmd == "test-auth":
        try:
            get_access_token(load_config())
            print("Credentials OK.")
            return 0
        except Exception as e:
            print(f"Auth failed: {e}")
            return 1

    if args.cmd == "status":
        print(json.dumps(store_summary(), indent=2, default=str))
        return 0

    if args.cmd == "update":
        if getattr(args, "auto", False) and not is_stale():
            print(f"Data is fresh ({days_since_update():.1f} days old). Skipping.")
            return 0
        try:
            update()
            return 0
        except Exception as e:
            print(f"Update failed: {e}")
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
