"""Hub vs Node — basis risk analysis.

Compares the plant's resource-node RT15 price against its nearest trading
hub. Basis (node − hub) is the spread captured above (or below) the fungible
hub price — driven by local congestion, reactive power, or transmission
constraints. For a node-settled VPPA this directly affects the CfD payment.
"""

from __future__ import annotations

import datetime as dt

import _boot  # noqa: F401
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from portal import branding, contract, hub  # noqa: E402

a = contract.ASSET
NODE = a["resource_node"]
HUB  = a["hub"]

branding.hero(st, "Hub vs Node",
              f"{NODE} node price vs {HUB} hub · basis and capture analysis")

win_start, win_end = hub.settlement_window(NODE)
if win_start is None:
    st.info("No price data cached for this asset yet.")
    st.stop()

# ── period picker ────────────────────────────────────────────────────────────
st.sidebar.header("Period")
mode = st.sidebar.radio("Period type", ["Month", "Quarter", "Year", "Custom"],
                        horizontal=True)
years = list(range(win_end.year, win_start.year - 1, -1))


def _eom(y, m):
    import calendar
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _last_full_month(we):
    import calendar
    _, last_day = calendar.monthrange(we.year, we.month)
    if we.day >= last_day:
        return we.year, we.month
    prev = we.replace(day=1) - dt.timedelta(days=1)
    return prev.year, prev.month


_lfy, _lfm = _last_full_month(win_end)

if mode == "Month":
    c1, c2 = st.sidebar.columns(2)
    yr_def = years.index(_lfy) if _lfy in years else 0
    yr = c1.selectbox("Year", years, index=yr_def)
    mo = c2.selectbox("Month", list(range(1, 13)), index=_lfm - 1,
                      format_func=lambda m: dt.date(2000, m, 1).strftime("%b"))
    start_d, end_d = dt.date(yr, mo, 1), _eom(yr, mo)
elif mode == "Quarter":
    c1, c2 = st.sidebar.columns(2)
    yr = c1.selectbox("Year", years)
    q = c2.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"])
    sm = (int(q[1]) - 1) * 3 + 1
    start_d, end_d = dt.date(yr, sm, 1), _eom(yr, sm + 2)
elif mode == "Year":
    yr = st.sidebar.selectbox("Year", years)
    start_d, end_d = dt.date(yr, 1, 1), dt.date(yr, 12, 31)
else:
    c1, c2 = st.sidebar.columns(2)
    start_d = c1.date_input("Start", value=win_start, min_value=win_start, max_value=win_end)
    end_d   = c2.date_input("End",   value=win_end,   min_value=win_start, max_value=win_end)

start_d = max(start_d, win_start)
end_d   = min(end_d,   win_end)
if start_d > end_d:
    st.error(f"Period outside settled window ({win_start} → {win_end}).")
    st.stop()

# ── load data ────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading prices…", ttl=3600)
def _load(start_d, end_d):
    s  = pd.Timestamp(start_d)
    e  = pd.Timestamp(end_d) + pd.Timedelta(days=1)
    np_ = hub.node_prices(NODE, s, e)[["interval_start", "spp"]].rename(columns={"spp": "node"})
    hp_ = hub.hub_prices(HUB,  s, e)[["interval_start", "spp"]].rename(columns={"spp": "hub"})
    np_["interval_start"] = pd.to_datetime(np_["interval_start"])
    hp_["interval_start"] = pd.to_datetime(hp_["interval_start"])
    df = np_.merge(hp_, on="interval_start", how="inner")
    df["basis"] = df["node"] - df["hub"]
    df["month"] = df["interval_start"].dt.to_period("M").dt.to_timestamp()
    df["hour"]  = df["interval_start"].dt.hour

    gen = hub.generation(NODE, s, e)
    if gen is not None and not gen.empty:
        gen = gen[["interval_start", "mw"]].copy()
        gen["interval_start"] = pd.to_datetime(gen["interval_start"])
        df = df.merge(gen, on="interval_start", how="left")
    else:
        df["mw"] = np.nan
    return df


