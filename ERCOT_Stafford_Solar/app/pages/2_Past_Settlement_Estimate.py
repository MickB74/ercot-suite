"""Past Settlement — the auditable record for any historical period.

Pick a period, see exactly how the net settlement was built: metered MWh ×
real-time node price vs. the strike, interval by interval, with a downloadable
export for the customer's records.
"""

from __future__ import annotations

import datetime as dt

import _boot  # noqa: F401
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from portal import analytics, branding, contract, hub  # noqa: E402
from ercot_core import settle_ui  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)   # settlement reference (node or a hub)
terms, loc = settle_ui.choose(st, contract, terms)  # sidebar Node↔Hub toggle (view-only)

branding.hero(st, "Past Settlement Estimate",
              f"Audit any period · {terms['structure']} at ${terms['strike']:,.2f}/MWh · "
              f"{contract.offtake_label(terms)} offtake")

win_start, win_end = hub.settlement_window(a["resource_node"], loc)
if win_start is None:
    st.info("No settled data is available yet for this asset.")
    st.stop()

# ── period picker (clamped to the available settled window) ─────────────────
st.sidebar.header("Period")
mode = st.sidebar.radio("Period type", ["Month", "Quarter", "Year", "Custom"],
                        horizontal=True)
years = list(range(win_end.year, win_start.year - 1, -1))


def _eom(y, m):
    import calendar
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _last_full_month(we: dt.date) -> tuple[int, int]:
    """Return (year, month) of the last calendar month fully within we."""
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
    months = list(range(1, 13))
    mdef = _lfm if yr == _lfy else (win_end.month if yr == win_end.year else 12)
    mo = c2.selectbox("Month", months, index=mdef - 1,
                      format_func=lambda m: dt.date(2000, m, 1).strftime("%b"))
    start_d, end_d = dt.date(yr, mo, 1), _eom(yr, mo)
elif mode == "Quarter":
    c1, c2 = st.sidebar.columns(2)
    yr = c1.selectbox("Year", years, index=0)
    q = c2.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"])
    sm = (int(q[1]) - 1) * 3 + 1
    start_d, end_d = dt.date(yr, sm, 1), _eom(yr, sm + 2)
elif mode == "Year":
    yr = st.sidebar.selectbox("Year", years, index=0)
    start_d, end_d = dt.date(yr, 1, 1), dt.date(yr, 12, 31)
else:
    c1, c2 = st.sidebar.columns(2)
    start_d = c1.date_input("Start", value=win_start, min_value=win_start, max_value=win_end)
    end_d = c2.date_input("End", value=win_end, min_value=win_start, max_value=win_end)

# clamp to the available window
start_d = max(start_d, win_start)
end_d = min(end_d, win_end)
if start_d > end_d:
    st.error("That period is outside the settled window "
             f"({win_start} → {win_end}). Pick another.")
    st.stop()

st.caption(f"Settling **{start_d} → {end_d}** · settles at "
           f"**{loc}** on real-time (RT15) price.")


@st.cache_data(show_spinner="Settling…")
def _run(start_d, end_d, terms_key):
    res = analytics.settle(start_d, end_d, dict(terms_key))
    if res is None:
        return None
    return res["summary"], res["intervals"]


out = _run(start_d, end_d, tuple(sorted(terms.items())))
if out is None:
    st.info("No generation/price data for this period.")
    st.stop()
s, d = out
if d.empty:
    st.warning("No overlapping generation and price intervals in this period.")
    st.stop()

cov_min = pd.to_datetime(d["interval_start"]).min().date()
cov_max = pd.to_datetime(d["interval_start"]).max().date()
mwh = s["total_mwh"]
cap = s["capture_price"]
mktrev = s["merchant_revenue"]
strike_val = s["ppa_revenue"]
net = s["cfd_settlement"]              # offtaker frame: + ⇒ customer receives
receives = net >= 0

verb = "you **receive**" if receives else "you **pay**"
st.success(
    f"Over **{cov_min} → {cov_max}**, Stafford Solar produced **{mwh:,.0f} MWh**, captured at "
    f"**\\${cap:,.2f}/MWh**. At a **\\${terms['strike']:,.2f}** strike the energy's market "
    f"value is **\\${mktrev:,.0f}** vs **\\${strike_val:,.0f}** at strike, so the net "
    f"settlement is **{branding.signed_money(net)}** — {verb}.")

