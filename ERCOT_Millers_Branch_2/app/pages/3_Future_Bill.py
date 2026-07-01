"""Projected Bill — forward estimate of upcoming settlement.

Two views:
  • This month & next (weather forecast) — day-level chart combining actual
    settled intervals with an Open-Meteo weather-driven generation forecast,
    calibrated against SCED history.
  • Long range (TMY / history) — the original whole-month projection using the
    historical metered shape (or a calibrated model if a TMY profile is cached).
"""

from __future__ import annotations

import _boot  # noqa: F401
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from portal import analytics, branding, contract, hub  # noqa: E402
from ercot_core import settle_ui  # noqa: E402
from ercot_core import near_term_bill  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
is_wind = "wind" in str(a.get("tech", "")).lower()
loc = contract.settle_location(terms)
terms, loc = settle_ui.choose(st, contract, terms)  # sidebar Node↔Hub toggle (view-only)
strike = float(terms.get("strike", 0.0))
share = float(terms.get("volume_share_pct", 100.0)) / 100.0

branding.hero(st, "Projected Settlements",
              f"Forward estimate · {terms['structure']} at ${strike:,.2f}/MWh · "
              f"{contract.offtake_label(terms)} offtake")
st.info("📌 **Estimate only.** Generation is modelled and the market price is your "
        "forward assumption. Actual settlement is on the **Past Settlement** page.")

win_start, win_end = hub.settlement_window(a["resource_node"], loc)
if win_start is None:
    st.info("No historical data is available yet to base a projection on.")
    st.stop()

tab_near, tab_long = st.tabs(["📅 Next 4 months — weather forecast",
                               "📈 Long range — TMY / history"])

# ── shared history ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Building generation profile from history…")
def _history(win_start, win_end, terms_key):
    res = analytics.settle(win_start, win_end, dict(terms_key))
    if res is None:
        return None, None
    iv = res["intervals"]
    monthly = analytics.monthly_breakdown(iv)
    iv2 = iv.copy()
    iv2["_cm"] = pd.to_datetime(iv2["interval_start"]).dt.month
    interval_counts = iv2.groupby("_cm")["interval_start"].count()
    return monthly, interval_counts


monthly, _interval_counts = _history(win_start, win_end, tuple(sorted(terms.items())))
if monthly is None or monthly.empty:
    st.info("Not enough history to project from.")
    st.stop()

m = monthly.copy()
m["cal_month"] = pd.to_datetime(m["Month"] + "-01").dt.month
hist_mwh = m.groupby("cal_month")["MWh"].mean()
hist_counts = m.groupby("cal_month")["MWh"].count()
tmy_mwh = analytics.tmy_monthly_mwh(share)
cal = (analytics.calibrate(hist_mwh, tmy_mwh, monthly_intervals=_interval_counts,
                           monthly_counts=hist_counts)
       if tmy_mwh is not None else None)
trailing_cap = (m["Market_value"].sum() / m["MWh"].sum()) if m["MWh"].sum() else strike

# ── price forecast (per-hub P10/P50/P90, capture-adjusted) ────────────────────
from ercot_core import price_forecast  # noqa: E402,PLC0415

hub_name = loc if loc.upper().startswith("HB_") else str(a.get("hub") or "HB_NORTH")


# Forecast horizon, in months. We request a full 10-year band; the engine
# returns as many months as the gas strip supports (~12), and the projection
# loop repeats the seasonal forward shape beyond that (see the long-range tab).
FORECAST_HORIZON_MONTHS = 120


@st.cache_data(show_spinner=f"Loading {hub_name} price forecast…")
def _forecast_band(hub_name, horizon, ratio_key, asof_iso):
    ratios = dict(ratio_key) if ratio_key else 1.0
    return price_forecast.monthly_band(hub_name, asof=asof_iso, horizon_months=horizon,
                                        capture_to_hub=ratios)


@st.cache_data(show_spinner="Calibrating capture-to-hub ratio…")
def _capture_ratios(hub_name, win_start_iso, win_end_iso, monthly_key):
    try:
        h = hub.hub_prices(hub_name,
                           pd.Timestamp(win_start_iso),
                           pd.Timestamp(win_end_iso) + pd.Timedelta(days=1))
    except Exception:  # noqa: BLE001 — fall back to 1.0 if hub history can't load
        h = pd.DataFrame()
    price_col = next((c for c in ("spp", "settlement_point_price", "price")
                      if c in h.columns), "spp")
    md = pd.DataFrame(list(monthly_key), columns=["MWh", "Market_value", "cal_month"])
    cal_months = md["cal_month"].astype(int)
    d = price_forecast.capture_to_hub_monthly(md, h, price_col=price_col,
                                               cal_months=cal_months,
                                               fleet_fallback=price_forecast.fleet_capture_ratios(hub_name))
    return tuple(sorted(d.items()))


_mb_key = tuple(zip(m["MWh"].astype(float).tolist(),
                    m["Market_value"].astype(float).tolist(),
                    m["cal_month"].astype(int).tolist()))
