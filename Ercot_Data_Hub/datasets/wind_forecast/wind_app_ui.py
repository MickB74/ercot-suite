"""Shared Streamlit UI for the wind production forecast.

Rendered identically by the standalone app (``app.py``) and (when mirrored) an
ERCOT Data Hub page. Host-specific bits — credential storage, the parquet cache
dir, and an optional ERCOT-SCED actuals loader — are injected via ``Wiring``.
"""

from __future__ import annotations

import datetime as _dt
from ercot_core import tz as _tz  # noqa: E402
def _central_today():
    return _tz.now_central().date()
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

import power_curves
import turbine_db as tdb
import wind_calibration as cal
import wind_power as wp

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ERCOT-relevant quick-pick coordinates (lat, lon).
PRESETS = {
    "Custom": None,
    "West — Sweetwater (Nolan Co.)": (32.47, -100.41),
    "West — McCamey (Upton Co.)": (31.13, -102.22),
    "Panhandle — Amarillo": (35.22, -101.83),
    "North — Throckmorton (Azure Sky)": (33.1534, -99.2847),
    "South — Rio Grande Valley": (26.40, -97.70),
    "Gulf Coast — Kenedy Co.": (26.90, -97.70),
}


@dataclass
class Wiring:
    """Host-specific hooks (standalone vs. Hub)."""

    get_api_key: Callable[[], str]
    save_creds: Callable[[str], None]
    cache_dir: Path
    # Optional: (lat, lon, year) -> dict describing the EIA wind plant nearest the
    # forecast coordinate and its actual ERCOT SCED generation, to overlay vs the
    # forecast. Expected keys: plant_name, plant_id, county, capacity_mw,
    # distance_km, resources (list[str]), monthly (DataFrame with `month` +
    # `sced_mwh`). The Hub wires this to EIA-860 + the SCED↔EIA crosswalk; the
    # standalone app leaves it None and offers a CSV upload for actuals instead.
    sced_loader: "Callable[[float, float, int], dict] | None" = None
    # Optional: project/plant name (or ERCOT queue ID) -> list of coordinate
    # candidates, so the user can type "Azure Sky" instead of hunting lat/long.
    # Each dict: {label, lat, lon} (+ optional capacity_mw, county). The Hub wires
    # this to EIA-860 + project_lookup; the standalone app leaves it None and the
    # project-search box simply doesn't render.
    resolve_project: "Callable[[str], list[dict]] | None" = None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, lat: float, lon: float, label: str,
                fleet: wp.FleetConfig) -> Path:
    safe = _re.sub(r"[^0-9A-Za-z]+", "-", str(label)).strip("-")
    tag = f"{lat:.3f}_{lon:.3f}_{safe}_{int(round(fleet.capacity_mw))}mw_{len(fleet.segments)}seg"
    return cache_dir / f"wind_{tag}.parquet"


def cached_count(cache_dir: Path) -> int:
    return len(list(cache_dir.glob("wind_*.parquet"))) if cache_dir.exists() else 0


def run_or_load(wiring: Wiring, lat: float, lon: float, weather_token: str,
                fleet: wp.FleetConfig, use_wpl: bool,
                force_refresh: bool = False) -> tuple[str, pd.DataFrame, wp.WeatherResult]:
    """Build (or load cached) one forecast for the given weather token.

    Token forms: ``"era5:<start>:<end>"`` or ``"forecast"``.
    """
    label = _label_for(weather_token)
    cache = _cache_path(wiring.cache_dir, lat, lon, label, fleet)
    if cache.exists() and not force_refresh:
        # Cache hit: skip the network. Stub the weather so the sources caption
        # still reflects the token without re-pulling Open-Meteo.
        srcs = ("ensemble",) if weather_token == "forecast" else ("era5",)
        stub = wp.WeatherResult(data=pd.DataFrame(), metadata={}, label=label,
                                latitude=lat, longitude=lon, sources=srcs)
        return label, pd.read_parquet(cache), stub
    weather = _fetch(lat, lon, weather_token)
    res = wp.run_wind(weather, fleet, use_windpowerlib=use_wpl)
    wiring.cache_dir.mkdir(parents=True, exist_ok=True)
    res.to_parquet(cache)
    return label, res, weather


