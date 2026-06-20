"""Plant-level SCED operating data — browse, fetch on demand, and visualize."""

from __future__ import annotations

import sys
import pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _export)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import _export  # noqa: E402
import sced_plants as sp  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
@st.cache_data(show_spinner=False)
def get_registry():
    return sp.load_registry()


@st.cache_data(show_spinner=True)
def fetch(resources, start, end):
    results = sp.fetch_plants(list(resources), start, end)
    frames = [df for df in results.values() if not df.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


try:
    reg = get_registry()
except Exception as e:
    st.title("🏭 ERCOT Plant SCED")
    st.warning(f"No plant registry yet ({e}). Run **Update** for *Plant SCED* on the "
               "Home page (it refreshes the registry from the latest disclosure).")
    st.stop()

latest = sp.latest_available_date()
st.title("🏭 ERCOT Plant SCED")
st.caption(f"ERCOT 60-Day SCED Disclosure (shared cache) · {len(reg):,} resources · "
           f"native interval resolution · available through ~{latest} (~60-day lag).")

with st.container(border=True):
    st.header("1 · Select plants")
    groups = sorted(reg["fuel_group"].unique())
    pick_groups = st.multiselect("Fuel group", groups, default=[])
    search = st.text_input("Search (code or plant name)", "")

    filt = reg
    if pick_groups:
        filt = filt[filt["fuel_group"].isin(pick_groups)]
    if search:
        m = (filt["resource_name"].str.contains(search, case=False, na=False)
             | filt["plant_name"].str.contains(search, case=False, na=False))
        filt = filt[m]
    filt = filt.sort_values("resource_name")

    st.caption(f"{len(filt):,} match")
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

if go_btn and plants:
    with st.spinner(f"Pulling {len(plants)} plant(s) from ERCOT…"):
        st.session_state["psced_data"] = fetch(tuple(plants), str(start), str(end))
        st.session_state["psced_sel"] = list(plants)

df = st.session_state.get("psced_data")
if df is None or df.empty:
    if go_btn:
        st.warning("No data for that selection / range (recent dates may be within the 60-day lag).")
    else:
        st.info("← Pick plant(s) and a time frame, then **Fetch & store**.")
    st.stop()

sel = st.session_state.get("psced_sel", sorted(df["resource_name"].unique()))
st.success(f"{len(df):,} intervals across {df['resource_name'].nunique()} plant(s) · stored to data lake")

info = reg.set_index("resource_name")
st.markdown("  ".join(
    f"**{info.loc[c, 'plant_name']}** (`{c}` · _{info.loc[c, 'name_source']}_)"
    for c in sel if c in info.index and "name_source" in info.columns
))

def _total_mwh(frame, value_col="telemetered_net_output"):
    """Energy = Σ MW × interval duration, per plant (SCED intervals are irregular).

    Each row's MW applies until the next SCED timestamp for that plant; the last
    row uses the median spacing, and durations are capped at 3× the median so a
    data gap doesn't inflate the total.
    """
    total = 0.0
    for _, g in frame.groupby("resource_name"):
        ts = pd.to_datetime(g.sort_values("sced_timestamp")["sced_timestamp"])
        dt_h = (ts.shift(-1) - ts).dt.total_seconds() / 3600.0
        med = dt_h.median()
        if pd.isna(med):
            continue
        dt_h = dt_h.fillna(med).clip(upper=med * 3)
        mw = pd.to_numeric(g.sort_values("sced_timestamp")[value_col],
                           errors="coerce").fillna(0.0).to_numpy()
        total += float((mw * dt_h.to_numpy()).sum())
    return total


total_mwh = _total_mwh(df)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Intervals", f"{len(df):,}")
c2.metric("Plants", df["resource_name"].nunique())
c3.metric("Total energy", f"{total_mwh:,.0f} MWh",
          help="Σ telemetered MW × interval duration, summed per plant. SCED intervals "
               "are irregular (~5 min), so this is time-weighted, not interval count × 0.25h.")
c4.metric("Peak net output (MW)", f"{df['telemetered_net_output'].max():,.1f}")
c5.metric("Avg net output (MW)", f"{df['telemetered_net_output'].mean():,.1f}")

st.subheader("Output vs. dispatch")
metric = st.selectbox("Series", ["telemetered_net_output", "base_point", "output_schedule",
                                 "hsl", "lsl", "state_of_charge"], index=0)
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

with st.expander("Telemetered status breakdown"):
    st.dataframe(df.groupby(["resource_name", "status"]).size().rename("intervals").reset_index(),
                 use_container_width=True, hide_index=True)

st.subheader("Data")
st.dataframe(df.head(5000), use_container_width=True, hide_index=True)
_export.download_block(st, df, name=f"sced_{'_'.join(sel)[:40]}_{start}_{end}",
                       title="Plant SCED output",
                       meta={"Resources": ", ".join(sel)[:80], "Period": f"{start} → {end}",
                             "Rows": f"{len(df):,}"})
