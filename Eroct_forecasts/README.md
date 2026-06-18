# ERCOT Price Forecast

Forward power-price forecasts for the ERCOT trading hubs, built from a
**market-implied heat-rate model** with Monte Carlo scenario bands.

```
Forward power ($/MWh)  =  Gas forward strip ($/MMBtu)  ×  Heat-rate multiplier (MMBtu/MWh)  +  scarcity tail
                          └─ market-traded (NYMEX NG) ─┘   └─ realized from YOUR hub history ─┘   └─ Monte Carlo ─┘
```

The gas strip carries the **price level** (it's the liquid, market-traded part);
the heat-rate multiplier — computed from your own ERCOT history as
`hub price ÷ Henry Hub gas` — carries the **ERCOT shape and scarcity**. You
forecast a slow-moving ratio and let the traded gas curve do the heavy lifting,
which is what makes this robust.

## Why median, not mean
Heat rates are bucketed by **calendar month × peak/off-peak** across all history
years. The **median** anchors the central (P50) forecast; the **full sample
spread** (including Feb-2021 Winter Storm Uri) drives the scenario tails. So a
February forecast has a sane base case (~$45/MWh) *and* a fat P90 (~$300/MWh)
that reflects real winter-scarcity risk — instead of one event permanently
distorting every February.

## Quick start
```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp config.example.json config.json     # optional: add a free EIA API key

# terminal — one hub, several hubs, or all; prints a hub × month price matrix
./.venv/bin/python cli.py --hub HB_NORTH --horizon 36 --shape
./.venv/bin/python cli.py --hub HB_NORTH HB_HOUSTON HB_WEST --block peak
./.venv/bin/python cli.py --all-hubs --horizon 24

# UI
./.venv/bin/streamlit run app.py        # or double-click "Open ERCOT Forecast.command"
```

## Gas curve — automatic (no CSV needed)
The Henry Hub gas leg is pulled **automatically from EIA**: NYMEX Henry Hub
futures contracts 1–4 (the actual traded near strip) for the front months, plus
the STEO forecast beyond. Add a free [EIA API key](https://www.eia.gov/opendata/register.php)
once — in the app (key box under "Gas forward") or in `config.json` — and the
curve refreshes itself (cached ~3 days). The app also shows the strip in an
**editable table** so you can tweak any month inline; and `python cli.py
--refresh-gas` refreshes both the daily history and the forward from the terminal.

Without a key the app still runs, using a **seasonal estimate** from history
(clearly labeled) until you add one.

**Beyond the quoted horizon** (past STEO's ~2 years) the curve doesn't go flat:
the de-seasonalized level **mean-reverts toward a long-term anchor** (default
~$4/MMBtu, adjustable in the app, with a reversion-speed control) and the
historical monthly **seasonal shape is re-applied**, so far-out years keep their
winter/summer structure. The whole resolved/edited strip is what the forecast
uses — what you see in the gas table is exactly what's priced.

### Optional manual overrides (`data/inputs/`, ship empty)
| File | What it is | When to use |
|---|---|---|
| `gas_curve.csv` | Henry Hub **forward** strip ($/MMBtu) | force a specific strip (e.g. paste live CME/Barchart NG settlements) — overrides EIA |
| `ercot_power_strip.csv` | ERCOT power **futures** ($/MWh) | calibrate near months to traded ICE ERCOT forwards (not on a free API); blends in and fades over `--fade-months` |
| `henry_hub_monthly_seed.csv` | bootstrap gas **history** | only the offline fallback; EIA daily spot supersedes it |

**Gas-source precedence:** manual `gas_curve.csv` → EIA (NYMEX + STEO) → seasonal hold.

## Outputs (`data/forecasts/`)
- `forecast_<HUB>_<ASOF>.parquet` / `.csv` — monthly strip: `gas, ihr_p50,
  p5..p95, mean, std, traded, blend_w` per month × block.
- `forecast_<HUB>_<ASOF>_8760.parquet` — hourly P10/P50/P90 shaped from the
  historical hour-of-day × month profile (for VPPA / load settlement).
- `.meta.json` — run parameters and data sources.

## Modules
| File | Role |
|---|---|
| `pf_history.py` | load RTM 15-min hub prices, DST-correct, tag peak/off-peak |
| `gas_curve.py` | Henry Hub history (EIA/seed) + forward strip |
| `heat_rate.py` | implied heat-rate buckets — median anchor + sample distribution |
| `scenarios.py` | Monte Carlo (lognormal gas × lognormal heat rate) → P10/P50/P90, std |
| `power_futures.py` | manual ERCOT futures ingest + horizon-faded blend |
| `forecast.py` | assemble the full forecast for one hub |
| `shape.py` | spread the monthly strip into an 8760 hourly curve |
| `forecast_store.py` | persist / reload artifacts |
| `cli.py`, `app.py`, `pf_app_ui.py` | terminal + Streamlit front ends |

## Method notes & limitations
- **Peak = ERCOT 5×16** (Mon–Fri, HE 7–22); NERC holidays counted on-peak (v1).
- Gas and heat rate are sampled **independently** in the Monte Carlo; in reality
  they're positively correlated in winter (a correlation knob is a natural v2).
- Heat-rate buckets pool ~6 years — thin buckets fall back to a block-wide
  relative spread so scenarios always carry uncertainty.
- Default price cap = $5,000/MWh (ERCOT system offer cap, 2024+).
- Reads the shared hub-price lake from `../Ercot_Data_Hub/data/hub_prices`
  (auto-detected; override with `hub_lake_dir` in `config.json`). Also exposed as
  **Data Hub page 16** sharing the same engine.
