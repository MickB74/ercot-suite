"""interconnection.fyi client — resolve an ERCOT queue id to its canonical
project name (and POI / county / capacity / status).

Why this helps: ERCOT's live GIS queue drops long-operational projects, so a
queue id like 21INR0477 (Azure Sky Solar, operational since 2021) isn't in the
gridstatus queue anymore. interconnection.fyi keeps operational projects, so it
fills that gap — turning a bare queue id back into a name we can map to a
resource node.

It's a Next.js site; each project page embeds a clean JSON record in
``__NEXT_DATA__``. We fetch one page per id, on demand, and cache the result
locally (be polite — no bulk crawling). Names found here are the strongest
input to the resource-node search.
"""

from __future__ import annotations

import json
import re
import time

import requests

from ercot_core import paths

_BASE = "https://www.interconnection.fyi/project"
_SITE = "https://www.interconnection.fyi"
_UA = {"User-Agent": "ERCOT-Data-Hub research crawler (contact michaelbarry@sustainround.com)"}
_CACHE = paths.PLANT_SCED_DIR / "ifyi_cache.json"
_TIMEOUT = 30

# Map the rich record's keys to our tidy names.
_FIELDS = {
    "Queue ID": "queue_id",
    "Project Name": "name",
    "Interconnecting Entity": "entity",
    "County": "county",
    "State": "state",
    "Interconnection Location": "poi",
    "Capacity (MW)": "capacity_mw",
    "Fuel": "fuel",
    "Status": "status",
    "Power Market": "market",
    "Queue Date": "queue_date",
    "Actual Completion Date": "actual_completion",
    "Proposed Completion Date": "proposed_completion",
}


def normalize_id(raw: str, market: str = "ercot") -> str:
    """'21INR0477' / 'ercot-21inr0477' / 'ERCOT-21inr0477' -> 'ercot-21inr0477'."""
    s = str(raw).strip().lower()
    s = re.sub(r"^ercot[-_ ]", "", s)
    return f"{market}-{s}"


def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(cache, indent=2, default=str))


def fetch_project(queue_id: str, market: str = "ercot",
                  allow_fetch: bool = True, refresh: bool = False) -> dict | None:
    """Tidy record for one project, or None if not found.

    Keys: queue_id, name, entity, county, state, poi, capacity_mw, fuel,
    status, market, url. Cached locally by unique id.
    """
    uid = normalize_id(queue_id, market)
    cache = _load_cache()
    if uid in cache and not refresh:
        return cache[uid]
    if not allow_fetch:
        return None

    url = f"{_BASE}/{uid}"
    try:
        resp = requests.get(url, headers=_UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        rec = data["props"]["pageProps"]["serializedProjectProps"]
        if isinstance(rec, str):
            rec = json.loads(rec)
        rec = rec.get("json", rec)
    except Exception:
        return None

    out = {tidy: rec.get(raw) for raw, tidy in _FIELDS.items()}
    out["url"] = url
    cache[uid] = out
    _save_cache(cache)
    return out


def name_for(queue_id: str, **kw) -> str | None:
    """Just the canonical project name for a queue id (or None)."""
    rec = fetch_project(queue_id, **kw)
    return rec.get("name") if rec else None


# ── bulk crawl: every ERCOT project (robots-allowed; sitemap-discovered) ─────
def _build_id() -> str | None:
    try:
        html = requests.get(_SITE + "/", headers=_UA, timeout=_TIMEOUT).text
        m = re.search(r'"buildId":"([^"]+)"', html)
        return m.group(1) if m else None
    except requests.RequestException:
        return None


def ercot_project_ids() -> list[str]:
    """All ERCOT project unique-ids (e.g. 'ercot-21inr0477') from the sitemap."""
    idx = requests.get(_SITE + "/sitemap.xml", headers=_UA, timeout=_TIMEOUT).text
    ids: list[str] = []
    for child in re.findall(r"<loc>(.*?)</loc>", idx):
        if not child.endswith(".xml"):
            continue
        t = requests.get(child, headers=_UA, timeout=_TIMEOUT).text
        ids += re.findall(r"/project/(ercot-[0-9a-zA-Z]+)", t)
    return sorted(set(ids))


def _tidy(rec: dict, uid: str) -> dict:
    out = {tidy: rec.get(raw) for raw, tidy in _FIELDS.items()}
    out["url"] = f"{_BASE}/{uid}"
    return out


def _fetch_record(uid: str, build_id: str | None) -> dict | None:
    """One project's tidy record via the light _next/data JSON (HTML fallback)."""
    if build_id:
        try:
            r = requests.get(f"{_SITE}/_next/data/{build_id}/project/{uid}.json",
                             headers=_UA, timeout=_TIMEOUT)
            if r.status_code == 200:
                rec = r.json()["pageProps"]["serializedProjectProps"]
                if isinstance(rec, str):
                    rec = json.loads(rec)
                return _tidy(rec.get("json", rec), uid)
        except Exception:
            pass
    # fallback: full page __NEXT_DATA__
    return fetch_project(uid.split("ercot-")[-1])


def fetch_all_ercot(throttle: float = 0.3, limit: int | None = None, progress=None) -> int:
    """Crawl every ERCOT project into the local cache, then write a parquet.

    Resumable (skips ids already cached), throttled, and polite. Returns the
    number of projects in the consolidated dataset. `progress(done, total, uid)`
    is an optional callback.
    """
    import pandas as pd

    ids = ercot_project_ids()
    if limit:
        ids = ids[:limit]
    cache = _load_cache()
    build_id = _build_id()
    total = len(ids)
    done = 0
    for i, uid in enumerate(ids):
        if uid not in cache:
            rec = _fetch_record(uid, build_id)
            if rec:
                cache[uid] = rec
                done += 1
                if done % 50 == 0:
                    _save_cache(cache)          # periodic checkpoint
            time.sleep(throttle)
        if progress and i % 25 == 0:
            progress(i + 1, total, uid)
    _save_cache(cache)

    rows = [v for k, v in cache.items() if str(k).startswith("ercot-")]
    df = pd.DataFrame(rows)
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(paths.IFYI_ERCOT_PARQUET, index=False)
    if progress:
        progress(total, total, "done")
    return len(df)


def load_ercot_projects():
    """The consolidated interconnection.fyi ERCOT dataset (after fetch_all_ercot)."""
    import pandas as pd
    if paths.IFYI_ERCOT_PARQUET.exists():
        return pd.read_parquet(paths.IFYI_ERCOT_PARQUET)
    return pd.DataFrame()


if __name__ == "__main__":
    import sys
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    n = fetch_all_ercot(limit=lim, progress=lambda d, t, u: print(f"  {d}/{t} … {u}", flush=True))
    print(f"interconnection.fyi ERCOT projects cached: {n:,} -> {paths.IFYI_ERCOT_PARQUET}")
