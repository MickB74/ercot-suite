"""Node explorer — a resource node's 15-min SCED generation vs its SPP price.

Reads the per-node parquets in the data lake and can pull missing ranges on
demand (SCED generation ~60-day lag; SPP price available to ~recent days).
"""

from __future__ import annotations

import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import resource_catalog as rc  # noqa: E402
import node_generation as ng  # noqa: E402
import node_prices as npx  # noqa: E402
import pull_nodes as pn  # noqa: E402
import settlement_points as sp  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("📈 ERCOT Settlement Points — Generation & Price")


@st.cache_data(show_spinner=False)
def _catalog_mtime() -> float:
    return os.path.getmtime(rc.CATALOG_PATH) if os.path.exists(rc.CATALOG_PATH) else 0.0


@st.cache_data(show_spinner=False)
def search_catalog(query: str, rtype: str, _mtime: float) -> pd.DataFrame:
    return rc.search(query or None, rtype or None)


def _read_stored(template, key_node_col, nodes, start, end_excl):
    frames = []
    for year in range(start.year, end_excl.year + 1):
        path = pn._path(template, year)
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        df = df[df[key_node_col].isin(nodes)]
        df = df[(df["interval_start"] >= start) & (df["interval_start"] < end_excl)]
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def read_generation(locs, start, end_excl):
    return _read_stored(pn.GEN_TEMPLATE, "resource_node", locs, start, end_excl)


def read_price(locs, start, end_excl):
    # RT15 only — DAM is not surfaced (hub store is RT15-only and historical DAM
    # isn't reliably available); drop any DAM rows left in the cache from old pulls.
    df = _read_stored(pn.PRICE_TEMPLATE, "location", locs, start, end_excl)
    return df[df["market"] == "RT15"] if (not df.empty and "market" in df.columns) else df


def pull_and_store(locs, location_type, start, end, want_gen, want_price):
    fetched_at = pd.Timestamp.now(tz="UTC")
    if want_price:
        with st.spinner("Pulling settlement-point prices (SPP)…"):
            price = npx.fetch_prices(locs, start, end, location_type=location_type,
                                     markets=["RT15"], fetched_at=fetched_at, verbose=False)
            pn._merge_save(price, pn.PRICE_TEMPLATE, pn.PRICE_KEY)
    if want_gen:
        with st.spinner("Pulling SCED generation (60-day-lagged, can be slow)…"):
            gen = ng.fetch_generation(locs, start, end, fetched_at=fetched_at, verbose=False)
            pn._merge_save(gen, pn.GEN_TEMPLATE, pn.GEN_KEY)


with st.container(border=True):
    st.header("Settlement point")
    location_type = st.radio("Type", sp.LOCATION_TYPES)
    is_node = location_type == "Resource Node"

    if is_node:
        if not os.path.exists(rc.CATALOG_PATH):
            st.warning("No node catalog yet.")
            if st.button("Build catalog"):
                with st.spinner("Building resource-node catalog…"):
                    rc.build_catalog()
                st.cache_data.clear()
                st.rerun()
            st.stop()
        if st.button("Rebuild catalog"):
            with st.spinner("Rebuilding…"):
                rc.build_catalog()
            st.cache_data.clear()
            st.rerun()
        query = st.text_input("Name contains", placeholder="e.g. RNCH, SOLAR, WIND")
        cat = rc.load_catalog()
        types = sorted([t for t in cat["resource_type"].dropna().unique()])
        rtype = st.selectbox("Resource type", [""] + types) if types else ""
        matches = search_catalog(query, rtype, _catalog_mtime())
        loc_options = sorted(matches["resource_node"].unique().tolist())
        st.caption(f"{len(loc_options)} node(s) match")
    else:
        loc_options = sp.locations(location_type)
        st.caption(f"{location_type} — price only (no generation)")

    locs = st.multiselect(f"{location_type}(s)", loc_options,
                          default=loc_options[:1] if loc_options else [])

    st.header("Date range")
    start_d, end_d = _common.period_picker(st, key="node", default_mode="Custom days")
    st.caption(f"**{start_d} → {end_d}**")

    st.header("Series")
    want_gen = is_node and st.checkbox("Generation (SCED, ~60-day lag)", value=True)
    want_price = st.checkbox("Price (SPP RT15)", value=True)

    load = st.button("Load data", type="primary")
    pull = st.button("Pull/refresh from ERCOT")

_common.assumptions_bar(st, {"📍": ", ".join(locs) if locs else "—",
                             "🏷️": location_type, "📅": f"{start_d} → {end_d}"})

if not locs:
    st.info(f"Pick at least one {location_type.lower()} above.")
    st.stop()

start = pd.Timestamp(start_d)
end_excl = pd.Timestamp(end_d) + pd.Timedelta(days=1)

if pull:
    pull_and_store(locs, location_type, start, pd.Timestamp(end_d), want_gen, want_price)
    st.success("Pulled and stored.")

if not (load or pull):
    st.info("Set your selection, then **Load data** (or **Pull/refresh from ERCOT**).")
    st.stop()

gen = read_generation(locs, start, end_excl) if want_gen else pd.DataFrame()
price = read_price(locs, start, end_excl) if want_price else pd.DataFrame()

if gen.empty and price.empty:
    st.warning("No stored data for that selection. Click **Pull/refresh from ERCOT**. "
               "(Generation needs dates ~60+ days in the past.)")
    st.stop()

gen_node = (gen.groupby(["resource_node", "interval_start"], as_index=False)["mw"].sum()
            if not gen.empty else pd.DataFrame())

fig = go.Figure()
for loc in locs:
    if not gen_node.empty:
        g = gen_node[gen_node["resource_node"] == loc].sort_values("interval_start")
        if not g.empty:
            fig.add_trace(go.Scatter(x=g["interval_start"], y=g["mw"],
                                     name=f"{loc} — gen MW", mode="lines", yaxis="y1"))
    if not price.empty:
        p = price[price["location"] == loc].sort_values("interval_start")
        if not p.empty:
            fig.add_trace(go.Scatter(x=p["interval_start"], y=p["spp"],
                                     name=f"{loc} — RT15 $/MWh", mode="lines", yaxis="y2"))
fig.update_layout(height=520, hovermode="x unified",
                  legend=dict(orientation="h", yanchor="bottom", y=1.02),
                  yaxis=dict(title="Generation (MW)"),
                  yaxis2=dict(title="Price ($/MWh)", overlaying="y", side="right", showgrid=False),
                  margin=dict(l=10, r=10, t=40, b=10))
st.plotly_chart(fig, use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.subheader("Generation")
    if gen_node.empty:
        st.caption("none")
    else:
        st.dataframe(gen_node.groupby("resource_node")["mw"].agg(["count", "mean", "max"]).round(1),
                     use_container_width=True)
        _export.download_block(st, gen, name="node_generation",
                               title="Node generation", key="ndx_gen")
with c2:
    st.subheader("Price")
    if price.empty:
        st.caption("none")
    else:
        st.dataframe(price.groupby(["location", "market"])["spp"].agg(["count", "mean", "max", "min"]).round(2),
                     use_container_width=True)
        _export.download_block(st, price, name="node_price",
                               title="Node price", key="ndx_price")
