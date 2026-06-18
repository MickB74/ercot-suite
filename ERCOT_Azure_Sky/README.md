# Azure Sky Wind — Settlement Portal

A focused, customer-facing web app for a **single asset**: Azure Sky Wind
(350 MW wind, Throckmorton County, ERCOT North hub, aggregate resource
`AZURE_SKY_WIND_AGG` = units `VORTEX_WIND1..4`). It lets the offtaker log in and
see, in plain language:

- **Overview** — headline KPIs for the latest settled month and year-to-date,
  plus a monthly settlement chart.
- **Past Settlement** — the auditable record for any historical period: metered
  MWh × real-time hub price vs. the contract strike, down to the 15-minute
  interval, with CSV/Excel/Markdown/PDF export.
- **Projected Bill** — a forward *estimate* (clearly labelled as such, not an
  invoice). Generation defaults to a **weather-calibrated model**: the cached
  typical-year wind profile for Azure Sky's coordinates/fleet, rescaled to match
  the plant's real metered output, then degraded forward. The basis (calibrated /
  raw typical year / historical shape), calibration factor, degradation, and
  forward price are all editable in the sidebar.
- **Invoice Audit** — upload a settlement statement and reconcile it, interval by
  interval, against ERCOT-published metered generation and HB_NORTH price.
- **Contract Terms** — set the structure, strike, volume share, and price floor.

## How it works

The portal does **no market math of its own**. It reuses the proven engine and
the cached data lake from the sibling **`Ercot_Data_Hub`** repo
(`ercot_core.settlement`, `ercot_core.invoice`, the DST-correct timezone layer,
and the export helper). So the figures a customer sees here are *identical* to
the internal Data Hub's — this repo is just a clean, single-asset front end.

Two facts make Azure Sky different from a single-node solar asset, and both are
handled in `azuresky/hub.py` so the shared engine is reused unchanged:

1. **It's an aggregate of four units.** There is no single node-generation series
   for `AZURE_SKY_WIND_AGG`. The portal reads the four `VORTEX_WIND1..4` SCED
   unit files from the Hub's `plant_sced` lake and aggregates them into clean
   15-minute MW (flooring in UTC to stay DST-correct, then averaging telemetry
   per bucket — ≈ time-weighted output).
2. **It settles at its trading hub (HB_NORTH), not a resource node.** Settlement
   uses the Hub's shared `ercot_hub_prices_15min` store via
   `ercot_core.prices.hub_store_prices`.

Settlement is **offtaker-signed**: positive = the customer receives, negative =
the customer pays. The default deal is a **$17.34/MWh VPPA/CfD** at 100% volume
share that **curtails at negative prices** (no settlement when HB_NORTH RT15 is
below the $0 floor — "no electrons sold").

### Requirements

- The `Ercot_Data_Hub` repo present as a sibling directory (or point
  `AZURE_HUB_ROOT` at it). That repo owns the engine and the Azure data it has
  already pulled and cached (the VORTEX SCED units and the HB_NORTH hub prices).
- Python 3.10+.

## Run

```bash
./.venv/bin/streamlit run app/Home.py
# or just double-click "Open Azure Sky Portal.command"
```

The launcher creates a local `.venv` and installs `requirements.txt` on first
run.

## Configuration

Contract terms live in `config.json` (git-ignored; copy `config.example.json`).
Seeded with a VPPA/CfD at **$17.34/MWh** — edit on the **Contract Terms** page or
in the file. Everything dollar-denominated flows from these terms.

## Keeping data current

SCED publishes on a ~60-day lag, so there's roughly a month of new generation to
collect each month. Top up Azure Sky's four VORTEX units to the latest available
date with one command — run it in the **Hub's** venv (it has the ERCOT-pull
dependencies and API credentials):

```bash
~/Documents/Github/Ercot_Data_Hub/.venv/bin/python refresh.py
# or double-click "Refresh Azure Sky Data.command"
```

Incremental by default (re-pulls a 5-day overlap to catch ERCOT revisions).
Flags: `--full` (rebuild from 2025-01-01), `--start YYYY-MM-DD`. HB_NORTH prices
are a **shared** Hub resource the Data Hub maintains for every project, so
`refresh.py` only reports their freshness — if they're behind, top them up with
the Hub's own `datasets/hub_prices/Update ERCOT Prices.command`. The portal's
settled window then extends automatically — no code change.

## Notes & limitations

- **Settled history** covers the window where the Hub has *both* metered VORTEX
  generation and the HB_NORTH real-time price (currently full-year 2025 and into
  2026; SCED data publishes on a ~60-day lag). The Overview / Past Settlement
  pages clamp automatically to what's available.
- The **Projected Bill** is a planning figure, not a binding statement. Finalised
  settlement always comes from the Past Settlement page once ERCOT publishes the
  real data.
- The four VORTEX units are summed by the shared engine; the 15-minute
  aggregation here uses a bucket-mean of SCED telemetry. The internal Data Hub /
  `price_settlements` tooling uses a fuller time-weighted aggregation, so a given
  interval's MW can differ by a hair — month and period totals tie out.
- There is **no login gate** in this version (by request). Add one before
  exposing the app publicly.
