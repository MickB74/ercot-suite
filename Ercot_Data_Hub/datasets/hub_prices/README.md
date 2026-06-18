# ERCOT Hub Price Downloader

A one-button macOS app that pulls **15-minute Real-Time Settlement Point Prices
for every ERCOT trading hub**, straight from the **official ERCOT Public API**
(`api.ercot.com`, report NP6-905-CD). It keeps a local copy up to date and can
refresh itself once a week so you never have to remember.

Hubs included: `HB_BUSAVG`, `HB_HUBAVG`, `HB_HOUSTON`, `HB_NORTH`, `HB_PAN`,
`HB_SOUTH`, `HB_WEST`.

---

## Quick start

1. **Get a free ERCOT API account** (one time):
   - Sign up at **https://apiexplorer.ercot.com**
   - After signing in, open your profile and copy your **Primary subscription key**.

2. **Open the app** — double-click **`Update ERCOT Prices.command`** in this folder.
   - On first launch it asks for your ERCOT username, password, and subscription key.
     These are saved locally in `config.json` (and git-ignored — never uploaded).

3. **Click `⬇ Update Now`.**
   - The first run backfills history (a few minutes — it's a lot of 15-minute data).
   - Every run after that is incremental and fast.

Your data lands in the **`data/`** folder:

| File | What it is |
|------|------------|
| `ercot_hub_prices_15min.csv` | Opens directly in Excel / Numbers |
| `ercot_hub_prices_15min.parquet` | Same data, compact & fast for Python/pandas |

### Columns
`interval_ending_central`, `settlement_point`, `price` ($/MWh), `delivery_date`,
`delivery_hour`, `delivery_interval`, `settlement_point_type`, `dst_flag`.

> `interval_ending_central` is the **interval-ENDING** time on the ERCOT
> (US/Central) clock. E.g. the value at `00:15` covers `00:00–00:15`.

---

## Weekly auto-update ("run it if I don't")

Click **`Enable Weekly Auto-Update`** in the app once (or double-click
`install_weekly_autoupdate.command`). This installs a macOS `launchd` job that:

- runs **Mondays at 07:30**,
- **catches up on login** if your Mac was asleep at that time,
- only actually downloads when the data is **more than 7 days old**.

It logs to `data/autoupdate.log`. To turn it off:

```bash
launchctl unload ~/Library/LaunchAgents/com.ercotprice.weekly.plist
rm ~/Library/LaunchAgents/com.ercotprice.weekly.plist
```

---

## Command line (optional)

```bash
.venv/bin/python ercot_api.py set-credentials   # enter login
.venv/bin/python ercot_api.py test-auth         # check login works
.venv/bin/python ercot_api.py update            # fetch + update store
.venv/bin/python ercot_api.py status            # what's downloaded
```

## Setup from scratch (if the venv is missing)

```bash
cd "$(dirname Ercot_Price_Data)"   # this folder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## How far back does the first download go?

Set by `backfill_start` in `config.json` (currently **2020-01-01**). Change it
to pull a different range, e.g. `"2019-01-01"`.

### Recent vs. deep history (two ERCOT sources, both direct)

ERCOT's *live* API endpoint only serves about the **last 90 days**. So the tool
automatically uses:

- **Live API** for the most recent ~80 days — fast, one query per hub.
- **Archive API** for everything older — ERCOT stores one zipped file per
  15-minute interval (~96/day), so a multi-year backfill downloads hundreds of
  thousands of files. Expect the **first deep backfill to take ~30–60 minutes**.

**It's resumable.** The archive backfill saves a checkpoint after each month. If
you close the app or it's interrupted, just click **Update Now** again — it
picks up where it left off instead of restarting. During the backfill only the
`.parquet` is written; the `.csv` is exported once at the very end.
