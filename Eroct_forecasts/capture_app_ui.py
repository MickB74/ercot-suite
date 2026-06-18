"""Streamlit UI for Wind Capture & Revenue — overlays the price forecast on a
cached wind-production run. Mirrors the Solar/Wind forecast page layout
(project inputs → annual summary → monthly → hourly profile) with a revenue and
VPPA-settlement layer added. Call ``render()`` from a Streamlit script."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import forecast
import pf_history
import shape as shaping
import wind_revenue as wr


@st.cache_data(show_spinner=False)
def _rt(hub: str) -> pd.DataFrame:
    return pf_history.load_rt15(hub)


def _capture_vs_atc_chart(mo: pd.DataFrame) -> go.Figure:
    x = pd.to_datetime(mo["month"])
    fig = go.Figure()
    if "atc_p90" in mo and "atc_p10" in mo:
        fig.add_trace(go.Scatter(x=x, y=mo["atc_p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=mo["atc_p10"], line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(150,150,150,0.15)", name="ATC P10–P90"))
    fig.add_trace(go.Scatter(x=x, y=mo["atc_p50"], line=dict(color="#888", width=2, dash="dot"),
                             name="ATC average (P50)"))
    fig.add_trace(go.Scatter(x=x, y=mo["capture_p50"], line=dict(color="#1f9e89", width=3),
                             name="Wind capture (P50)"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=40, b=10),
                      title="Wind capture price vs round-the-clock average",
                      yaxis_title="$/MWh", hovermode="x unified")
    return fig


def _profile_chart(prof: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=prof["hour"], y=prof["wind_cf"], name="Wind capacity factor",
                         marker_color="rgba(31,158,137,0.45)"), secondary_y=False)
    fig.add_trace(go.Scatter(x=prof["hour"], y=prof["price_p50"], name="Avg price (P50)",
                             line=dict(color="#d62728", width=3)), secondary_y=True)
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=10),
                      title="Why capture lags: wind output vs price, by hour of day",
                      xaxis_title="Hour (Central)", hovermode="x unified")
    fig.update_yaxes(title_text="Wind capacity factor", secondary_y=False)
    fig.update_yaxes(title_text="Price $/MWh", secondary_y=True)
    return fig


def render(resolve_name=None, preferred=None) -> None:
    """Render the Wind Capture & Revenue page.

    ``resolve_name`` is an optional ``(lat, lon) -> str | None`` hook that labels
    each cached run with the nearest real ERCOT project name. The Hub wires it to
    EIA-860; the standalone app leaves it None and shows coordinates only.

    ``preferred`` is an optional ``(lat, lon, label)`` — the Hub's universal plant.
    When given, the wind-site picker defaults to the cached run nearest that
    coordinate (within 25 km), so picking a plant elsewhere carries here.
    """
    st.title("💨 Wind Capture & Revenue")
    st.caption("Overlays the ERCOT price forecast on a wind-production run to get "
               "capture price, cannibalization, revenue and VPPA settlement — with "
               "P10/P50/P90 scenario bands.")

    sites = wr.list_wind_sites()
    if not sites:
        st.error("No cached wind-production runs found. Open the **Wind Forecast** "
                 "page first and run a site — its 8760 output feeds this page.")
        return

    # Prefix each run with the nearest real ERCOT project name when available, so
    # the picker reads "Azure Sky Wind Project · 33.168, -99.291 · …" instead of
    # bare coordinates. Falls back to the coordinate label on any miss.
    for s in sites:
        nm = None
        if resolve_name is not None:
            try:
                nm = resolve_name(s["lat"], s["lon"])
            except Exception:
                nm = None
        s["display"] = f"{nm} · {s['label']}" if nm else s["label"]

    # Universal plant → default the picker to the nearest cached run.
    default_ix, pref_note = 0, None
    if preferred:
        import math

        plat, plon, plabel = preferred

        def _km(a, b, c, d):
            r = 6371.0
            p1, p2 = math.radians(a), math.radians(c)
            dphi, dlmb = math.radians(c - a), math.radians(d - b)
            h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
            return 2 * r * math.asin(math.sqrt(h))

        bi, bd = min(((i, _km(plat, plon, s["lat"], s["lon"])) for i, s in enumerate(sites)),
                     key=lambda t: t[1])
        if bd <= 25.0:
            default_ix = bi
            pref_note = f"🌎 Universal plant **{plabel}** → nearest cached run ({bd:.1f} km)."
        else:
            pref_note = (f"🌎 Universal plant **{plabel}** has no cached wind run within 25 km — "
                         "run it on the **Wind Forecast** page and it'll appear here.")

    with st.sidebar:
        st.header("Wind project")
        if pref_note:
            st.caption(pref_note)
        labels = [s["display"] for s in sites]
        sel = st.selectbox("Wind site", labels, index=default_ix,
                           help="A wind location from the Wind Forecast page. All its "
                                "cached weather years are blended into one month × hour "
                                "capacity-factor shape, which drives capture. Names are "
                                "the nearest ERCOT plant (EIA-860) to the run coordinate.")
        site = sites[labels.index(sel)]
        if len(site["years"]) > 1:
            st.caption(f"Blending {len(site['years'])} weather years: {', '.join(site['years'])}.")
        default_mw = float(site["nameplate_mw"]) if not np.isnan(site["nameplate_mw"]) else 100.0
        nameplate = st.number_input("Nameplate (MW)", min_value=1.0, value=default_mw,
                                    step=10.0, help="Scales generation and revenue. "
                                    "Defaults to the cached fleet size; capture price "
                                    "and cannibalization don't depend on it.")
        st.header("Price")
        st.caption("🔗 Hub, as-of, horizon, simulations & strike are shared with the "
                   "Price Forecast and Plant Value pages.")
        # Shared "forecast context" (fx_*) — persists hub/as-of/horizon/sims/strike
        # across the Price Forecast and Plant Value pages too. Seed from
        # session_state, write the live value back.
        _hubs = list(pf_history.HUBS)
        _fx_hub = st.session_state.get("fx_hub", _hubs[0])
        hub = st.selectbox("Settlement hub", _hubs,
                           index=_hubs.index(_fx_hub) if _fx_hub in _hubs else 0,
                           help="Which ERCOT hub the project settles against.")
        st.session_state["fx_hub"] = hub
        asof = st.date_input("As of", value=st.session_state.get("fx_asof", pd.Timestamp.today().date()))
        st.session_state["fx_asof"] = asof
        horizon = st.slider("Horizon (months)", 12, 60,
                            min(max(int(st.session_state.get("fx_horizon", 24)), 12), 60), step=6)
        st.session_state["fx_horizon"] = horizon
        _simopts = [1000, 2000, 5000, 10000]
        _fx_sims = st.session_state.get("fx_sims", 2000)
        sims = st.select_slider("Simulations", _simopts,
                                value=_fx_sims if _fx_sims in _simopts else 2000)
        st.session_state["fx_sims"] = sims
        st.header("VPPA (optional)")
        strike = st.number_input("Strike price ($/MWh)", min_value=0.0,
                                 value=float(st.session_state.get("fx_strike", 0.0)), step=5.0,
                                 help="Fixed price in a virtual PPA. Settlement = "
                                      "(captured market price − strike) × generation. "
                                      "Leave 0 to skip settlement.")
        st.session_state["fx_strike"] = strike
        go_btn = st.button("Run capture analysis", type="primary")

    if not go_btn:
        st.info("Pick a wind run + hub on the left, then **Run capture analysis**.")
        st.stop()

    with st.spinner("Forecasting prices and computing capture…"):
        shp, wmeta = wr.load_cf_shape_blended(site["paths"], nameplate)
        curve, _ = forecast.run(hub, asof=str(asof), horizon_months=horizon, n_sims=int(sims))
        p8760 = shaping.build_8760(curve, _rt(hub))
        mo = wr.capture(p8760, shp, nameplate)
        ann = wr.annual(mo)
        prof = wr.hourly_profile(shp, p8760)

    # ---- annual headline (like solar's "Annual production") --------------
    a0 = ann.iloc[0]
    yrs = f" · {wmeta['n_years']}-yr blend" if wmeta.get("n_years", 1) > 1 else ""
    st.subheader(f"{site['lat']:.3f}, {site['lon']:.3f} · {nameplate:.0f} MW · {hub}{yrs}")
    c = st.columns(5)
    c[0].metric("ATC avg price", f"${a0['atc_p50']:.1f}/MWh")
    c[1].metric("Wind capture price", f"${a0['capture_p50']:.1f}/MWh",
                f"{a0['cannib_pct']:+.0f}% vs ATC")
    c[2].metric("Capacity factor", f"{wmeta['annual_cf']*100:.1f}%")
    c[3].metric(f"Generation ({a0['year']})", f"{a0['gen_gwh']:.0f} GWh")
    c[4].metric(f"Revenue ({a0['year']}, P50)", f"${a0['revenue_p50_m']:.1f}M",
                f"${a0.get('revenue_p10_m', float('nan')):.0f}–{a0.get('revenue_p90_m', float('nan')):.0f}M P10–P90")

    st.caption("**Capture price** is the generation-weighted average price the project "
               "actually earns. It sits **below the round-the-clock (ATC) average** "
               "because wind blows hardest at night and in spring, when prices are low "
               "— the gap is **cannibalization**.")

    tabs = st.tabs(["📈 Capture vs ATC", "🔢 Monthly", "🕐 Why (hourly)", "🧾 VPPA settlement"])

    with tabs[0]:
        st.plotly_chart(_capture_vs_atc_chart(mo), use_container_width=True)
        st.markdown("##### Annual summary")
        show = ann.copy()
        cols = {"year": "Year", "gen_gwh": "Gen (GWh)", "cf": "CF",
                "atc_p50": "ATC $", "capture_p50": "Capture $", "cannib_pct": "Cannib %",
                "revenue_p50_m": "Revenue $M (P50)"}
        st.dataframe(show[list(cols)].rename(columns=cols).round(2),
                     use_container_width=True, hide_index=True)

    with tabs[1]:
        m = mo.copy()
        disp = pd.DataFrame({
            "Month": m["month"],
            "Gen (MWh)": m["gen_mwh"].round(0),
            "ATC $": m["atc_p50"].round(1),
            "Capture $": m["capture_p50"].round(1),
            "Capture P10–P90": [f"{a:.0f} – {b:.0f}" for a, b in zip(m["capture_p10"], m["capture_p90"])],
            "Cannib %": m["cannib_pct"].round(1),
            "Revenue $ (P50)": m["revenue_p50"].round(0),
        })
        st.dataframe(disp, use_container_width=True, hide_index=True,
                     height=min(560, 40 + 36 * len(disp)))
        st.download_button("⬇️ Download monthly CSV", mo.round(2).to_csv(index=False),
                           file_name=f"wind_capture_{hub}_{asof}.csv")

    with tabs[2]:
        st.plotly_chart(_profile_chart(prof), use_container_width=True)
        st.caption("Wind output (bars) peaks overnight; price (line) peaks late afternoon. "
                   "That mismatch is the structural reason wind capture < ATC. Summer is "
                   "worst (afternoon scarcity + low daytime wind); winter is mildest.")

    with tabs[3]:
        if strike <= 0:
            st.info("Set a **strike price** in the sidebar to model VPPA settlement.")
        else:
            s = wr.settlement(mo, strike)
            tot = s["settle_p50_$"].sum()
            st.metric(f"Net VPPA settlement to offtaker ({strike:.0f} $/MWh strike)",
                      f"${tot/1e6:+.2f}M",
                      help="Positive = project pays buyer (market above strike); "
                           "negative = buyer tops up to the strike.")
            sd = pd.DataFrame({
                "Month": s["month"],
                "Gen (MWh)": s["gen_mwh"].round(0),
                "Capture $": s["capture_p50"].round(1),
                "Strike $": strike,
                "Settlement $ (P50)": s["settle_p50_$"].round(0),
            })
            if "settle_p10_$" in s:
                sd["Settlement P10–P90"] = [f"{a:,.0f} – {b:,.0f}"
                                            for a, b in zip(s["settle_p10_$"], s["settle_p90_$"])]
            st.dataframe(sd, use_container_width=True, hide_index=True,
                         height=min(560, 40 + 36 * len(sd)))
            st.caption("Settlement = (captured market price − strike) × generation, per "
                       "month. The offtaker locks in the strike; this is the cash that "
                       "changes hands. Capture (not ATC) is the right market price to use "
                       "because the swap settles on the project's actual generation.")
