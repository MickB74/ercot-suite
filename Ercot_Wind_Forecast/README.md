# ERCOT Wind Forecast

Robust hourly wind-production forecasts by **latitude/longitude** that take the
**actual turbines installed at the site** into account, blend **multiple weather
sources**, and **calibrate against real generation** to be as accurate as
possible. The sibling of the [ERCOT Solar Forecast](../Ercot_Solar_Forecast)
(PVWatts) — same standalone-app + shared-engine + Data-Hub-page shape.

## What makes it accurate

1. **Real turbine fleet, not a guess.** Give it a coordinate and it finds the
   nearest project in the **US Wind Turbine Database** (USGS/LBNL, turbine-level)
   and reads off the real fleet — manufacturer, model, **hub height**, rotor
   diameter, rated power, turbine count — then models each turbine segment with a
   matching power curve and sums by capacity. (Manual fleet entry is also
   supported for greenfield sites.)
2. **Measured wind shear, not 1/7-power.** The shear exponent α is computed
   *per hour* from the ERA5 10 m and 100 m winds and used to extrapolate to the
   actual hub height. Falls back to a calibrated regional α only when the
   measured shear is unreliable.
3. **Air-density-corrected power curves.** ρ is computed from temperature and
   pressure at hub height; the IEC 61400-12 correction maps wind speed onto each
   turbine's reference-density curve. Real **windpowerlib** OEDB manufacturer
   curves are used when installed; a benchmarked parametric library is the
   always-available fallback.
4. **Multiple weather sources.**
   - **ERA5** reanalysis (Open-Meteo archive) — 1940 → ~5 days ago, **no API
     key**. The workhorse for historical / backcast and recent comparison.
   - **Multi-model NWP forecast** (Open-Meteo) — ECMWF + GFS + ICON + GEM
     ensemble for a real forward forecast (next ~14 days) with **P10/P50/P90**
     bands from across-model disagreement.
   - Sources are blended (weighted mean) to cut single-model bias.
5. **ERCOT calibration.**
   - **Region priors** — ERCOT-hub modeled-vs-realized bias multipliers, a Texas
     seasonal capacity-factor shape, and **SCED-learned month-hour residuals**
     (`wind_calibration.json`).
   - **Live site calibration** — upload the project's actual hourly output and
     the model fits an overall + per-month bias correction (with correlation /
     RMSE diagnostics) so the forecast is re-centred on *that* site.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py        # or double-click "Open Wind Forecast.command"
```

No API key is required — ERA5 and the NWP forecast come keyless from Open-Meteo.
A free [NREL key](https://developer.nrel.gov/signup/) is only reserved for a
future WIND Toolkit cross-check.

## Files

| File | Purpose |
|------|---------|
| `wind_power.py`       | Engine: weather fetch/blend, hub-height shear, air density, power conversion, summaries, P10/P50/P90 bands. numpy/pandas/requests only. |
| `power_curves.py`     | Parametric turbine power-curve library + spec→curve resolver + density correction. |
| `turbine_db.py`       | USWTDB resolver: coordinate → real project turbine fleet. |
| `wind_calibration.py` | Region priors + SCED bias + **live calibration against actuals**. |
| `wind_calibration.json` | ERCOT-hub shear/bias priors, Texas monthly CF, SCED-learned residuals. |
| `wind_app_ui.py`      | Shared Streamlit UI (`render(st, wiring)`), reusable by a Data Hub page. |
| `app.py`              | Standalone entry point + local `config.json` / parquet cache. |
| `reference/uswtdb_tx.json` | Bundled Texas turbine database (19k turbines). |
| `refresh_turbine_db.py` | Refresh the turbine DB from the USGS API (national or per-state). |

## Programmatic use

```python
import turbine_db as tdb, wind_power as wp, wind_calibration as cal

lat, lon = 33.1534, -99.2847                      # Azure Sky Wind, Throckmorton Co.
proj = tdb.find_project_near(lat, lon, radius_km=8)   # → real fleet from USWTDB
fleet = wp.FleetConfig(segments=[
    wp.TurbineSpec(count=s.count, rated_kw=s.rated_kw, hub_height_m=s.hub_height_m,
                   rotor_m=s.rotor_m, curve_key=s.curve_key,
                   label=f"{s.manufacturer} {s.model}")
    for s in proj.segments])

weather = wp.fetch_weather_era5(lat, lon, "2024-01-01", "2024-12-31")   # no key
hourly = wp.run_wind(weather, fleet)                                    # gross/net MW
net = cal.apply_region_priors(hourly["net_mw"], fleet.capacity_mw, lat=lat, lon=lon)
print(wp.summarize(hourly, fleet))                  # annual_mwh, capacity_factor, …

# Site-calibrate against actual generation:
fit = cal.calibrate_against_actuals(hourly["net_mw"], actual_mw_series, fleet.capacity_mw)
calibrated = cal.apply_calibration(hourly["net_mw"], fit, fleet.capacity_mw)
```

## Data Hub integration

`wind_power.py` / `power_curves.py` / `turbine_db.py` / `wind_calibration.py` /
`wind_app_ui.py` are dependency-light and self-contained, so they mirror into
`Ercot_Data_Hub/datasets/wind_forecast/` and surface as a **🌬️ Wind Forecast**
page exactly like the Solar Forecast page — wire `Wiring.sced_loader` to the
Hub's SCED↔EIA crosswalk to compare/calibrate against ERCOT actuals in-app.
