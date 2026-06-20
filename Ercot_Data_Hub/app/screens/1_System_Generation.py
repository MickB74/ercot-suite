"""System generation by fuel (15-min) — interactive Qlik-style dashboard.

Filter pane (years · date range · fuels · resample) drives every view: KPI
tiles, a time-series with range slider, the fuel-mix donut, the average
hour-of-day profile, and a monthly stack. Click legend entries to isolate
fuels; box-zoom the time series to drill in. Built on the Fuel Mix Report
dataset (one row per 15-min interval × fuel, provenance-merged).
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
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import paths  # noqa: E402

# Consistent fuel colours + stacking order across every chart.
FUEL_COLORS = {
    "Nuclear": "#9467bd", "Coal": "#555555", "Gas": "#e45756", "Gas-CC": "#f58518",
    "Hydro": "#17becf", "Biomass": "#8c6d31", "Wind": "#4c9be0", "Solar": "#f4c430",
    "Power Storage": "#54a24b", "Other": "#999999",
}
FUEL_ORDER = ["Nuclear", "Coal", "Gas", "Gas-CC", "Hydro", "Biomass", "Wind",
              "Solar", "Power Storage", "Other"]
RENEWABLE = {"Wind", "Solar", "Hydro", "Biomass"}
INTERVAL_H = 0.25

st.title("🔥 System Generation by Fuel")
st.caption("ERCOT Interval Generation by Fuel — provenance-merged "
           "(FINAL > INITIAL > API > dashboard). 15-min resolution, naive Central.")


def _years() -> list[int]:
    out = []
    for f in sorted(paths.SYSTEM_GEN_DIR.glob("ercot_gen_by_fuel_*.parquet")):
        try:
            out.append(int(f.stem.rsplit("_", 1)[-1]))
        except ValueError:
            pass
    return out


@st.cache_data(show_spinner=False)
def load(years: tuple[int, ...]) -> pd.DataFrame:
    frames = []
    for y in years:
        p = paths.SYSTEM_GEN_DIR / f"ercot_gen_by_fuel_{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["interval_start"] = pd.to_datetime(df["interval_start"])
    return df


years = _years()
if not years:
    _common.empty_state(
        st, "No System Generation data yet.",
        hint="Refresh it from the Control Tower (or `python orchestrate.py update system_gen`).",
        page="views/home.py", page_label="Go to Control Tower")

# ── Filter pane ─────────────────────────────────────────────────────────────
with st.container(border=True):
    st.header("Filters")
    sel_years = st.multiselect("Year(s)", years, default=years[-1:])
    if not sel_years:
        st.warning("Pick at least one year.")
        st.stop()

raw = load(tuple(sorted(sel_years)))
if raw.empty:
    st.warning("No rows for that selection.")
    st.stop()

dmin, dmax = raw["interval_start"].min().date(), raw["interval_start"].max().date()
with st.container(border=True):
    rng = st.date_input("Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax)
    if isinstance(rng, tuple) and len(rng) == 2:
        start_d, end_d = rng
    else:
        start_d, end_d = dmin, dmax
    all_fuels = [f for f in FUEL_ORDER if f in set(raw["fuel"].unique())]
    sel_fuels = st.multiselect("Fuels", all_fuels, default=all_fuels)
    freq = st.selectbox("Resample", ["15min", "Hourly", "Daily", "Monthly"],
                        index=2)
    units = st.radio("Units", ["MW", "% share"], horizontal=True)
    chart_kind = st.radio("Time chart", ["Stacked area", "Lines"], horizontal=True)

df = raw[(raw["interval_start"].dt.date >= start_d)
         & (raw["interval_start"].dt.date <= end_d)
         & (raw["fuel"].isin(sel_fuels))].copy()
if df.empty:
    st.warning("No rows for that selection — widen the date range or add fuels.")
    st.stop()

_common.data_status(
    st, path=[paths.SYSTEM_GEN_DIR / f"ercot_gen_by_fuel_{y}.parquet" for y in sel_years],
    rows=len(df), span=(start_d, end_d))

df["mwh"] = df["mw"] * INTERVAL_H
n_int = df["interval_start"].nunique()
sys_by_int = df.groupby("interval_start")["mw"].sum()
total_mwh = df["mwh"].sum()
ren_mwh = df.loc[df["fuel"].isin(RENEWABLE), "mwh"].sum()
peak_mw = sys_by_int.max()
peak_at = sys_by_int.idxmax()
by_fuel_mwh = df.groupby("fuel")["mwh"].sum().sort_values(ascending=False)

# ── KPI tiles ───────────────────────────────────────────────────────────────
k = st.columns(5)
k[0].metric("Total energy", f"{total_mwh/1e6:,.2f} TWh")
k[1].metric("Avg system load", f"{sys_by_int.mean():,.0f} MW")
k[2].metric("Peak", f"{peak_mw:,.0f} MW", help=f"at {peak_at:%Y-%m-%d %H:%M} CT")
k[3].metric("Renewable share", f"{(ren_mwh/total_mwh*100 if total_mwh else 0):,.1f}%",
            help="Wind + Solar + Hydro + Biomass ÷ total energy.")
k[4].metric("Top fuel", by_fuel_mwh.index[0] if len(by_fuel_mwh) else "—",
            delta=f"{by_fuel_mwh.iloc[0]/total_mwh*100:,.0f}% of energy" if total_mwh else None,
            delta_color="off")

freq_map = {"15min": "15min", "Hourly": "h", "Daily": "D", "Monthly": "MS"}
rule = freq_map[freq]


def _fuel_kwargs(cats):
    return dict(color="fuel", color_discrete_map=FUEL_COLORS,
                category_orders={"fuel": [f for f in FUEL_ORDER if f in cats]})


# ── Main time series ────────────────────────────────────────────────────────
pivot = (df.pivot_table(index="interval_start", columns="fuel", values="mw", aggfunc="mean")
         .sort_index())
if rule != "15min":
    pivot = pivot.resample(rule).mean()
pivot = pivot[[f for f in FUEL_ORDER if f in pivot.columns]]

if units == "% share":
    shares = pivot.div(pivot.sum(axis=1).replace(0, pd.NA), axis=0) * 100
    plot_df = shares
    y_title = "Share of generation (%)"
else:
    plot_df = pivot
    y_title = "Average MW"

long = plot_df.reset_index().melt("interval_start", var_name="fuel", value_name="val").dropna()
st.subheader(f"Generation over time · {freq.lower()} · {units}")
if chart_kind == "Stacked area":
    fig = px.area(long, x="interval_start", y="val", **_fuel_kwargs(set(pivot.columns)))
else:
    fig = px.line(long, x="interval_start", y="val", **_fuel_kwargs(set(pivot.columns)))
fig.update_layout(height=420, hovermode="x unified", legend_title_text="",
                  margin=dict(t=10, b=0, l=0, r=0), yaxis_title=y_title, xaxis_title="")
fig.update_xaxes(
    rangeslider_visible=True,
    rangeselector=dict(buttons=[
        dict(count=7, label="7d", step="day", stepmode="backward"),
        dict(count=1, label="1m", step="month", stepmode="backward"),
        dict(count=3, label="3m", step="month", stepmode="backward"),
        dict(step="all", label="All"),
    ]))
st.plotly_chart(fig, use_container_width=True)
st.caption("Tip: click a fuel in the legend to hide it · double-click to isolate · "
           "drag to box-zoom · use the range buttons/slider to scrub.")

# ── Mix donut + hour-of-day profile ─────────────────────────────────────────
c_left, c_right = st.columns(2)
with c_left:
    st.subheader("Fuel mix (energy share)")
    mix = by_fuel_mwh.reset_index()
    donut = px.pie(mix, names="fuel", values="mwh", hole=0.55,
                   color="fuel", color_discrete_map=FUEL_COLORS,
                   category_orders={"fuel": [f for f in FUEL_ORDER if f in set(mix["fuel"])]})
    donut.update_traces(textposition="inside", textinfo="percent+label", sort=False)
    donut.update_layout(height=380, showlegend=False, margin=dict(t=10, b=0, l=0, r=0))
    st.plotly_chart(donut, use_container_width=True)

with c_right:
    st.subheader("Average by hour of day (CT)")
    hod = (df.assign(hour=df["interval_start"].dt.hour)
           .groupby(["hour", "fuel"])["mw"].mean().reset_index())
    hfig = px.area(hod, x="hour", y="mw", **_fuel_kwargs(set(hod["fuel"])))
    hfig.update_layout(height=380, hovermode="x unified", legend_title_text="",
                       margin=dict(t=10, b=0, l=0, r=0), yaxis_title="Avg MW",
                       xaxis_title="Hour (Central)")
    hfig.update_xaxes(dtick=3)
    st.plotly_chart(hfig, use_container_width=True)

# ── Monthly stack (only when the window spans >1 month) ─────────────────────
months = df["interval_start"].dt.to_period("M")
if months.nunique() > 1:
    st.subheader("Monthly energy by fuel")
    mo = (df.assign(month=months.dt.to_timestamp())
          .groupby(["month", "fuel"])["mwh"].sum().reset_index())
    mo["gwh"] = mo["mwh"] / 1e3
    mfig = px.bar(mo, x="month", y="gwh", **_fuel_kwargs(set(mo["fuel"])))
    mfig.update_layout(height=360, barmode="stack", legend_title_text="",
                       margin=dict(t=10, b=0, l=0, r=0), yaxis_title="GWh", xaxis_title="")
    st.plotly_chart(mfig, use_container_width=True)

# ── Detail: provenance + export ─────────────────────────────────────────────
with st.expander("Provenance & data table"):
    st.dataframe(
        df.groupby(["source", "settlement_type"]).size().rename("rows").reset_index(),
        hide_index=True, use_container_width=True)
    st.dataframe(by_fuel_mwh.rename("MWh").reset_index(), hide_index=True,
                 use_container_width=True)

_export.download_block(st, df, name=f"ercot_gen_by_fuel_{start_d}_{end_d}",
                       title="ERCOT system generation by fuel",
                       meta={"Period": f"{start_d} → {end_d}", "Rows": f"{len(df):,}"})
