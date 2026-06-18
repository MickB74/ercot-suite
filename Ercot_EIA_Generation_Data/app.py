"""Streamlit explorer for ERCOT EIA-923 monthly generation & fuel data.

Run:  streamlit run app.py
Data: built by build_cache.py into eia923_<region>_<year>.parquet files.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import streamlit as st

import eia923

st.set_page_config(page_title="ERCOT EIA-923 Generation", layout="wide")

# Stable color per fuel category across all charts.
FUEL_COLORS = {
    "Gas": "#d62728", "Coal": "#7f7f7f", "Nuclear": "#9467bd",
    "Wind": "#2ca02c", "Solar": "#ff7f0e", "Hydro": "#1f77b4",
    "Biomass": "#8c564b", "Geothermal": "#e377c2", "Oil": "#bcbd22",
    "Other Gas": "#17becf", "Storage": "#aec7e8", "Other": "#c7c7c7",
}


@st.cache_data(show_spinner=False)
def load(region: str, years: tuple[int, ...]) -> pd.DataFrame:
    return eia923.load(years=list(years), region=region)


def fmt_mwh(x: float) -> str:
    if x >= 1e9:
        return f"{x / 1e9:.1f} TWh"
    if x >= 1e6:
        return f"{x / 1e6:.1f} GWh"
    return f"{x:,.0f} MWh"


# --------------------------------------------------------------------------- #
# Sidebar — region, data availability, on-demand build
# --------------------------------------------------------------------------- #

st.sidebar.title("EIA-923 · ERCOT")
region = st.sidebar.selectbox(
    "Region", ["ercot", "tx", "all"], index=0,
    help="ercot = balancing authority ERCO · tx = all Texas plants · all = US",
)

cached = eia923.available_years(region=region)

with st.sidebar.expander("Get / update data", expanded=not cached):
    import tzutil
    this_year = tzutil.now_central().year
    build_year = st.number_input("Year", min_value=2008, max_value=this_year,
                                 value=this_year - 1, step=1)
    force = st.checkbox("Force re-download", value=False,
                        help="Current-year files are revised monthly.")
    if st.button(f"Download & build {int(build_year)}"):
        with st.spinner(f"Downloading EIA-923 {int(build_year)} from eia.gov…"):
            try:
                df = eia923.build_year(int(build_year), region=region,
                                       force_download=force)
                st.success(f"Built {len(df):,} rows for {int(build_year)}.")
                load.clear()
            except Exception as exc:
                st.error(f"Failed: {exc}")
    st.caption("CLI: `python build_cache.py 2020 2024`")

if not cached:
    st.title("ERCOT EIA-923 Generation & Fuel")
    st.info("No cached data yet. Use **Get / update data** in the sidebar, "
            "or run `python build_cache.py` from the project folder.")
    st.stop()

years = st.sidebar.multiselect("Years", cached, default=cached)
if not years:
    st.warning("Select at least one year.")
    st.stop()

df = load(region, tuple(sorted(years)))
if df.empty:
    st.warning("No rows for the selected years.")
    st.stop()

# --------------------------------------------------------------------------- #
# Sidebar — filters
# --------------------------------------------------------------------------- #

cats = sorted(df["fuel_category"].dropna().unique())
sel_cats = st.sidebar.multiselect("Fuel category", cats, default=cats)
sectors = sorted(df["sector"].dropna().unique())
sel_sectors = st.sidebar.multiselect("Sector", sectors, default=sectors)
search = st.sidebar.text_input("Plant / operator search").strip().lower()

mask = df["fuel_category"].isin(sel_cats) & df["sector"].isin(sel_sectors)
if search:
    mask &= (
        df["plant_name"].fillna("").str.lower().str.contains(search)
        | df["operator_name"].fillna("").str.lower().str.contains(search)
    )
fdf = df[mask]

# --------------------------------------------------------------------------- #
# Header + KPIs
# --------------------------------------------------------------------------- #

st.title("ERCOT EIA-923 Generation & Fuel")
st.caption(f"Region **{region.upper()}** · {min(years)}–{max(years)} · "
           f"plant-level monthly net generation and fuel consumption (EIA Form 923)")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Net generation", fmt_mwh(fdf["netgen_mwh"].sum()))
c2.metric("Plants", f"{fdf['plant_id'].nunique():,}")
c3.metric("Fuel burned", f"{fdf['total_mmbtu'].sum() / 1e6:,.1f} MMMBtu")
top_fuel = (fdf.groupby("fuel_category")["netgen_mwh"].sum().idxmax()
            if not fdf.empty else "—")
c4.metric("Top fuel", top_fuel)

tab_mix, tab_trend, tab_plants, tab_data = st.tabs(
    ["Fuel mix", "Monthly trend", "Plants", "Data"])

# --------------------------------------------------------------------------- #
# Fuel mix
# --------------------------------------------------------------------------- #

with tab_mix:
    by_fuel = (fdf.groupby("fuel_category")["netgen_mwh"].sum()
               .sort_values(ascending=False))
    left, right = st.columns([2, 1])
    with left:
        st.subheader("Net generation by fuel")
        st.bar_chart(by_fuel, color="#2ca02c")
    with right:
        st.subheader("Share")
        share = (by_fuel / by_fuel.sum() * 100).round(1)
        st.dataframe(
            share.rename("% of net gen").reset_index(),
            hide_index=True, use_container_width=True,
        )

# --------------------------------------------------------------------------- #
# Monthly trend (stacked area by fuel category)
# --------------------------------------------------------------------------- #

with tab_trend:
    st.subheader("Monthly net generation by fuel category")
    ts = (fdf.groupby(["date", "fuel_category"])["netgen_mwh"].sum()
          .unstack("fuel_category").fillna(0).sort_index())
    colors = [FUEL_COLORS.get(c, "#999999") for c in ts.columns]
    st.area_chart(ts, color=colors)

    st.subheader("Monthly fuel consumption (Elec MMBtu)")
    ts2 = (fdf.groupby(["date", "fuel_category"])["elec_mmbtu"].sum()
           .unstack("fuel_category").fillna(0).sort_index())
    st.line_chart(ts2, color=[FUEL_COLORS.get(c, "#999999") for c in ts2.columns])

# --------------------------------------------------------------------------- #
# Plants
# --------------------------------------------------------------------------- #

with tab_plants:
    st.subheader("Plants by net generation")
    plants = (
        fdf.groupby(["plant_id", "plant_name", "operator_name"])
        .agg(netgen_mwh=("netgen_mwh", "sum"),
             fuels=("fuel_category", lambda s: ", ".join(sorted(set(s)))))
        .reset_index()
        .sort_values("netgen_mwh", ascending=False)
    )
    st.dataframe(
        plants, hide_index=True, use_container_width=True, height=520,
        column_config={
            "netgen_mwh": st.column_config.NumberColumn(
                "Net gen (MWh)", format="%,d"),
            "plant_id": "Plant ID", "plant_name": "Plant",
            "operator_name": "Operator", "fuels": "Fuels",
        },
    )

# --------------------------------------------------------------------------- #
# Raw data + download
# --------------------------------------------------------------------------- #

with tab_data:
    st.subheader(f"Tidy rows ({len(fdf):,})")
    st.dataframe(fdf, hide_index=True, use_container_width=True, height=520)
    st.download_button(
        "Download filtered CSV",
        fdf.to_csv(index=False).encode(),
        file_name=f"eia923_{region}_{min(years)}_{max(years)}.csv",
        mime="text/csv",
    )
