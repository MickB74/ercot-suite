# ERCOT Suite

A monorepo of ERCOT market-data, renewable-generation forecasting, and PPA
settlement tools. The projects share one engine (`Ercot_Data_Hub/ercot_core`),
one data lake, and one set of conventions (timezone, units, settlement sign),
and locate each other as sibling folders inside this repo.

This README documents not just how to run the suite but **how every number is
produced** — the data sources, the weather models, the price model, the
settlement math, and every material assumption and default baked into the code.

---

## 1. Projects

| Folder | What it does |
| --- | --- |
| `Ercot_Data_Hub` | The hub: shared `ercot_core` engine, unified data lake, Streamlit app + `orchestrate.py` CLI |
| `Ercot_Price_Data` | ERCOT Real-Time Market settlement-point price ETL |
| `Ercot_Generation_Data` | 15-minute generation-by-fuel ETL (Fuel Mix Report + provisional supplements) |
| `Ercot_EIA_Generation_Data` | Plant-level monthly generation/fuel ETL (EIA Form 923) |
| `Ercot_Plant_Data` | SCED plant registry + resource→plant-name crosswalk (legacy; logic now in `ercot_core`) |
| `Ercot_Solar_Forecast` | PVWatts solar generation forecast by lat/long |
| `Ercot_Wind_Forecast` | Wind generation forecast with real turbine fleet + multi-source weather |
| `Eroct_forecasts` | Forward power-price forecast (implied heat rate × gas strip + Monte Carlo) |
| `Ercot Queue` | Interconnection-queue search, analytics & due-diligence dossier builder ([README](Ercot%20Queue/README.md)) |
| `ERCOT_Markum` | Markum Solar settlement portal (AdventHealth) |
| `ERCOT_Azure_Sky` | Azure Sky Wind settlement portal |
| `ERCOT_Hidalgo_Mirasole_Wind` | Hidalgo Los Mirasoles Wind settlement portal (GM / Home Depot / Bloomberg) |
| `ERCOT_Hornet_Solar` | Hornet Solar settlement portal (Pfizer / Brunswick) |
| `ERCOT_Miller` | Miller(s Branch) Solar settlement portal (Thermo Fisher) |
| `ERCOT_Mesquite_Star` | Mesquite Star Wind settlement portal (Brown University / aggregated) |
| `ERCOT_Stafford_Solar` | Stafford Solar settlement portal (AdventHealth) |
| `ERCOT_Heart_of_Texas` | Heart of Texas Wind settlement portal (AdventHealth / Scout) |
| `Ercot_Project Hub` | Data-quality index of every project loaded into the suite — completeness, source verification, calibration, and tool coverage per asset ([README](Ercot_Project%20Hub/README.md) · [CSV](Ercot_Project%20Hub/data_quality.csv)) |

> **Project Hub:** A self-updating quality scorecard for all assets in the shared
> registry. Regenerate with `python3 "Ercot_Project Hub/build_hub.py"`, or browse
> it live in the Data Hub app under **Tools → 🗂️ Project Hub**.

