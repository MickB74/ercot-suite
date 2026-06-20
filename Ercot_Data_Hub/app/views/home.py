"""Control Tower — dataset freshness + one-click refresh with live logs.

Rendered as the default page by the router in app/Home.py. The router owns
``st.set_page_config`` and ``paths.ensure_dirs``; this script only renders.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402

import streamlit as st  # noqa: E402

import orchestrate  # noqa: E402
from ercot_core import credentials, paths  # noqa: E402

import solar_app_ui as solar_ui  # noqa: E402
import solar_pvwatts as solar  # noqa: E402

st.title("⚡ ERCOT Data Hub")
st.caption(
    "Unified orchestration for four ERCOT datasets — one credential store, one "
    "shared 60-day SCED cache, one data lake. Refresh anything below, then work "
    "through the sidebar: **Explore** the data, **Resolve & Map** identities, "
    "then **Analyze** (settlements, reconciliation)."
)

# --------------------------------------------------------------------------
# Task router — jump straight to a goal instead of hunting the sidebar
# --------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("**What do you want to do?**")
    q1, q2, q3 = st.columns(3)
    with q1:
        st.page_link("screens/7_PPA_Settlement.py", label="Settle a PPA", icon="🧾")
        st.page_link("screens/2_Hub_Prices.py", label="Explore hub prices", icon="💵")
    with q2:
        st.page_link("screens/8_Reconciliation.py", label="Reconcile a plant", icon="🔁")
        st.page_link("screens/5_Node_Explorer.py", label="Explore a node", icon="📈")
    with q3:
        st.page_link("screens/6_Project_Lookup.py", label="Find a project's node", icon="🔎")
        st.page_link("screens/13_Solar_Forecast.py", label="Forecast solar (lat/long)", icon="☀️")

# --------------------------------------------------------------------------
# Credentials (shared config.json) — only hub_prices + system_gen wind/solar need it
# --------------------------------------------------------------------------
with st.expander("🔑 ERCOT API credentials (shared by all datasets)",
                 expanded=not credentials.have_credentials()):
    cfg = credentials.load_config()
    have = credentials.have_credentials(cfg)
    if have:
        st.success(f"Credentials configured for **{cfg.get('username','?')}**. "
                   "Used by hub prices (direct API) and system-gen wind/solar.")
    else:
        st.warning("No credentials yet. Hub prices and the wind/solar supplement "
                   "need a free ERCOT API account (apiexplorer.ercot.com). The "
                   "Fuel-Mix, SCED, and EIA-923 datasets work without one.")
    with st.form("creds"):
        u = st.text_input("Username / email", value=cfg.get("username", ""))
        p = st.text_input("Password", value=cfg.get("password", ""), type="password")
        k = st.text_input("Subscription key", value=cfg.get("subscription_key", ""),
                          type="password")
        bf = st.text_input("Hub-price backfill start (YYYY-MM-DD)",
                           value=cfg.get("backfill_start", "2024-01-01"))
        if st.form_submit_button("Save credentials"):
            cfg.update({"username": u.strip(), "password": p.strip(),
                        "subscription_key": k.strip(), "backfill_start": bf.strip()})
            credentials.save_config(cfg)
            st.success("Saved to config.json (chmod 600).")
            st.rerun()

# --------------------------------------------------------------------------
# Live-run target (set by the per-dataset buttons below)
# --------------------------------------------------------------------------
run_target = st.session_state.pop("_run_target", None)

# --------------------------------------------------------------------------
# Dataset status cards
# --------------------------------------------------------------------------
st.subheader("Datasets")

snap = orchestrate.status()


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


cards = [
    ("system_gen", "🔥", lambda s: [
        ("Years", ", ".join(map(str, s.get("years", []))) or "—"),
        ("Latest interval", s.get("latest_interval") or "—"),
        ("Parquet files", _fmt(s.get("files", 0))),
    ]),
    ("hub_prices", "💵", lambda s: [
        ("Rows", _fmt(s.get("rows", 0))),
        ("Range", f"{s.get('start','—')} → {s.get('end','—')}"),
    ]),
    ("plant_sced", "🏭", lambda s: [
        ("Resources", _fmt(s.get("resources", 0))),
        ("Cached SCED days", _fmt(s.get("disclosure_days", 0))),
        ("Per-plant files", _fmt(s.get("plant_files", 0))),
    ]),
    ("eia923", "📅", lambda s: [
        ("Years", ", ".join(map(str, s.get("years", []))) or "—"),
    ]),
]

cols = st.columns(len(cards))
for col, (key, icon, rows_fn) in zip(cols, cards):
    job = orchestrate.JOBS[key]
    with col:
        st.markdown(f"### {icon} {job.label}")
        for label, val in rows_fn(snap.get(key, {})):
            st.markdown(f"**{label}:** {val}")
        st.caption(job.note)
        if st.button(f"Update", key=f"btn_{key}", use_container_width=True):
            st.session_state["_run_target"] = key
            st.rerun()

st.divider()
c1, c2 = st.columns([1, 3])
with c1:
    if st.button("⟳ Update ALL datasets", type="primary", use_container_width=True):
        st.session_state["_run_target"] = "__all__"
        st.rerun()
with c2:
    st.caption("Each updater runs as a subprocess with live logs below. "
               "Hub-price first-run backfill and EIA-923 downloads can take a while.")

# --------------------------------------------------------------------------
# Solar forecast — run PVWatts on a solar project by lat/long, right here.
# Unlike the datasets above (batch ETL), this is on-demand: each run pulls NREL
# NSRDB weather for the coordinate and runs the PVWatts model. "Force refresh"
# re-pulls fresh NREL data instead of reading the cached result.
# --------------------------------------------------------------------------
st.divider()
st.subheader("☀️ Solar Forecast (PVWatts)")

@st.cache_data(show_spinner=False)
def _solar_projects() -> list[dict]:
    """ERCOT solar plants (lat/long + capacity) from EIA-860, for the picker."""
    import eia860

    import pandas as pd

    g = eia860.solar_plants(region="ercot")
    return [
        {"label": f"{r.plant_name} — {r.county} ({r.nameplate_mw:,.0f} MW)",
         "plant_id": int(r.plant_id), "lat": float(r.latitude), "lon": float(r.longitude),
         "capacity_mw": float(r.nameplate_mw), "array_type": r.array_type,
         "module_type": r.module_type,
         "tilt": None if pd.isna(r.tilt) else float(r.tilt),
         "azimuth": None if pd.isna(r.azimuth) else float(r.azimuth)}
        for r in g.itertuples()
    ]


_solar_wiring = solar_ui.Wiring(
    get_api_key=credentials.get_nrel_api_key,
    get_email=credentials.get_nrel_email,
    save_creds=credentials.save_nrel_credentials,
    cache_dir=paths.SOLAR_FORECAST_DIR,
    project_loader=_solar_projects,
)
_have_nrel = bool(credentials.get_nrel_api_key() and credentials.get_nrel_email())

with st.container(border=True):
    n_cached = solar_ui.cached_count(paths.SOLAR_FORECAST_DIR)
    status_bits = [f"{n_cached} cached forecast(s)"]
    status_bits.append("🟢 NREL key set" if _have_nrel else "⚠️ no NREL key")
    st.caption("  ·  ".join(status_bits) +
               "  ·  on-demand: pulls NSRDB weather for the coordinate, then runs PVWatts")

    if not _have_nrel:
        st.warning("Add a free NREL API key (and the email it's registered to) on the "
                   "**☀️ Solar Forecast** page first — https://developer.nrel.gov/signup/")

    # Working values live in session_state so the project picker (which must sit
    # OUTSIDE the form to react on change) can auto-fill lat/long + capacity.
    st.session_state.setdefault("ct_lat", 31.050)
    st.session_state.setdefault("ct_lon", -103.100)
    st.session_state.setdefault("ct_cap", 100.0)

    try:
        _projects = _solar_projects()
    except Exception:  # noqa: BLE001 — picker optional; never block the tower
        _projects = []
    if _projects:
        _labels = [p["label"] for p in _projects]
        _psel = st.selectbox(
            f"ERCOT solar project — auto-fill lat/long ({len(_projects)} from EIA-860)",
            ["(enter coordinates manually)"] + _labels, key="ct_proj")
        if _psel != "(enter coordinates manually)" and st.session_state.get("_ct_proj_applied") != _psel:
            _p = next(x for x in _projects if x["label"] == _psel)
            st.session_state["ct_lat"] = round(_p["lat"], 4)
            st.session_state["ct_lon"] = round(_p["lon"], 4)
            st.session_state["ct_cap"] = round(float(_p["capacity_mw"]), 1)
            if _p.get("array_type") in solar.ARRAY_TYPES:
                st.session_state["ct_array"] = _p["array_type"]
            st.session_state["_ct_proj_applied"] = _psel

    with st.form("solar_quick"):
        a, b, c, d = st.columns([1, 1, 1, 1])
        s_lat = a.number_input("Latitude", format="%.4f",
                               min_value=-90.0, max_value=90.0, key="ct_lat")
        s_lon = b.number_input("Longitude", format="%.4f",
                               min_value=-180.0, max_value=180.0, key="ct_lon")
        s_cap = c.number_input("DC capacity (MW)", min_value=0.0,
                               step=0.1, format="%.2f", key="ct_cap")
        st.session_state.setdefault("ct_array", "1-Axis Tracker")
        s_array = d.selectbox("Array type", list(solar.ARRAY_TYPES.keys()), key="ct_array")
        e, f, g = st.columns([1, 1, 2])
        s_mode = e.selectbox("Weather", ["TMY (typical year)", "Actual weather year"])
        s_year = f.selectbox("Year", [str(y) for y in range(2023, 2017, -1)], index=1,
                             disabled=s_mode.startswith("TMY"))
        s_refresh = g.checkbox("🔄 Force refresh from NREL",
                               help="Re-pull fresh NSRDB data instead of using the cached result.")
        s_run = st.form_submit_button("Run forecast", type="primary",
                                      disabled=not _have_nrel)

    if s_run and _have_nrel:
        year = "tmy" if s_mode.startswith("TMY") else s_year
        is_tracker = solar.ARRAY_TYPES[s_array][1]
        cfg = solar.SystemConfig(
            capacity_kw_dc=s_cap * 1000.0, array_type=s_array,
            tilt_deg=0.0 if is_tracker else 25.0, azimuth_deg=180.0,
        )
        try:
            with st.spinner(f"{'Refreshing' if s_refresh else 'Building'} forecast for "
                            f"{s_lat:.3f}, {s_lon:.3f}…"):
                lbl, res = solar_ui.run_or_load(
                    _solar_wiring, s_lat, s_lon, year, cfg,
                    credentials.get_nrel_api_key().strip(),
                    credentials.get_nrel_email().strip(),
                    force_refresh=s_refresh,
                )
        except Exception as exc:  # noqa: BLE001 — surface API/credential errors
            st.error(f"Forecast failed: {exc}")
            st.caption("Check the NREL key/email, the coordinate, and the selected year "
                       "(NSRDB CONUS coverage), or the daily request limit.")
        else:
            s = solar.summarize(res, cfg)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(f"Energy ({lbl})", f"{s['annual_mwh']:,.0f} MWh")
            m2.metric("Capacity factor (AC)", f"{s['capacity_factor_ac']*100:.1f}%")
            m3.metric("Specific yield", f"{s['specific_yield_kwh_per_kw']:,.0f} kWh/kW")
            m4.metric("Peak AC", f"{s['peak_ac_kw']/1000:,.2f} MW")

            import plotly.graph_objects as go

            me = solar.monthly_energy(res)
            fig = go.Figure(go.Bar(x=me.index, y=me["ac_mwh"], name="AC MWh"))
            fig.update_layout(height=240, yaxis_title="AC energy (MWh)",
                              margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.download_button(
                "⬇ hourly CSV", res.reset_index().to_csv(index=False),
                file_name=f"solar_{s_lat:.3f}_{s_lon:.3f}_{lbl}.csv", mime="text/csv")

    st.page_link("screens/13_Solar_Forecast.py",
                 label="Open full Solar Forecast page (tilt, module, losses, compare TMY vs actual)",
                 icon="☀️")

# --------------------------------------------------------------------------
# Run + stream logs
# --------------------------------------------------------------------------
if run_target:
    st.divider()
    keys = list(orchestrate.JOBS) if run_target == "__all__" else [run_target]
    for k in keys:
        with st.status(f"Updating {orchestrate.JOBS[k].label}…", expanded=True):
            _common.run_with_logs(st, k)
    st.cache_data.clear()
    if st.button("↻ Refresh status"):
        st.rerun()
