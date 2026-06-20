"""Hub settlement-point prices (RTM 15-min) — explore the hub_prices dataset."""

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

HUB_COLORS = {
    "HB_HOUSTON": "#d62728", "HB_NORTH": "#1f77b4", "HB_SOUTH": "#2ca02c",
    "HB_WEST": "#ff7f0e", "HB_BUSAVG": "#9467bd", "HB_HUBAVG": "#8c564b",
    "HB_PAN": "#e377c2",
}

# Page config is set centrally by the router (app/Home.py).
st.title("💵 ERCOT Hub Prices (RTM 15-min SPP)")
st.caption("Real-Time Market Settlement Point Prices for the 7 trading hubs, "
           "pulled directly from the ERCOT Public API (NP6-905-CD).")


@st.cache_data(show_spinner=True)
def load() -> pd.DataFrame:
    if paths.HUB_PRICES_PARQUET.exists():
        return pd.read_parquet(paths.HUB_PRICES_PARQUET)
    return pd.DataFrame()


df = load()
if df.empty:
    _common.empty_state(
        st, "No Hub Prices data yet.",
        hint="Set credentials and refresh *Hub prices* from the Control Tower "
             "(or `python orchestrate.py update hub_prices`).",
        page="views/home.py", page_label="Go to Control Tower")

df["interval_ending_central"] = pd.to_datetime(df["interval_ending_central"])
hubs = sorted(df["settlement_point"].unique())
dmin = df["interval_ending_central"].min().date()
dmax = df["interval_ending_central"].max().date()
_common.data_status(st, path=paths.HUB_PRICES_PARQUET, rows=len(df), span=(dmin, dmax))

with st.container(border=True):
    st.header("Filters")
    sel_hubs = st.multiselect("Hubs", hubs, default=[h for h in hubs if h in ("HB_HOUSTON", "HB_NORTH")] or hubs[:2])
    start, end = _common.period_picker(st, key="hub", min_year=dmin.year, default_mode="Month")
    st.caption(f"**{start} → {end}** · data available {dmin} → {dmax}")
    freq = st.selectbox("Resample", ["15min", "Hourly", "Daily"], index=1)
    scarcity = st.number_input("Scarcity threshold ($/MWh)", min_value=0, value=100, step=25,
                               help="Intervals at or above this price are counted as scarcity.")
    logy = st.checkbox("Log price axis", value=False,
                       help="Compress scarcity spikes so the normal price range stays readable. "
                            "Non-positive prices are dropped on a log axis.")

if not sel_hubs:
    st.warning("Pick at least one hub.")
    st.stop()

mask = (
    df["settlement_point"].isin(sel_hubs)
    & (df["interval_ending_central"].dt.date >= start)
    & (df["interval_ending_central"].dt.date <= end)
)
sub = df[mask]
if sub.empty:
    st.warning("No rows for that selection.")
    st.stop()

price = sub["price"]
n_days = (end - start).days + 1
spike = price >= scarcity
neg = price < 0
st.caption(f"Showing **{start} → {end}** ({n_days} days) · {', '.join(sel_hubs)}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Avg $/MWh", f"{price.mean():,.2f}",
          help="Mean price across the selected hubs and window.")
c2.metric("Median $/MWh", f"{price.median():,.2f}",
          help="Typical price — robust to scarcity spikes, unlike the average.")
c3.metric("Max $/MWh", f"{price.max():,.2f}",
          help="Highest single 15-min interval in the window.")
c4.metric("Min $/MWh", f"{price.min():,.2f}",
          help="Lowest interval. Negative values = oversupply (paid to consume).")
c5.metric(f"Intervals ≥ ${scarcity:,}", f"{int(spike.sum()):,}",
          help=f"15-min intervals at or above ${scarcity:,}/MWh "
               f"({spike.mean() * 100:.1f}% of the window). Negatives: {int(neg.sum()):,}.")

rule = {"15min": "15min", "Hourly": "h", "Daily": "D"}[freq]
pivot = (sub.pivot_table(index="interval_ending_central", columns="settlement_point",
                         values="price", aggfunc="mean").sort_index())
if rule != "15min":
    pivot = pivot.resample(rule).mean()

tab_trend, tab_dur, tab_summary = st.tabs(["📈 Trend", "📉 Duration curve", "📋 Summary"])

with tab_trend:
    st.subheader(f"Price ($/MWh) · {freq.lower()}")
    long = (pivot.reset_index()
            .melt(id_vars="interval_ending_central", var_name="Hub", value_name="$/MWh")
            .dropna(subset=["$/MWh"]))
    fig = px.line(long, x="interval_ending_central", y="$/MWh", color="Hub",
                  color_discrete_map=HUB_COLORS)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=420,
                      legend=dict(orientation="h", y=1.08), xaxis_title=None)
    if logy:
        fig.update_yaxes(type="log")
    st.plotly_chart(fig, use_container_width=True)
    if logy and (price <= 0).any():
        st.caption(f"⚠️ {int((price <= 0).sum()):,} non-positive intervals are hidden on the log axis.")

with tab_dur:
    st.subheader("Price duration curve")
    st.caption("Prices sorted high → low. Read it as: *for X% of the window, price was at "
               "or above $Y.* The steep left tail is scarcity; a dip below zero is oversupply.")
    fig2 = go.Figure()
    for hub in sel_hubs:
        s = sub.loc[sub["settlement_point"] == hub, "price"].sort_values(ascending=False)
        if s.empty:
            continue
        pct = (pd.RangeIndex(1, len(s) + 1) / len(s)) * 100
        fig2.add_scatter(x=pct, y=s.values, mode="lines", name=hub,
                         line=dict(color=HUB_COLORS.get(hub)))
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=420,
                       xaxis_title="% of intervals at or above price",
                       yaxis_title="$/MWh", legend=dict(orientation="h", y=1.08))
    if logy:
        fig2.update_yaxes(type="log")
    st.plotly_chart(fig2, use_container_width=True)

with tab_summary:
    st.subheader("Summary by hub")
    g = sub.groupby("settlement_point")["price"]
    summary = pd.DataFrame({
        "intervals": g.count(),
        "avg": g.mean(),
        "median": g.median(),
        "p95": g.quantile(0.95),
        "max": g.max(),
        "min": g.min(),
        f"≥${scarcity:,}": g.apply(lambda s: int((s >= scarcity).sum())),
        "negative": g.apply(lambda s: int((s < 0).sum())),
    }).round(2)
    st.dataframe(summary, use_container_width=True)
    _export.download_block(st, sub, name=f"ercot_hub_prices_{start}_{end}",
                           title="ERCOT hub prices",
                           meta={"Period": f"{start} → {end}", "Rows": f"{len(sub):,}"})
