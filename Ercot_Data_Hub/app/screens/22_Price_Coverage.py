"""Price data coverage — what hub & node SPP do we actually have on disk?

A read-only inventory page: it scans the hub price store and the per-year node
price parquets in the data lake and reports, per location and per market, the
date span, row count and freshness. Nothing is pulled here — it answers
"do we already have this?" before you go to the Node Explorer / Hub Prices
pages to load or pull a slice.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import paths  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("📊 Price Data Coverage")
st.caption("What hub & resource-node settlement-point prices we already hold in the data lake. "
           "Read-only — pull missing ranges from **Hub Prices** or **Node Explorer**.")

HUB_DAM_PARQUET = paths.HUB_PRICES_DIR / "ercot_hub_dam_hourly.parquet"


def _mb(path: pathlib.Path) -> float:
    return path.stat().st_size / 1_048_576 if path.exists() else 0.0


@st.cache_data(show_spinner="Scanning hub price store…")
def hub_coverage(_rt_mtime: float, _dam_mtime: float) -> pd.DataFrame:
    """One row per (hub, market) with span and row count."""
    rows = []

    # RT15 — interval-ending Central, one wide parquet.
    if paths.HUB_PRICES_PARQUET.exists():
        df = pd.read_parquet(paths.HUB_PRICES_PARQUET,
                             columns=["settlement_point", "interval_ending_central"])
        df["interval_ending_central"] = pd.to_datetime(df["interval_ending_central"])
        g = df.groupby("settlement_point")["interval_ending_central"]
        for hub, span in g.agg(["min", "max", "count"]).iterrows():
            rows.append({"Hub": hub, "Market": "RT15",
                         "Start": span["min"].date(), "End": span["max"].date(),
                         "Rows": int(span["count"])})

    # DAM hourly — separate store, tidy (location, market, interval_start).
    if HUB_DAM_PARQUET.exists():
        df = pd.read_parquet(HUB_DAM_PARQUET, columns=["location", "interval_start"])
        df["interval_start"] = pd.to_datetime(df["interval_start"])
        g = df.groupby("location")["interval_start"]
        for hub, span in g.agg(["min", "max", "count"]).iterrows():
            rows.append({"Hub": hub, "Market": "DAM",
                         "Start": span["min"].date(), "End": span["max"].date(),
                         "Rows": int(span["count"])})

    return pd.DataFrame(rows).sort_values(["Hub", "Market"]).reset_index(drop=True)


@st.cache_data(show_spinner="Scanning node price store…")
def node_coverage(_mtimes: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (per-year summary, per-node detail) for the node_price_<year> files."""
    by_year, by_node = [], []
    template = paths.NODE_DATA_DIR / "node_price_{year}.parquet"
    for year in range(2018, 2031):
        path = pathlib.Path(str(template).format(year=year))
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["location", "interval_start", "market"])
        df["interval_start"] = pd.to_datetime(df["interval_start"])
        markets = sorted(df["market"].dropna().unique().tolist()) if "market" in df else []
        by_year.append({
            "Year": year,
            "Markets": ", ".join(markets) or "—",
            "Nodes": int(df["location"].nunique()),
            "Rows": int(len(df)),
            "Start": df["interval_start"].min().date(),
            "End": df["interval_start"].max().date(),
            "File MB": round(_mb(path), 1),
        })
        g = df.groupby("location")["interval_start"].agg(["min", "max", "count"])
        for node, span in g.iterrows():
            by_node.append({"Node": node, "Year": year,
                            "Start": span["min"].date(), "End": span["max"].date(),
                            "Rows": int(span["count"])})
    ydf = pd.DataFrame(by_year).sort_values("Year").reset_index(drop=True)
    ndf = pd.DataFrame(by_node)
    return ydf, ndf


# --------------------------------------------------------------------------- #
rt_mtime = paths.HUB_PRICES_PARQUET.stat().st_mtime if paths.HUB_PRICES_PARQUET.exists() else 0.0
dam_mtime = HUB_DAM_PARQUET.stat().st_mtime if HUB_DAM_PARQUET.exists() else 0.0
node_files = sorted(paths.NODE_DATA_DIR.glob("node_price_*.parquet"))
node_mtimes = tuple(p.stat().st_mtime for p in node_files)

