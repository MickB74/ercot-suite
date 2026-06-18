"""Shared Streamlit UI for the PVWatts solar forecast.

Rendered identically by the standalone app (``app.py``) and the ERCOT Data Hub
page. Credential storage and the parquet cache directory differ between the two
hosts, so they're injected via ``Wiring`` rather than hard-coded here.
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

import solar_pvwatts as sf


def _parse_year(year):
    """Decode the polymorphic weather token → (kind, a, b).

    "tmy" → ("tmy", None, None); "2022" → ("year", "2022", None);
    "era5:2025-01-01:2025-12-31" → ("era5", start, end).
    """
    s = str(year)
    if s.lower() == "tmy":
        return ("tmy", None, None)
    if s.startswith("era5:"):
        _, a, b = s.split(":", 2)
        return ("era5", a, b)
    return ("year", s, None)


def _label_for(year) -> str:
    kind, a, b = _parse_year(year)
    if kind == "tmy":
        return "TMY"
    if kind == "era5":
        return f"ERA5 {a}→{b}"
    return a


def _is_era5(year) -> bool:
    return str(year).startswith("era5:")

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# A few ERCOT-relevant coordinates as quick-pick defaults (lat, lon).
PRESETS = {
    "Custom": None,
    "West Texas — Pecos County": (31.05, -103.10),
    "Far West — Upton County": (31.37, -102.08),
    "Central — Austin": (30.27, -97.74),
    "South — Laredo": (27.52, -99.49),
    "Panhandle — Amarillo": (35.22, -101.83),
    "Houston": (29.76, -95.37),
}


@dataclass
class Wiring:
    """Host-specific hooks (Hub vs standalone)."""

    get_api_key: Callable[[], str]
    get_email: Callable[[], str]
    save_creds: Callable[[str, str], None]   # (api_key, email) -> None
    cache_dir: Path
    # Optional: returns a list of {label, lat, lon, capacity_mw, plant_id,
    # array_type, module_type, tilt, azimuth} solar projects to pick from
    # (auto-fills the system). The Hub wires this to EIA-860; the standalone app
    # leaves it None (manual lat/long entry only).
    project_loader: "Callable[[], list[dict]] | None" = None
    # Optional: (eia_plant_id, year) -> {"resources": [...], "monthly": DataFrame|None}
    # of actual ERCOT SCED generation for that plant, to overlay vs the forecast.
    # The Hub wires this to the SCED↔EIA crosswalk; standalone leaves it None.
    sced_loader: "Callable[[int, int], dict] | None" = None


def _cache_path(cache_dir: Path, lat: float, lon: float, label: str, cfg: sf.SystemConfig) -> Path:
    safe = _re.sub(r"[^0-9A-Za-z]+", "-", str(label)).strip("-")
    tag = (f"{lat:.3f}_{lon:.3f}_{safe}_{int(cfg.capacity_kw_dc)}kw"
           f"_{cfg.array_type.split()[0].lower()}_{int(cfg.tilt_deg)}t_{int(cfg.azimuth_deg)}a")
    return cache_dir / f"solar_{tag}.parquet"


def cached_count(cache_dir: Path) -> int:
    """Number of cached forecast parquets on disk."""
    return len(list(cache_dir.glob("solar_*.parquet"))) if cache_dir.exists() else 0


def run_or_load(wiring: "Wiring", lat: float, lon: float, year: str,
                cfg: sf.SystemConfig, api_key: str, email: str,
                force_refresh: bool = False) -> tuple[str, pd.DataFrame]:
    """Return ``(label, hourly_df)`` for one forecast.

    Reads the parquet cache when present, unless ``force_refresh`` — then it
    re-pulls NSRDB weather from NREL and re-runs PVWatts, overwriting the cache.
    Raises on fetch/model errors (callers surface them).
    """
    label = _label_for(year)
    cache = _cache_path(wiring.cache_dir, lat, lon, label, cfg)
    if cache.exists() and not force_refresh:
        return label, pd.read_parquet(cache)
    kind, a, b = _parse_year(year)
    if kind == "era5":
        weather = sf.fetch_weather_era5(lat, lon, a, b)          # no API key needed
    else:
        weather = sf.fetch_weather(lat, lon, api_key, email, year=(a if kind == "year" else "tmy"))
    res = sf.run_pvwatts(weather, cfg)
    wiring.cache_dir.mkdir(parents=True, exist_ok=True)
    res.to_parquet(cache)
    return weather.label, res


def _render_sced_compare(st, go, wiring: Wiring, project: dict, results: dict, year) -> None:
    """Overlay actual ERCOT SCED generation vs the PVWatts forecast (monthly)."""
    with st.expander("⚖️ Compare to actual ERCOT SCED generation", expanded=False):
        pid = int(project["plant_id"])
        kind, a, _b = _parse_year(year)
        if kind == "year":
            yr_guess = int(a)
        elif kind == "era5":
            yr_guess = int(str(a)[:4])               # forecast period's start year
        else:
            yr_guess = _central_today().year - 1     # TMY → default to last full year
        default_year = min(max(yr_guess, 2018), _central_today().year)
        c1, c2 = st.columns([1, 2])
        cyear = c1.number_input("SCED calendar year", min_value=2018,
                                max_value=_central_today().year, value=default_year, step=1,
                                key="sf_sced_year")
        c2.caption("Actual metered/dispatched output for this plant's ERCOT resource(s), "
                   "via the SCED↔EIA crosswalk. Forecast = expected from weather; the gap "
                   "reflects curtailment, outages, soiling and model error. SCED has a ~60-day lag.")
        if not st.button("Load SCED actuals", key="sf_sced_btn"):
            return
        with st.spinner("Loading ERCOT SCED generation…"):
            try:
                info = wiring.sced_loader(pid, int(cyear)) or {}
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load SCED data: {exc}")
                return
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

        # Forecast monthly: prefer a result matching the SCED year, else the last run.
        fc_lbl = str(int(cyear)) if str(int(cyear)) in results else list(results)[-1]
        fc = sf.monthly_energy(results[fc_lbl])["ac_mwh"]
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
            f"Forecast is sized to the EIA plant nameplate "
            f"({project.get('capacity_mw', 0):,.0f} MW DC). SCED is the summed output of the "
            "mapped ERCOT resource(s) above — their total capacity may differ from the EIA "
            "plant, so if *realized %* is far from ~100% the crosswalk likely spans extra "
            "units/phases (review on the **🔁 Reconciliation** page).")

        fig = go.Figure()
        fig.add_trace(go.Bar(x=_MONTHS, y=tbl["forecast_mwh"], name=f"Forecast ({fc_lbl})"))
        fig.add_trace(go.Bar(x=_MONTHS, y=tbl["sced_mwh"], name=f"Actual SCED ({int(cyear)})"))
        fig.update_layout(height=340, barmode="group", yaxis_title="AC energy (MWh)",
                          margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(tbl, use_container_width=True)
        if str(year).lower() == "tmy":
            st.caption("Note: forecast here is **TMY** (typical year), not the SCED year's "
                       "actual weather. For a like-for-like weather comparison, set the weather "
                       "source to **Actual weather year** matching the SCED year.")


def render(st, wiring: Wiring) -> None:
    st.title("☀️ Solar Production Forecast — PVWatts")
    st.caption("NREL PVWatts model (via pvlib) on NSRDB weather, by latitude/longitude. "
               "TMY = expected typical year · Actual = backcast on a real weather year.")

    wiring.cache_dir.mkdir(parents=True, exist_ok=True)

    with st.sidebar:
        st.header("Location")

        # session_state holds the working lat/lon/capacity so the project picker
        # and quick-pick presets can auto-fill them.
        st.session_state.setdefault("sf_lat", 31.050)
        st.session_state.setdefault("sf_lon", -103.100)
        st.session_state.setdefault("sf_cap", 1.0)

        selected_project = None
        projects = []
        if wiring.project_loader is not None:
            try:
                projects = wiring.project_loader() or []
            except Exception:  # noqa: BLE001 — picker is optional, never block the app
                projects = []
        if projects:
            labels = [p["label"] for p in projects]
            psel = st.selectbox("ERCOT solar project (EIA-860)", ["(manual entry)"] + labels,
                                key="sf_proj",
                                help="Pick a real ERCOT solar plant to auto-fill lat/long + capacity.")
            if psel != "(manual entry)":
                selected_project = next((x for x in projects if x["label"] == psel), None)
            if psel != "(manual entry)" and st.session_state.get("_sf_proj_applied") != psel:
                p = next(x for x in projects if x["label"] == psel)
                st.session_state["sf_lat"] = round(float(p["lat"]), 4)
                st.session_state["sf_lon"] = round(float(p["lon"]), 4)
                if p.get("capacity_mw"):
                    st.session_state["sf_cap"] = round(float(p["capacity_mw"]), 1)
                # Auto-set array + module type (reliable EIA Y/N flags). Tilt/azimuth
                # in EIA are noisy (placeholders like 0°/north or 60° are common), so
                # apply them only when physically sane for a TX fixed array — else keep
                # the 25°/180° defaults the user can adjust.
                if p.get("array_type") in sf.ARRAY_TYPES:
                    st.session_state["sf_array"] = p["array_type"]
                if p.get("module_type") in sf.MODULE_TYPES:
                    st.session_state["sf_mod"] = p["module_type"]
                tl, az = p.get("tilt"), p.get("azimuth")
                if tl is not None and 5 <= tl <= 50:
                    st.session_state["sf_tilt"] = int(round(tl))
                if az is not None and 135 <= az <= 225:           # roughly south-facing
                    st.session_state["sf_az"] = int(round(az))
                st.session_state["_sf_proj_applied"] = psel

        preset = st.selectbox("Quick pick (region)", list(PRESETS.keys()), index=0)
        if PRESETS[preset] and st.session_state.get("_sf_preset_applied") != preset:
            st.session_state["sf_lat"] = round(float(PRESETS[preset][0]), 4)
            st.session_state["sf_lon"] = round(float(PRESETS[preset][1]), 4)
            st.session_state["_sf_preset_applied"] = preset

        c1, c2 = st.columns(2)
        lat = c1.number_input("Latitude", format="%.4f",
                              min_value=-90.0, max_value=90.0, key="sf_lat")
        lon = c2.number_input("Longitude", format="%.4f",
                              min_value=-180.0, max_value=180.0, key="sf_lon")

        st.header("Weather")
        mode = st.radio("Source", ["TMY (typical year)", "Actual year (NSRDB)",
                                   "Recent / current (ERA5)"], key="sf_mode")
        if mode.startswith("TMY"):
            year = "tmy"
        elif mode.startswith("Actual"):
            # NSRDB GOES CONUS v4 coverage (pvlib PSM4). Default 2022.
            year = st.selectbox("Year", [str(y) for y in range(2023, 2017, -1)],
                                index=1, key="sf_year")
            st.caption("NSRDB GOES CONUS v4 (needs NREL key) — lags ~1 year.")
        else:  # ERA5 via Open-Meteo — recent / current, no key
            today = _central_today()
            end_d = st.session_state.get("sf_era5_end") or (today - _dt.timedelta(days=6))
            start_d = st.session_state.get("sf_era5_start") or (
                _dt.date(end_d.year - 1, end_d.month, 1) if hasattr(end_d, "year") else today)
            c1, c2 = st.columns(2)
            sdate = c1.date_input("Start", value=start_d, min_value=_dt.date(1980, 1, 1),
                                  max_value=today, key="sf_era5_start")
            edate = c2.date_input("End", value=end_d, min_value=_dt.date(1980, 1, 1),
                                  max_value=today, key="sf_era5_end")
            year = f"era5:{sdate}:{edate}"
            st.caption("ERA5 reanalysis via Open-Meteo — hourly, **no API key**, current to "
                       "~5 days ago. Use this to cover 2024–today and compare to recent SCED.")

        st.header("System")
        capacity_mw = st.number_input("DC capacity (MW)", min_value=0.0,
                                      step=0.1, format="%.2f", key="sf_cap")
        array_type = st.selectbox("Array type", list(sf.ARRAY_TYPES.keys()), key="sf_array")
        module_type = st.selectbox("Module type", list(sf.MODULE_TYPES.keys()), key="sf_mod")
        is_tracker = sf.ARRAY_TYPES[array_type][1]
        if is_tracker:
            tilt = 0.0
            azimuth = 180.0
            st.caption("Tracker: tilt/azimuth follow the sun (N-S axis).")
        else:
            st.session_state.setdefault("sf_tilt", 25)
            st.session_state.setdefault("sf_az", 180)
            tilt = st.slider("Tilt (°)", 0, 90, key="sf_tilt")
            azimuth = st.slider("Azimuth (° from N; 180=S)", 0, 359, key="sf_az")
        dc_ac = st.slider("DC/AC ratio", 1.0, 1.6, 1.2, 0.05, key="sf_dcac")
        losses = st.slider("System losses (%)", 0.0, 30.0, sf.DEFAULT_LOSSES_PCT, 0.5,
                           key="sf_loss")

        with st.expander("🔑 NREL API key", expanded=not wiring.get_api_key()):
            st.caption("Free key + the email it's registered to: "
                       "https://developer.nrel.gov/signup/")
            key_in = st.text_input("API key", value=wiring.get_api_key(),
                                   type="password", key="sf_key")
            email_in = st.text_input("Email", value=wiring.get_email(), key="sf_email")
            if st.button("Save key"):
                wiring.save_creds(key_in.strip(), email_in.strip())
                st.success("Saved.")

        run = st.button("Run forecast", type="primary")
        refresh = st.checkbox("🔄 Force refresh weather", value=False,
                              help="Re-pull weather from the source (NSRDB or ERA5) and re-run, "
                                   "ignoring the cached result.")
        compare = st.checkbox("Also overlay TMY (typical year)", value=False,
                              help="Run TMY alongside the selected actual/ERA5 weather and overlay.",
                              disabled=mode.startswith("TMY"))

    cfg = sf.SystemConfig(
        capacity_kw_dc=capacity_mw * 1000.0,
        tilt_deg=float(tilt), azimuth_deg=float(azimuth),
        array_type=array_type, module_type=module_type,
        dc_ac_ratio=float(dc_ac), losses_pct=float(losses),
    )

    api_key = (st.session_state.get("sf_key") or wiring.get_api_key()).strip()
    email = (st.session_state.get("sf_email") or wiring.get_email()).strip()

    years = [year] if not compare else ["tmy", year]
    needs_key = any(not _is_era5(y) for y in years)   # ERA5 (Open-Meteo) needs no key

    if run and needs_key and (not api_key or not email):
        st.error("An NREL API key **and** the registered email are required for TMY / NSRDB "
                 "weather. Add them in the sidebar — or use the **Recent / current (ERA5)** "
                 "source, which needs no key.")
        st.stop()

    # Compute only on an explicit Run click (so slider/picker reruns don't refetch
    # from NREL), then latch the result in session_state. Secondary interactions —
    # e.g. "Load SCED actuals" below — rerun the script with run=False and reuse
    # the latched forecast instead of recomputing or vanishing.
    if run:
        results: dict[str, pd.DataFrame] = {}
        summaries: dict[str, dict] = {}
        for yr in years:
            verb = "Refreshing" if refresh else "Building"
            try:
                with st.spinner(f"{verb} forecast ({_label_for(yr)}) "
                                f"for {lat:.3f}, {lon:.3f}…"):
                    lbl, res = run_or_load(wiring, lat, lon, yr, cfg, api_key, email,
                                           force_refresh=refresh)
            except Exception as exc:  # noqa: BLE001 — surface API/credential errors
                st.error(f"Failed to build forecast for {yr}: {exc}")
                st.caption("Common causes: invalid API key, wrong registered email, NSRDB has "
                           "no data for that coordinate/year, or the daily request limit was hit.")
                st.stop()
            results[lbl] = res
            summaries[lbl] = sf.summarize(res, cfg)
        st.session_state["sf_results"] = (results, summaries)
    elif "sf_results" in st.session_state:
        results, summaries = st.session_state["sf_results"]
    else:
        st.info("Set location, weather and system in the sidebar, then **Run forecast**.")
        if needs_key and (not api_key or not email):
            st.warning("TMY / NSRDB weather needs a free NREL API key (under **🔑 NREL API key** "
                       "in the sidebar) — or use **Recent / current (ERA5)**, which needs no key.")
        st.stop()

    # ---- headline metrics -------------------------------------------------
    st.subheader("Annual production")
    cols = st.columns(len(summaries) * 2 if len(summaries) > 1 else 4)
    if len(summaries) == 1:
        lbl, s = next(iter(summaries.items()))
        cols[0].metric("Energy", f"{s['annual_mwh']:,.0f} MWh")
        cols[1].metric("Capacity factor (AC)", f"{s['capacity_factor_ac']*100:.1f}%")
        cols[2].metric("Specific yield", f"{s['specific_yield_kwh_per_kw']:,.0f} kWh/kW")
        cols[3].metric("Peak AC", f"{s['peak_ac_kw']/1000:,.2f} MW")
    else:
        for i, (lbl, s) in enumerate(summaries.items()):
            cols[i*2].metric(f"{lbl} energy", f"{s['annual_mwh']:,.0f} MWh")
            cols[i*2+1].metric(f"{lbl} CF (AC)", f"{s['capacity_factor_ac']*100:.1f}%")

    # ---- monthly chart ----------------------------------------------------
    import plotly.graph_objects as go

    st.subheader("Monthly energy")
    figm = go.Figure()
    for lbl, res in results.items():
        me = sf.monthly_energy(res)
        figm.add_trace(go.Bar(x=me.index, y=me["ac_mwh"], name=f"{lbl} (MWh)"))
    figm.update_layout(height=360, barmode="group", yaxis_title="AC energy (MWh)",
                       margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(figm, use_container_width=True)

    # ---- compare to actual ERCOT SCED generation --------------------------
    if wiring.sced_loader is not None and selected_project and selected_project.get("plant_id"):
        _render_sced_compare(st, go, wiring, selected_project, results, year)

    # ---- representative daily profile (avg by hour) -----------------------
    st.subheader("Average daily profile (AC MW by hour, Central)")
    figd = go.Figure()
    for lbl, res in results.items():
        by_hour = res.groupby(res.index.hour)["ac_kw"].mean() / 1000.0
        figd.add_trace(go.Scatter(x=by_hour.index, y=by_hour.values, mode="lines",
                                  name=f"{lbl}", fill="tozeroy" if len(results) == 1 else None))
    figd.update_layout(height=320, xaxis_title="Hour of day (Central)",
                       yaxis_title="Avg AC (MW)", margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(figd, use_container_width=True)

    # ---- table + download -------------------------------------------------
    st.subheader("Hourly output")
    primary_lbl = list(results.keys())[-1]
    res = results[primary_lbl]
    show = res.reset_index().rename(columns={"timestamp": "timestamp_local"})
    st.dataframe(sf.monthly_energy(res), use_container_width=True)
    st.download_button(
        f"⬇ hourly forecast CSV ({primary_lbl})",
        show.to_csv(index=False),
        file_name=f"solar_forecast_{lat:.3f}_{lon:.3f}_{primary_lbl}.csv",
        mime="text/csv",
    )
    st.caption(f"PVWatts via pvlib · {len(res):,} hourly intervals · "
               f"cache: {wiring.cache_dir}")
