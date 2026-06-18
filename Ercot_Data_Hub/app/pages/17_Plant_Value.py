"""Plant Value — what a specific solar plant's output is worth at its hub.

Picks a plant from the curated renewable registry, forecasts its solar
generation (PVWatts, page 13's engine) and its hub's hourly forward price
(page 16's engine), and combines them into a generation-weighted **capture
price** + revenue for the next few calendar years. The orchestration lives in
``ercot_core.plant_value``; this page is just the UI.
"""

from __future__ import annotations

import os
import pathlib
import sys

# repo root (for ercot_core) + app/ (for _common), matching the other pages.
HUB_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HUB_ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/

from ercot_core import paths as hub_paths  # noqa: E402

# Route the price-forecast engine's lake at the Hub before it is imported
# (pf_paths reads these at import time) — same wiring as page 16.
os.environ.setdefault("PF_DATA", str(hub_paths.DATA / "price_forecast"))
os.environ.setdefault("PF_HUB_LAKE_DIR", str(hub_paths.HUB_PRICES_DIR))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import _common  # noqa: F401,E402  (path bootstrap)
import _export  # noqa: E402

from ercot_core import credentials, paths, plant_value  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🔆 Plant Value — Capture Price")
st.caption("Pick a solar or wind plant; value its generation against its hub's "
           "forward price curve. **Capture price** = the generation-weighted price "
           "the plant actually earns, vs the flat all-hours (ATC) average.")

paths.ensure_dirs()

# --- universal plant (shared sidebar selector) -----------------------------
asset = _common.universal_plant_picker(st)
if asset is None:
    st.error("No plants found in the curated registry (sibling `price_settlements` repo).")
    st.stop()
is_wind = str(asset.get("tech", "")).lower() == "wind"

# Carry this plant's hub out to the other forecast pages.
try:
    st.session_state["fx_hub"] = plant_value.to_hub_code(asset["hub"])
except Exception:  # noqa: BLE001
    pass

# Changing the universal plant clears the last run so stale numbers never show
# under a different plant.
if st.session_state.get("_pv_plant") != asset["resource_name"]:
    st.session_state.pop("pv_args", None)
    st.session_state["_pv_plant"] = asset["resource_name"]

st.caption(f"**{asset.get('project_name', asset['resource_name'])}** · "
           f"{str(asset.get('tech', '')).title()} · {asset['capacity_mw']:,.0f} MW · "
           f"{asset.get('county', '?')} · {asset['hub']} hub")
with st.expander("Registry record"):
    st.json(asset)

# --- NREL credential gate (solar weather only; wind is keyless) ------------
if not is_wind:
    has_creds = bool(credentials.get_nrel_api_key()) and bool(credentials.get_nrel_email())
    if not has_creds:
        st.warning("Solar generation needs free NREL/NSRDB credentials (api key + the "
                   "email registered to it).")
        with st.expander("Add NREL credentials (free, one-time)", expanded=True):
            st.markdown("Get a key at https://developer.nrel.gov/signup/")
            k = st.text_input("NREL API key", type="password", key="nrel_key_in")
            em = st.text_input("Registered email", key="nrel_email_in")
            if st.button("Save credentials") and k and em:
                credentials.save_nrel_credentials(k, em)
                st.success("Saved. Re-run below.")
                st.rerun()
        st.stop()

# --- controls --------------------------------------------------------------
# Shared "forecast context" (fx_*) — horizon / as-of / simulations / strike
# persist across Price Forecast and Wind Capture. Seed from session_state, write
# the live value back. (Weather year + offtaker share stay page-local.)
st.caption("🔗 Hub (via plant), horizon, as-of, simulations & PPA price are shared "
           "with the Price Forecast and Wind Capture pages.")
c1, c2 = st.columns(2)
horizon = c1.slider("Forecast horizon (months)", 12, 60,
                    min(max(int(st.session_state.get("fx_horizon", 36)), 12), 60), step=6,
                    help="Months of forward price curve to value the plant against.")
st.session_state["fx_horizon"] = horizon
if is_wind:
    year = c2.selectbox("Weather year", ["2024", "2023", "2022", "2021"], index=0,
                        help="ERA5 reanalysis year (Open-Meteo, keyless) used to shape "
                             "the wind generation 8760. No TMY for wind — pick a recent "
                             "complete year.")
else:
    year = c2.selectbox("Weather year", ["tmy", "2023", "2022", "2021"], index=0,
                        help="TMY = typical year (expected). Or backcast a real weather year.")

