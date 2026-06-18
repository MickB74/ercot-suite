"""EIA-860 — the full ERCOT plant & generator directory (identity, siting, sizing)."""

from __future__ import annotations

import sys
import pathlib
import datetime as _dt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import eia860  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🗺️ EIA-860 — ERCOT Plant Directory")
st.caption("Every ERCOT plant/generator from EIA Form 860: identity, county, lat/lon, "
           "nameplate MW, technology/fuel, status, and online (or planned) date — "
           "operable, proposed, and retired.")


@st.cache_data(show_spinner=False)
def load(years):
    return eia860.load(years=list(years), region="ercot")


cached = eia860.available_years(region="ercot")

with st.sidebar:
    with st.expander("Get / update data", expanded=not cached):
        from ercot_core import tz
        _yr_now = tz.now_central().year
        yr = st.number_input("Year", min_value=2013, max_value=_yr_now,
                              value=_yr_now - 2, step=1)
        force = st.checkbox("Force re-download", value=False)
        if st.button(f"Download & build EIA-860 {int(yr)}"):
            with st.spinner(f"Downloading EIA-860 {int(yr)} from eia.gov…"):
                try:
                    df = eia860.build_year(int(yr), force_download=force)
                    st.success(f"Built {len(df):,} generators for {int(yr)}.")
                    load.clear()
                except Exception as e:
                    st.error(f"Failed: {e}")

if not cached:
    _common.empty_state(
        st, "No EIA-860 data cached yet.",
        hint="Use **Get / update data** in the sidebar, or "
             "`python datasets/eia923/eia860.py 2024`.",
        page="views/home.py", page_label="Go to Control Tower")

with st.sidebar:
    st.header("Filters")
    year = st.selectbox("Vintage year", cached, index=len(cached) - 1)
    df = load((year,))
    sg = st.multiselect("Status", sorted(df["status_group"].dropna().unique()), default=["operable"])
    fuels_sel = st.multiselect("Fuel", sorted(df["fuel_category"].dropna().unique()),
                               default=sorted(df["fuel_category"].dropna().unique()))
    county = st.text_input("County contains").strip().lower()
    name = st.text_input("Plant name contains").strip().lower()
    cmin = float(pd.to_numeric(df["nameplate_mw"], errors="coerce").fillna(0).min())
    cmax = float(pd.to_numeric(df["nameplate_mw"], errors="coerce").fillna(0).max())
    cap = st.slider("Nameplate MW range", 0.0, round(cmax, 0), (0.0, round(cmax, 0)))

f = df[df["status_group"].isin(sg) & df["fuel_category"].isin(fuels_sel)]
if county:
    f = f[f["county"].fillna("").str.lower().str.contains(county)]
if name:
    f = f[f["plant_name"].fillna("").str.lower().str.contains(name)]
f = f[pd.to_numeric(f["nameplate_mw"], errors="coerce").fillna(0).between(cap[0], cap[1])]

_common.data_status(st, rows=len(f), span=(f"vintage {year}", f"{len(df):,} generators total"))

c = st.columns(4)
c[0].metric("Plants", f"{f['plant_id'].nunique():,}")
c[1].metric("Generators", f"{len(f):,}")
c[2].metric("Nameplate", f"{f['nameplate_mw'].sum():,.0f} MW")
top = f.groupby("fuel_category")["nameplate_mw"].sum().idxmax() if not f.empty else "—"
c[3].metric("Top fuel (MW)", top)

tab_tbl, tab_map, tab_mix = st.tabs(["Generators", "Map", "Capacity mix"])

with tab_tbl:
    st.dataframe(f.sort_values("nameplate_mw", ascending=False), hide_index=True,
                 use_container_width=True, height=520)
    _export.download_block(st, f, name=f"eia860_ercot_{year}",
                           title=f"EIA-860 ERCOT plants — {year}",
                           meta={"Year": year, "Rows": f"{len(f):,}"})

with tab_map:
    geo = f.dropna(subset=["latitude", "longitude"]).copy()
    geo["latitude"] = pd.to_numeric(geo["latitude"], errors="coerce")
    geo["longitude"] = pd.to_numeric(geo["longitude"], errors="coerce")
    geo = geo.dropna(subset=["latitude", "longitude"])
    if geo.empty:
        st.caption("No coordinates for this selection.")
    else:
        st.map(geo[["latitude", "longitude"]])
        st.caption(f"{geo['plant_id'].nunique():,} plants located.")

with tab_mix:
    mix = (f.groupby(["fuel_category", "status_group"])["nameplate_mw"].sum()
           .unstack("status_group").fillna(0).sort_values(
               by=[c for c in ["operable"] if "operable" in f["status_group"].unique()] or None,
               ascending=False) if not f.empty else pd.DataFrame())
    st.bar_chart(mix)

st.caption("This directory is the key to matching ERCOT SCED resources to EIA plants "
           "(by county + capacity + online date) — used by the Reconciliation pages.")
