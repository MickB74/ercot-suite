# ERCOT Solar Forecast — PVWatts

Hourly solar PV production forecasts by **latitude/longitude** using the NREL
**PVWatts** model (via [`pvlib`](https://pvlib-python.readthedocs.io/)) on NSRDB
weather:

- **TMY** — a Typical Meteorological Year. The *expected* annual production
  profile for a representative year (the "forecast").
- **Actual weather year** — real measured irradiance for a chosen historical
  year (~1998 → latest published PSM3). A *backcast* of what the array would
  have produced given the weather that actually occurred.

The PV simulation matches the public PVWatts calculator methodology: plane-of-
array transposition → cell temperature → DC with temperature derate → system
losses → inverter clipping at the DC/AC ratio.

## Setup

1. **Get a free NREL API key** (instant): https://developer.nrel.gov/signup/
   Note the email you register with — both the key *and* that email are needed
   for NSRDB downloads.

2. **Install** (a venv is recommended):
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. **Run**:
   ```bash
   .venv/bin/streamlit run app.py
   ```
   Paste your key + email into the sidebar (**🔑 NREL API key**, saved to a
   git-ignored `config.json`), set the location/system, and **Run forecast**.

## Files

| File | Purpose |
|------|---------|
| `solar_pvwatts.py` | Self-contained engine: `fetch_weather()`, `run_pvwatts()`, `summarize()`, `monthly_energy()`. Only depends on `pvlib` + `pandas`. |
| `solar_app_ui.py`  | Shared Streamlit UI (`render(st, wiring)`), reused by the Data Hub page. |
| `app.py`           | Standalone Streamlit entry point + local `config.json` credential storage. |

The same `solar_pvwatts.py` / `solar_app_ui.py` are mirrored into the ERCOT
Data Hub at `datasets/solar_forecast/`, where they're surfaced as the
**☀️ Solar Forecast** page (credentials come from the Hub's shared
`config.json`).

## System parameters

- **DC capacity (MW)** — array nameplate DC power.
- **Array type** — Fixed Open Rack, Fixed Roof Mount, or 1-Axis Tracker (N-S
  axis with backtracking).
- **Module type** — Standard / Premium / Thin film (sets the DC power
  temperature coefficient).
- **Tilt / Azimuth** — fixed arrays only (180° azimuth = due south).
- **DC/AC ratio** — inverter loading ratio (clips AC at `capacity / ratio`).
- **System losses (%)** — PVWatts default is 14.08%.

## Programmatic use

```python
import solar_pvwatts as sf

w = sf.fetch_weather(31.05, -103.10, api_key="...", email="you@example.com", year="tmy")
cfg = sf.SystemConfig(capacity_kw_dc=1000, array_type="1-Axis Tracker")
hourly = sf.run_pvwatts(w, cfg)          # DataFrame: poa_global, cell_temperature, dc_kw, ac_kw
print(sf.summarize(hourly, cfg))          # annual_mwh, capacity_factor_ac, specific_yield, ...
```
