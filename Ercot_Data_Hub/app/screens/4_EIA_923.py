"""EIA-923 plant-level monthly generation & fuel — explore the eia923 dataset."""

from __future__ import annotations

import sys
import pathlib
import datetime as _dt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import streamlit as st  # noqa: E402

import eia923  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
FUEL_COLORS = {
    "Gas": "#d62728", "Coal": "#7f7f7f", "Nuclear": "#9467bd", "Wind": "#2ca02c",
    "Solar": "#ff7f0e", "Hydro": "#1f77b4", "Biomass": "#8c564b",
    "Geothermal": "#e377c2", "Oil": "#bcbd22", "Other Gas": "#17becf",
    "Storage": "#aec7e8", "Other": "#c7c7c7",
}


# Region / RTO picker → engine region key. RTOs/ISOs are served by filtering the
# nationwide "all" cache (no per-RTO download needed).
REGIONS = {
    "ERCOT (Texas)": "ercot",
    "MISO (Midwest)": "miso",
    "PJM (Mid-Atlantic)": "pjm",
    "CAISO (California)": "caiso",
    "SPP (Central)": "spp",
    "ISO-NE (New England)": "isone",
    "NYISO (New York)": "nyiso",
    "Texas — all plants": "tx",
    "US — all balancing authorities": "all",
}


@st.cache_data(show_spinner=False)
def load(region, years):
    return eia923.load_region(region, years=list(years))


def fmt_mwh(x):
    if x >= 1e9:
        return f"{x / 1e9:.1f} TWh"
    if x >= 1e6:
        return f"{x / 1e6:.1f} GWh"
    return f"{x:,.0f} MWh"


_panel = st.container(border=True)
_panel.title("EIA-923")
region_label = _panel.selectbox(
    "Region / RTO", list(REGIONS), index=0,
    help="ERCOT, MISO, PJM, CAISO, SPP, ISO-NE and NYISO each filter one EIA "
         "balancing authority; Texas filters by state; US is everything. RTOs/ISOs "
         "are read from the nationwide cache.")
region = REGIONS[region_label]
build_region = eia923.cache_region(region)  # 'all' backs every RTO/ISO
cached = eia923.region_years(region)

with _panel.expander("Get / update data", expanded=not cached):
    from ercot_core import tz
    this_year = tz.now_central().year
    by = st.number_input("Year", min_value=2008, max_value=this_year, value=this_year - 1, step=1)
    force = st.checkbox("Force re-download", value=False)
    if build_region != region:
        st.caption(f"{region_label} is served from the **US (all)** cache — building "
                   "downloads the nationwide year once, then every RTO/ISO reads from it.")
    if st.button(f"Download & build {int(by)}"):
        with st.spinner(f"Downloading EIA-923 {int(by)}…"):
            try:
                df = eia923.build_year(int(by), region=build_region, force_download=force)
                st.success(f"Built {len(df):,} rows for {int(by)} ({build_region}).")
                load.clear()
            except Exception as exc:
                st.error(f"Failed: {exc}")

if not cached:
    st.title("📅 EIA-923 Generation & Fuel")
    _common.empty_state(
        st, f"No cached EIA-923 data yet for {region_label}.",
        hint="Use **Get / update data** in the sidebar, or refresh from the Control Tower.",
        page="views/home.py", page_label="Go to Control Tower")

years = _panel.multiselect("Years", cached, default=cached)
if not years:
    st.warning("Select at least one year.")
    st.stop()

df = load(region, tuple(sorted(years)))
if df.empty:
    st.warning("No rows for the selected years.")
    st.stop()

cats = sorted(df["fuel_category"].dropna().unique())
sel_cats = _panel.multiselect("Fuel category", cats, default=cats)
sectors = sorted(df["sector"].dropna().unique())
sel_sectors = _panel.multiselect("Sector", sectors, default=sectors)
search = _panel.text_input("Plant / operator search").strip().lower()

mask = df["fuel_category"].isin(sel_cats) & df["sector"].isin(sel_sectors)
if search:
    mask &= (df["plant_name"].fillna("").str.lower().str.contains(search)
             | df["operator_name"].fillna("").str.lower().str.contains(search))
fdf = df[mask]

st.title("📅 EIA-923 Generation & Fuel")
st.caption(f"**{region_label}** · {min(years)}–{max(years)} · "
           f"plant-level monthly net generation and fuel consumption (EIA Form 923)")
_common.data_status(st, rows=len(fdf), span=(min(years), max(years)))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Net generation", fmt_mwh(fdf["netgen_mwh"].sum()))
c2.metric("Plants", f"{fdf['plant_id'].nunique():,}")
c3.metric("Fuel burned", f"{fdf['total_mmbtu'].sum() / 1e6:,.1f} MMMBtu")
c4.metric("Top fuel", fdf.groupby("fuel_category")["netgen_mwh"].sum().idxmax() if not fdf.empty else "—")

tab_mix, tab_trend, tab_plants, tab_data = st.tabs(["Fuel mix", "Monthly trend", "Plants", "Data"])

with tab_mix:
    by_fuel = fdf.groupby("fuel_category")["netgen_mwh"].sum().sort_values(ascending=False)
    left, right = st.columns([2, 1])
    left.subheader("Net generation by fuel")
    left.bar_chart(by_fuel, color="#2ca02c")
    right.subheader("Share")
    right.dataframe((by_fuel / by_fuel.sum() * 100).round(1).rename("% of net gen").reset_index(),
                    hide_index=True, use_container_width=True)

with tab_trend:
    st.subheader("Monthly net generation by fuel category")
    ts = (fdf.groupby(["date", "fuel_category"])["netgen_mwh"].sum()
          .unstack("fuel_category").fillna(0).sort_index())
    st.area_chart(ts, color=[FUEL_COLORS.get(c, "#999999") for c in ts.columns])

with tab_plants:
    st.subheader("Plants by net generation")
    plants = (fdf.groupby(["plant_id", "plant_name", "operator_name"])
              .agg(netgen_mwh=("netgen_mwh", "sum"),
                   fuels=("fuel_category", lambda s: ", ".join(sorted(set(s)))))
              .reset_index().sort_values("netgen_mwh", ascending=False))
    st.dataframe(plants, hide_index=True, use_container_width=True, height=520)

with tab_data:
    st.subheader(f"Tidy rows ({len(fdf):,})")
    st.dataframe(fdf, hide_index=True, use_container_width=True, height=520)
    _export.download_block(
        st, fdf, name=f"eia923_{region}_{min(years)}_{max(years)}",
        title=f"EIA-923 Generation & Fuel — {region_label}",
        meta={"Region": region_label, "Years": f"{min(years)}–{max(years)}",
              "Rows": f"{len(fdf):,}"})