df = _load(start_d, end_d)
if df.empty:
    st.info("No overlapping node + hub price data for this period.")
    st.stop()

has_gen = df["mw"].notna().any()

# ── period-level summary stats ───────────────────────────────────────────────
avg_node  = df["node"].mean()
avg_hub   = df["hub"].mean()
avg_basis = df["basis"].mean()
basis_pct = avg_basis / avg_hub * 100 if avg_hub else 0.0

if has_gen:
    gw        = df["mw"].fillna(0)
    gw_sum    = gw.sum()
    cap_node  = (df["node"] * gw).sum() / gw_sum if gw_sum else np.nan
    cap_hub   = (df["hub"]  * gw).sum() / gw_sum if gw_sum else np.nan
    cap_basis = cap_node - cap_hub if not np.isnan(cap_node) else np.nan
    cr_node   = cap_node / avg_node if avg_node else np.nan
    cr_hub    = cap_hub  / avg_hub  if avg_hub  else np.nan
else:
    cap_node = cap_hub = cap_basis = cr_node = cr_hub = np.nan

# row 1: spot and basis
r1 = st.columns(4)
r1[0].metric("Avg node price",  f"${avg_node:.2f}/MWh")
r1[1].metric("Avg hub price",   f"${avg_hub:.2f}/MWh")
r1[2].metric("Avg basis (node−hub)", f"${avg_basis:+.2f}/MWh",
             delta=f"{basis_pct:+.1f}% of hub", delta_color="normal")
r1[3].metric("Gen-wtd capture basis",
             f"${cap_basis:+.2f}/MWh" if has_gen and not np.isnan(cap_basis) else "—",
             help="Generation-weighted capture price at node minus hub. "
                  "Positive = node captures more than the hub.")

# row 2: capture prices and ratios (only when generation is available)
if has_gen and not np.isnan(cap_node):
    r2 = st.columns(4)
    r2[0].metric("Capture price — node",  f"${cap_node:.2f}/MWh",
                 help="Gen-weighted avg price at the plant node during generation hours.")
    r2[1].metric("Capture price — hub",   f"${cap_hub:.2f}/MWh",
                 help="Gen-weighted avg hub price during the same generation hours.")
    r2[2].metric("Capture ratio — node",  f"{cr_node:.1%}" if not np.isnan(cr_node) else "—",
                 delta=f"{'above' if cr_node >= 1 else 'below'} flat avg" if not np.isnan(cr_node) else None,
                 delta_color="normal" if cr_node >= 1 else "inverse",
                 help="Node capture ÷ avg node spot. >100% = plant's generation hours "
                      "command above-average prices at this node.")
    r2[3].metric("Capture ratio — hub",   f"{cr_hub:.1%}"  if not np.isnan(cr_hub)  else "—",
                 delta=f"{'above' if cr_hub >= 1 else 'below'} flat avg" if not np.isnan(cr_hub) else None,
                 delta_color="normal" if cr_hub >= 1 else "inverse",
                 help="Hub capture ÷ avg hub spot. Shows the hub-level shape effect — "
                      "typically < 1 for solar because midday depresses hub prices.")

st.caption(f"Settled window: **{start_d} → {end_d}** · "
           f"{len(df):,} 15-min intervals · node **{NODE}** vs hub **{HUB}**")

# ── monthly aggregation ──────────────────────────────────────────────────────
monthly = (df.groupby("month")
             .agg(node=("node", "mean"),
                  hub=("hub",  "mean"),
                  basis=("basis", "mean"))
             .reset_index())
monthly["basis_pct"] = monthly["basis"] / monthly["hub"] * 100

if has_gen:
    def _cap(g):
        w = g["mw"].fillna(0)
        ws = w.sum()
        cap_n = (g["node"] * w).sum() / ws if ws else np.nan
        cap_h = (g["hub"]  * w).sum() / ws if ws else np.nan
        return pd.Series({"cap_node": cap_n, "cap_hub": cap_h})

    cap_m = df.groupby("month").apply(_cap).reset_index()
    cap_m["cap_basis"] = cap_m["cap_node"] - cap_m["cap_hub"]
    monthly = monthly.merge(cap_m, on="month", how="left")
    monthly["cap_ratio_node"] = monthly["cap_node"] / monthly["node"]
    monthly["cap_ratio_hub"]  = monthly["cap_hub"]  / monthly["hub"]

