# ERCOT Grid Monitor

A self-contained app inside the **ercot-suite** monorepo (at
`ercot-suite/Ercot_Grid_Monitor`) — a free, self-hosted take on GridStatus.io's
paid **Starter** tier:

- **📍 Price Map** — average settlement-point price ($/MWh) for ERCOT **trading
  hubs**, **load zones**, and **individual resource nodes** (real plant sites),
  plotted across Texas and coloured low→high.
- **🔔 Grid-Event Alerts** — email (and/or SMS) when a price crosses a threshold
  (spike or negative), checked on a schedule.

It has its own venv and launcher and runs independently of the rest of the suite.

## Run it

```bash
# one-time + every launch (creates .venv on first run)
./Open\ ERCOT\ Monitor.command
# or manually:
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

## Price Map

Pick a **settlement-point type** (Trading Hub / Load Zone / Resource Node), a
**market** (RT15 real-time 15-min, or DAM day-ahead hourly), and a **date range**:

- **Custom** — any From/To window.
- **Month** / **Year** — one click for a whole period (back to 2010).

You get a colour-scaled map, a hover tooltip (name · node code · substation ·
load zone · Avg/High/Low), "Cheapest / Avg / Priciest" cards, a per-location bar
chart, and a sortable table with CSV download. Resource-node rows also show
**trust** and **gen✓** (see *Node data quality* below).

### Where prices come from

`get_prices()` resolves each request in priority order:

1. **Data lake** — the sibling `Ercot_Data_Hub/data` lake. Trading hubs come from
   `hub_prices/`; resource nodes from `system_gen/node_data/node_price_{year}.parquet`.
   (Override the lake path with `ERCOT_HUB_DATA`.)
2. **ERCOT public API** — for anything the lake misses (load zones, and any
   uncached window), via gridstatus `ErcotAPI` (live endpoint for recent dates,
   archive for older). Needs ERCOT API credentials in the shared
   `Ercot_Data_Hub/config.json` (`username` / `password` / `subscription_key`).
   This is ~30× faster than MIS scraping and serves full history.
3. **MIS scrape** (gridstatus `Ercot().get_spp`) — last-resort fallback, no key,
   but only retains a recent window.

The source used is shown in a caption under the map.

## Node data quality

Resource nodes are physical plants, so each pin is a real lat/lon. Building a
trustworthy node→location map is the hard part (no public ERCOT↔EIA key), so it's
assembled and **cross-validated** rather than guessed:

1. **node → SCED unit** — `resource_node_catalog.parquet` (exact).
2. **unit → readable plant name** — ERCOT's *Stand-Alone Generation Resources*
   report (`ercot_resources.parquet`): Unit Code → Generator Station Description.
3. **plant name → coordinates** — EIA-860 (ERCOT) plant lat/lon.
4. **corroboration** — a coordinate is kept only when two independent matches
   agree (≤15 km), or it's portal-authoritative. Single-source pins are flagged.
5. **generation confirmation** — January SCED telemetered output (60-Day
   Disclosure), summed per facility, is compared to **EIA-923** January net
   generation. A node within ±10% is marked `gen✓` — independent proof the
   crosswalk is right, not just plausible.

Current coverage: **152 nodes plotted** (131 high-trust, ~80 generation-confirmed).
The full **1,024-node** ERCOT universe (NP4-160 *Settlement Points List &
Electrical Buses Mapping*: node · substation · load zone · bus) is browsable in
the **All ERCOT resource nodes** expander with search + CSV, whether or not a node
is plotted.

Notes & caveats:
- Hub/zone markers are **representative regional centroids** (a hub price indexes
  many buses); node markers are the **actual plant POI**.
- Wind farms are multi-phase: several ERCOT nodes can map to one EIA plant, so
  per-node generation is compared at the **facility** level.
- A few nodes keep a name-matched location but can't be generation-confirmed
  (e.g. plant absent from EIA-923, or SCED unit-name gaps) — flagged `gen✓ = ✗`.

## Alerts

Edit/save directly in the app's **Alerts** tab, or copy the example:

```bash
cp alerts_config.example.json alerts_config.json
```

- **Email** — any SMTP account. For Gmail / Google Workspace, use an
  [app password](https://support.google.com/accounts/answer/185833) (host
  `smtp.gmail.com`, port 465). Want a *text* without Twilio? Set `to` to your
  carrier's email-to-SMS gateway (e.g. `number@vtext.com`).
- **SMS** — a [Twilio](https://www.twilio.com/) account (account SID, auth token,
  an SMS-capable from-number; US 10-digit numbers need A2P 10DLC registration).

Test delivery with **Send a test alert now** (ignores cooldowns), then run on a
schedule:

```bash
./Install\ Alerts\ Schedule.command   # launchd, every 15 min
# or ad-hoc / your own cron:
./.venv/bin/python run_alerts.py
```

Each rule has a `cooldown_min` so a sustained spike doesn't re-alert every run;
state is tracked in `.alerts_state.json`. `alerts_config.json` and the state file
are git-ignored.

### Rule fields

| field | meaning |
| --- | --- |
| `metric` | `rt_price` (real-time 15-min) or `dam_price` (day-ahead hourly) |
| `location` | settlement point, e.g. `HB_HUBAVG`, `HB_WEST`, `LZ_HOUSTON` |
| `location_type` | `Trading Hub` or `Load Zone` |
| `op` / `threshold` | fires when latest price `op` threshold (e.g. `>` `500`) |
| `cooldown_min` | minimum minutes between alerts for this rule |

## Files

| file | role |
| --- | --- |
| `app.py` | Streamlit UI (Price Map + Alerts tabs) |
| `ercot.py` | price access — data lake → ERCOT API → MIS fallback |
| `coords.py` | hub/zone centroids + node coords/names (loads `node_coords.json`, `node_names.json`) |
| `spmap.py` | NP4-160 node↔substation↔load-zone mapping (cached) |
| `alerts.py` | rule engine + email/SMS notifiers |
| `run_alerts.py` | scheduler entry point |
| `node_coords.json` / `node_names.json` | plotted node coords (+trust/gen✓) and authoritative ERCOT names |
| `ercot_resources.parquet` | ERCOT Stand-Alone Generation Resources (unit → station name) |

## Data sources

- Prices: ERCOT NP6-905 (RTM) / NP4-190 (DAM) via gridstatus + ERCOT public API.
- Node mapping: ERCOT NP4-160 (settlement points & electrical buses), ERCOT
  Stand-Alone Generation Resources report.
- Coordinates & generation: EIA-860 (plant lat/lon) and EIA-923 (monthly net
  generation), ERCOT 60-Day SCED Disclosure (telemetered output).
