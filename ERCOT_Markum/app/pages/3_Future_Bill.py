"""Projected Bill — a forward estimate of upcoming settlement.

A VPPA bill can't be known until ERCOT publishes the metered output and prices,
so this page *estimates* it: expected generation from Markum's own historical
shape (same calendar month, averaged over available history) × a forward market
price you can set, settled against the strike. It's a planning figure, not an
invoice — clearly labelled as such.
"""

from __future__ import annotations

import datetime as dt

import _boot  # noqa: F401
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from markum import analytics, branding, contract, hub  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)   # settlement reference (node or a hub)
strike = float(terms.get("strike", 0.0))
share = float(terms.get("volume_share_pct", 100.0)) / 100.0

branding.hero(st, "Projected Bill",
              f"Forward estimate · {terms['structure']} at ${strike:,.2f}/MWh · "
              f"{contract.offtake_label(terms)} offtake")
st.info("📌 **Estimate only.** Generation is modelled — by default from a "
        "weather-calibrated production model (PVWatts typical year, anchored to "
        "Markum's real metered output) — and the market price is your forward "
        "assumption. Switch the basis and tune the assumptions in the sidebar. "
        "Actual settlement is finalised from ERCOT-published data on the "
        "**Past Settlement** page.")

win_start, win_end = hub.settlement_window(a["resource_node"], loc)
if win_start is None:
    st.info("No historical data is available yet to base a projection on.")
    st.stop()


@st.cache_data(show_spinner="Building generation profile from history…")
def _history(win_start, win_end, terms_key):
    res = analytics.settle(win_start, win_end, dict(terms_key))
    if res is None:
        return None
    monthly = analytics.monthly_breakdown(res["intervals"])
    return monthly


monthly = _history(win_start, win_end, tuple(sorted(terms.items())))
if monthly is None or monthly.empty:
    st.info("Not enough history to project from.")
    st.stop()

# Historical monthly shape = mean MWh per calendar month across available years
# (already at the contracted volume share, since settle() applied mw_scale).
m = monthly.copy()
m["cal_month"] = pd.to_datetime(m["Month"] + "-01").dt.month
hist_mwh = m.groupby("cal_month")["MWh"].mean()
# Typical-year shape from the cached PVWatts TMY run (None if not cached yet).
tmy_mwh = analytics.tmy_monthly_mwh(share)
cal = analytics.calibrate(hist_mwh, tmy_mwh) if tmy_mwh is not None else None
# Trailing capture price = sensible default forward.
trailing_cap = (m["Market_value"].sum() / m["MWh"].sum()) if m["MWh"].sum() else strike

# ── controls ────────────────────────────────────────────────────────────────
st.sidebar.header("Generation assumptions")
bases = ["Calibrated model", "Physical model (TMY)", "Historical shape"]
if tmy_mwh is None:
    bases = ["Historical shape"]
    st.sidebar.caption("No TMY weather profile is cached for this plant yet, so "
                       "only the historical shape is available. Run the Hub's "
                       "plant-value step to enable the calibrated model.")
basis = st.sidebar.radio(
    "Generation basis", bases, index=0,
    help="**Calibrated model** — PVWatts typical-meteorological-year shape, "
         "rescaled so it matches Markum's real metered output (captures actual "
         "availability, curtailment and losses). **Physical model** — raw TMY, "
         "no calibration. **Historical shape** — mean of each calendar month "
         "across the metered history.")

auto_factor = float(cal["factor"]) if cal else 1.0
cal_factor = auto_factor
if basis == "Calibrated model":
    cal_factor = st.sidebar.number_input(
        "Calibration factor", value=round(auto_factor, 3), step=0.01,
        format="%.3f",
        help=f"Metered output runs at {auto_factor:,.1%} of the TMY typical year "
             f"over the {cal['months'] if cal else 0} overlapping calendar months. "
             "Editable — raise/lower to stress availability.")
degr = st.sidebar.slider(
    "Annual degradation (%/yr)", 0.0, 2.0, 0.5, 0.1,
    help="PV output decline applied forward from the latest settled month "
         "(industry norm ≈0.5%/yr).") / 100.0

st.sidebar.header("Price assumptions")
n_months = st.sidebar.slider("Months to project", 1, 12, 6)
fwd = st.sidebar.number_input(
    "Forward market price ($/MWh)", value=round(float(trailing_cap), 2), step=1.0,
    help=f"Your assumed average real-time price going forward. Default is the "
         f"trailing capture price (\\${trailing_cap:,.2f}/MWh) from history.")
