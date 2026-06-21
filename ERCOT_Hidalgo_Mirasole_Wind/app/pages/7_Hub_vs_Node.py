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

    # generation (optional — used for capture-weighted averages)
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

# ── summary metrics ──────────────────────────────────────────────────────────
avg_node  = df["node"].mean()
avg_hub   = df["hub"].mean()
avg_basis = df["basis"].mean()
basis_pct = avg_basis / avg_hub * 100 if avg_hub else 0.0

if has_gen:
    gen_wt = df["mw"].fillna(0)
    gen_wt_sum = gen_wt.sum()
    cap_node = (df["node"] * gen_wt).sum() / gen_wt_sum if gen_wt_sum else np.nan
    cap_hub  = (df["hub"]  * gen_wt).sum() / gen_wt_sum if gen_wt_sum else np.nan
    cap_basis = cap_node - cap_hub if not np.isnan(cap_node) else np.nan
else:
    cap_node = cap_hub = cap_basis = np.nan

cols = st.columns(4)
cols[0].metric("Avg node price",  f"${avg_node:.2f}/MWh")
cols[1].metric("Avg hub price",   f"${avg_hub:.2f}/MWh")
cols[2].metric("Avg basis (node−hub)",
               f"${avg_basis:+.2f}/MWh",
               delta=f"{basis_pct:+.1f}% of hub",
               delta_color="normal")
if has_gen:
    cols[3].metric("Gen-wtd capture premium",
                   f"${cap_basis:+.2f}/MWh" if not np.isnan(cap_basis) else "—",
                   help="Generation-weighted capture price at node minus hub. "
                        "Positive = node captures more than the hub average.")
else:
    cols[3].metric("Gen-wtd capture premium", "—",
                   help="No generation data cached for this period.")

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
        return pd.Series({
            "cap_node": (g["node"] * w).sum() / ws if ws else np.nan,
            "cap_hub":  (g["hub"]  * w).sum() / ws if ws else np.nan,
        })
    cap_m = df.groupby("month").apply(_cap).reset_index()
    cap_m["cap_basis"] = cap_m["cap_node"] - cap_m["cap_hub"]
    monthly = monthly.merge(cap_m, on="month", how="left")

# ── chart: price comparison ──────────────────────────────────────────────────
st.subheader("Node vs hub price — monthly average")
fig1 = go.Figure()
fig1.add_trace(go.Scatter(
    x=monthly["month"], y=monthly["node"].round(2), name=f"Node ({NODE})",
    mode="lines+markers", line=dict(color=branding.PRIMARY, width=2)))
fig1.add_trace(go.Scatter(
    x=monthly["month"], y=monthly["hub"].round(2), name=f"Hub ({HUB})",
    mode="lines+markers", line=dict(color=branding.ACCENT, width=2, dash="dot")))
fig1.update_layout(height=320, margin=dict(t=20, b=10),
                   yaxis_title="$/MWh", hovermode="x unified",
                   legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig1, use_container_width=True)

# ── chart: basis ─────────────────────────────────────────────────────────────
st.subheader("Basis (node − hub) — monthly average")
basis_colors = [branding.GOOD if v >= 0 else branding.BAD for v in monthly["basis"]]
fig2 = go.Figure()
fig2.add_bar(x=monthly["month"], y=monthly["basis"].round(2),
             marker_color=basis_colors, name="Basis",
             hovertemplate="%{x|%b %Y}<br>$%{y:+.2f}/MWh<extra></extra>")
fig2.add_hline(y=0, line_width=1, line_color="#888")
fig2.update_layout(height=280, margin=dict(t=20, b=10),
                   yaxis_title="$/MWh (node − hub)", hovermode="x unified")
st.plotly_chart(fig2, use_container_width=True)
st.caption("Green = node settles **above** hub (favourable for a node-settled CfD); "
           "red = node settles **below** hub.")

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
    tbl = tbl.rename(columns={"cap_node": "Cap node ($/MWh)", "cap_hub": "Cap hub ($/MWh)",
                               "cap_basis": "Cap basis ($/MWh)"})
    cols_show += ["Cap node ($/MWh)", "Cap hub ($/MWh)", "Cap basis ($/MWh)"]

tbl = tbl[cols_show].copy()

# totals row
tot: dict = {"Month": "Average"}
for c in cols_show[1:]:
    try:
        tot[c] = tbl[c].mean()
    except Exception:
        tot[c] = np.nan
tbl = pd.concat([tbl, pd.DataFrame([tot])], ignore_index=True)

price_cols = [c for c in cols_show if "$/MWh" in c and "Basis" not in c and "Cap basis" not in c]
basis_cols = [c for c in cols_show if "Basis $/MWh" in c or "Cap basis" in c]
pct_cols   = [c for c in cols_show if "%" in c]

fmt = {c: "${:,.2f}" for c in price_cols}
fmt.update({c: "${:+,.2f}" for c in basis_cols})
fmt.update({c: "{:+.1f}%" for c in pct_cols})

def _basis_color(v):
    if pd.isna(v):
        return ""
    return f"color:{branding.GOOD}" if v >= 0 else f"color:{branding.BAD}"

sty = tbl.style.format(fmt, na_rep="—")
for c in basis_cols + pct_cols:
    if c in tbl.columns:
        sty = sty.map(_basis_color, subset=[c])
st.dataframe(sty, hide_index=True, use_container_width=True)

# ── export ────────────────────────────────────────────────────────────────────
download_block = hub.export_block()
if download_block is not None:
    download_block(st, tbl.iloc[:-1],  # drop totals row
                   name=f"hub_vs_node_{start_d}_{end_d}",
                   title=f"{a['project_name']} — hub vs node {start_d} → {end_d}",
                   meta={"Asset": a["project_name"], "Node": NODE, "Hub": HUB,
                         "Period": f"{start_d} → {end_d}",
                         "Avg basis": f"${avg_basis:+.2f}/MWh ({basis_pct:+.1f}%)"})
else:
    st.download_button("⬇ Download CSV", tbl.iloc[:-1].to_csv(index=False).encode(),
                       file_name=f"hub_vs_node_{start_d}_{end_d}.csv", mime="text/csv")

branding.footer(st)
