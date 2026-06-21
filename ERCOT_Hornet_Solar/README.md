# Hornet Solar — Settlement Portal

A focused, customer-facing web app for a **single asset**: Hornet Solar
(161 MW single-axis PV, Bosque County, ERCOT North hub, resource node
`HRNT_SLR_RN`). It lets the offtaker log in and see, in plain language:

- **Overview** — headline KPIs for the latest settled month and year-to-date,
  plus a monthly settlement chart.
- **Past Settlement** — the auditable record for any historical period: metered
  MWh × real-time node price vs. the contract strike, down to the 15-minute
  interval, with CSV/Excel/Markdown/PDF export.
- **Projected Bill** — a forward *estimate* (clearly labelled as such, not an
  invoice). Generation defaults to a **weather-calibrated model**: the PVWatts
  typical-meteorological-year shape for Hornet Solar's coordinates/system, rescaled to
  match the plant's real metered output, then degraded forward. The basis
  (calibrated / raw TMY / historical shape), calibration factor, degradation, and
  forward price are all editable in the sidebar.
- **Invoice Audit** — upload a settlement statement and reconcile it, interval by
  interval, against ERCOT-published metered generation and price.
- **Contract Terms** — set the structure, strike, volume share, and price floor.

## How it works

The portal does **no market math of its own**. It reuses the proven engine and
the cached data lake from the sibling **`Ercot_Data_Hub`** repo
(`ercot_core.settlement`, `ercot_core.invoice`, the DST-correct timezone layer,
and the export helper). So the figures a customer sees here are *identical* to
the internal Data Hub's — this repo is just a clean, single-asset front end.

Settlement is **offtaker-signed**: positive = the customer receives, negative =
the customer pays.

### Requirements

- The `Ercot_Data_Hub` repo present as a sibling directory (or point
  `HORNET_SOLAR_HUB_ROOT` at it). That repo owns the engine and the Hornet Solar data it
  has already pulled and cached.
- Python 3.10+.

## Run

```bash
./.venv/bin/streamlit run app/Home.py
# or just double-click "Open Hornet Solar Portal.command"
```

The launcher creates a local `.venv` and installs `requirements.txt` on first
run.

## Configuration

Contract terms live in `config.json` (git-ignored; copy `config.example.json`).
Seeded with a VPPA/CfD at **$35/MWh** — edit on the **Contract Terms** page or in
the file. Everything dollar-denominated flows from these terms.

## Keeping data current

SCED publishes on a ~60-day lag, so there's roughly a month of new data to
collect each month. Top up Hornet Solar's generation **and** node prices to the
latest available date with one command — run it in the **Hub's** venv (it has
the ERCOT-pull dependencies and API credentials):

```bash
~/Documents/Github/Ercot_Data_Hub/.venv/bin/python refresh.py
# or double-click "Refresh Hornet Solar Data.command"
```

Incremental by default (re-pulls a 5-day overlap to catch ERCOT revisions).
Flags: `--full` (rebuild from 2025-01-01), `--start YYYY-MM-DD`, `--gen-only`,
`--price-only`. It is archive-aware, so it backfills older-month node prices that
the stock live API can't reach. The portal's settled window then extends
automatically — no code change.

## Notes & limitations

- **Settled history** covers the window where ERCOT has published *both* metered
  generation and the node's real-time price (currently full-year 2025 and into
  2026; SCED data publishes on a ~60-day lag). The Overview/Past Settlement pages
  clamp automatically to what's available.
- The **Projected Bill** is a planning figure, not a binding statement. Finalised
  settlement always comes from the Past Settlement page once ERCOT publishes the
  real data.
- There is **no login gate** in this version (by request). Add one before exposing
  the app publicly.