hub = hub_coverage(rt_mtime, dam_mtime)
year_cov, node_cov = node_coverage(node_mtimes)

# Top-line summary -----------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Hubs", hub["Hub"].nunique() if not hub.empty else 0)
c2.metric("Hub price rows", f"{int(hub['Rows'].sum()):,}" if not hub.empty else "0")
c3.metric("Resource nodes", node_cov["Node"].nunique() if not node_cov.empty else 0)
c4.metric("Node price rows", f"{int(year_cov['Rows'].sum()):,}" if not year_cov.empty else "0")

# Overall span across everything for the freshness line.
spans = []
if not hub.empty:
    spans += [hub["Start"].min(), hub["End"].max()]
if not year_cov.empty:
    spans += [year_cov["Start"].min(), year_cov["End"].max()]
_common.data_status(
    st,
    path=[p for p in [paths.HUB_PRICES_PARQUET, HUB_DAM_PARQUET, *node_files] if p.exists()],
    rows=(int(hub["Rows"].sum()) if not hub.empty else 0)
         + (int(year_cov["Rows"].sum()) if not year_cov.empty else 0),
    span=(min(spans), max(spans)) if spans else None,
    fresh_within_days=7,
)

st.divider()

# Hubs -----------------------------------------------------------------------
st.subheader("💵 Hub settlement points")
if hub.empty:
    _common.empty_state(
        st, "No hub prices on disk yet.",
        hint="Refresh *Hub prices* from the Control Tower.",
        page="views/home.py", page_label="Go to Control Tower", stop=False)
else:
    st.caption(f"{hub['Hub'].nunique()} hubs · markets: "
               f"{', '.join(sorted(hub['Market'].unique()))}. "
               "RT15 = real-time 15-min (interval-ending); DAM = day-ahead hourly.")
    st.dataframe(hub, use_container_width=True, hide_index=True,
                 column_config={"Rows": st.column_config.NumberColumn(format="%,d")})
    _export.download_block(st, hub, name="hub_price_coverage",
                           title="Hub coverage", key="cov_hub")

st.divider()

# Nodes ----------------------------------------------------------------------
st.subheader("📈 Resource nodes — by year")
if year_cov.empty:
    _common.empty_state(
        st, "No node prices on disk yet.",
        hint="Pull a node + date range from the Node Explorer to populate the store.",
        page="screens/5_Node_Explorer.py", page_label="Go to Node Explorer", stop=False)
else:
    st.dataframe(year_cov, use_container_width=True, hide_index=True,
                 column_config={"Rows": st.column_config.NumberColumn(format="%,d"),
                                "Year": st.column_config.NumberColumn(format="%d")})
    st.caption("Node SPP is stored one parquet per year. RT15 retained ~2yr on ERCOT MIS; "
               "DAM appears in newer pulls. Coverage is sparse — only nodes you've pulled are kept.")

    # Per-node detail (potentially hundreds of nodes) — searchable.
    with st.expander(f"🔎 Per-node detail ({node_cov['Node'].nunique()} nodes)", expanded=False):
        q = st.text_input("Node name contains", placeholder="e.g. AZURE, MLB_SLR, RTS")
        view = node_cov
        if q:
            view = view[view["Node"].str.contains(q, case=False, na=False)]
        # Collapse multi-year nodes into one row: overall span + total rows + years held.
        agg = (view.groupby("Node")
               .agg(Start=("Start", "min"), End=("End", "max"),
                    Rows=("Rows", "sum"), Years=("Year", lambda s: ", ".join(map(str, sorted(s)))))
               .reset_index()
               .sort_values("Node"))
        st.caption(f"{len(agg):,} node(s)")
        st.dataframe(agg, use_container_width=True, hide_index=True,
                     column_config={"Rows": st.column_config.NumberColumn(format="%,d")})
        _export.download_block(st, agg, name="node_price_coverage",
                               title="Node coverage", key="cov_node")
