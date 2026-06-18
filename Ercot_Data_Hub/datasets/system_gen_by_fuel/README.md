# ERCOT 15-Minute Generation by Source

Builds and maintains a tidy dataset of ERCOT generation by fuel source at
15-minute settlement-interval resolution, pulled **directly from ERCOT** and
kept up to date incrementally. Every row is tagged with its provenance, and
provisional rows are automatically **replaced** by authoritative ones as ERCOT
publishes them.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python update_generation.py 2026          # build/update one year
python update_generation.py               # current year, incremental
python update_generation.py --backfill 2018-2026
python update_generation.py --no-supplements   # authoritative report only
```

Output: one tidy parquet per year, `ercot_gen_by_fuel_<year>.parquet`.

## Data sources (all direct from ERCOT)

| Source | Fuels | Resolution | Coverage | Credentials |
|---|---|---|---|---|
| **Interval Generation by Fuel Report** (backbone) | All 10 | 15-min | Authoritative; lags real time by days–weeks; revised INITIAL→FINAL | none |
| **Real-time Fuel Mix dashboard** | 8 (coarser) | 5-min → 15-min | ~last 2 days | none |
| **Public API** wind/solar actuals | Wind, Solar only | hourly → 15-min | recent weeks | yes |

- **Report** — yearly Excel workbooks from <https://www.ercot.com/gridinfo/generation>.
  Native fuels: Biomass, Coal, Gas, Gas-CC, Hydro, Nuclear, Other, Power Storage
  (WSL), Solar, Wind. This is the canonical taxonomy.
- **Dashboard** — the JSON behind <https://www.ercot.com/gridmktinfo/dashboards/fuelmix>
  (via `gridstatus`). Coarser: "Natural Gas" maps to `Gas` (combines Gas+Gas-CC);
  "Other" includes biomass. Used only to fill the most recent ~2 days.
- **Public API** (`api.ercot.com`) — has **no all-fuel product**; only system-wide
  wind and solar actuals, which gridstatus exposes hourly. We expand hourly→15-min
  to fill renewables in the gap. Set these env vars to enable it:
  ```bash
  export ERCOT_API_USERNAME=...
  export ERCOT_API_PASSWORD=...
  export ERCOT_PUBLIC_API_SUBSCRIPTION_KEY=...
  ```

### The current-month gap (important)

The report lags real time (e.g. on 2026-06-14 it ended 2026-05-31). ERCOT has
**no 15-minute all-fuel source** for the in-between days — only wind/solar (API)
and the last ~2 days (dashboard). So for the current month, non-renewable fuels
may be missing until the report posts. The pipeline fills what each source
allows and marks it provisional; nothing is fabricated.

## Schema (tidy long format)

| column | type | notes |
|---|---|---|
| `interval_start` | datetime (naive **Central Prevailing Time**) | start of the 15-min interval |
| `interval_end` | datetime (naive CPT) | |
| `fuel` | str | one of the 10 canonical fuels |
| `mw` | float | average MW over the interval (negative for storage charging) |
| `settlement_type` | str | `FINAL` / `INITIAL` / `PRELIM` (report) or `PROVISIONAL` (supplements) |
| `source` | str | `fuel_mix_report` / `ercot_dashboard` / `ercot_api` |
| `priority` | int | lower = more authoritative (derived) |
| `fetched_at` | datetime (UTC) | when the row was retrieved |

Times are stored as naive CPT (lossless across DST). Use
`ercot_fuels.to_utc(df["interval_start"])` for an absolute timeline.

## Provenance & replacement

Each `(interval_start, fuel)` resolves to a single row — the most authoritative
available:

```
FINAL report  >  INITIAL/PRELIM report  >  Public API (wind/solar)  >  dashboard
```

Ties break on the most recent `fetched_at`. Consequences:

- A provisional dashboard/API row for June is **replaced** by the report once
  ERCOT publishes June.
- An `INITIAL` report value is **replaced** by its `FINAL` revision on the next run.
- Re-running is idempotent — row counts stay stable.

This is implemented in `ercot_fuels.merge_with_provenance`.

## CSV export (on demand)

Parquet is the canonical store (≈15× smaller than CSV, keeps dtypes/tz). When you
need a slice for Excel or sharing, export to `csv_exports/` (git-ignored):

```bash
python export_csv.py 2025                     # whole year (long format)
python export_csv.py 2026 --month 5           # just May
python export_csv.py 2025 --fuel Solar Wind   # only those fuels
python export_csv.py 2026 --month 5 --wide    # pivot: one column per fuel (great for charts)
```

## Settlement points (resource nodes, hubs, zones)

Beyond the system-wide fuel mix, you can drill down to individual settlement
points. Three types carry a price (SPP); only **resource nodes** also have
generation:

| Type | Generation | Price |
|---|---|---|
| Resource Node | ✅ SCED telemetered MW | ✅ |
| Trading Hub | — | ✅ |
| Load Zone | — | ✅ |

```bash
python resource_catalog.py --build          # build the searchable node catalog (once)
python pull_nodes.py search RNCH            # find nodes by name
python pull_nodes.py search --type WIND     # or by resource type (needs --build --with-types)