# ── chart: price and capture comparison ─────────────────────────────────────
st.subheader("Avg spot vs capture price — node and hub")
fig1 = go.Figure()
fig1.add_trace(go.Scatter(
    x=monthly["month"], y=monthly["node"].round(2), name=f"Avg spot — node ({NODE})",
    mode="lines+markers", line=dict(color=branding.PRIMARY, width=2)))
fig1.add_trace(go.Scatter(
    x=monthly["month"], y=monthly["hub"].round(2), name=f"Avg spot — hub ({HUB})",
    mode="lines+markers", line=dict(color=branding.ACCENT, width=2, dash="dot")))
if has_gen and "cap_node" in monthly.columns:
    fig1.add_trace(go.Scatter(
        x=monthly["month"], y=monthly["cap_node"].round(2),
        name="Capture — node", mode="lines+markers",
        line=dict(color=branding.PRIMARY, width=1.5, dash="dash"),
        marker=dict(symbol="diamond", size=6)))
    fig1.add_trace(go.Scatter(
        x=monthly["month"], y=monthly["cap_hub"].round(2),
        name="Capture — hub", mode="lines+markers",
        line=dict(color=branding.ACCENT, width=1.5, dash="dash"),
        marker=dict(symbol="diamond", size=6)))
fig1.update_layout(height=340, margin=dict(t=20, b=10),
                   yaxis_title="$/MWh", hovermode="x unified",
                   legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig1, use_container_width=True)
if has_gen:
    st.caption("Solid lines = flat average price; dashed diamonds = generation-weighted "
               "capture price. A capture line below the spot line means the plant "
               "generates when prices are relatively low (solar value-factor discount).")

# ── chart: capture premium (value factor deviation) ──────────────────────────
if has_gen and "cap_node" in monthly.columns:
    st.subheader("Capture premium — how much more/less than flat avg")
    node_prem = (monthly["cap_node"] - monthly["node"]).round(2)
    hub_prem  = (monthly["cap_hub"]  - monthly["hub"]).round(2)
    fig_vf = go.Figure()
    fig_vf.add_trace(go.Bar(
        x=monthly["month"], y=node_prem,
        name=f"Node capture premium",
        marker_color=[branding.GOOD if v >= 0 else branding.BAD for v in node_prem],
        hovertemplate="%{x|%b %Y}<br>$%{y:+.2f}/MWh<extra>Node cap − avg spot</extra>"))
    fig_vf.add_trace(go.Bar(
        x=monthly["month"], y=hub_prem,
        name=f"Hub capture premium",
        marker_color=[branding.PRIMARY if v >= 0 else branding.ACCENT for v in hub_prem],
        opacity=0.65,
        hovertemplate="%{x|%b %Y}<br>$%{y:+.2f}/MWh<extra>Hub cap − avg spot</extra>"))
    fig_vf.add_hline(y=0, line_width=1, line_color="#888")
    fig_vf.update_layout(
        height=300, margin=dict(t=20, b=10), barmode="group",
        yaxis_title="$/MWh (capture − avg spot)", hovermode="x unified",
        legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig_vf, use_container_width=True)
    st.caption("Positive = the plant's generation hours command above-average prices; "
               "negative = generation hours are cheap. Node and hub shown side by side — "
               "if the hub premium is already negative, the node premium shows the "
               "additional nodal effect on top.")

# ── chart: basis ─────────────────────────────────────────────────────────────
st.subheader("Basis (node − hub) — monthly average")
basis_colors = [branding.GOOD if v >= 0 else branding.BAD for v in monthly["basis"]]
fig2 = go.Figure()
fig2.add_bar(x=monthly["month"], y=monthly["basis"].round(2),
             marker_color=basis_colors, name="Avg basis",
             hovertemplate="%{x|%b %Y}<br>$%{y:+.2f}/MWh<extra></extra>")
