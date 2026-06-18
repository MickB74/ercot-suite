# ERCOT EIA-923 Generation Data

Plant-level **monthly** net generation and fuel consumption for ERCOT, sourced
from **EIA Form 923** ("Schedules 2/3/4/5", Page 1). This is the
annual-resolution, plant-level companion to the 15-minute system Fuel-Mix-Report
ETL in `../Ercot_Generation_Data`.

EIA-923 tells you *which plants* generated *how much* from *which fuel* each
month — including net generation (MWh), total/electric fuel consumed (MMBtu),
and physical fuel quantity — at the plant × prime-mover × fuel grain.

## How it works

1. **`eia923.py`** — downloads the annual ZIP from
   [eia.gov/electricity/data/eia923](https://www.eia.gov/electricity/data/eia923/)
   (current year under `/xls/`, older years under `/archive/xls/`), parses
   "Page 1 Generation and Fuel Data", melts the wide monthly columns into a tidy
   long table, filters to a region (default ERCOT = balancing authority `ERCO`),
   maps EIA fuel codes to a canonical taxonomy (Gas, Coal, Wind, Solar, Nuclear,
   Hydro, Biomass, Oil, Storage, …), and caches one parquet per year.
2. **`build_cache.py`** — CLI to build/refresh the parquet cache.
3. **`app.py`** — Streamlit explorer: fuel mix, monthly trends, plant rankings,
   filterable tidy table with CSV export, plus an in-app "download & build"
   button.

Raw ZIPs (`raw/`) and derived parquets (`eia923_*.parquet`) are git-ignored and
regenerated on demand.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Build the data

```bash
python build_cache.py                 # ERCOT, 2018..current year
python build_cache.py 2024            # single year
python build_cache.py 2020 2024       # inclusive range
python build_cache.py --region tx 2024   # all Texas plants instead of ERCO
python build_cache.py --force 2025       # re-download (current year is revised)
```

Regions: `ercot` (BA code `ERCO`; falls back to Texas plants for vintages
predating BA-code reporting), `tx` (all Texas plants), `all` (full US).

## Explore

```bash
streamlit run app.py
```

Or build data straight from the sidebar's **Get / update data** panel.

## Data notes

- **Cadence / lag.** Final full-year data is published with a ~6-month lag (full
  prior year ~late September/October). A same-year file holds year-to-date
  monthly data and is **revised monthly** — re-run with `--force` to refresh.
- **Net generation** can be negative for storage (`MWH`) — charging losses.
- **`fuel_category`** is a convenience mapping of EIA's `Reported Fuel Type
  Code`; unmapped codes fall to `Other`. The raw `fuel_code` is preserved.
- ERCOT ≈ Texas but not identical: a few Texas plants sit in SPP/MISO/WECC, and
  ERCOT covers ~90% of Texas load. Use `--region tx` for the full state.

## Tidy schema (one row per plant × prime-mover × fuel × month)

| column | meaning |
|---|---|
| `year`, `month`, `date` | reporting month (`date` = month start) |
| `plant_id`, `plant_name`, `operator_name`, `operator_id` | plant identity |
| `state`, `ba_code`, `nerc_region`, `sector` | location / classification |
| `prime_mover`, `fuel_code`, `fuel_category`, `fuel_unit` | technology & fuel |
| `netgen_mwh` | net generation (MWh) |
| `total_mmbtu`, `elec_mmbtu` | fuel consumed total / for electricity (MMBtu) |
| `fuel_quantity`, `elec_fuel_quantity` | physical fuel quantity (`fuel_unit`) |
