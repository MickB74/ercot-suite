# ERCOT Grid Monitor

A self-contained app inside the **ercot-suite** monorepo (at
`ercot-suite/Ercot_Grid_Monitor`) that recreates two of GridStatus.io's paid
**Starter** tier features for free:

- **📍 Price Map** — average settlement-point price ($/MWh) for ERCOT trading
  hubs and load zones, plotted across Texas and coloured low→high.
- **🔔 Grid-Event Alerts** — get an email and/or SMS when a price crosses a
  threshold (spike or negative), checked on a schedule.

It has its own venv and launcher and runs independently of the rest of the
suite. Prices come straight from the open-source
[`gridstatus`](https://github.com/gridstatus/gridstatus) library, which reads
ERCOT's public reports — **no API key required**.

For **trading hubs** it will read the sibling `Ercot_Data_Hub/data` lake first
(if that store has been built) and only fall back to a live pull for anything
the lake doesn't cover; load zones always pull live. Override the lake location
with the `ERCOT_HUB_DATA` environment variable.

## Run it

```bash
# one-time + every launch (creates .venv on first run)
./Open\ ERCOT\ Monitor.command
# or manually:
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

## Alerts

1. Copy the example and edit it (or edit/save directly in the app's **Alerts** tab):
   ```bash
   cp alerts_config.example.json alerts_config.json
   ```
2. Set your thresholds and turn on a notifier:
   - **Email** — any SMTP account. For Gmail, use an [app password](https://support.google.com/accounts/answer/185833).
   - **SMS** — a [Twilio](https://www.twilio.com/) account (account SID, auth token, a from-number).
3. Test delivery with **🧪 Test-fire** in the app (ignores cooldowns).
4. Run on a schedule:
   ```bash
   ./Install\ Alerts\ Schedule.command   # launchd, every 15 min
   # or ad-hoc / your own cron:
   ./.venv/bin/python run_alerts.py
   ```

Each rule has a `cooldown_min` so a sustained spike doesn't re-alert every run;
state is tracked in `.alerts_state.json`. `alerts_config.json` and the state file
are git-ignored.

## Rule fields

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
| `ercot.py` | gridstatus price access (no API key) |
| `coords.py` | hub/zone regional centroids for the map |
| `alerts.py` | rule engine + email/SMS notifiers |
| `run_alerts.py` | scheduler entry point |

## Notes

- Hub/zone markers sit on **representative regional centroids**, not exact
  pricing buses (a hub price is an index over many buses).
- `gridstatus` serves recent SPP dates, so keep the map window near today.
- Resource-node (true nodal) coordinates need an ERCOT↔EIA crosswalk; that lives
  in the full `ercot-suite` Data Hub. This app focuses on hubs and zones.