# resource nodes: gen + price
python pull_nodes.py pull --node 7RNCHSLR_ALL --start 2026-04-01 --end 2026-04-07
python pull_nodes.py pull --query RNCH --price-only --start 2026-06-01 --end 2026-06-13
python pull_nodes.py pull --type WIND --gen-only --start 2026-03-01 --end 2026-03-31

# hubs / zones (price only). bare --hub/--zone = all of them
python pull_nodes.py pull --hub --start 2026-06-01 --end 2026-06-13
python pull_nodes.py pull --hub HB_NORTH HB_HOUSTON --start 2026-06-01 --end 2026-06-13
python pull_nodes.py pull --zone LZ_WEST --start 2026-06-01 --end 2026-06-13
```

Hub/zone names live in `settlement_points.py`. Note: DAM and RT use different
`get_spp` date windows internally — `node_prices.py` handles the alignment so a
requested delivery day comes back on that day for both markets.

### Streamlit app

```bash
.venv/bin/streamlit run app.py
```

Pick a settlement-point **type** (Resource Node / Trading Hub / Load Zone),
search/select locations, choose a date range, then **Load data** (reads stored
parquets) or **Pull/refresh from ERCOT** (fetches the range live and stores it).
Charts node generation (MW) against price (RT15 + DAM, $/MWh) on a shared
timeline; hubs/zones show price only.

How it works:
- A resource node maps to one or more generating units. SCED names each unit
  `{Unit Substation}_{Unit Name}`, so a node's generation = the sum of its units'
  **Telemetered Net Output** (15-min), and its price = **SPP** at that node.
- **Generation** comes from the 60-day SCED disclosure (**~60-day lag**); daily
  pulls are cached under `node_data/sced_cache/` so re-pulls are cheap.
- **Price** comes from `get_spp` (real-time 15-min + day-ahead hourly).
- Stored as yearly parquet in `node_data/` (`node_generation_<year>.parquet`,
  `node_price_<year>.parquet`), merged idempotently — re-runs don't duplicate.

Catalog / node modules: `resource_catalog.py`, `node_generation.py`,
`node_prices.py`, `pull_nodes.py`.

## Files

- `ercot_fuels.py` — taxonomy, schema, provenance rules, merge engine.
- `fuel_mix_report.py` — backbone: download + parse the yearly report.
- `dashboard_source.py` — provisional all-fuel supplement (~2 days).
- `api_source.py` — optional provisional wind/solar supplement (needs creds).
- `update_generation.py` — orchestrator / CLI.
- `export_csv.py` — on-demand CSV slices.
- `update_all_data.sh` — one-command refresh (previous + current year).

## Keeping it current

Re-run `python update_generation.py` on a schedule (e.g. daily). Each run
re-downloads the report, tops up the tail from the supplements, and lets the
merge replace provisional data as authoritative data arrives.