def _fetch(lat, lon, token) -> wp.WeatherResult:
    if token == "forecast":
        return wp.fetch_weather_forecast(lat, lon)
    _, a, b = token.split(":", 2)
    return wp.fetch_weather_era5(lat, lon, a, b)


def _label_for(token) -> str:
    if token == "forecast":
        return "Forecast"
    _, a, b = token.split(":", 2)
    return f"ERA5 {a}→{b}"


# ---------------------------------------------------------------------------
# Sidebar: fleet builder
# ---------------------------------------------------------------------------

def _fleet_from_db(fleet_db: tdb.ProjectFleet) -> list[dict]:
    return [{
        "label": f"{s.manufacturer} {s.model}".strip(),
        "count": s.count, "rated_kw": s.rated_kw, "hub_height_m": s.hub_height_m,
        "rotor_m": s.rotor_m, "curve_key": s.curve_key,
    } for s in fleet_db.segments]


def _build_fleet(seg_rows: list[dict], losses: dict) -> wp.FleetConfig:
    segs = [wp.TurbineSpec(
        count=int(r["count"]), rated_kw=float(r["rated_kw"]),
        hub_height_m=float(r["hub_height_m"]), rotor_m=float(r.get("rotor_m", 120) or 120),
        curve_key=r.get("curve_key", "GENERIC_IEC2"), label=str(r["label"]),
    ) for r in seg_rows if int(r.get("count", 0)) > 0]
    if not segs:
        segs = [wp.TurbineSpec(label="generic")]
    return wp.FleetConfig(segments=segs, losses=losses)


def _render_project_search(st, wiring: Wiring):
    """Type an ERCOT project / plant name (or queue ID) and snap to its coordinate.

    Only rendered when the host wires ``resolve_project`` (the Hub does; the
    standalone app leaves it None). Picking a match queues the coordinate via the
    same ``_wf_pending_coord`` mechanism the turbine detector uses, so the lat/lon
    widgets above update on the rerun.
    """
    if wiring.resolve_project is None:
        return
    with st.expander("🔎 Find by ERCOT project / plant name", expanded=True):
        q = st.text_input("Project or plant name (or queue ID)", key="wf_proj_q",
                          placeholder="e.g. Azure Sky, Roscoe, 21INR0477")
        if st.button("Search", key="wf_proj_search"):
            query = (q or "").strip()
            if query:
                with st.spinner("Resolving project…"):
                    st.session_state["wf_proj_hits"] = wiring.resolve_project(query)
            else:
                st.session_state.pop("wf_proj_hits", None)

        if "wf_proj_hits" in st.session_state:
            hits = st.session_state["wf_proj_hits"] or []
            if not hits:
                st.caption("No matching ERCOT wind project found. Try a shorter or "
                           "different name, or enter the lat/long manually below.")
            else:
                labels = [h["label"] for h in hits]
                pick = st.selectbox("Matches", labels, key="wf_proj_pick")
                chosen = hits[labels.index(pick)]
                if st.button("📍 Use this location", key="wf_proj_use"):
                    st.session_state["_wf_pending_coord"] = (chosen["lat"], chosen["lon"])
                    st.session_state["_wf_proj_msg"] = (
                        f"Snapped to **{chosen['label']}** "
                        f"({chosen['lat']:.4f}, {chosen['lon']:.4f}). "
                        "Detect turbines to load its real fleet.")
                    st.rerun()