d1, d2 = st.columns(2)
asof = d1.date_input("As of", value=st.session_state.get("fx_asof", pd.Timestamp.today().date()),
                     help="Forecast start date. Shared with the Price Forecast and Wind "
                          "Capture pages.")
st.session_state["fx_asof"] = asof
_simopts = [1000, 2000, 5000, 10000]
_fx_sims = st.session_state.get("fx_sims", 5000)
sims = d2.select_slider("Simulations", _simopts, value=_fx_sims if _fx_sims in _simopts else 5000,
                        help="Monte Carlo price paths behind the P10/P50/P90 bands.")
st.session_state["fx_sims"] = sims

cc1, cc2 = st.columns(2)
strike = cc1.number_input("PPA price ($/MWh)", min_value=0.0,
                          value=float(st.session_state.get("fx_strike", 0.0)), step=1.0,
                          help="Fixed price the offtaker pays. Net settlement to offtaker = Σ "
                               "contracted gen × (capture price − PPA): positive ⇒ offtaker "
                               "receives, negative ⇒ offtaker pays. Set 0 to ignore (pure merchant).")
st.session_state["fx_strike"] = strike
offtake_pct = cc2.number_input("Offtaker share (% of output)", min_value=0.0,
                               max_value=100.0, value=100.0, step=5.0,
                               help="Fraction of the plant's output under this PPA. "
                                    "<100% leaves the rest sold merchant; the settlement "
                                    "scales to the contracted volume only.")
share = offtake_pct / 100.0
contracted_mw = asset["capacity_mw"] * share

run = st.button("⚡ Run valuation", type="primary")


@st.cache_data(show_spinner=True)
def _value(resource_name: str, horizon_months: int, weather_year: str,
           asof: str, n_sims: int):
    pool = plant_value.load_solar_assets() + plant_value.load_wind_assets()
    a = next(x for x in pool if x["resource_name"] == resource_name)
    return plant_value.value_plant(
        a, horizon_months=horizon_months, year=weather_year,
        asof=asof, n_sims=int(n_sims),
        api_key=credentials.get_nrel_api_key(),
        email=credentials.get_nrel_email(),
    )


# Persist the last run across reruns (so editing widgets doesn't blank the page).
if run:
    st.session_state["pv_args"] = (asset["resource_name"], horizon, year, str(asof), int(sims))

args = st.session_state.get("pv_args")
if not args:
    st.info("Pick a plant and click **Run valuation**.")
    st.stop()

try:
    with st.spinner(f"Forecasting {'wind' if is_wind else 'solar'} generation + hub prices…"):
        res = _value(*args)
except Exception as e:  # noqa: BLE001
    st.error(f"Valuation failed: {e}")
    st.stop()

# --- headline metrics ------------------------------------------------------
gs = res["gen_summary"]
by = res["by_year"].copy()
st.subheader(f"{asset.get('project_name', asset['resource_name'])} · {res['hub_code']}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Nameplate", f"{asset['capacity_mw']:,.0f} MW")
m2.metric("Annual generation", f"{gs['annual_mwh']:,.0f} MWh")
m3.metric(f"Capacity factor ({gs['cf_label']})", f"{gs['capacity_factor']*100:,.1f}%")
m4.metric("Price source", res["price_meta"].get("gas_source", "—"), help="Gas → power model input")

# Wind: show which real turbine fleet shaped the generation (USWTDB).
fm = res.get("fleet_meta")
if fm:
    if fm.get("fleet_name"):
        st.caption(f"Generation shape from the **{fm['fleet_name']}** turbine fleet "
                   f"(USWTDB, {fm['fleet_distance_km']} km away, "
                   f"{fm['fleet_capacity_mw']:.0f} MW installed), rescaled to nameplate.")
    else:
        st.caption("No USWTDB turbine array within 15 km — generation shaped by a "
                   "generic 2.5 MW-class fleet at the plant's coordinates.")

HOURS_FULL = 8000  # below this a calendar year is partial (forecast start/end stub)
ATC_GREY, CAP_AMBER, NET_GREEN = "#9aa0a6", "#f5a623", "#34a853"
LEGEND = dict(orientation="h", yanchor="bottom", y=1.02, x=0,
              bgcolor="rgba(0,0,0,0)")  # flat top-left, no opaque box

if strike > 0:
    by = plant_value.add_net_settlement(by, strike, share=share)

net_col = f"Net @ ${strike:,.0f}" + ("" if share >= 1 else f" · {offtake_pct:.0f}%")