if has_gen and "cap_basis" in monthly.columns:
    fig2.add_trace(go.Scatter(
        x=monthly["month"], y=monthly["cap_basis"].round(2),
        name="Gen-wtd capture basis", mode="lines+markers",
        line=dict(color="#555", width=1.5, dash="dot"),
        marker=dict(symbol="diamond", size=5),
        hovertemplate="%{x|%b %Y}<br>$%{y:+.2f}/MWh (gen-wtd)<extra></extra>"))
fig2.add_hline(y=0, line_width=1, line_color="#888")
fig2.update_layout(height=300, margin=dict(t=20, b=10),
                   yaxis_title="$/MWh (node − hub)", hovermode="x unified",
                   legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig2, use_container_width=True)
st.caption("Green = node settles **above** hub (favourable for node-settled CfD); "
           "red = below. Dotted line = gen-weighted capture basis "
           "(what the plant actually experienced).")

# ── chart: diurnal basis profile ─────────────────────────────────────────────
with st.expander("Diurnal basis profile — avg basis by hour of day"):
    hourly_agg = df.groupby("hour").agg(avg_basis=("basis", "mean")).reset_index()
    if has_gen:
        def _gw_basis(g):
            w = g["mw"].fillna(0)
            ws = w.sum()
            return (g["basis"] * w).sum() / ws if ws > 0 else np.nan
        gw_hourly = df.groupby("hour").apply(_gw_basis).reset_index(name="gw_basis")
        hourly_agg = hourly_agg.merge(gw_hourly, on="hour", how="left")

    figh = go.Figure()
    figh.add_trace(go.Bar(
        x=hourly_agg["hour"], y=hourly_agg["avg_basis"].round(2),
        name="Avg basis", opacity=0.5,
        marker_color=[branding.GOOD if v >= 0 else branding.BAD
                      for v in hourly_agg["avg_basis"]],
        hovertemplate="Hour %{x}:00<br>$%{y:+.2f}/MWh<extra></extra>"))
    if has_gen and "gw_basis" in hourly_agg.columns:
        figh.add_trace(go.Scatter(
            x=hourly_agg["hour"], y=hourly_agg["gw_basis"].round(2),
            name="Gen-wtd basis", mode="lines+markers",
            line=dict(color=branding.PRIMARY, width=2),
            hovertemplate="Hour %{x}:00<br>$%{y:+.2f}/MWh (gen-weighted)<extra></extra>"))
    figh.add_hline(y=0, line_width=1, line_color="#888")
    figh.update_layout(
        height=300, margin=dict(t=10, b=10),
        xaxis=dict(title="Hour of day (CPT)", tickmode="linear", dtick=2),
        yaxis_title="$/MWh (node − hub)", hovermode="x unified",
        legend=dict(orientation="h", y=1.1))
    st.plotly_chart(figh, use_container_width=True)
    st.caption("Shows when the nodal spread is most positive or negative across the day. "
               "The gen-weighted line reflects hours when the plant actually generates — "
               "the relevant signal for settlement impact.")

# ── chart: scatter correlation ────────────────────────────────────────────────
with st.expander("Node vs hub — 15-minute scatter"):
    sample = df.sample(min(len(df), 5000), random_state=42)
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=sample["hub"], y=sample["node"], mode="markers",
        marker=dict(color=branding.PRIMARY, opacity=0.3, size=4),
        hovertemplate="Hub: $%{x:.1f}<br>Node: $%{y:.1f}<extra></extra>"))
    mn = min(sample["hub"].min(), sample["node"].min())
    mx = max(sample["hub"].max(), sample["node"].max())
    fig3.add_trace(go.Scatter(x=[mn, mx], y=[mn, mx], mode="lines",
                              line=dict(color="#888", dash="dash", width=1),
                              name="1:1", showlegend=True))
    corr = sample["node"].corr(sample["hub"])
    fig3.update_layout(
        height=380, margin=dict(t=30, b=10),
        xaxis_title=f"Hub {HUB} ($/MWh)", yaxis_title=f"Node {NODE} ($/MWh)",
        title=f"Correlation: {corr:.3f}  ·  up to 5,000 intervals sampled",
        hovermode="closest")
    st.plotly_chart(fig3, use_container_width=True)

