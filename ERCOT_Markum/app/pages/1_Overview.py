"""Overview — the at-a-glance dashboard a customer lands on.

Headline KPIs for the most recent settled month, year-to-date totals, and a
monthly settlement chart over the full available history.
"""

from __future__ import annotations

import _boot  # noqa: F401  (path bootstrap — must be first)
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from markum import analytics, branding, contract, hub  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)   # settlement reference (node or a hub)

branding.hero(
    st, "Markum Solar — Settlement Overview",
    f"{terms['structure']} at ${terms['strike']:,.2f}/MWh · "
    f"{contract.offtake_label(terms)} offtake",
)

if contract.is_placeholder_strike(terms):
    st.warning("⚠️ The contract **strike price is $0** — a placeholder. Set your "
               "real contract price on the **Contract Terms** page so the dollar "
               "figures are meaningful.")

win_start, win_end = hub.settlement_window(a["resource_node"], loc)
if win_start is None:
    st.info("No settled data is available yet for this asset.")
    branding.footer(st)
    st.stop()

st.caption(f"🟢 Settled data available **{win_start} → {win_end}** "
           "(ERCOT-published metered generation × real-time node price).")


@st.cache_data(show_spinner="Loading settlement history…")
def _history(win_start, win_end, terms_key):
    res = analytics.settle(win_start, win_end, dict(terms_key))
    if res is None:
        return None, None
    return res["summary"], analytics.monthly_breakdown(res["intervals"])


summary, monthly = _history(win_start, win_end, tuple(sorted(terms.items())))
if summary is None or monthly is None or monthly.empty:
    st.info("No overlapping generation and price intervals to settle.")
    branding.footer(st)
    st.stop()

# ── most-recent settled month KPIs ──────────────────────────────────────────
last = monthly.iloc[-1]
cfd = float(last["CfD"])
receives = cfd >= 0
st.subheader(f"Latest settled month — {last['Month']}")
k = st.columns(4)
k[0].metric("Energy", f"{last['MWh']:,.0f} MWh")
k[1].metric("Capture price", f"${last['Capture_$/MWh']:,.2f}/MWh",
            help="Generation-weighted average market price the energy earned.")
k[2].metric("Market value", f"${last['Market_value']:,.0f}",
            help="Σ MWh × real-time node price.")
k[3].metric("Net settlement", branding.signed_money(cfd),
            delta=("you receive" if receives else "you pay"),
            delta_color=("normal" if receives else "inverse"),
            help="Σ MWh × (market price − strike). Positive ⇒ you receive.")

# ── year-to-date / full-history rollup ──────────────────────────────────────
ytd_year = pd.to_datetime(monthly["Month"] + "-01").dt.year.max()
ytd = monthly[pd.to_datetime(monthly["Month"] + "-01").dt.year == ytd_year]
st.subheader(f"{ytd_year} year-to-date")
y = st.columns(4)
y[0].metric("Energy", f"{ytd['MWh'].sum():,.0f} MWh")
cap = (ytd["Market_value"].sum() / ytd["MWh"].sum()) if ytd["MWh"].sum() else 0.0
y[1].metric("Capture price", f"${cap:,.2f}/MWh")
y[2].metric("Market value", f"${ytd['Market_value'].sum():,.0f}")
ytd_cfd = float(ytd["CfD"].sum())
y[3].metric("Net settlement YTD", branding.signed_money(ytd_cfd),
            delta=("you receive" if ytd_cfd >= 0 else "you pay"),
            delta_color=("normal" if ytd_cfd >= 0 else "inverse"))

# ── monthly settlement chart ─────────────────────────────────────────────────
st.subheader("Monthly settlement")
fig = go.Figure()
colors = [branding.GOOD if v >= 0 else branding.BAD for v in monthly["CfD"]]
fig.add_bar(x=monthly["Month"], y=monthly["CfD"], marker_color=colors,
            name="Net settlement", hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
fig.add_trace(go.Scatter(x=monthly["Month"], y=monthly["MWh"], name="Energy (MWh)",
                         yaxis="y2", mode="lines+markers",
                         line=dict(color=branding.ACCENT, width=2)))
fig.update_layout(
    height=400, hovermode="x unified", margin=dict(t=30, b=10),
    yaxis=dict(title="Net settlement ($)", zeroline=True, zerolinecolor="#ccc"),
    yaxis2=dict(title="Energy (MWh)", overlaying="y", side="right", showgrid=False),
    legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, use_container_width=True)
st.caption("Green months: market above your strike (you receive). "
           "Red months: market below strike (you pay the difference).")

with st.expander("Monthly detail"):
    show = monthly.rename(columns={"Capture_$/MWh": "Capture $/MWh",
                                   "Market_value": "Market value $",
                                   "Strike_value": "Strike value $",
                                   "CfD": "Net settlement $"})
    st.dataframe(show, hide_index=True, use_container_width=True)

branding.footer(st)