m = st.columns(4)
m[0].metric("Energy", f"{mwh:,.0f} MWh")
m[1].metric("Capture price", f"${cap:,.2f}/MWh", help="Generation-weighted market price.")
m[2].metric("Market value", f"${mktrev:,.0f}", help="Σ MWh × real-time node price.")
m[3].metric("Value at strike", f"${strike_val:,.0f}",
            help="Σ MWh × strike — the contract reference value.")
m2 = st.columns(4)
m2[0].metric("Net settlement", branding.signed_money(net),
             delta=("you receive" if receives else "you pay"),
             delta_color=("normal" if receives else "inverse"),
             help="Σ MWh × (market − strike). Positive ⇒ you receive.")
m2[1].metric("Settled intervals", f"{s['intervals']:,}")
excl = s.get("excluded_mwh", 0.0) or 0.0
if excl > 0:
    m2[2].metric("Unsettled (price < floor)", f"{excl:,.0f} MWh",
                 help=f"Generation in intervals below the ${terms.get('price_floor', 0):,.2f} "
                      "floor — not part of the swap (standard VPPA treatment).")
m2[3].metric("Avg net $/MWh", f"${(net / mwh if mwh else 0):,.2f}",
             help="Net settlement per settled MWh.")

# ── cumulative build chart ──────────────────────────────────────────────────
d = d.sort_values("interval_start")
d["Market value (cum)"] = d["merchant"].cumsum()
d["Value at strike (cum)"] = d["ppa_revenue"].cumsum()
fig = go.Figure()
for col, color in (("Market value (cum)", branding.PRIMARY),
                   ("Value at strike (cum)", branding.ACCENT)):
    fig.add_trace(go.Scatter(x=d["interval_start"], y=d[col], name=col, mode="lines",
                             line=dict(color=color)))
fig.update_layout(height=380, hovermode="x unified", margin=dict(t=20, b=10),
                  yaxis_title="Cumulative $", legend=dict(orientation="h", y=1.05))
st.plotly_chart(fig, use_container_width=True)
st.caption("Where **market value** runs below **value at strike**, you are topping the "
           "generator up to the strike (you pay); above it, you receive.")

