"""Projected Bill — forward estimate of upcoming settlement.

Two views:
  • This month & next (weather forecast) — day-level chart combining actual
    settled intervals with an Open-Meteo weather-driven generation forecast,
    calibrated against SCED history.
  • Long range (TMY / history) — the original whole-month projection using the
    weather-typical wind model or historical shape.
"""

from __future__ import annotations

import _boot  # noqa: F401
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from azuresky import analytics, branding, contract, hub  # noqa: E402
from ercot_core import near_term_bill  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)
strike = float(terms.get("strike", 0.0))
share = float(terms.get("volume_share_pct", 100.0)) / 100.0

branding.hero(st, "Projected Bill",
              f"Forward estimate · {terms['structure']} at ${strike:,.2f}/MWh · "
              f"{contract.offtake_label(terms)} offtake")
st.info("📌 **Estimate only.** Generation is modelled and the market price is your "
        "forward assumption. Actual settlement is on the **Past Settlement** page.")

win_start, win_end = hub.settlement_window(a["units"], loc)
if win_start is None:
    st.info("No historical data is available yet to base a projection on.")
    st.stop()

tab_near, tab_long = st.tabs(["📅 This month & next — weather forecast",
                               "📈 Long range — TMY / history"])

# ── shared history ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Building generation profile from history…")
def _history(win_start, win_end, terms_key):
    res = analytics.settle(win_start, win_end, dict(terms_key))
    if res is None:
        return None
    return analytics.monthly_breakdown(res["intervals"])


monthly = _history(win_start, win_end, tuple(sorted(terms.items())))
if monthly is None or monthly.empty:
    st.info("Not enough history to project from.")
    st.stop()

m = monthly.copy()
m["cal_month"] = pd.to_datetime(m["Month"] + "-01").dt.month
hist_mwh = m.groupby("cal_month")["MWh"].mean()
typ_mwh = analytics.typical_monthly_mwh(share)
cal = analytics.calibrate(hist_mwh, typ_mwh) if typ_mwh is not None else None
trailing_cap = (m["Market_value"].sum() / m["MWh"].sum()) if m["MWh"].sum() else strike

# ── shared sidebar price input ────────────────────────────────────────────────
st.sidebar.header("Price assumptions")
fwd = st.sidebar.number_input(
    "Forward market price ($/MWh)", value=round(float(trailing_cap), 2), step=1.0,
    help=f"Market price assumption used in both tabs. Default is the trailing "
         f"capture price (\\${trailing_cap:,.2f}/MWh) from history.")
band = st.sidebar.slider("Sensitivity band (± $/MWh, long-range tab)", 0, 30, 10)

# ── tab: near-term weather forecast ──────────────────────────────────────────
with tab_near:
    near_term_bill.render_near_term_tab(
        st,
        a=a,
        hub=hub,
        analytics=analytics,
        branding=branding,
        terms=terms,
        win_start=win_start,
        win_end=win_end,
        hist_mwh=hist_mwh,
        fwd_price=fwd,
        # Azure Sky: generation is summed across 4 SCED units
        gen_kwargs={"units": a["units"]},
    )

# ── tab: long-range TMY / history ─────────────────────────────────────────────
with tab_long:
    st.sidebar.header("Generation assumptions (long range)")
    bases = ["Calibrated model", "Physical model (typical year)", "Historical shape"]
    if typ_mwh is None:
        bases = ["Historical shape"]
        st.sidebar.caption("No typical-year wind profile is cached yet — run the "
                           "Hub's plant-value step to enable the calibrated model.")
    basis = st.sidebar.radio(
        "Generation basis", bases, index=0,
        help="**Calibrated model** — weather-typical wind profile rescaled to match "
             "Azure Sky's real metered output. **Physical model** — raw typical year, "
             "uncalibrated. **Historical shape** — mean of each calendar month.")

    auto_factor = float(cal["factor"]) if cal else 1.0
    cal_factor = auto_factor
    if basis == "Calibrated model":
        cal_factor = st.sidebar.number_input(
            "Calibration factor", value=round(auto_factor, 3), step=0.01, format="%.3f",
            help=f"Metered output runs at {auto_factor:,.1%} of the typical year "
                 f"over {cal['months'] if cal else 0} overlapping months.")
    degr = st.sidebar.slider(
        "Annual degradation (%/yr)", 0.0, 2.0, 0.0, 0.1,
        help="Wind turbines show little systematic degradation — default 0%.") / 100.0
    n_months = st.sidebar.slider("Months to project", 1, 12, 6)

    def _expected_mwh(cal_month: int) -> float:
        if basis == "Historical shape":
            return float(hist_mwh.get(cal_month, hist_mwh.mean()))
        base = float(typ_mwh.get(cal_month, typ_mwh.mean()))
        return base * cal_factor if basis == "Calibrated model" else base

    start_month = (win_end.replace(day=1) + pd.offsets.MonthBegin(1)).date()
    rows = []
    for i in range(n_months):
        mdate = (pd.Timestamp(start_month) + pd.offsets.MonthBegin(i)).date()
        deg = (1.0 - degr) ** (i / 12.0)
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
            f"Generation basis: **calibrated model** — weather-typical wind profile "
            f"(**{typ_mwh.sum():,.0f} MWh/yr** at your share) scaled by "
            f"**{cal_factor:.3f}** over {cal['months']} months, degraded {degr:.1%}/yr.")
    elif basis == "Physical model (typical year)":
        st.caption(f"**Physical model** — raw typical-year wind profile "
                   f"(**{typ_mwh.sum():,.0f} MWh/yr** at your share), uncalibrated.")
    else:
        st.caption("**Historical shape** — mean of each calendar month across metered history.")
    verb = "you **receive**" if receives else "you **pay**"
    st.success(
        f"Projected energy **{tot_mwh:,.0f} MWh** at **\\${fwd:,.2f}/MWh** "
        f"vs **\\${strike:,.2f}** strike ⇒ **{branding.signed_money(tot_net)}** — {verb}. "
        f"Range: **{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}**.")

    k = st.columns(3)
    k[0].metric("Projected energy", f"{tot_mwh:,.0f} MWh")
    k[1].metric("Net (expected)", branding.signed_money(tot_net),
                delta=("you receive" if receives else "you pay"),
                delta_color=("normal" if receives else "inverse"))
    k[2].metric("Range (± band)", f"{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}")

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
        show["Expected MWh"] = show["Expected MWh"].map(lambda v: f"{v:,.0f}")
        for c in ("Net @ low", "Net (expected)", "Net @ high"):
            show[c] = show[c].map(branding.signed_money_raw)
        st.dataframe(show, hide_index=True, use_container_width=True)

branding.footer(st)