cap_ratio_key = _capture_ratios(hub_name, str(win_start), str(win_end), _mb_key)
cap_ratios = dict(cap_ratio_key)
import statistics as _stats
cap_ratio = _stats.median(cap_ratios.values()) if cap_ratios else 1.0
forecast_ok = True
try:
    fwd_band = _forecast_band(hub_name, FORECAST_HORIZON_MONTHS, cap_ratio_key,
                              str(pd.Timestamp.today().date()))
    if fwd_band.empty:
        forecast_ok = False
except Exception as _fe:  # noqa: BLE001 — degrade gracefully
    fwd_band = pd.DataFrame()
    forecast_ok = False
    st.sidebar.warning(f"Price forecast unavailable: {_fe}")

# ── shared sidebar price input ────────────────────────────────────────────────
st.sidebar.header("Price assumptions")
if forecast_ok:
    p50_now = float(fwd_band["p50"].iloc[0])
    st.sidebar.caption(
        f"**{hub_name} forecast** · P50 \\${p50_now:,.2f}/MWh next month · "
        f"capture-adjusted (median {cap_ratio:,.2f}×, "
        f"{min(cap_ratios.values()) if cap_ratios else 1:.2f}–"
        f"{max(cap_ratios.values()) if cap_ratios else 1:.2f}× by month). "
        f"Range shown = P10/P90.")
    use_manual = st.sidebar.checkbox("Override with a flat manual price", value=False)
else:
    p50_now = float(trailing_cap)
    use_manual = True
    st.sidebar.caption("Forecast unavailable — using a flat manual price.")

if use_manual:
    fwd_manual = st.sidebar.number_input(
        "Forward market price ($/MWh)",
        value=round(float(p50_now), 2), step=1.0,
        help=f"Trailing capture price from history is \\${trailing_cap:,.2f}/MWh.")
    band_manual = st.sidebar.slider("Sensitivity band (± $/MWh, long-range tab)", 0, 30, 10)
else:
    fwd_manual = None
    band_manual = 0

fwd = float(fwd_manual) if use_manual else p50_now

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
        fwd_price_by_month=(None if use_manual else
                            (dict(zip(fwd_band["Month"], fwd_band["p50"]))
                             if forecast_ok and not fwd_band.empty else None)),
        fwd_band_df=(None if use_manual else (fwd_band if forecast_ok and not fwd_band.empty else None)),
    )

