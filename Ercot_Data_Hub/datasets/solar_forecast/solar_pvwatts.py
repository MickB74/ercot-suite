"""PVWatts solar production model driven by NREL NSRDB weather, by lat/long.

This is the self-contained engine shared by the standalone Streamlit app and
the ERCOT Data Hub page. It depends only on ``pvlib`` and ``pandas`` (no
``ercot_core``) so it drops cleanly into either project.

Two weather modes, both pulled from NREL's NSRDB (PSM3) for the requested
coordinate:

  * **TMY**  — a Typical Meteorological Year. The "expected" / forecast annual
    production profile a site should see in a representative year.
  * **Actual year** — real measured irradiance for a chosen historical year
    (~1998 through the latest published PSM3 year). A *backcast* of what the
    array would have produced given the weather that actually occurred.

The PV simulation is the NREL PVWatts model as implemented in pvlib
(``pvwatts_dc`` + ``inverter.pvwatts``), matching the public PVWatts calculator
methodology: POA transposition → cell temperature → DC with temperature
derate → system losses → inverter clipping at the DC/AC ratio.

Requires a free NREL API key (https://developer.nrel.gov/signup/) and the email
the key is registered to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# ---------------------------------------------------------------------------
# System configuration
# ---------------------------------------------------------------------------

# PVWatts module types → DC power temperature coefficient (gamma_pdc, 1/°C) and
# the pvlib SAPM thermal parameter set that best matches each mounting style.
MODULE_TYPES = {
    "Standard": -0.0047,   # crystalline silicon, glass/polymer
    "Premium": -0.0035,    # high-efficiency mono c-Si
    "Thin film": -0.0020,
}

# array_type label → (pvlib SAPM temperature model key, is_single_axis_tracker)
ARRAY_TYPES = {
    "Fixed - Open Rack": ("open_rack_glass_polymer", False),
    "Fixed - Roof Mount": ("close_mount_glass_glass", False),
    "1-Axis Tracker": ("open_rack_glass_polymer", True),
}

# PVWatts default total system losses (%), the calculator's out-of-the-box value.
DEFAULT_LOSSES_PCT = 14.08


@dataclass
class SystemConfig:
    """PVWatts system definition. Capacity is DC nameplate in kW."""

    capacity_kw_dc: float = 1000.0
    tilt_deg: float = 25.0
    azimuth_deg: float = 180.0          # 180 = due south (northern hemisphere)
    array_type: str = "Fixed - Open Rack"
    module_type: str = "Standard"
    dc_ac_ratio: float = 1.2
    inv_efficiency: float = 0.96        # nominal inverter efficiency
    losses_pct: float = DEFAULT_LOSSES_PCT
    axis_azimuth_deg: float = 180.0     # tracker rotation axis (N-S → tracks E-W)
    gcr: float = 0.35                   # ground coverage ratio (tracker backtracking)

    @property
    def gamma_pdc(self) -> float:
        return MODULE_TYPES.get(self.module_type, MODULE_TYPES["Standard"])


# ---------------------------------------------------------------------------
# Weather (NSRDB PSM3 via pvlib)
# ---------------------------------------------------------------------------

@dataclass
class WeatherResult:
    data: pd.DataFrame                  # hourly: ghi, dni, dhi, temp_air, wind_speed
    metadata: dict
    label: str                          # e.g. "TMY" or "2022"
    latitude: float = field(default=0.0)
    longitude: float = field(default=0.0)


def fetch_weather(latitude: float, longitude: float, api_key: str, email: str,
                  year: str = "tmy") -> WeatherResult:
    """Fetch hourly NSRDB PSM4 weather for a coordinate (pvlib ≥ 0.15).

    ``year`` is the string ``"tmy"`` for a Typical Meteorological Year (pulled
    from the NSRDB GOES TMY v4 API), or a four-digit year (e.g. ``"2022"``) for
    a specific historical year (NSRDB GOES CONUS v4 — covers the continental US,
    so all ERCOT/Texas coordinates qualify).

    Returns hourly columns ``ghi``/``dni``/``dhi``/``temp_air``/``wind_speed``
    (pvlib-standard names via ``map_variables``).
    """
    from pvlib import iotools

    if str(year).lower() == "tmy":
        data, meta = iotools.get_nsrdb_psm4_tmy(
            latitude=latitude, longitude=longitude, api_key=api_key, email=email,
            year="tmy", time_step=60, leap_day=False, map_variables=True, timeout=60,
        )
        label = "TMY"
    else:
        data, meta = iotools.get_nsrdb_psm4_conus(
            latitude=latitude, longitude=longitude, api_key=api_key, email=email,
            year=int(year), time_step=60, leap_day=False, map_variables=True, timeout=60,
        )
        label = str(year)
    return WeatherResult(data=data, metadata=meta, label=label,
                         latitude=float(latitude), longitude=float(longitude))


def fetch_weather_era5(latitude: float, longitude: float, start, end,
                       tz: str = "US/Central") -> WeatherResult:
    """Fetch hourly ERA5 reanalysis weather via the Open-Meteo archive API.

    Free, no API key. ERA5 runs to ~2–5 days before today (NSRDB lags ~1 year),
    so this is the source for recent / current-era forecasts and for comparing
    against recent ERCOT SCED. ``start``/``end`` are dates (YYYY-MM-DD).

    Open-Meteo provides GHI/DNI/DHI directly (no decomposition needed). Data is
    requested in **UTC** (unambiguous for solar-position math), then the index is
    converted to ``tz`` for display — default ``US/Central`` (DST-aware) so hours
    line up with ERCOT market data. Columns use the pvlib-standard names.
    """
    import requests

    params = {
        "latitude": latitude, "longitude": longitude,
        "start_date": str(start), "end_date": str(end),
        "hourly": "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,"
                  "temperature_2m,wind_speed_10m",
        "wind_speed_unit": "ms", "timezone": "GMT",
    }
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=60)
    if r.status_code != 200:
        try:
            reason = r.json().get("reason", r.text[:200])
        except Exception:  # noqa: BLE001
            reason = r.text[:200]
        raise ValueError(f"Open-Meteo ERA5 request failed ({r.status_code}): {reason}")
    j = r.json()
    h = j.get("hourly") or {}
    if not h.get("time"):
        raise ValueError(j.get("reason") or "Open-Meteo returned no data for that range.")

    idx = pd.to_datetime(h["time"]).tz_localize("UTC")   # Open-Meteo GMT == UTC
    if tz:
        idx = idx.tz_convert(tz)                          # DST-aware local (e.g. US/Central)
    df = pd.DataFrame({
        "ghi": h.get("shortwave_radiation"),
        "dni": h.get("direct_normal_irradiance"),
        "dhi": h.get("diffuse_radiation"),
        "temp_air": h.get("temperature_2m"),
        "wind_speed": h.get("wind_speed_10m"),
    }, index=idx)
    for c in ("ghi", "dni", "dhi"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["temp_air"] = pd.to_numeric(df["temp_air"], errors="coerce").interpolate().bfill().ffill()
    df["wind_speed"] = pd.to_numeric(df["wind_speed"], errors="coerce").fillna(1.0)
    df["albedo"] = 0.2
    meta = {"latitude": float(latitude), "longitude": float(longitude),
            "altitude": float(j.get("elevation", 0) or 0)}
    return WeatherResult(data=df, metadata=meta, label=f"ERA5 {start}→{end}",
                         latitude=float(latitude), longitude=float(longitude))


# ---------------------------------------------------------------------------
# PVWatts simulation
# ---------------------------------------------------------------------------

def run_pvwatts(weather: WeatherResult, system: SystemConfig) -> pd.DataFrame:
    """Run the PVWatts model. Returns an hourly DataFrame indexed by local time.

    Columns: ``poa_global`` (W/m²), ``cell_temperature`` (°C),
    ``dc_kw`` (after losses), ``ac_kw`` (after inverter).
    """
    import pvlib
    from pvlib import irradiance, location, temperature, pvsystem, inverter, tracking
    from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

    df = weather.data
    meta = weather.metadata
    idx = df.index
    # Guarantee a tz-aware Central index (ERA5 already is; NSRDB/PSM4 can be
    # naive) so the output lines up with Central-time ERCOT generation.
    if isinstance(idx, pd.DatetimeIndex):
        from ercot_core import tz
        idx = tz.localize_central(idx)
        df = df.set_axis(idx, axis=0)

    lat = float(meta.get("latitude", meta.get("Latitude", weather.latitude)))
    lon = float(meta.get("longitude", meta.get("Longitude", weather.longitude)))
    altitude = float(meta.get("altitude", meta.get("Elevation", 0)) or 0)

    site = location.Location(lat, lon, altitude=altitude)
    solpos = site.get_solarposition(idx, temperature=df.get("temp_air", 20))
    dni_extra = irradiance.get_extra_radiation(idx)

    temp_key, is_tracker = ARRAY_TYPES.get(system.array_type, ARRAY_TYPES["Fixed - Open Rack"])

    if is_tracker:
        tr = tracking.singleaxis(
            apparent_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
            axis_tilt=0.0,
            axis_azimuth=system.axis_azimuth_deg,
            max_angle=60.0,
            backtrack=True,
            gcr=system.gcr,
        )
        surface_tilt = tr["surface_tilt"].fillna(0)
        surface_azimuth = tr["surface_azimuth"].fillna(system.axis_azimuth_deg)
    else:
        surface_tilt = system.tilt_deg
        surface_azimuth = system.azimuth_deg

    poa = irradiance.get_total_irradiance(
        surface_tilt=surface_tilt,
        surface_azimuth=surface_azimuth,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=df["dni"],
        ghi=df["ghi"],
        dhi=df["dhi"],
        dni_extra=dni_extra,
        albedo=df.get("albedo", 0.2),
        model="haydavies",
    )
    poa_global = poa["poa_global"].fillna(0)

    temp_params = TEMPERATURE_MODEL_PARAMETERS["sapm"][temp_key]
    cell_temp = temperature.sapm_cell(
        poa_global=poa_global,
        temp_air=df["temp_air"],
        wind_speed=df.get("wind_speed", 1.0),
        **temp_params,
    )

    pdc0_w = system.capacity_kw_dc * 1000.0  # DC nameplate in W
    dc = pvsystem.pvwatts_dc(poa_global, cell_temp, pdc0_w, gamma_pdc=system.gamma_pdc)
    dc = dc.clip(lower=0) * (1.0 - system.losses_pct / 100.0)  # system losses

    # Inverter: AC nameplate = DC nameplate / DC-AC ratio; pvwatts inverter takes
    # its DC input limit (pdc0) = AC limit / nominal efficiency.
    ac_nameplate_w = pdc0_w / system.dc_ac_ratio
    pdc0_inv = ac_nameplate_w / system.inv_efficiency
    ac = inverter.pvwatts(dc, pdc0_inv, eta_inv_nom=system.inv_efficiency)
    ac = ac.clip(lower=0)

    out = pd.DataFrame({
        "poa_global": poa_global,
        "cell_temperature": cell_temp,
        "dc_kw": dc / 1000.0,
        "ac_kw": ac / 1000.0,
    }, index=idx)
    out.index.name = "timestamp"
    return out


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def summarize(ac_df: pd.DataFrame, system: SystemConfig) -> dict:
    """Headline production metrics from an hourly AC result (kW → kWh = kW·1h)."""
    ac = ac_df["ac_kw"]
    annual_kwh = float(ac.sum())                      # hourly → kWh
    capacity_kw_dc = system.capacity_kw_dc
    ac_nameplate_kw = capacity_kw_dc / system.dc_ac_ratio
    hours = len(ac)
    cf_dc = annual_kwh / (capacity_kw_dc * hours) if capacity_kw_dc and hours else 0.0
    cf_ac = annual_kwh / (ac_nameplate_kw * hours) if ac_nameplate_kw and hours else 0.0
    specific_yield = annual_kwh / capacity_kw_dc if capacity_kw_dc else 0.0  # kWh/kWdc
    return {
        "annual_kwh": annual_kwh,
        "annual_mwh": annual_kwh / 1000.0,
        "capacity_factor_dc": cf_dc,
        "capacity_factor_ac": cf_ac,
        "specific_yield_kwh_per_kw": specific_yield,
        "peak_ac_kw": float(ac.max()),
        "hours": hours,
    }


def monthly_energy(ac_df: pd.DataFrame) -> pd.DataFrame:
    """Monthly AC energy (MWh) plus average POA, for charting/tables."""
    m = ac_df.copy()
    m["month"] = m.index.month
    agg = m.groupby("month").agg(ac_mwh=("ac_kw", lambda s: s.sum() / 1000.0),
                                 poa_kwh_m2=("poa_global", lambda s: s.sum() / 1000.0))
    agg.index = [pd.Timestamp(2000, mo, 1).strftime("%b") for mo in agg.index]
    agg.index.name = "month"
    return agg.round(2)