def _render_sidebar(st, wiring: Wiring):
    st.header("📍 Location")
    st.session_state.setdefault("wf_lat", 33.1534)
    st.session_state.setdefault("wf_lon", -99.2847)
    st.session_state.setdefault("wf_segments", None)
    st.session_state.setdefault("wf_proj_name", None)

    # Apply any coordinate queued by a previous button click (turbine detection)
    # BEFORE the lat/lon widgets are instantiated — Streamlit forbids mutating a
    # widget's own session_state key once the widget exists in the same run.
    if "_wf_pending_coord" in st.session_state:
        _plat, _plon = st.session_state.pop("_wf_pending_coord")
        st.session_state["wf_lat"] = round(_plat, 4)
        st.session_state["wf_lon"] = round(_plon, 4)

    preset = st.selectbox("Quick pick (region)", list(PRESETS.keys()), index=0)
    if PRESETS[preset] and st.session_state.get("_wf_preset") != preset:
        st.session_state["wf_lat"] = round(PRESETS[preset][0], 4)
        st.session_state["wf_lon"] = round(PRESETS[preset][1], 4)
        st.session_state["_wf_preset"] = preset

    _render_project_search(st, wiring)

    # Surfaced once, on the rerun triggered by picking a project below.
    _proj_msg = st.session_state.pop("_wf_proj_msg", None)
    if _proj_msg:
        st.success(_proj_msg)

    c1, c2 = st.columns(2)
    lat = c1.number_input("Latitude", format="%.4f", min_value=-90.0, max_value=90.0, key="wf_lat")
    lon = c2.number_input("Longitude", format="%.4f", min_value=-180.0, max_value=180.0, key="wf_lon")

    st.header("🌀 Turbines")
    radius = st.slider("Detection radius (km)", 2.0, 25.0, 8.0, 1.0, key="wf_radius",
                       help="Search the USGS US Wind Turbine Database for the project nearest "
                            "this coordinate and read off its real turbine fleet.")
    if st.button("🔎 Detect turbines at this location (USWTDB)"):
        with st.spinner("Searching US Wind Turbine Database…"):
            fdb = tdb.find_project_near(lat, lon, radius_km=radius)
        if fdb is None:
            st.warning("No turbines found within the radius. Enter the fleet manually below, "
                       "or widen the radius / move the point onto the array.")
            st.session_state["wf_segments"] = None
            st.session_state["wf_proj_name"] = None
        else:
            st.session_state["wf_segments"] = _fleet_from_db(fdb)
            st.session_state["wf_proj_name"] = fdb.name
            # Snap the point onto the detected project. Queue the coordinate and
            # rerun so it's applied before the lat/lon widgets are created above.
            st.session_state["_wf_pending_coord"] = (fdb.lat, fdb.lon)
            st.session_state["_wf_detect_msg"] = (
                f"**{fdb.name}** — {fdb.n_turbines} turbines, "
                f"{fdb.capacity_mw:.0f} MW, mean hub {fdb.mean_hub_height_m:.0f} m "
                f"({fdb.distance_km} km away).")
            st.rerun()

    _detect_msg = st.session_state.pop("_wf_detect_msg", None)
    if _detect_msg:  # surfaced once, on the rerun triggered by a successful detection
        st.success(_detect_msg)

    seg_rows = st.session_state.get("wf_segments")
    if seg_rows:
        st.caption(f"Detected fleet — **{st.session_state.get('wf_proj_name','project')}** "
                   "(edit counts/heights as needed):")
        edited = st.data_editor(
            pd.DataFrame(seg_rows),
            column_config={
                "label": st.column_config.TextColumn("Turbine"),
                "count": st.column_config.NumberColumn("Count", min_value=0, step=1),
                "rated_kw": st.column_config.NumberColumn("Rated kW", min_value=0.0, step=50.0),
                "hub_height_m": st.column_config.NumberColumn("Hub m", min_value=10.0, step=1.0),
                "rotor_m": st.column_config.NumberColumn("Rotor m", min_value=10.0, step=1.0),
                "curve_key": st.column_config.SelectboxColumn(
                    "Power curve", options=list(power_curves.PARAMETRIC_CURVES.keys())),
            },
            num_rows="dynamic", hide_index=True, key="wf_seg_editor",
        )
        seg_rows = edited.to_dict("records")
    else:
        st.caption("Manual single-turbine fleet:")
        n = st.number_input("Number of turbines", min_value=1, value=50, step=1, key="wf_n")
        rated = st.number_input("Rated power per turbine (kW)", min_value=100.0, value=2800.0,
                                step=100.0, key="wf_rated")
        hub = st.number_input("Hub height (m)", min_value=20.0, value=90.0, step=5.0, key="wf_hub")
        rotor = st.number_input("Rotor diameter (m)", min_value=20.0, value=127.0, step=1.0, key="wf_rotor")
        curve = st.selectbox("Power curve class", list(power_curves.PARAMETRIC_CURVES.keys()),
                             key="wf_curve",
                             help="Auto-suggested from rotor + rated power if unsure.")
        seg_rows = [{"label": curve, "count": n, "rated_kw": rated, "hub_height_m": hub,
                     "rotor_m": rotor, "curve_key": curve}]

    use_wpl = st.checkbox("Use real windpowerlib power curves (if installed)", value=False,
                          help="Pull manufacturer curves from the Open-Energy-Database turbine "
                               "library. Requires `pip install windpowerlib`; falls back to the "
                               "parametric curves otherwise.")

    st.header("☁️ Weather source")
    mode = st.radio("Source", ["Historical year (ERA5)", "Custom range (ERA5)",
                               "Forward forecast (multi-model)"], key="wf_mode")
    if mode.startswith("Historical"):
        yr = st.selectbox("Year", [str(y) for y in range(_central_today().year, 2014, -1)],
                          index=1, key="wf_year")
        token = f"era5:{yr}-01-01:{yr}-12-31"
        st.caption("ERA5 reanalysis (Open-Meteo) — no API key, runs to ~5 days ago.")
    elif mode.startswith("Custom"):
        today = _central_today()
        c1, c2 = st.columns(2)
        sd = c1.date_input("Start", value=_dt.date(today.year - 1, 1, 1),
                           min_value=_dt.date(1980, 1, 1), max_value=today, key="wf_sd")
        ed = c2.date_input("End", value=today - _dt.timedelta(days=6),
                           min_value=_dt.date(1980, 1, 1), max_value=today, key="wf_ed")
        token = f"era5:{sd}:{ed}"
    else:
        token = "forecast"
        st.caption("Ensemble of ECMWF + GFS + ICON + GEM (Open-Meteo) — next ~14 days, "
                   "with model-spread P10/P50/P90 bands.")

    st.header("⚙️ Losses & calibration")
    wake = st.slider("Wake loss (%)", 0.0, 20.0, 7.0, 0.5, key="wf_wake")
    avail = st.slider("Availability loss (%)", 0.0, 10.0, 3.0, 0.5, key="wf_avail")
    other = st.slider("Electrical + other losses (%)", 0.0, 12.0, 4.0, 0.5, key="wf_other")
    use_region = st.checkbox("Apply ERCOT-region bias priors", value=True, key="wf_region",
                             help="Hub-level modeled-vs-realized multiplier + Texas seasonal shape.")
    use_sced = st.checkbox("Apply SCED-learned hourly bias", value=True, key="wf_scedbias",
                           help="Month-hour residual multipliers learned from real ERCOT generation.")

    losses = {"wake": wake / 100, "availability": avail / 100,
              "electrical": other / 200, "other": other / 200}

    run = st.button("Run forecast", type="primary")
    refresh = st.checkbox("🔄 Force refresh weather", value=False)
    compare_year = st.checkbox("Overlay a second weather year", value=False,
                               disabled=not mode.startswith("Historical"))
    token2 = None
    if compare_year and mode.startswith("Historical"):
        yr2 = st.selectbox("Compare to year", [str(y) for y in range(_central_today().year, 2014, -1)],
                           index=2, key="wf_year2")
        token2 = f"era5:{yr2}-01-01:{yr2}-12-31"

    return dict(lat=lat, lon=lon, seg_rows=seg_rows, use_wpl=use_wpl, token=token,
                token2=token2, losses=losses, use_region=use_region, use_sced=use_sced,
                run=run, refresh=refresh)