# ── tab: long-range TMY / history ─────────────────────────────────────────────
with tab_long:
    st.sidebar.header("Generation assumptions (long range)")
    bases = ["Calibrated model", "Physical model (TMY)", "Historical shape"]
    if tmy_mwh is None:
        bases = ["Historical shape"]
        st.sidebar.caption(
            "No TMY weather profile is cached for this plant yet — only the "
            "historical shape is available. Run the Hub's plant-value step to "
            "enable the calibrated model.")
    basis = st.sidebar.radio(
        "Generation basis", bases, index=0,
        help="**Calibrated model** — PVWatts typical-year shape rescaled to match "
             "Miller's real metered output. **Physical model** — raw TMY, "
             "uncalibrated. **Historical shape** — mean of each calendar month.")

    auto_factor = float(cal["factor"]) if cal else 1.0
    per_month_factors = cal.get("per_month", {}) if cal else {}
    cal_factor = auto_factor
    if basis == "Calibrated model":
        cal_factor = st.sidebar.number_input(
            "Calibration factor", value=round(auto_factor, 3), step=0.01, format="%.3f",
            help=f"Metered output runs at {auto_factor:,.1%} of the TMY typical year "
                 f"over {cal['months'] if cal else 0} overlapping months.")
    degr = st.sidebar.slider(
        "Annual degradation (%/yr)", 0.0, 2.0, 0.0 if is_wind else 0.5, 0.1,
        help="Solar PV norm ≈0.5%/yr; wind default 0%.") / 100.0
    n_months = st.sidebar.slider("Months to project", 1, 120, 72,
                                 help="Up to 10 years. The price forecast band covers "
                                      "as far as the gas strip supports (~12 months); "
                                      "beyond that the seasonal forward shape repeats and "
                                      "the P10/P90 band widens with horizon.")

    def _expected_mwh(cal_month: int) -> float:
        if basis == "Historical shape":
            return float(hist_mwh.get(cal_month, hist_mwh.mean()))
        base = float(tmy_mwh.get(cal_month, tmy_mwh.mean()))
        if basis == "Calibrated model":
            return base * per_month_factors.get(cal_month, 1.0)
        return base

    hist_cap = (
        m.groupby("cal_month")["Capture_$/MWh"].mean()
        if "Capture_$/MWh" in m.columns
        else m.groupby("cal_month")["Market_value"].sum() / m.groupby("cal_month")["MWh"].sum()
    )

    start_month = (win_end.replace(day=1) + pd.offsets.MonthBegin(1)).date()
    use_forecast = forecast_ok and not use_manual and not fwd_band.empty
    band_idx = fwd_band.set_index("Month") if use_forecast else None
    # The engine returns only the liquid horizon (~12 months). Beyond it, repeat
    # the per-calendar-month forward shape (so seasonality persists instead of a
    # flat price) and fan the P10/P90 band out with horizon (so uncertainty grows
    # rather than collapsing to a single line).
    band_cal = band_last = None
    if use_forecast:
        _bc = fwd_band.copy()
        _bc["cal_month"] = pd.to_datetime(_bc["Month"] + "-01").dt.month
        band_cal = _bc.groupby("cal_month")[["p10", "p50", "p90"]].mean()
        band_last = pd.Period(fwd_band["Month"].max(), freq="M")
    rows = []
    for i in range(n_months):
        mdate = (pd.Timestamp(start_month) + pd.offsets.MonthBegin(i)).date()
        deg = (1.0 - degr) ** (i / 12.0)
        e_mwh = _expected_mwh(mdate.month) * deg
        hist_p = float(hist_cap.get(mdate.month, float(hist_cap.mean())))
        month_key = mdate.strftime("%Y-%m")
        if band_idx is not None and month_key in band_idx.index:
            p10 = float(band_idx.at[month_key, "p10"])
            p50 = float(band_idx.at[month_key, "p50"])
            p90 = float(band_idx.at[month_key, "p90"])
        elif band_cal is not None and mdate.month in band_cal.index:
            row = band_cal.loc[mdate.month]
            p50 = float(row["p50"])
            months_beyond = max((pd.Period(month_key, "M") - band_last).n, 0)
            widen = 1.0 + 0.5 * (months_beyond / 12.0) ** 0.5
            p10 = p50 - (p50 - float(row["p10"])) * widen
            p90 = p50 + (float(row["p90"]) - p50) * widen
        else:
            p50 = float(fwd)
            delta = float(band_manual or 0)
            p10, p90 = p50 - delta, p50 + delta
        rows.append({
            "Month": month_key,
            "Expected MWh": e_mwh,
            "P10 price ($/MWh)": p10,
            "P50 price ($/MWh)": p50,
            "P90 price ($/MWh)": p90,
            "Hist. capture ($/MWh)": hist_p,
            "Net @ low": e_mwh * (p10 - strike),
            "Net (expected)": e_mwh * (p50 - strike),
            "Net @ high": e_mwh * (p90 - strike),
        })
    proj = pd.DataFrame(rows)

    tot_mwh = proj["Expected MWh"].sum()
    tot_net = proj["Net (expected)"].sum()
    tot_lo = proj["Net @ low"].sum()
    tot_hi = proj["Net @ high"].sum()
    receives = tot_net >= 0

    _hdr = (f"Next {n_months // 12} year(s)" if n_months >= 12 and n_months % 12 == 0
            else f"Next {n_months} month(s)")
    st.subheader(_hdr)
    if basis == "Calibrated model" and cal:
        st.caption(
            f"Generation basis: **calibrated model** — PVWatts typical year "
            f"(**{tmy_mwh.sum():,.0f} MWh/yr** at your share) scaled by "
            f"**{cal_factor:.3f}** over {cal['months']} months, degraded {degr:.1%}/yr.")
    elif basis == "Physical model (TMY)":
        st.caption(f"**Physical model** — raw PVWatts typical year "
                   f"(**{tmy_mwh.sum():,.0f} MWh/yr** at your share), uncalibrated.")
    else:
        st.caption("**Historical shape** — mean of each calendar month across metered history.")
    verb = "you **receive**" if receives else "you **pay**"
    if forecast_ok and not use_manual:
        price_label = (f"P50 \\${proj['P50 price ($/MWh)'].min():,.2f}–"
                       f"\\${proj['P50 price ($/MWh)'].max():,.2f}/MWh")
        range_label = "P10–P90"
    else:
        price_label = f"\\${fwd:,.2f}/MWh"
        range_label = f"± \\${int(band_manual or 0)}/MWh"
    st.success(
        f"Projected energy **{tot_mwh:,.0f} MWh** at **{price_label}** "
        f"vs **\\${strike:,.2f}** strike ⇒ **{branding.signed_money(tot_net)}** — {verb}. "
        f"{range_label} range: **{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}**.")

    k = st.columns(3)
    k[0].metric("Projected energy", f"{tot_mwh:,.0f} MWh")
    k[1].metric("Net (expected)", branding.signed_money(tot_net),
                delta=("you receive" if receives else "you pay"),
                delta_color=("normal" if receives else "off"))
    k[2].metric(f"Range ({range_label})", f"{branding.signed_money(tot_lo)} … {branding.signed_money(tot_hi)}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(proj["Month"]) + list(proj["Month"][::-1]),
        y=list(proj["Net @ high"]) + list(proj["Net @ low"][::-1]),
        fill="toself", fillcolor="rgba(136,169,24,0.18)", line=dict(width=0),
        name=range_label, hoverinfo="skip"))
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
        for _pc in ("P10 price ($/MWh)", "P50 price ($/MWh)", "P90 price ($/MWh)",
                    "Hist. capture ($/MWh)"):
            show[_pc] = show[_pc].map(lambda v: f"${v:,.2f}")
        for c in ("Net @ low", "Net (expected)", "Net @ high"):
            show[c] = show[c].map(branding.signed_money_raw)
        st.dataframe(show, hide_index=True, use_container_width=True)

branding.footer(st)