# Pre-format as strings so thousands separators / currency always render
# (Streamlit NumberColumn printf grouping is frontend-only and version-sensitive).
def _usd(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


# --- settlement KPIs -------------------------------------------------------
if strike > 0:
    full_mask = [h >= HOURS_FULL for h in by["hours"]]
    bf = by[full_mask] if any(full_mask) else by   # fall back to all if none full
    n_full = max(len(bf), 1)
    net_total = float(bf["net_settlement"].sum())
    contracted_total = float(bf["contracted_mwh"].sum())
    ppa_total = float(bf["ppa_revenue"].sum())
    spread = net_total / contracted_total if contracted_total else float("nan")
    recv = net_total >= 0

    st.markdown(f"#### PPA settlement to offtaker — ${strike:,.0f}/MWh on {offtake_pct:.0f}% "
                f"({contracted_mw:,.0f} MW)")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Net CfD settlement (to offtaker)", _usd(net_total),
              delta=("offtaker receives" if recv else "offtaker pays"),
              delta_color=("normal" if recv else "inverse"),
              help="Full forecast years only. Σ contracted gen × (capture price − PPA); "
                   "positive ⇒ offtaker receives from generator (market above strike), "
                   "negative ⇒ offtaker tops the generator up to the strike.")
    k2.metric("Per full year", _usd(net_total / n_full))
    k3.metric("Contracted volume", f"{contracted_total / n_full:,.0f} MWh/yr",
              help=f"{offtake_pct:.0f}% of output. PPA gross ≈ {_usd(ppa_total / n_full)}/yr.")
    k4.metric("Settlement spread", f"${spread:,.2f}/MWh",
              help="Net per contracted MWh to the offtaker = generation-weighted "
                   "capture price − PPA.")
else:
    st.info("Set a **PPA price** above to see settlement KPIs (CfD true-up vs the "
            "plant's capture price).")

# --- per-year table --------------------------------------------------------
st.markdown("#### By calendar year")
disp = pd.DataFrame({
    "Year": by["year"].astype(int).astype(str),
    "Period": ["full" if h >= HOURS_FULL else "partial" for h in by["hours"]],
    "Generation (MWh)": [f"{x:,.0f}" for x in by["gen_mwh"]],
    "ATC P50 ($/MWh)": [f"${x:,.2f}" for x in by["atc_p50"]],
    "Capture P50 ($/MWh)": [f"${x:,.2f}" for x in by["capture_p50"]],
    "Capture P10–P90 ($/MWh)": [f"${lo:,.0f} – ${hi:,.0f}"
                                for lo, hi in zip(by["capture_p10"], by["capture_p90"])],
    "Capture ratio": [f"{x:.3f}" for x in by["capture_ratio"]],
    "Merchant rev": [_usd(x) for x in by["revenue_p50"]],
})
if strike > 0:
    disp["Contracted (MWh)"] = [f"{x:,.0f}" for x in by["contracted_mwh"]]
    disp[net_col] = [_usd(x) for x in by["net_settlement"]]
st.dataframe(disp, hide_index=True, use_container_width=True)

_pv_meta = {"Plant": asset.get("project_name", asset["resource_name"]),
            "Hub": res["hub_code"], "Tech": str(asset.get("tech", "")).title(),
            "Nameplate": f"{asset['capacity_mw']:,.0f} MW",
            "PPA strike": (f"${strike:,.0f}/MWh" if strike > 0 else "merchant"),
            "Offtaker share": f"{offtake_pct:.0f}%"}
_export.download_block(
    st, by, name=f"plant_value_{asset['resource_name']}_by_year",
    title=f"Plant value (by year) — {asset.get('project_name', asset['resource_name'])}",
    meta=_pv_meta, key="pv_year")

if strike > 0:
    net_full = by.loc[[h >= HOURS_FULL for h in by["hours"]], "net_settlement"].sum()
    who = "offtaker **receives**" if net_full >= 0 else "offtaker **pays**"
    scope = (f"on **{offtake_pct:.0f}%** of output (**{contracted_mw:,.0f} MW** of "
             f"{asset['capacity_mw']:,.0f} MW)" if share < 1 else "on **100%** of output")
    st.caption(f"At a **\\${strike:,.0f}/MWh** PPA {scope}, net CfD settlement to the "
               f"offtaker over full years totals **\\${net_full:,.0f}** — {who} "
               "(Σ contracted gen × (capture price − PPA)).")
_discount = ("the usual solar 'capture discount' (midday over-production)" if not is_wind
             else "the usual wind 'cannibalization' (windiest overnight/spring, when prices sag)")