band = st.sidebar.slider("Sensitivity band (± $/MWh)", 0, 30, 10,
                         help="Show how the bill swings if prices land above/below "
                              "your forward assumption.")


def _expected_mwh(cal_month: int) -> float:
    """Expected MWh for a calendar month under the chosen generation basis."""
    if basis == "Historical shape":
        return float(hist_mwh.get(cal_month, hist_mwh.mean()))
    base = float(tmy_mwh.get(cal_month, tmy_mwh.mean()))
    return base * cal_factor if basis == "Calibrated model" else base


start_month = (win_end.replace(day=1) + pd.offsets.MonthBegin(1)).date()

rows = []
for i in range(n_months):
    mdate = (pd.Timestamp(start_month) + pd.offsets.MonthBegin(i)).date()
    deg = (1.0 - degr) ** (i / 12.0)               # compound forward from now
    e_mwh = _expected_mwh(mdate.month) * deg
    rows.append({
        "Month": mdate.strftime("%Y-%m"),
        "Expected MWh": e_mwh,
        "Net @ low": e_mwh * ((fwd - band) - strike),
        "Net (expected)": e_mwh * (fwd - strike),
        "Net @ high": e_mwh * ((fwd + band) - strike),
    })
proj = pd.DataFrame(rows)

tot_mwh = proj["Expected MWh"].sum()
tot_net = proj["Net (expected)"].sum()
tot_lo = proj["Net @ low"].sum()
tot_hi = proj["Net @ high"].sum()
receives = tot_net >= 0

st.subheader(f"Next {n_months} month(s)")
if basis == "Calibrated model" and cal:
    st.caption(
        f"Generation basis: **calibrated model** — PVWatts typical year "
        f"(**{tmy_mwh.sum():,.0f} MWh/yr** at your share) scaled by "
        f"**{cal_factor:.3f}** to match metered output over "
        f"{cal['months']} overlapping months, then degraded {degr:.1%}/yr.")
elif basis == "Physical model (TMY)":
    st.caption(f"Generation basis: **physical model** — raw PVWatts typical year "
               f"(**{tmy_mwh.sum():,.0f} MWh/yr** at your share), uncalibrated.")
else:
    st.caption("Generation basis: **historical shape** — mean of each calendar "
               "month across the metered history.")
verb = "you **receive**" if receives else "you **pay**"
st.success(
    f"Projected energy **{tot_mwh:,.0f} MWh** at a forward price of **\\${fwd:,.2f}/MWh** "
    f"vs a **\\${strike:,.2f}** strike ⇒ net settlement of **{branding.signed_money(tot_net)}** "
    f"— {verb}. Within ±\\${band}/MWh the range is "
    f"**{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}**.")

k = st.columns(3)
k[0].metric("Projected energy", f"{tot_mwh:,.0f} MWh")
k[1].metric("Net (expected)", branding.signed_money(tot_net),
            delta=("you receive" if receives else "you pay"),
            delta_color=("normal" if receives else "inverse"))
k[2].metric("Range (± band)",
            f"{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}")

# ── chart ────────────────────────────────────────────────────────────────────
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=list(proj["Month"]) + list(proj["Month"][::-1]),
    y=list(proj["Net @ high"]) + list(proj["Net @ low"][::-1]),
    fill="toself", fillcolor="rgba(136,169,24,0.18)", line=dict(width=0),
    name=f"± ${band}/MWh", hoverinfo="skip"))
colors = [branding.GOOD if v >= 0 else branding.BAD for v in proj["Net (expected)"]]
fig.add_bar(x=proj["Month"], y=proj["Net (expected)"], marker_color=colors,
            name="Net (expected)", hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
fig.update_layout(height=380, hovermode="x unified", margin=dict(t=30, b=10),
                  yaxis=dict(title="Projected net settlement ($)", zeroline=True,
                             zerolinecolor="#ccc"),
                  legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig, use_container_width=True)

with st.expander("Projection detail"):
    show = proj.copy()
    for c in ("Expected MWh",):
        show[c] = show[c].map(lambda v: f"{v:,.0f}")
    for c in ("Net @ low", "Net (expected)", "Net @ high"):
        show[c] = show[c].map(branding.signed_money_raw)
    st.dataframe(show, hide_index=True, use_container_width=True)
    st.caption(f"Expected MWh = **{basis}** for each calendar month (at your "
               "contracted volume share), degraded forward. "
               "Net = MWh × (price − strike).")

branding.footer(st)