# ---------------------------------------------------------------------------
# Live calibration against uploaded actuals
# ---------------------------------------------------------------------------

def _render_calibration(st, go, results: dict, fleet: wp.FleetConfig, p: dict):
    with st.expander("🎯 Calibrate to actual generation (recommended for accuracy)", expanded=False):
        st.caption("Upload this project's **actual** hourly output to bias-correct the model to "
                   "the site. CSV with a timestamp column and an MW column. The fit re-centres "
                   "the forecast (overall + per-month factors) and reports correlation / RMSE.")
        up = st.file_uploader("Actual generation CSV", type=["csv"], key="wf_actuals")
        if up is None:
            if results:
                lbl = list(results)[-1]
                st.info(f"No actuals uploaded — showing the raw physics + region-prior forecast "
                        f"for **{lbl}**. Upload actuals here to site-calibrate it.")
            return None
        try:
            raw = pd.read_csv(up)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read CSV: {exc}")
            return None
        cols = list(raw.columns)
        c1, c2 = st.columns(2)
        tcol = c1.selectbox("Timestamp column", cols, key="wf_tcol")
        mcol = c2.selectbox("Actual MW column", cols, index=min(1, len(cols) - 1), key="wf_mcol")
        actual = pd.Series(pd.to_numeric(raw[mcol], errors="coerce").to_numpy(),
                           index=pd.to_datetime(raw[tcol], errors="coerce", utc=True)).dropna()
        if actual.empty:
            st.error("No valid rows parsed from those columns.")
            return None
        # Align tz to the model (US/Central).
        actual.index = actual.index.tz_convert("US/Central")
        lbl = list(results)[-1]
        modeled = results[lbl]["net_mw"]
        fit = cal.calibrate_against_actuals(modeled, actual, capacity_mw=fleet.capacity_mw)
        if not fit.get("ok"):
            st.warning(f"Not enough overlapping data to calibrate (n={fit.get('n', 0)}). "
                       "Make sure the actuals window overlaps the forecast window.")
            return None
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Bias factor", f"{fit['overall_factor']:.3f}")
        k2.metric("Correlation", f"{fit['correlation']:.3f}" if fit.get("correlation") else "—")
        k3.metric("RMSE", f"{fit['rmse_mw']:.1f} MW")
        k4.metric("Overlap", f"{fit['n']:,} hrs")
        st.caption("Bias factor >1 means the physics under-predicts this site; the calibrated "
                   "forecast below multiplies the model by these fitted factors.")
        return fit


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(st, wiring: Wiring) -> None:
    import plotly.graph_objects as go

    st.title("🌬️ Wind Production Forecast")
    st.caption("Physics by lat/long — real turbine fleet (USWTDB) · measured hourly wind shear · "
               "air-density-corrected power curves · multi-source weather · ERCOT calibration.")
    wiring.cache_dir.mkdir(parents=True, exist_ok=True)

    with st.container(border=True):
        p = _render_sidebar(st, wiring)

    fleet = _build_fleet(p["seg_rows"], p["losses"])
    tokens = [p["token"]] + ([p["token2"]] if p["token2"] else [])

    # Compute on explicit Run, then latch in session_state so secondary
    # interactions (calibration upload, SCED compare) reuse the result.
    if p["run"]:
        results, summaries, weathers = {}, {}, {}
        for tok in tokens:
            try:
                with st.spinner(f"Building forecast ({_label_for(tok)}) for {p['lat']:.3f}, {p['lon']:.3f}…"):
                    lbl, res, weather = run_or_load(wiring, p["lat"], p["lon"], tok, fleet,
                                                    p["use_wpl"], force_refresh=p["refresh"])
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed to build forecast for {tok}: {exc}")
                st.stop()
            # Region priors (no actuals needed).
            res = res.copy()
            if p["use_region"] or p["use_sced"]:
                res["net_mw"] = cal.apply_region_priors(
                    res["net_mw"], fleet.capacity_mw, lat=p["lat"], lon=p["lon"],
                    use_bias=p["use_region"], use_sced=p["use_sced"])
            results[lbl] = res
            summaries[lbl] = wp.summarize(res, fleet)
            weathers[lbl] = weather
        st.session_state["wf_results"] = (results, summaries, weathers, fleet)
    elif "wf_results" in st.session_state:
        results, summaries, weathers, fleet = st.session_state["wf_results"]
    else:
        st.info("Set the location, detect/enter the turbine fleet, choose a weather source, "
                "then **Run forecast** in the sidebar.")
        return

    # ---- fleet summary ----------------------------------------------------
    fc1, fc2, fc3 = st.columns(3)
    fc1.metric("Nameplate capacity", f"{fleet.capacity_mw:,.0f} MW")
    fc2.metric("Turbines", f"{sum(s.count for s in fleet.segments):,}")
    fc3.metric("Mean hub height", f"{fleet.mean_hub_height_m:,.0f} m")
    st.caption("Fleet: " + " · ".join(f"{s.count}× {s.label} ({s.curve_key})" for s in fleet.segments))

    # ---- headline metrics -------------------------------------------------
    st.subheader("Production")
    cols = st.columns(max(4, len(summaries) * 2))
    if len(summaries) == 1:
        lbl, s = next(iter(summaries.items()))
        cols[0].metric("Energy", f"{s['annual_mwh']:,.0f} MWh")
        cols[1].metric("Net capacity factor", f"{s['capacity_factor']*100:.1f}%")
        cols[2].metric("Specific yield", f"{s['specific_yield_mwh_per_mw']:,.0f} MWh/MW")
        cols[3].metric("Mean hub wind", f"{s['mean_hub_wind_ms']:.1f} m/s")
        bands = wp.probabilistic_bands(results[lbl], fleet)
        if bands:
            st.caption(f"Ensemble spread → P90 **{bands['p90']:,.0f}** · "
                       f"P50 **{bands['p50']:,.0f}** · P10 **{bands['p10']:,.0f}** MWh "
                       "(from across-model wind disagreement).")
    else:
        for i, (lbl, s) in enumerate(summaries.items()):
            cols[i * 2].metric(f"{lbl} energy", f"{s['annual_mwh']:,.0f} MWh")
            cols[i * 2 + 1].metric(f"{lbl} CF", f"{s['capacity_factor']*100:.1f}%")

    # ---- live calibration -------------------------------------------------
    fit = _render_calibration(st, go, results, fleet, p)
    if fit:
        for lbl in list(results):
            results[lbl] = results[lbl].copy()
            results[lbl]["net_mw"] = cal.apply_calibration(
                results[lbl]["net_mw"], fit, capacity_mw=fleet.capacity_mw)
            summaries[lbl] = wp.summarize(results[lbl], fleet)
        c = st.columns(3)
        lbl0 = list(summaries)[0]
        c[0].metric("Calibrated energy", f"{summaries[lbl0]['annual_mwh']:,.0f} MWh")
        c[1].metric("Calibrated CF", f"{summaries[lbl0]['capacity_factor']*100:.1f}%")

    # ---- monthly --------------------------------------------------------
    st.subheader("Monthly energy")
    figm = go.Figure()
    for lbl, res in results.items():
        me = wp.monthly_energy(res)
        figm.add_trace(go.Bar(x=me.index, y=me["energy_mwh"], name=f"{lbl}"))
    figm.update_layout(height=340, barmode="group", yaxis_title="Net energy (MWh)",
                       margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(figm, use_container_width=True)

    # ---- daily profile --------------------------------------------------
    st.subheader("Average daily profile (Central)")
    figd = go.Figure()
    for lbl, res in results.items():
        by_hour = res.groupby(res.index.hour)["net_mw"].mean()
        figd.add_trace(go.Scatter(x=by_hour.index, y=by_hour.values, mode="lines", name=lbl,
                                  fill="tozeroy" if len(results) == 1 else None))
    figd.update_layout(height=300, xaxis_title="Hour of day (Central)", yaxis_title="Avg net MW",
                       margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(figd, use_container_width=True)

    # ---- wind resource + power curve ------------------------------------
    st.subheader("Wind resource & power curve")
    primary = list(results)[-1]
    res = results[primary]
    cc1, cc2 = st.columns(2)
    import numpy as _np
    figh = go.Figure(go.Histogram(x=res["ws_hub"], nbinsx=40, histnorm="probability"))
    figh.update_layout(height=300, title="Hub-height wind speed", xaxis_title="m/s",
                       yaxis_title="frequency", margin=dict(l=10, r=10, t=40, b=10))
    cc1.plotly_chart(figh, use_container_width=True)
    vv = _np.linspace(0, 28, 140)
    figp = go.Figure()
    for s in fleet.segments:
        figp.add_trace(go.Scatter(x=vv, y=power_curves.get_normalized_power(vv, s.curve_key),
                                  mode="lines", name=s.curve_key))
    figp.update_layout(height=300, title="Normalized power curve(s)", xaxis_title="m/s",
                       yaxis_title="P / Prated", margin=dict(l=10, r=10, t=40, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    cc2.plotly_chart(figp, use_container_width=True)

    # ---- SCED compare (Hub wiring) --------------------------------------
    if wiring.sced_loader is not None:
        _render_sced_compare(st, go, wiring, p["lat"], p["lon"], results, fleet)

    # ---- table + download ----------------------------------------------
    st.subheader("Hourly output")
    show = res.reset_index().rename(columns={"timestamp": "timestamp_local"})
    st.dataframe(wp.monthly_energy(res), use_container_width=True)
    st.download_button(
        f"⬇ hourly forecast CSV ({primary})",
        show.to_csv(index=False),
        file_name=f"wind_forecast_{p['lat']:.3f}_{p['lon']:.3f}_{primary}.csv".replace(" ", "_"),
        mime="text/csv",
    )
    src = ", ".join(weathers[primary].sources) if weathers.get(primary) else "—"
    st.caption(f"{len(res):,} hourly intervals · weather sources: {src} · cache: {wiring.cache_dir}")


def _render_sced_compare(st, go, wiring: Wiring, lat: float, lon: float,
                         results: dict, fleet: wp.FleetConfig) -> None:
    """Overlay actual ERCOT SCED generation vs the wind forecast (monthly).

    The forecast is built by coordinate; the Hub wires ``sced_loader`` to find
    the EIA-860 wind plant nearest that coordinate, then pull its mapped ERCOT
    resource(s)' stored SCED generation via the SCED↔EIA crosswalk.
    """
    with st.expander("⚖️ Compare to actual ERCOT SCED generation", expanded=False):
        # Default the SCED year to a forecast run's weather year if one is an
        # ERA5 historical year; else the last full calendar year.
        guess = _central_today().year - 1
        for lbl in results:
            mo = _re.search(r"(20\d\d)", str(lbl))
            if mo:
                guess = int(mo.group(1))
                break
        c1, c2 = st.columns([1, 2])
        cyear = c1.number_input("SCED calendar year", min_value=2018,
                                max_value=_central_today().year,
                                value=min(max(guess, 2018), _central_today().year),
                                step=1, key="wf_sced_year")
        c2.caption("Actual dispatched output for the EIA wind plant nearest this coordinate, "
                   "via the SCED↔EIA crosswalk. Forecast = expected from weather; the gap "
                   "reflects curtailment, outages and model error. SCED has a ~60-day lag.")
        if not st.button("Load SCED actuals", key="wf_sced_btn"):
            return
        with st.spinner("Finding nearest EIA wind plant and loading ERCOT SCED…"):
            try:
                info = wiring.sced_loader(lat, lon, int(cyear)) or {}
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load SCED data: {exc}")
                return

        if not info.get("plant_name"):
            st.info("No EIA-860 wind plant found near this coordinate. Pull EIA-860 on the "
                    "**🗺️ EIA-860 Plants** page, or move the point onto a known wind site.")
            return
        dist = info.get("distance_km")
        st.caption(
            f"Nearest EIA plant: **{info['plant_name']}**"
            + (f" — {info['county']} Co." if info.get("county") else "")
            + (f" ({info['capacity_mw']:,.0f} MW)" if info.get("capacity_mw") else "")
            + (f", {dist:.1f} km from the forecast point." if dist is not None else "."))

        resources = info.get("resources") or []
        monthly = info.get("monthly")
        if not resources:
            st.info("No SCED↔EIA crosswalk for this plant yet — map it on the "
                    "**🧩 Auto-Crosswalk** page, then it'll appear here.")
            return
        st.caption("Mapped ERCOT resource(s): " + ", ".join(f"`{r}`" for r in resources))
        if monthly is None or getattr(monthly, "empty", True):
            st.warning(f"No stored SCED generation for {int(cyear)}. Pull it on the "
                       "**📈 Node Explorer** / **🏭 Plant SCED** page first (≈60-day lag).")
            return

        # Forecast monthly: prefer a result whose label carries the SCED year.
        fc_lbl = next((l for l in results if str(int(cyear)) in str(l)), list(results)[-1])
        fc = wp.monthly_energy(results[fc_lbl])["energy_mwh"]
        m = monthly.copy()
        m["mon"] = pd.to_datetime(m["month"]).dt.strftime("%b")
        sced_by_mon = m.groupby("mon")["sced_mwh"].sum()

        tbl = pd.DataFrame({
            "forecast_mwh": [round(float(fc.get(mo, float("nan"))), 1) for mo in _MONTHS],
            "sced_mwh": [round(float(sced_by_mon.get(mo, float("nan"))), 1) for mo in _MONTHS],
        }, index=_MONTHS)
        tbl["ratio"] = (tbl["sced_mwh"] / tbl["forecast_mwh"]).round(2)

        fc_total = float(tbl["forecast_mwh"].sum())
        sced_total = float(tbl["sced_mwh"].sum(skipna=True))
        realized = sced_total / fc_total if fc_total else float("nan")
        k1, k2, k3 = st.columns(3)
        k1.metric(f"Forecast ({fc_lbl})", f"{fc_total:,.0f} MWh")
        k2.metric(f"Actual SCED ({int(cyear)})", f"{sced_total:,.0f} MWh")
        k3.metric("Realized vs forecast", f"{realized*100:,.0f}%" if realized == realized else "—")
        st.caption(
            f"Forecast is sized to the modeled turbine fleet ({fleet.capacity_mw:,.0f} MW). "
            "SCED is the summed output of the mapped ERCOT resource(s) above — their total "
            "capacity may differ from the modeled fleet, so a *realized %* far from ~100% "
            "usually means the crosswalk spans extra units (review on the **🔁 Reconciliation** page).")

        fig = go.Figure()
        fig.add_trace(go.Bar(x=_MONTHS, y=tbl["forecast_mwh"], name=f"Forecast ({fc_lbl})"))
        fig.add_trace(go.Bar(x=_MONTHS, y=tbl["sced_mwh"], name=f"Actual SCED ({int(cyear)})"))
        fig.update_layout(height=340, barmode="group", yaxis_title="Net energy (MWh)",
                          margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(tbl, use_container_width=True)
        if not any(str(int(cyear)) in str(l) for l in results):
            st.caption("Note: no forecast run matches the SCED year's actual weather. For a "
                       "like-for-like comparison, set the weather source to **Historical year "
                       f"(ERA5)** = {int(cyear)} and re-run.")