> **Not in this repo:** `price_settlements` is a **separate** project with its own
> GitHub repo. The hub vendors the 18 KB curated asset registry
> (`Ercot_Data_Hub/ercot_core/registry/ercot_assets.json`) so it has no hard
> dependency on a `price_settlements` checkout. See [§9](#9-the-price_settlements-relationship).

### 1.1 Root-level scripts

| Script | What it does |
| --- | --- |
| `restart_portals.sh` | Kill + restart portal Streamlit servers by name (`markum`, `hidalgo`, `azure`, `miller`, or `all`); pass no arg to restart whichever are currently running |
| `gap_fill_generic.py` | Fill the 60-day SCED lag gap with ERA5 weather-modeled generation for any node/tech (`python gap_fill_generic.py NODE TECH CAP LAT LON`); rows tagged `source=era5_model` |
| `build_forecast_deck.py` | Generate the SR Inc. executive briefing PowerPoint (`SR_ERCOT_Forecast_Methodology.pptx`) |
| `backfill_mirasole.py` | One-off backfill of Hidalgo Los Mirasoles price + SCED history to 2020 |

---

## 2. Setup

### 2.1 Per-project virtual environments
Each project carries its own `.venv` (not committed). From a project folder:
```bash
cd Ercot_Data_Hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
Repeat for any project you run. The Streamlit apps launch with
`streamlit run app.py` (the hub) or `streamlit run <name>_app_ui.py` (the
standalone forecasters); see each folder's own README for the exact entrypoint.

### 2.2 Credentials and config
Secrets live in per-project `config.json` files, which are **git-ignored**. Copy
the committed template and fill in your own keys:
```bash
cp Ercot_Data_Hub/config.example.json Ercot_Data_Hub/config.json
```

| Key | Used by | Where to get it |
| --- | --- | --- |
| `username`, `password`, `subscription_key` | ERCOT Public API (prices, wind/solar actuals) | ERCOT API Explorer registration |
| `nrel_api_key`, `nrel_email` | Solar irradiance (NSRDB) | https://developer.nrel.gov/signup/ |
| `eia_api_key` | Gas history / forward (price forecast) | https://www.eia.gov/opendata/ |

> ⚠️ Treat these as live secrets. If any were ever exposed in plaintext, rotate them.

### 2.3 Optional environment variables
| Var | Effect |
| --- | --- |
| `ERCOT_HUB_DATA` | Relocate the hub data lake (e.g. a shared drive) |
| `ERCOT_ASSETS_PATH` | Override the curated asset registry location |
| `ERCOT_SCED_REUSE_DIR` | Read-only SCED disclosure cache to reuse (speed only) |
| `*_HUB_ROOT` (`MARKUM_HUB_ROOT`, `AZURE_HUB_ROOT`, `HOT_WIND_HUB_ROOT`, etc.) | Point a settlement portal at a specific hub checkout (each portal has its own; defaults to sibling `Ercot_Data_Hub`) |
| `PF_HUB_LAKE_DIR` | Point the price forecaster at a specific hub-price lake |

---

## 3. Data sources & publication lag

All ETLs write yearly parquet files into the shared data lake. **Every source has
a publication lag** — the current year is always partial.

| Source | What | Provider / API | Credentials | Lag |
| --- | --- | --- | --- | --- |
| **Interval Generation by Fuel** | 15-min all-fuel generation (backbone) | ERCOT file download (yearly Excel) | none | days–weeks; revised INITIAL→FINAL |
| **60-day SCED Disclosure** | Plant-level dispatch, limits, ancillary, telemetry, battery SoC | `gridstatus` → ERCOT public API | none | **60 days** (`DISCLOSURE_LAG_DAYS = 60`) |
| **Real-time Fuel Mix dashboard** | 5-min telemetry (last ~2 days), resampled to 15-min | ERCOT dashboard JSON via `gridstatus` | none | ~2 days |
| **Wind/Solar actuals** | System-wide hourly actuals (expanded to 15-min) | ERCOT public API via `gridstatus` | ERCOT API keys | recent weeks |
| **RTM Settlement-Point Prices** | 15-min prices at hubs/nodes/zones (NP6-905-CD) | ERCOT public API | ERCOT API keys | days |
| **EIA Form 923** | Monthly plant net generation (MWh) + fuel (MMBtu) | EIA file download (yearly ZIP) | none | **~6 months** (prior-year final ~Oct) |
| **NSRDB PSM4** | Hourly solar irradiance (TMY + 1998–present) | NREL via `pvlib` | NREL key | TMY/historical (~1 yr) |
| **Open-Meteo (ERA5 + NWP)** | Recent + forecast weather (wind & solar) | Open-Meteo archive/forecast | none | ~5 days back to live |

---

## 4. Conventions (apply across the whole suite)

### 4.1 Timezone — DST-correct, naive-Central storage
The lake stores interval timestamps as **naive US/Central** (opens cleanly in
Excel, lossless within the ERCOT footprint). DST is the trap, so all
settlement-grade joins **lift to tz-aware Central first** (`ercot_core/tz.py`,
plus a per-repo `tzutil.py`):

- **Spring-forward** (02:00–03:00 doesn't exist): `nonexistent="shift_forward"`.
- **Fall-back** (01:00–02:00 happens twice): resolved by ERCOT's
  *Repeated Hour Flag* (`Y` = second pass/CST, `N` = first pass/CDT) when present,
  else `ambiguous="infer"` from sort order. This prevents the silent double-count
  that naive joins would produce on the November fall-back hour.

### 4.2 Units & sign
- Energy in **MWh**, power in **MW**, prices in **$/MWh**, gas in **$/MMBtu**,
  heat rate in **MMBtu/MWh**.
- **Settlement is offtaker-signed**: positive = offtaker receives. A
  generator-sign toggle negates the statement for reconciliation.

### 4.3 Data-lake layout (`Ercot_Data_Hub/data/`, git-ignored)
```
data/
  system_gen/     ercot_gen_by_fuel_<year>.parquet, resource_node_catalog.parquet,
                  node_data/ node_generation_<year>.parquet, node_price_<year>.parquet
  eia923/         eia923_<region>_<year>.parquet, raw/ (cached zips)
  plant_sced/     plants.parquet, plant_names.csv, plants/<RESOURCE>_<YEAR>.parquet
  hub_prices/     ercot_hub_prices_15min.parquet, .last_update.json
  sced_cache/     disclosure_<date>.parquet   (SHARED 60-day SCED, downloaded once)
  solar_forecast/ wind_forecast/ plant_value/ csv_exports/
```
Yearly parquets keep each file small and fast; incremental updates merge
idempotently. The 60-day SCED cache is shared so `plant_sced` and `system_gen`
don't download the same day twice.

### 4.4 The ERCOT↔EIA crosswalk gap
There is **no public key** linking ERCOT resource codes (`FRYE_SLR_UNIT1`) to EIA
plant IDs (`12345`). The suite bridges them with fuzzy name matching
(≥4-char shared tokens, filtered to fuel-compatible candidates), with a manual
override CSV (`data/plant_sced/eia_sced_crosswalk.csv`) that always wins.

---

## 5. Weather → generation modeling

### 5.1 Solar (`Ercot_Solar_Forecast`, `ercot_core` plant-value solar path)

**Irradiance source.** NREL **NSRDB PSM4** via `pvlib` for TMY and historical
years (hourly GHI/DNI/DHI, air temp, wind speed; needs an NREL key). For recent
dates (NSRDB lags ~1 year) it falls back to **ERA5** via Open-Meteo (keyless).
Weather is requested in UTC for unambiguous solar geometry, then converted to
Central to align with market data.

**PV model.** NREL **PVWatts** (`pvwatts_dc` + `inverter.pvwatts`), with
Hay-Davies transposition for plane-of-array irradiance. Single-axis trackers use
backtracking (max rotation 60°). Output is clipped at zero and at the inverter
(AC nameplate = DC ÷ DC/AC ratio); **no separate curtailment/soiling model** —
the 14.08% system-loss bucket is the catch-all, and realized de-rating shows up
only when anchored to SCED actuals.

**Solar defaults / assumptions**

| Parameter | Default | Notes |
| --- | --- | --- |
| System losses | **14.08%** | PVWatts calculator default (wiring, soiling, mismatch, temp, etc.) |
| Inverter efficiency | 0.96 | |
| DC/AC ratio | 1.2 (UI) / **1.3** (registry fallback) | |
| Fixed-tilt tilt / azimuth | 25° / 180° (due south) | Texas-friendly |
| Array type | Fixed – Open Rack | also Roof Mount, 1-Axis Tracker |
| Ground coverage ratio (GCR) | 0.35 | tracker backtracking |
| Temp coefficient (γ) | −0.0047 /°C (std c-Si) | −0.0035 premium, −0.0020 thin film |
| Albedo | 0.2 | fixed; **bifaciality not modeled** |
| Registry field fallbacks | tracking→Fixed, dc_ac_ratio→1.3, gcr→0.35 | when an asset record omits them |

### 5.2 Wind (`Ercot_Wind_Forecast`, `ercot_core` wind path)

**Weather source.** Open-Meteo (keyless), two paths: **ERA5 reanalysis**
(~1940→5 days ago) for backcasting, and a **multi-model NWP ensemble** (ECMWF
IFS, GFS, ICON, GEM) for forecasting. Variables at 10 m and 100 m, plus 2 m temp
and surface pressure. The model spread across NWP members is carried through as
`ws_spread` to drive P10/P50/P90 bands.

**Turbine fleet.** Real machines from the **USWTDB** (US Wind Turbine Database,
Texas extract in `reference/uswtdb_tx.json`). The nearest project within 8 km is
matched; its turbines are grouped into homogeneous (manufacturer, model, hub
height, rotor diameter, rating) segments, each modeled at its own hub height and
summed.

**Physics.**
- **Hub-height extrapolation** via an hourly shear exponent α = ln(v₁₀₀/v₁₀)/ln(10),
  used only when both speeds ≥ 1.5 m/s, clipped to [0, 0.55]; otherwise a
  region-prior α is used. Hubs ≥ 55 m extrapolate from 100 m, below from 10 m.
- **Air density** from the barometric formula (lapse −6.5 K/km, R=287.05,
  g=9.80665), clipped to [0.8, 1.5] kg/m³, then an **IEC 61400-12** density
  correction v* = v·(ρ/ρ₀)^(1/3) with ρ₀ = 1.225 kg/m³.
- **Power curves**: parametric curves tuned to ERCOT-dominant turbine types
  (e.g. generic IEC: cut-in 3, rated 12, cut-out 25 m/s; modern low-specific-power
  machines rate at ~10.5 m/s), with a smooth ramp and soft taper near cut-out.
  Real manufacturer curves are used if `windpowerlib` is installed.

**Loss buckets** (multiplicative): wake 7%, availability 3%, electrical 2%,
other 2% → net factor ≈ **0.887** (≈11.3% total).

**Calibration.** ERCOT is split into five hub regions, each with a prior shear and
bias multiplier and a 12-month capacity-factor profile learned from SCED. A
**SCED calendar anchor** scales the typical-year profile to match metered output
by calendar month (scale clamped to [0.3, 5.0]); a live bias fit against actuals
(clamped to [0.5, 1.8], ≥24 points, screening out curtailed/offline hours) is
applied when overlapping data exists.

**Wind defaults / assumptions**

| Parameter | Default |
| --- | --- |
| Fallback hub height / rating | 90 m / 2500 kW |
| Project match radius | 8.0 km |
| Region-prior shear α (N/S/W/H/Pan) | 0.34 / 0.33 / 0.31 / 0.24 / 0.32 (global fallback 0.20) |
| Reference air density ρ₀ | 1.225 kg/m³ |
| Net loss factor | ~0.887 |
| Missing pressure fill | 101,325 Pa |

> **Known limitations (in code comments):** raw physics can under-predict before
> calibration, and some USWTDB records carry registry mislabels — both are why the
> SCED anchor / live-bias steps exist.

---

## 6. Power-price modeling (`Eroct_forecasts`)

**Historical input.** 15-minute RTM settlement-point prices for the seven trading
hubs (HB_NORTH, HOUSTON, SOUTH, WEST, PAN, BUSAVG, HUBAVG), loaded from the hub
lake and localized to tz-aware Central (interval-start basis).

**Forward model — implied heat rate × gas.** For each (year, month, block) the
realized **implied heat rate** is `hub price ÷ Henry Hub gas` (MMBtu/MWh).
Distributions are pooled per calendar month across years → median (the P50
anchor) plus quantiles and the raw per-year sample array for bootstrapping. P50
power price = **gas forward × median heat rate**. Basis and congestion are *not*
forecast separately — they're embedded in the realized heat-rate distribution.

**Gas strip** precedence: a manual `gas_curve.csv` override → EIA live NYMEX
Henry Hub futures (+ STEO beyond quoted months) → an offline seasonal-hold
fallback. Beyond the last quote, the level mean-reverts exponentially to a
long-term anchor of **$4.00/MMBtu** with a **24-month** e-folding time, times a
seasonal shape.

**Monte Carlo.** Two independent lognormal drivers per (month, block):
- **Gas**: martingale-centered lognormal, cumulative log-vol = `gas_vol·√t`
  (`gas_vol = 0.5`), so near months are tight and far months wide.
- **Heat rate**: lognormal anchored on the **median** of realized samples (robust
  to single scarcity years), log-σ floored at 0.10.
- Price = gas × heat rate, then capped at the ERCOT offer cap **$5,000/MWh**.

Defaults: **5,000 paths**, RNG **seed 42**, outputs mean/std and P5/P10/P25/P50/
P75/P90/P95. **Winter Storm Uri (Feb 2021)** is *not* hand-tuned — it lives in the
realized heat-rate samples, so it naturally fattens the upper tail (which is why
the forecast is median-anchored, not mean-anchored).

**Hourly shaping.** The monthly strip is spread to 8,760 hours using a normalized
hour-of-day × month shape from realized RTM history. Peak = 5×16 (Mon–Fri,
hour-ending 7–22; NERC holidays treated as on-peak in v1). Each P10/P50/P90 band
is scaled by its monthly band/P50 ratio so the distribution shape is preserved
hour to hour.

**Price-model assumptions**

| Parameter | Default |
| --- | --- |
| Monte Carlo paths / seed | 5,000 / 42 |
| Gas log-volatility (`gas_vol`) | 0.5 (×√t) |
| Heat-rate log-σ floor | 0.10 |
| Thin-bucket fallback CV | 0.20 (min 2 years of samples) |
| ERCOT price cap | $5,000/MWh |
| Long-term gas anchor / reversion | $4.00/MMBtu / 24 months |
| Peak definition | 5×16, HE 7–22, holidays on-peak |

---

## 7. Plant value (capture price)

`ercot_core` combines a generation profile (solar PVWatts or wind physics, above)
with historical or forecast hub prices to produce a **capture price** — the
generation-weighted average price the asset actually realizes, versus the simple
time-average. Profiles can be **SCED-anchored** to metered output by calendar
month before valuation. This is the engine behind the hub's Plant Value page and
all settlement portals.

---

## 8. Settlement modeling (portals + `ercot_core/invoice.py`)

The engine computes several structures in parallel over 15-min intervals:

| Structure | Per-interval settlement |
| --- | --- |
| Merchant | `gen_MWh × market_price` |
| **CfD / VPPA** (primary) | `gen_MWh × (market_price − strike)` |
| Physical PPA | `gen_MWh × strike` |
| Basis (tracked separately) | `gen_MWh × (node_SPP − hub_SPP)` |

**Sign:** offtaker-signed (positive = offtaker receives); a generator toggle
negates. **Basis risk** is reported independently, so the nodal-vs-hub spread is
visible even when the contract settles at a hub.

**Negative-price / floor mechanics.**
- `settle_below_floor = false` (default): intervals below the floor are excluded
  from totals (standard VPPA).
- `settle_below_floor = true`: the market price is floored but the interval still
  settles.
- `price_floor = null`: full negative-price exposure.

**Settlement portals — contract defaults**

Each portal is a standalone Streamlit app with a fixed port in the Data Hub
Control Tower (`Ercot_Data_Hub/app/views/home.py`). All share the same
`ercot_core` settlement engine; only the `config.json` differs.

| Portal | Asset / Resource | Type | Strike | Settle at | Vol share | Port |
| --- | --- | --- | --- | --- | --- | --- |
| Markum | Markum Solar (`MRKM_SLR_PV1`, EIA 67580) | Solar | $35.00 | node | 100% | 8502 |
| Azure Sky | Azure Sky Wind (`AZURE_SKY_WIND_AGG`, EIA 64164, 350 MW) | Wind | $17.34 | hub HB_NORTH | 19.43% | 8503 |
| Hidalgo Mirasole | Hidalgo Los Mirasoles Wind (`MIRASOLE_GEN`, EIA 57617) | Wind | $35.00 | hub HB_SOUTH | 100% | 8504 |
| Hornet Solar | Hornet Solar (`HRNT_SLR_RN`) | Solar | $25.00 | node | 100% | 8505 |
| Miller | Miller(s Branch) Solar (`MLB_SLR_RN`) | Solar | $35.00 | hub HB_NORTH | 100% | 8506 |
| Mesquite Star | Mesquite Star Wind (`WH_WIND_ALL`) | Wind | $29.00 | node | 100% | 8507 |
| Stafford Solar | Stafford Solar (`STAFFORD_SOLAR_AGG`, EIA 68458, 252 MW) | Solar | $42.55 | hub HB_WEST | 100% | 8508 |
| Heart of Texas | Heart of Texas Wind (`SHANNONW_RN`, EIA 61032, 180 MW) | Wind | $30.00 | hub HB_WEST | 50% | 8509 |

Notable per-portal overrides:
- **Stafford** has a negative price floor of **−$3.00/MWh** with `settle_below_floor = true` and a defined term (2025-10-01 → 2040-09-30).
- **Heart of Texas** settles only **50%** of volume (AdventHealth share).
- **Azure Sky** settles **19.43%** of volume (config override).
- All others use the standard VPPA defaults: floor $0.00, `settle_below_floor = false`, 100% volume.

**Invoice validation** (`ercot_core/invoice.py`) auto-detects column roles
(time/location/price/volume/amount), normalizes DST-aware timestamps, validates
each interval (price/volume/amount within tolerances → match or *_mismatch),
audits the net CfD bottom line, and cross-checks against the plant's EIA-923
monthly net generation.

---

## 9. The `price_settlements` relationship

`price_settlements` is a separate repo (its own GitHub remote) and is **not**
part of this monorepo. The only ties were read-only data:

- **Asset registry** — vendored here (`ercot_core/registry/ercot_assets.json`).
  Refresh from a local `price_settlements` checkout with
  `python Ercot_Data_Hub/scripts/sync_registry.py`.
- **SCED cache** — a pure speed cache; set `ERCOT_SCED_REUSE_DIR` to reuse one,
  otherwise the hub just re-downloads. No functional dependency.

---

## 10. Assumptions & limitations at a glance

- **Current year is always partial** — SCED lags 60 days, EIA-923 ~6 months.
- **Solar** is pure PVWatts physics: one lumped 14.08% loss, no explicit
  curtailment/soiling, no bifaciality, until SCED-anchored.
- **Wind** can under-predict on raw physics and depends on USWTDB fleet accuracy;
  region priors + SCED anchor + live bias correct for this.
- **Prices** assume basis/congestion persist via the realized heat-rate
  distribution (no separate basis forecast); gas mean-reverts to $4.00/MMBtu;
  scarcity tails come from realized samples (incl. Uri), not hand-tuning.
- **Settlement** defaults to excluding sub-floor intervals (standard VPPA);
  basis risk is reported but not hedged.
- **Weather forecasts** are only as good as the NWP ensemble; P-bands reflect
  model spread, not a full probabilistic field.

For module-level detail, each project folder has its own README.