# ── monthly breakdown (multi-month periods) ─────────────────────────────────
monthly = analytics.monthly_breakdown(d)
if len(monthly) > 1:
    st.subheader("Monthly breakdown")
    mfig = go.Figure()
    mcolors = [branding.GOOD if v >= 0 else branding.BAD for v in monthly["CfD"]]
    mfig.add_bar(x=monthly["Month"], y=monthly["CfD"], marker_color=mcolors,
                 name="Net settlement", hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
    mfig.add_trace(go.Scatter(x=monthly["Month"], y=monthly["MWh"], name="Energy (MWh)",
                              yaxis="y2", mode="lines+markers",
                              line=dict(color=branding.ACCENT, width=2)))
    mfig.update_layout(
        height=360, hovermode="x unified", margin=dict(t=30, b=10),
        yaxis=dict(title="Net settlement ($)", zeroline=True, zerolinecolor="#ccc"),
        yaxis2=dict(title="Energy (MWh)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.1))
    st.plotly_chart(mfig, use_container_width=True)

    # Table with a totals row, $-formatted for display.
    disp = monthly.copy()
    tot = {"Month": "Total", "MWh": disp["MWh"].sum(),
           "Capture_$/MWh": (disp["Market_value"].sum() / disp["MWh"].sum()
                             if disp["MWh"].sum() else 0.0),
           "Market_value": disp["Market_value"].sum(),
           "Strike_value": disp["Strike_value"].sum(), "CfD": disp["CfD"].sum()}
    disp = pd.concat([disp, pd.DataFrame([tot])], ignore_index=True)
    fmt = disp.copy()
    fmt["MWh"] = fmt["MWh"].map(lambda v: f"{v:,.0f}")
    fmt["Capture_$/MWh"] = fmt["Capture_$/MWh"].map(lambda v: f"${v:,.2f}")
    for c in ("Market_value", "Strike_value"):
        fmt[c] = fmt[c].map(lambda v: f"${v:,.0f}")
    fmt["CfD"] = fmt["CfD"].map(branding.signed_money_raw)
    fmt = fmt.rename(columns={"Capture_$/MWh": "Capture $/MWh",
                              "Market_value": "Market value", "Strike_value": "Value at strike",
                              "CfD": "Net settlement"})
    st.dataframe(fmt, hide_index=True, use_container_width=True)
    st.caption("Net settlement is offtaker-signed: positive = you receive, negative = you pay.")

    download_block_m = hub.export_block()
    if download_block_m is not None:
        download_block_m(
            st, monthly, name=f"portal_monthly_{start_d}_{end_d}",
            title=f"Stafford Solar — monthly settlement {start_d} → {end_d}",
            meta={"Asset": a["project_name"], "Settles at": loc,
                  "Structure": terms["structure"], "Strike": f"${terms['strike']:,.2f}/MWh",
                  "Period": f"{start_d} → {end_d}", "Months": f"{len(monthly)}",
                  "Net settlement": branding.signed_money_raw(net)},
            key="monthly_export")

# ── independent cross-check vs EIA-923 ──────────────────────────────────────
recon = analytics.reconcile_eia(start_d, end_d)
if recon is None:
    with st.expander("Cross-check vs EIA-923 (independent source)"):
        st.caption(
            "SCED metered generation can be cross-checked against the plant's own "
            "EIA-923 monthly filing — a useful tiebreaker when a month's output "
            "looks off. Stafford Solar isn't mapped to an EIA plant id yet (there's no "
            "public ERCOT→EIA crosswalk). Set **`eia_plant_id`** in `config.json` "
            "to that plant's EIA ORIS code to enable this check.")
elif not recon["table"].empty:
    rt = recon["table"]
    with st.expander(f"Cross-check vs EIA-923 · {recon['compared']} month(s) compared"
                     + (f" · ⚠ {recon['flagged']} divergent" if recon["flagged"] else " · ✓ all agree"),
                     expanded=bool(recon["flagged"])):
        st.caption(
            f"Plant-total SCED metered MWh vs EIA-923 net generation (EIA plant "
            f"**{recon['plant_id']}**). Months differing by more than "
            f"±{recon['tolerance_pct']:.0f}% are flagged — EIA-923 publishes on a "
            "~2-month lag, so the most recent months may show no EIA figure yet.")
        disp = rt.copy()
        disp["SCED_MWh"] = disp["SCED_MWh"].map(lambda v: f"{v:,.0f}")
        disp["EIA_MWh"] = disp["EIA_MWh"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
        disp["Delta_MWh"] = disp["Delta_MWh"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
        disp["Pct"] = disp["Pct"].map(lambda v: "—" if pd.isna(v) else f"{v:+.1f}%")
        disp["flag"] = rt["flag"].map(lambda f: "" if pd.isna(f) else ("⚠" if bool(f) else "✓"))
        disp = disp.rename(columns={"SCED_MWh": "SCED MWh", "EIA_MWh": "EIA-923 MWh",
                                    "Delta_MWh": "Δ MWh", "Pct": "Δ %", "flag": ""})
        st.dataframe(disp, hide_index=True, use_container_width=True)
        if recon["flagged"]:
            st.warning(
                "Flagged months diverge between ERCOT SCED telemetry and the plant's "
                "EIA-923 filing. SCED is the contractual settlement basis here; a large "
                "gap usually means a SCED data gap/curtailment-tagging issue or an EIA "
                "estimate — worth confirming before relying on that month's figure.")

with st.expander("Interval detail (15-minute)"):
    cols = [c for c in ["interval_start", "mw", "mwh", "price_raw", "price",
                        "merchant", "ppa_revenue", "cfd"] if c in d.columns]
    view = d[cols].rename(columns={
        "interval_start": "Interval (CPT)", "mw": "MW", "mwh": "MWh",
        "price_raw": "Price $/MWh (raw)", "price": "Price $/MWh (settled)",
        "merchant": "Market value $", "ppa_revenue": "Value at strike $",
        "cfd": "Net settlement $"})
    st.dataframe(view, hide_index=True, use_container_width=True, height=360)

# ── export (reuses the Hub's CSV/Excel/Markdown/PDF helper if present) ───────
download_block = hub.export_block()
if download_block is not None:
    download_block(
        st, d[[c for c in ["interval_start", "mw", "mwh", "price_raw", "price",
                           "merchant", "ppa_revenue", "cfd"] if c in d.columns]],
        name=f"portal_settlement_{start_d}_{end_d}",
        title=f"Stafford Solar settlement — {start_d} → {end_d}",
        meta={"Asset": a["project_name"], "Settles at": loc,
              "Structure": terms["structure"], "Strike": f"${terms['strike']:,.2f}/MWh",
              "Period": f"{start_d} → {end_d}", "Energy": f"{mwh:,.0f} MWh",
              "Capture price": f"${cap:,.2f}/MWh",
              "Net settlement": branding.signed_money_raw(net)})
else:
    st.download_button("⬇ Download interval CSV",
                       d.to_csv(index=False).encode("utf-8"),
                       file_name=f"portal_settlement_{start_d}_{end_d}.csv",
                       mime="text/csv")

branding.footer(st)