# ── monthly table ─────────────────────────────────────────────────────────────
st.subheader("Monthly detail")
tbl = monthly.copy()
tbl["Month"] = tbl["month"].dt.strftime("%Y-%m")
cols_show = ["Month",
             f"Node {NODE} ($/MWh)", f"Hub {HUB} ($/MWh)",
             "Basis $/MWh", "Basis %"]
tbl = tbl.rename(columns={
    "node": f"Node {NODE} ($/MWh)", "hub": f"Hub {HUB} ($/MWh)",
    "basis": "Basis $/MWh", "basis_pct": "Basis %"})

if has_gen and "cap_node" in tbl.columns:
    tbl = tbl.rename(columns={
        "cap_node": "Cap node ($/MWh)", "cap_hub": "Cap hub ($/MWh)",
        "cap_basis": "Cap basis ($/MWh)",
        "cap_ratio_node": "Cap ratio — node", "cap_ratio_hub": "Cap ratio — hub"})
    cols_show += ["Cap node ($/MWh)", "Cap hub ($/MWh)", "Cap basis ($/MWh)",
                  "Cap ratio — node", "Cap ratio — hub"]

tbl = tbl[cols_show].copy()

tot: dict = {"Month": "Average"}
for c in cols_show[1:]:
    try:
        tot[c] = tbl[c].mean()
    except Exception:
        tot[c] = np.nan
tbl = pd.concat([tbl, pd.DataFrame([tot])], ignore_index=True)

price_cols = [c for c in cols_show if "$/MWh" in c and "Basis" not in c and "Cap basis" not in c]
basis_cols = [c for c in cols_show if "Basis $/MWh" in c or "Cap basis" in c]
pct_cols   = [c for c in cols_show if "Basis %" in c]
ratio_cols = [c for c in cols_show if "Cap ratio" in c]

fmt = {c: "${:,.2f}" for c in price_cols}
fmt.update({c: "${:+,.2f}" for c in basis_cols})
fmt.update({c: "{:+.1f}%" for c in pct_cols})
fmt.update({c: "{:.1%}" for c in ratio_cols})


def _basis_color(v):
    if pd.isna(v):
        return ""
    return f"color:{branding.GOOD}" if v >= 0 else f"color:{branding.BAD}"


def _ratio_color(v):
    if pd.isna(v):
        return ""
    return f"color:{branding.GOOD}" if v >= 1.0 else f"color:{branding.BAD}"


sty = tbl.style.format(fmt, na_rep="—")
for c in basis_cols + pct_cols:
    if c in tbl.columns:
        sty = sty.map(_basis_color, subset=[c])
for c in ratio_cols:
    if c in tbl.columns:
        sty = sty.map(_ratio_color, subset=[c])
st.dataframe(sty, hide_index=True, use_container_width=True)

# ── export ────────────────────────────────────────────────────────────────────
download_block = hub.export_block()
if download_block is not None:
    download_block(st, tbl.iloc[:-1],
                   name=f"hub_vs_node_{start_d}_{end_d}",
                   title=f"{a['project_name']} — hub vs node {start_d} → {end_d}",
                   meta={"Asset": a["project_name"], "Node": NODE, "Hub": HUB,
                         "Period": f"{start_d} → {end_d}",
                         "Avg basis": f"${avg_basis:+.2f}/MWh ({basis_pct:+.1f}%)"})
else:
    st.download_button("⬇ Download CSV", tbl.iloc[:-1].to_csv(index=False).encode(),
                       file_name=f"hub_vs_node_{start_d}_{end_d}.csv", mime="text/csv")

branding.footer(st)
