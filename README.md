# ERCOT Suite

A monorepo of ERCOT market data, forecasting, and settlement projects. The
projects share a common engine (`Ercot_Data_Hub/ercot_core`) and locate each
other as sibling folders within this repo.

## Projects

| Folder | What it does |
| --- | --- |
| `Ercot_Data_Hub` | Unified hub: shared `ercot_core` engine, data lake, Streamlit app + `orchestrate.py` CLI |
| `Ercot_Price_Data` | ERCOT RTM settlement-point price ETL |
| `Ercot_Generation_Data` | 15-min generation-by-source ETL |
| `Ercot_EIA_Generation_Data` | Plant-level monthly gen/fuel ETL (EIA Form 923) |
| `Ercot_Plant_Data` | SCED plant registry + resource→plant-name crosswalk (legacy; logic now lives in `ercot_core`) |
| `Ercot_Solar_Forecast` | PVWatts solar forecast by lat/long |
| `Ercot_Wind_Forecast` | Wind forecast with real turbine fleet + multi-source weather |
| `Eroct_forecasts` | Forward power-price forecast (market-implied heat rate × gas strip + Monte Carlo) |
| `ERCOT_Markum` | Markham Solar single-asset settlement portal |
| `ERCOT_Azure_Sky` | Azure Sky Wind single-asset settlement portal |

## Not in this repo

`price_settlements` is a **separate** project with its own GitHub repository and
is intentionally excluded. The hub vendors the 18 KB curated asset registry
(`Ercot_Data_Hub/ercot_core/registry/ercot_assets.json`) so it has no hard
dependency on a `price_settlements` checkout. If you have both repos locally,
refresh the vendored copy with:

```bash
python Ercot_Data_Hub/scripts/sync_registry.py
```

## Setup

1. Each project has its own virtual environment (`.venv/`) — not committed.
2. Copy any `config.example.json` to `config.json` and fill in your own
   credentials (ERCOT API, NREL, EIA). `config.json` files are git-ignored.
3. Data lakes (`data/`) are local and regenerable from the source APIs — not
   committed.

## Optional environment variables

| Var | Effect |
| --- | --- |
| `ERCOT_ASSETS_PATH` | Override the curated asset registry location |
| `ERCOT_SCED_REUSE_DIR` | Read-only SCED disclosure cache to reuse (speed only) |
| `ERCOT_HUB_DATA` | Relocate the hub data lake (e.g. a shared drive) |
