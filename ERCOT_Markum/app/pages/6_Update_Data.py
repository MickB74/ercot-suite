"""Update data — pull the latest ERCOT generation + prices for this project.

Tops up the shared Hub's data lake for this asset's node, straight from the app
(the portal runs in the Hub's venv, so it has gridstatus + ERCOT credentials).
Incremental by default — re-pulls a short overlap before the last cached day
through the latest available date; "Full rebuild" goes back to the backfill start.
Same engine as the ``refresh.py`` command, with a progress bar.
"""

from __future__ import annotations

import datetime as dt

import _boot  # noqa: F401
import pandas as pd
import streamlit as st

_boot.ensure_hub(st)

from markum import branding, contract, hub  # noqa: E402

# Self-contained cached-window logic (mirrors refresh.py; kept local so this page
# works the same across portals regardless of their refresh.py internals).
BACKFILL_START = dt.date(2025, 1, 1)   # earliest data the portal cares about
OVERLAP_DAYS = 5                       # re-pull this many days before the last cached day

a = contract.ASSET
node = a["resource_node"]


def _cached_max(read_fn, node):
    """Latest cached interval date for a stream (scans all years), or None."""
    latest = None
    for year in range(BACKFILL_START.year, dt.date.today().year + 1):
        df = read_fn(node, pd.Timestamp(year, 1, 1), pd.Timestamp(year + 1, 1, 1))
        if df is not None and not df.empty:
            mx = pd.to_datetime(df["interval_start"]).max().date()
            latest = mx if latest is None else max(latest, mx)
    return latest


def _start_for(cached_max, full):
    if full or cached_max is None:
        return BACKFILL_START
    return max(BACKFILL_START, cached_max - dt.timedelta(days=OVERLAP_DAYS))

branding.hero(st, "Update data",
              "Pull the latest ERCOT generation and prices for this project into the "
              "shared data lake.")

ws, we = hub.settlement_window(node)
if ws:
    st.caption(f"Settlement window currently **{ws} → {we}** for node `{node}`.")
else:
    st.caption(f"No cached data yet for node `{node}`.")

st.info("Generation (SCED) publishes on a **~60-day lag**, so the most recent ~2 months "
        "can't be pulled yet. Recent prices come from ERCOT's live window (fast); older "
        "months come from the archive (slower). Needs ERCOT API credentials (set in the Hub).")

c1, c2 = st.columns(2)
do_gen = c1.checkbox("Generation (SCED)", value=True)
do_price = c2.checkbox("Prices (RT15)", value=True)
full = st.checkbox(f"Full rebuild (from {BACKFILL_START})", value=False,
                   help="Re-pull everything from the backfill start instead of just topping "
                        "up the last cached day. Slow — only needed to repair gaps.")


def _month_ranges(start_ts, end_ts):
    out, cur = [], pd.Timestamp(start_ts).normalize()
    end_ts = pd.Timestamp(end_ts).normalize()
    while cur <= end_ts:
        nxt = (cur + pd.offsets.MonthBegin(1)).normalize()
        out.append((cur, min(end_ts, nxt - pd.Timedelta(days=1))))
        cur = nxt
    return out


if st.button("⬇️ Refresh now", type="primary", disabled=not (do_gen or do_price)):
    try:
        pull_nodes, node_generation, spp_archive, sced = hub.datasets()
        latest = sced.latest_available_date()

        gen_chunks = price_chunks = []
        if do_gen:
            gstart = _start_for(_cached_max(hub.generation, node), full)
            gen_chunks = _month_ranges(gstart, latest) if gstart <= latest else []
        if do_price:
            pstart = _start_for(_cached_max(hub.node_prices, node), full)
            price_chunks = _month_ranges(pstart, latest) if pstart <= latest else []

        total = len(gen_chunks) + len(price_chunks)
        if total == 0:
            st.success(f"Already current — cached through the latest available date ({latest}).")
        else:
            bar = st.progress(0.0, text="Starting…")
            done = 0
            for cs, ce in gen_chunks:
                bar.progress(done / total, text=f"Generation · {cs:%b %Y}")
                g = node_generation.fetch_generation([node], cs, ce, verbose=False)
                if not g.empty:
                    pull_nodes._merge_save(g, pull_nodes.GEN_TEMPLATE, pull_nodes.GEN_KEY)
                done += 1
            for cs, ce in price_chunks:
                bar.progress(done / total, text=f"Prices · {cs:%b %Y} (archive can be slow)")
                p = spp_archive.fetch_rtm_spp([node], cs, ce, location_type="Resource Node",
                                              log=lambda *a: None)
                if not p.empty:
                    pull_nodes._merge_save(p, pull_nodes.PRICE_TEMPLATE, pull_nodes.PRICE_KEY)
                done += 1
            bar.progress(1.0, text="Done")
            st.cache_data.clear()
            nws, nwe = hub.settlement_window(node)
            st.success(f"✓ Updated. Settlement window is now **{nws} → {nwe}**.")
            st.caption("Reload the other pages to see the extended history.")
    except Exception as exc:  # noqa: BLE001 — surface creds/network failures plainly
        st.error(f"Refresh failed: {exc}")
        st.caption("If this mentions credentials, set the ERCOT API keys in the Hub "
                   "(API Keys page / config.json).")