st.caption("**Capture ratio** > 1 means the plant earns *more* than the flat ATC average "
           f"(output lines up with higher-priced hours); < 1 is {_discount}. "
           "Merchant revenue = capture price × generation, before any PPA/hedge. "
           "Partial years are the forecast's start/end stubs — a true Cal-26 figure needs "
           "the pre-forecast months backfilled with realized prices.")

# --- chart: capture vs ATC by year, with P10–P90 band ----------------------
yrs = [str(y) for y in by["year"]]
fig = go.Figure()
fig.add_trace(go.Bar(x=yrs, y=by["atc_p50"], name="ATC (flat)", marker_color=ATC_GREY))
fig.add_trace(go.Bar(
    x=yrs, y=by["capture_p50"], name="Capture P50", marker_color=CAP_AMBER,
    error_y=dict(type="data", symmetric=False, color="#c98a12", thickness=1.2, width=4,
                 array=(by["capture_p90"] - by["capture_p50"]).tolist(),
                 arrayminus=(by["capture_p50"] - by["capture_p10"]).tolist()),
))
fig.update_layout(height=360, barmode="group", yaxis_title="$/MWh",
                  margin=dict(l=10, r=10, t=40, b=10), legend=LEGEND)
st.plotly_chart(fig, use_container_width=True)

# --- monthly view ----------------------------------------------------------
st.markdown("#### By month")
bym = res["by_month"].copy()
bym["date"] = pd.to_datetime(dict(year=bym["year"], month=bym["month"], day=1))
if strike > 0:
    bym = plant_value.add_net_settlement(bym, strike, share=share)

mfig = go.Figure()
# P10–P90 capture band (filled), then ATC and capture P50 lines.
mfig.add_trace(go.Scatter(x=bym["date"], y=bym["capture_p90"], mode="lines",
                          line=dict(width=0), showlegend=False, hoverinfo="skip"))
mfig.add_trace(go.Scatter(x=bym["date"], y=bym["capture_p10"], mode="lines",
                          line=dict(width=0), fill="tonexty",
                          fillcolor="rgba(245,166,35,0.15)", name="Capture P10–P90"))
mfig.add_trace(go.Scatter(x=bym["date"], y=bym["atc_p50"], mode="lines",
                          line=dict(color=ATC_GREY, width=2, dash="dot"), name="ATC (flat)"))
mfig.add_trace(go.Scatter(x=bym["date"], y=bym["capture_p50"], mode="lines",
                          line=dict(color=CAP_AMBER, width=2.5), name="Capture P50"))
mfig.update_layout(height=340, yaxis_title="$/MWh", margin=dict(l=10, r=10, t=40, b=10),
                   legend=LEGEND, hovermode="x unified")
st.plotly_chart(mfig, use_container_width=True)

if strike > 0:
    nfig = go.Figure()
    colors = [NET_GREEN if v >= 0 else "#d23f31" for v in bym["net_settlement"]]
    nfig.add_trace(go.Bar(x=bym["date"], y=bym["net_settlement"], marker_color=colors,
                          name="Net settlement"))
    pct_note = "" if share >= 1 else f", {offtake_pct:.0f}% offtake"
    nfig.update_layout(height=300, yaxis_title="Net settlement ($)",
                       margin=dict(l=10, r=10, t=40, b=10), legend=LEGEND,
                       title=dict(text=f"Monthly CfD settlement to offtaker @ ${strike:,.0f}/MWh"
                                       f"{pct_note} (green = offtaker receives, red = offtaker pays)",
                                  font=dict(size=13)))
    st.plotly_chart(nfig, use_container_width=True)

with st.expander("Monthly detail table"):
    mdisp = pd.DataFrame({
        "Month": bym["date"].dt.strftime("%Y-%m"),
        "Generation (MWh)": [f"{x:,.0f}" for x in bym["gen_mwh"]],
        "ATC P50 ($/MWh)": [f"${x:,.2f}" for x in bym["atc_p50"]],
        "Capture P50 ($/MWh)": [f"${x:,.2f}" for x in bym["capture_p50"]],
        "Capture ratio": [f"{x:.3f}" for x in bym["capture_ratio"]],
        "Merchant rev": [_usd(x) for x in bym["revenue_p50"]],
    })
    if strike > 0:
        mdisp["Contracted (MWh)"] = [f"{x:,.0f}" for x in bym["contracted_mwh"]]
        mdisp[net_col] = [_usd(x) for x in bym["net_settlement"]]
    st.dataframe(mdisp, hide_index=True, use_container_width=True)
    _export.download_block(
        st, bym, name=f"plant_value_{asset['resource_name']}_by_month",
        title=f"Plant value (by month) — {asset.get('project_name', asset['resource_name'])}",
        meta=_pv_meta, key="pv_month")
