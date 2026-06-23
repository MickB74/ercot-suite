"""Update data — pull the latest ERCOT generation, prices, and hub prices.

Tops up the shared Hub's data lake for this asset's node and the trading
hub prices that the Hub vs Node page needs. Incremental by default — re-pulls
a short overlap before the last cached day; "Full rebuild" goes back to the
backfill start. Same engine as ``refresh.py``, with a progress bar.
"""

from __future__ import annotations

import datetime as dt
import sys

import _boot  # noqa: F401
import pandas as pd
import streamlit as st

_boot.ensure_hub(st)

from markum import branding, contract, hub  # noqa: E402

BACKFILL_START = dt.date(2024, 1, 1)
OVERLAP_DAYS   = 5

a    = contract.ASSET
node = a["resource_node"]
loc  = contract.settle_location(contract.load_contract())  # settlement reference (hub or node)


def _cached_max(read_fn, node):
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


def _month_ranges(start_ts, end_ts):
    out, cur = [], pd.Timestamp(start_ts).normalize()
    end_ts = pd.Timestamp(end_ts).normalize()
    while cur <= end_ts:
        nxt = (cur + pd.offsets.MonthBegin(1)).normalize()
        out.append((cur, min(end_ts, nxt - pd.Timedelta(days=1))))
        cur = nxt
    return out


branding.hero(st, "Update data",
              "Pull the latest ERCOT generation and prices for this project.")

ws, we = hub.settlement_window(node, loc)
if ws:
    st.caption(f"Settlement window currently **{ws} → {we}** for node `{node}`.")
else:
    st.caption(f"No cached data yet for node `{node}`.")

# ── what to update ───────────────────────────────────────────────────────────
st.subheader("This asset")
st.info("Generation (SCED) publishes on a **~60-day lag**, so the most recent ~2 months "
        "can't be pulled yet. Recent prices come from ERCOT's live window; older months "
        "come from the archive (slower).")

c1, c2 = st.columns(2)
do_gen   = c1.checkbox("Generation (SCED)", value=True)
do_price = c2.checkbox("Node prices (RT15)", value=True)
full     = st.checkbox(f"Full rebuild from {BACKFILL_START}", value=False,
                       help="Re-pull everything from the backfill start instead of "
                            "just topping up the last cached day. Slow — only needed "
                            "to repair gaps.")

st.divider()
st.subheader("Hub prices (Data Hub)")
st.caption("Hub RT15 prices (HB_NORTH, HB_HOUSTON, etc.) are stored in the shared Data Hub. "
           "Updating them here keeps the **Hub vs Node** page current.")

from ercot_core import prices as PX  # noqa: E402
cov = PX.hub_store_coverage()
if cov:
    st.caption(f"Hub store currently covers **{cov[0].date()} → {cov[1].date()}**.")
else:
    st.caption("Hub store: no data cached yet.")

do_hub = st.checkbox("Hub prices (HB_* RT15)", value=True)

st.divider()

if st.button("⬇️ Refresh now", type="primary",
             disabled=not (do_gen or do_price or do_hub)):
    # ── node generation + prices ─────────────────────────────────────────────
    if do_gen or do_price:
        try:
            pull_nodes, node_generation, spp_archive, sced = hub.datasets()
            latest = sced.latest_available_date()

            gen_chunks   = []
            price_chunks = []
            if do_gen:
                gstart = _start_for(_cached_max(hub.generation, node), full)
                gen_chunks = _month_ranges(gstart, latest) if gstart <= latest else []
            if do_price:
                pstart = _start_for(_cached_max(hub.node_prices, node), full)
                price_chunks = _month_ranges(pstart, latest) if pstart <= latest else []

            total = len(gen_chunks) + len(price_chunks)
            if total == 0:
                st.success(f"Node data already current through {latest}.")
            else:
                bar = st.progress(0.0, text="Starting…")
                done = 0
                for cs, ce in gen_chunks:
                    bar.progress(done / total, text=f"Generation · {cs:%b %Y}")
                    g = node_generation.fetch_generation([node], cs, ce, verbose=False)
                    if not g.empty:
                        pull_nodes._merge_save(g, pull_nodes.GEN_TEMPLATE,
                                               pull_nodes.GEN_KEY)
                    done += 1
                for cs, ce in price_chunks:
                    bar.progress(done / total,
                                 text=f"Node prices · {cs:%b %Y} (archive can be slow)")
                    p = spp_archive.fetch_rtm_spp([node], cs, ce,
                                                  location_type="Resource Node",
                                                  log=lambda *a: None)
                    if not p.empty:
                        pull_nodes._merge_save(p, pull_nodes.PRICE_TEMPLATE,
                                               pull_nodes.PRICE_KEY)
                    done += 1
                bar.progress(1.0, text="Done")
                hub.clear_data_caches()   # reflect the just-saved parquet data
                nws, nwe = hub.settlement_window(node, loc)
                st.success(f"✓ Node data updated — settlement window now **{nws} → {nwe}**.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Node refresh failed: {exc}")

    # ── hub prices via orchestrate ───────────────────────────────────────────
    if do_hub:
        hub_root = hub.hub_root()
        if str(hub_root) not in sys.path:
            sys.path.insert(0, str(hub_root))
        try:
            import orchestrate  # noqa: PLC0415
            with st.status("Updating hub prices…", expanded=True) as status:
                lines = []
                rc = None
                gen = orchestrate.stream_job("hub_prices")
                try:
                    while True:
                        line = next(gen)
                        lines.append(line)
                        # Show only meaningful lines (skip blank + raw debug)
                        if line.strip() and not line.startswith("DEBUG"):
                            st.write(line)
                except StopIteration as stop:
                    rc = stop.value or 0
                if rc == 0:
                    cov2 = PX.hub_store_coverage()
                    cov_str = (f" Hub store now covers **{cov2[0].date()} → {cov2[1].date()}**."
                               if cov2 else "")
                    status.update(label=f"✓ Hub prices updated.{cov_str}",
                                  state="complete")
                else:
                    status.update(label=f"Hub price update failed (exit {rc}).",
                                  state="error")
        except ImportError:
            st.error("Could not import `orchestrate` from the Data Hub. "
                     "Make sure the portal is running from the Hub's venv.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Hub price refresh failed: {exc}")

    st.cache_data.clear()
    st.caption("Reload the other pages to see the extended history.")

branding.footer(st)
