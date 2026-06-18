# ERCOT Plant-Level SCED Data

Select any ERCOT generation/storage resource and a time frame, then fetch and
store its **native SCED operating data** — pulled directly from ERCOT via
`gridstatus`. Companion to the price (RTM SPP) and generation-by-fuel datasets,
but at the individual-plant level.

Source: **ERCOT 60-Day SCED Disclosure** (Generation Resource + ESR frames).
Published with a **~60-day lag**, so the most recent ~2 months aren't available
yet. SCED runs every ~5 minutes (older years ~15 min); we keep **every interval
ERCOT publishes** — no resampling.

## Easiest: double-click launchers (macOS)

- **`Open ERCOT SCED UI.command`** — web UI (Streamlit). Filter by fuel group +
  name, pick plants, choose a time frame, fetch, chart, and download CSV.
- **`Pull ERCOT SCED.command`** — text menu, same flow, no browser.

Both set up the Python environment automatically on first run.

## Quick start (command line)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

streamlit run app.py            # the UI
```

### 1. Browse what's available (1,476 resources)

```bash
python fetch_plants.py --fuels                 # counts per fuel group
python fetch_plants.py --list                  # every resource
python fetch_plants.py --list --fuel Solar      # one group
python fetch_plants.py --list --fuel Wind Storage
python fetch_plants.py --search FRYE            # name contains FRYE
```

Fuel groups: `Wind Solar Storage Gas Gas-CC Nuclear Hydro Coal/Lignite Diesel Renewable`.

### 2. Pick resource(s) + a time frame → fetch & store

```bash
python fetch_plants.py FRYE_SLR_UNIT1 --year 2025
python fetch_plants.py FRYE_SLR_UNIT1 VORTEX_WIND1 --start 2026-01-01 --end 2026-03-31
python fetch_plants.py ABINDUST_ESR1 --start 2026-04-01 --end 2026-04-08 --csv
python fetch_plants.py --fuel Wind --year 2025          # every wind unit, full year
```

Output: one parquet per plant per year — `data/<RESOURCE>_<YEAR>.parquet`.
Re-running is idempotent (rows de-dup on resource + timestamp), so you can top
up a range any time.

## Schema (one row per SCED interval per plant)

| column | notes |
|---|---|
| `resource_name` | ERCOT resource id, e.g. `FRYE_SLR_UNIT1` |
| `resource_type` | raw ERCOT type (`PVGR`, `WIND`, `ESR`, `CCGT90`, …) |
| `fuel_group` | friendly group (Solar/Wind/Storage/Gas/…) |
| `sced_timestamp` | **tz-aware `US/Central`**, as published |
| `repeated_hour_flag` | DST fall-back marker |
| `status` | telemetered resource status (`ON`, `OFF`, `OUT`, `ONREG`, …) |
| `output_schedule` | scheduled output (MW) |
| `hsl` / `lsl` | high / low sustainable limit (MW) |
| `hasl` / `lasl` | high / low ancillary service limit (MW) |
| `hdl` / `ldl` | high / low dispatch limit (MW) |
| `base_point` | SCED dispatch instruction (MW) |
| `telemetered_net_output` | actual metered net output (MW) |
| `state_of_charge` | batteries only (MWh); NaN for conventional units |
| `as_regup` `as_regdn` `as_rrs` `as_rrsffr` `as_nsrs` `as_ecrs` | ancillary-service awards (MW) |

Timestamps are stored tz-aware Central, exactly as ERCOT publishes them:
- naive Central (to join the generation dataset): `s.dt.tz_localize(None)`
- absolute UTC (to join RTM prices): `s.dt.tz_convert("UTC")`

```python
import sced_plants as sp
df = sp.load_plant("FRYE_SLR_UNIT1")          # all stored years
df = sp.load_plant("FRYE_SLR_UNIT1", 2025)    # one year
```

## Plant names (the cryptic codes → readable names)

ERCOT resource codes like `FRYE_SLR_UNIT1` are mapped to human names like
*Frye Solar*. There is no free authoritative code→plant crosswalk, so names come
from a confidence-flagged cascade (recorded in `name_source`):

| source | how | confidence |
|---|---|---|
| `override` | `plant_names_overrides.csv` you edit by hand | highest |
| `curated` | hand-verified renewables (from the price project) | high |
| `known` | `KNOWN_MAPPINGS` prefix dictionary | high |
| `queue` | fuzzy match into ERCOT's Interconnection Queue (Project Name) | medium |
| `derived` | readable heuristic from the code itself (`FRYE_SLR_UNIT1`→*Frye Solar*) | low |

Today: ~344 resources get a real name (curated/known/queue); the rest get a
readable derived name. Build/refresh:

```bash
python fetch_plants.py --build-names      # writes plant_names.csv
```

**To correct a name**, add a row to `plant_names_overrides.csv`
(`resource_name,plant_name`) and re-run `--build-names`; overrides always win.
Names appear in the UI picker/legend, the text menu, and `fetch_plants.py --list`.

## How it works

- `get_60_day_sced_disclosure` returns the whole fleet for one operating day.
  We trim it to the operating-set columns and cache it once in
  `disclosure_cache/` (git-ignored). Fetching many plants over a range
  downloads each day **once** and shares it across all requested resources.
- Conventional generators come from the `sced_gen_resource` frame; batteries
  (ESRs) come from the separate `sced_esr` frame (split out at the 2026
  single-model go-live) — both are merged into one table, so storage is covered.
- Already-downloaded daily disclosures in the sibling `price_settlements/sced_cache`
  are reused read-only for **≤2025** dates (those still carried batteries in the
  gen frame). 2026+ is always fetched fresh so no storage is missed.

## Files

- `sced_plants.py` — core: disclosure fetch/cache, registry, per-plant extraction.
- `fetch_plants.py` — CLI: browse, select, fetch & store.
- `plants.csv` / `plants.parquet` — registry of available resources.
- `data/` — canonical per-plant-per-year parquets.
- `disclosure_cache/` — shared daily disclosures (git-ignored).

Refresh the available-resources list after new units come online:

```bash
python fetch_plants.py --refresh-registry
```
