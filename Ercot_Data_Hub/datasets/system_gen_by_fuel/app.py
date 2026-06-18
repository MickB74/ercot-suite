"""Streamlit explorer for ERCOT resource nodes — search, then view a node's
15-minute generation against its settlement-point price.

Reads the per-node parquets in node_data/ and can pull missing ranges on demand
(SCED generation has a ~60-day lag; SPP price is available to ~recent days).

Run:  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import resource_catalog as rc
import node_generation as ng
import node_prices as npx
import pull_nodes as pn
import settlement_points as sp

st.set_page_config(page_title="ERCOT Settlement Points", layout="wide")
st.title("⚡ ERCOT Settlement Points — Generation & Price")


# --- data helpers ----------------------------------------------------------
@st.cache_data(show_spinner=False)
def _catalog_mtime() -> float:
    return os.path.getmtime(rc.CATALOG_PATH) if os.path.exists(rc.CATALOG_PATH) else 0.0


@st.cache_data(show_spinner=False)
def search_catalog(query: str, rtype: str, _mtime: float) -> pd.DataFrame:
    return rc.search(query or None, rtype or None)


def _read_stored(template: str, key_node_col: str, nodes: list[str],
                 start: pd.Timestamp, end_excl: pd.Timestamp) -> pd.DataFrame:
    """Read stored rows for the given nodes across the relevant yearly files."""
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
    # RT15 only — DAM is not surfaced (historical DAM isn't reliably available);
    # drop any DAM rows left in the cache from old pulls.
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


# --- sidebar: settlement-point selection -----------------------------------
with st.sidebar:
    st.header("Settlement point")
    location_type = st.radio("Type", sp.LOCATION_TYPES, horizontal=False)
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

        col_b1, col_b2 = st.columns(2)
        if col_b1.button("Rebuild catalog"):
            with st.spinner("Rebuilding…"):
                rc.build_catalog()
            st.cache_data.clear()
            st.rerun()
        if col_b2.button("…with types", help="Adds resource types (pulls one SCED day, slower)"):
            with st.spinner("Rebuilding with types…"):
                rc.build_catalog(with_types=True)
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
    from ercot_core import tz
    today = tz.now_central().tz_localize(None).normalize()  # ERCOT "today"
    default_start = (today - pd.Timedelta(days=7)).date()
    start_d = st.date_input("Start", value=default_start)
    end_d = st.date_input("End (inclusive)", value=today.date())

    st.header("Series")
    want_gen = is_node and st.checkbox("Generation (SCED, ~60-day lag)", value=True)
    want_price = st.checkbox("Price (SPP RT15)", value=True)

    load = st.button("Load data", type="primary")
    pull = st.button("Pull/refresh from ERCOT",
                     help="Fetch this range live and store it (idempotent)")

# --- main ------------------------------------------------------------------
if not locs:
    st.info(f"Pick at least one {location_type.lower()} in the sidebar.")
    st.stop()

start = pd.Timestamp(start_d)
end_excl = pd.Timestamp(end_d) + pd.Timedelta(days=1)

if pull:
    pull_and_store(locs, location_type, start, pd.Timestamp(end_d), want_gen, want_price)
    st.success("Pulled and stored. Showing data below.")

if not (load or pull):
    st.info("Set your selection, then click **Load data** (or **Pull/refresh from ERCOT** "
            "to fetch a range you haven't stored yet).")
    st.stop()

gen = read_generation(locs, start, end_excl) if want_gen else pd.DataFrame()
price = read_price(locs, start, end_excl) if want_price else pd.DataFrame()

if gen.empty and price.empty:
    st.warning("No stored data for that selection. Click **Pull/refresh from ERCOT** to fetch it. "
               "(Generation needs dates ~60+ days in the past.)")
    st.stop()

# node-level generation = sum of the node's units per interval
gen_node = (gen.groupby(["resource_node", "interval_start"], as_index=False)["mw"].sum()
            if not gen.empty else pd.DataFrame())

# --- combined chart: generation (MW, left) vs RT15 price ($/MWh, right) ----
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

fig.update_layout(
    height=520, hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    yaxis=dict(title="Generation (MW)"),
    yaxis2=dict(title="Price ($/MWh)", overlaying="y", side="right", showgrid=False),
    margin=dict(l=10, r=10, t=40, b=10),
)
st.plotly_chart(fig, use_container_width=True)

# --- summary + downloads ---------------------------------------------------
c1, c2 = st.columns(2)
with c1:
    st.subheader("Generation")
    if gen_node.empty:
        st.caption("none")
    else:
        summ = (gen_node.groupby("resource_node")["mw"]
                .agg(["count", "mean", "max"]).round(1)
                .rename(columns={"count": "intervals", "mean": "avg MW", "max": "peak MW"}))
        st.dataframe(summ, use_container_width=True)
        st.download_button("⬇ generation CSV", gen.to_csv(index=False),
                           file_name="node_generation.csv", mime="text/csv")
with c2:
    st.subheader("Price")
    if price.empty:
        st.caption("none")
    else:
        summ = (price.groupby(["location", "market"])["spp"]
                .agg(["count", "mean", "max", "min"]).round(2)
                .rename(columns={"count": "intervals", "mean": "avg", "max": "max", "min": "min"}))
        st.dataframe(summ, use_container_width=True)
        st.download_button("⬇ price CSV", price.to_csv(index=False),
                           file_name="node_price.csv", mime="text/csv")
