"""
ERCOT Plant SCED — browse, fetch, and visualize plant-level SCED operating data.
Run:  streamlit run app.py   (or double-click "Open ERCOT SCED UI.command")
"""
import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import sced_plants as sp

st.set_page_config(page_title="ERCOT Plant SCED", page_icon="⚡", layout="wide")


@st.cache_data(show_spinner=False)
def get_registry():
    return sp.load_registry()


@st.cache_data(show_spinner=True)
def fetch(resources, start, end):
    # Returns a single tidy frame for the selection.
    results = sp.fetch_plants(list(resources), start, end)
    frames = [df for df in results.values() if not df.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


reg = get_registry()
latest = sp.latest_available_date()

st.title("⚡ ERCOT Plant SCED")
st.caption(
    f"Source: ERCOT 60-Day SCED Disclosure via gridstatus · {len(reg):,} resources · "
    f"native interval resolution · available through ~{latest} (~60-day lag)."
)

# --- Sidebar: select plants + time frame ---------------------------------
with st.sidebar:
    st.header("1 · Select plants")
    groups = sorted(reg["fuel_group"].unique())
    pick_groups = st.multiselect("Fuel group", groups, default=[])
    search = st.text_input("Search (code or plant name)", "")

    filt = reg
    if pick_groups:
        filt = filt[filt["fuel_group"].isin(pick_groups)]
    if search:
        # Match either the ERCOT code or the readable plant name.
        m = (filt["resource_name"].str.contains(search, case=False, na=False)
             | filt["plant_name"].str.contains(search, case=False, na=False))
        filt = filt[m]
    filt = filt.sort_values("resource_name")

    st.caption(f"{len(filt):,} match — pick one or more below")
    label = dict(zip(filt["resource_name"], filt["plant_name"] + "  ·  " + filt["resource_name"]))
    plants = st.multiselect("Plants", filt["resource_name"].tolist(), default=[],
                            format_func=lambda c: label.get(c, c))

    st.header("2 · Time frame")
    mode = st.radio("Range", ["Whole year", "Custom dates"], horizontal=True)
    if mode == "Whole year":
        yr = st.number_input("Year", min_value=2018, max_value=latest.year,
                             value=min(2025, latest.year), step=1)
        start, end = date(int(yr), 1, 1), date(int(yr), 12, 31)
    else:
        start = st.date_input("Start", value=date(latest.year, 1, 1),
                              min_value=date(2018, 1, 1), max_value=latest)
        end = st.date_input("End", value=latest, min_value=date(2018, 1, 1), max_value=latest)

    go_btn = st.button("Fetch & store", type="primary", use_container_width=True,
                       disabled=not plants)

# --- Main: results --------------------------------------------------------
if go_btn and plants:
    with st.spinner(f"Pulling {len(plants)} plant(s) from ERCOT…"):
        st.session_state["data"] = fetch(tuple(plants), str(start), str(end))
        st.session_state["sel"] = list(plants)

df = st.session_state.get("data")
if df is None or df.empty:
    if go_btn:
        st.warning("No data found for that selection / range (recent dates may be within the 60-day lag).")
    else:
        st.info("← Pick plant(s) and a time frame, then **Fetch & store**.")
    st.stop()

sel = st.session_state.get("sel", sorted(df["resource_name"].unique()))
st.success(f"{len(df):,} intervals across {df['resource_name'].nunique()} plant(s) · stored to data/")

# Selected plants with their name + confidence flag.
info = reg.set_index("resource_name")
st.markdown("  ".join(
    f"**{info.loc[c, 'plant_name']}** (`{c}` · _{info.loc[c, 'name_source']}_)"
    for c in sel if c in info.index
))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Intervals", f"{len(df):,}")
c2.metric("Plants", df["resource_name"].nunique())
c3.metric("Peak net output (MW)", f"{df['telemetered_net_output'].max():,.1f}")
c4.metric("Avg net output (MW)", f"{df['telemetered_net_output'].mean():,.1f}")

# Time-series chart
st.subheader("Output vs. dispatch")
metric = st.selectbox(
    "Series",
    ["telemetered_net_output", "base_point", "output_schedule",
     "hsl", "lsl", "state_of_charge"],
    index=0,
)
has_soc = df["state_of_charge"].notna().any()
namemap = dict(zip(reg["resource_name"], reg["plant_name"]))
fig = go.Figure()
for code in sel:
    g = df[df["resource_name"] == code].sort_values("sced_timestamp")
    if g.empty:
        continue
    fig.add_trace(go.Scatter(x=g["sced_timestamp"], y=g[metric],
                             name=namemap.get(code, code), mode="lines"))
fig.update_layout(height=420, hovermode="x unified", margin=dict(t=10, b=10),
                  yaxis_title=metric, legend_title="Plant")
st.plotly_chart(fig, use_container_width=True)

if has_soc and metric != "state_of_charge":
    st.caption("Tip: select **state_of_charge** to see battery SoC (MWh).")

# Status breakdown
with st.expander("Telemetered status breakdown"):
    st.dataframe(
        df.groupby(["resource_name", "status"]).size().rename("intervals").reset_index(),
        use_container_width=True, hide_index=True,
    )

# Data + download
st.subheader("Data")
st.dataframe(df.head(5000), use_container_width=True, hide_index=True)
st.download_button(
    "Download CSV", df.to_csv(index=False).encode(),
    file_name=f"sced_{'_'.join(sel)[:40]}_{start}_{end}.csv", mime="text/csv",
)
