# ERCOT Data Hub

A single home for four ERCOT datasets that used to be separate repos. One
virtualenv, one credential store, one shared data lake and 60-day SCED cache,
and one Streamlit app that orchestrates the lot.

```
Ercot_Data_Hub/
├── ercot_core/          shared library (de-duplicated logic)
│   ├── paths.py           unified data-lake layout
│   ├── credentials.py     ONE ERCOT API credential store (config.json + env)
│   ├── gridstatus_client.py
│   ├── settlement_points.py   canonical hub / zone lists
│   ├── fuels.py           fuel taxonomy + provenance + EIA/SCED crosswalks
│   ├── sced_disclosure.py ONE 60-day SCED download + shared daily cache
│   └── plant_names.py     resource-code -> plant-name crosswalk
├── datasets/
│   ├── system_gen_by_fuel/   15-min generation by fuel (Fuel Mix Report)
│   ├── hub_prices/           RTM 15-min hub settlement-point prices
│   ├── plant_sced/           plant-level ~5-min SCED operating data
│   └── eia923/               EIA-923 plant monthly generation & fuel
├── app/                  unified Streamlit app
│   ├── Home.py             control tower: status + refresh (live logs) + creds
│   └── pages/              one explorer per dataset (+ node explorer)
├── orchestrate.py        CLI: status / update [dataset…] / list
├── data/                 unified data lake (git-ignored)
└── config.json           ERCOT API credentials (git-ignored, chmod 600)
```

## Setup

```bash
cd ~/Documents/Github/Ercot_Data_Hub
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Run the app

```bash
./.venv/bin/streamlit run app/Home.py
# or double-click "Open ERCOT Data Hub.command"
```

The **Home** page shows each dataset's freshness, lets you refresh any (or all)
with live logs, and manages the shared ERCOT credentials. The sidebar pages are
the per-dataset explorers.

## Command line

```bash
./.venv/bin/python orchestrate.py status                 # snapshot of all datasets
./.venv/bin/python orchestrate.py update                 # update everything
./.venv/bin/python orchestrate.py update hub_prices       # one dataset
./.venv/bin/python orchestrate.py list                    # describe the jobs
```

Each dataset's original scripts still work too (run them from their directory),
because they now read/write the shared data lake via `ercot_core`.

## Credentials

Only **hub prices** (direct ERCOT API) and the **system-gen wind/solar
supplement** need a free ERCOT API account from
[apiexplorer.ercot.com](https://apiexplorer.ercot.com). Set them once in the
app's Home page or:

```bash
./.venv/bin/python orchestrate.py status   # then edit config.json (see config.example.json)
```

Fuel-Mix, SCED, and EIA-923 work with no credentials.

## What the merge de-duplicated

| Was duplicated | Now |
| --- | --- |
| 60-day SCED disclosure downloaded by **two** datasets into two caches | one `ercot_core.sced_disclosure` + shared `data/sced_cache/` |
| RTM SPP fetched two different ways | hub_prices keeps the deep archive backfill; node prices use gridstatus — both share the hub list |
| ERCOT credentials in env vars *and* `config.json` | one `config.json`, mirrored into env for gridstatus |
| Three fuel taxonomies | one `ercot_core.fuels` (canonical + EIA + SCED crosswalks) |
| Hub/zone lists in 3+ places | one `ercot_core.settlement_points` |
| Four virtualenvs / requirements | one |

## Data grains (they complement, not overlap)

- **system_gen** — ERCOT-wide, 15-minute, by fuel.
- **hub_prices** — 7 trading hubs, 15-minute prices.
- **plant_sced** — per-resource, ~5-minute dispatch/telemetry (60-day lag).
- **eia923** — per-plant, monthly net generation & fuel consumption.
