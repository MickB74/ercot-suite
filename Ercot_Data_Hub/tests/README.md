# ercot_core engine tests

Focused regression tests for the shared settlement engine. Deliberately small:
pure-math + invariants that protect the money numbers, not end-to-end Streamlit
or live-API coverage.

## Run

```bash
python -m pytest              # from the Ercot_Data_Hub root
```

Most tests are **hermetic** (no network, no data lake) and run anywhere. The
`test_settlement_golden.py` tests are **data-gated** — they run the real Aguayo
settlement against the cached parquet lake and skip automatically if it (or the
portal package) isn't present.

## What's covered

| File | Protects |
|---|---|
| `test_gen_forecast.py` | Coverage-aware daily aggregation (`_daily_from_hourly`), the water-fill cap (`_cap_fill`), the wind power curve, and the rule that **null wind hours stay NaN, never fabricated 0 m/s calm** (the 2026-07 root-cause bug). |
| `test_weather_forecast.py` | `fetch_archive` leaves wind nulls as NaN (solar keeps its night-fill). Hermetic via a seeded cache fixture. |
| `test_guard.py` | `_guard_forecast_months` invariants: month total pinned to EIA-P50, **no day exceeds nameplate**, degenerate weather shape falls back to a flat spread, and net ≡ (price − strike) × MWh. |
| `test_settlement_golden.py` | Penny-exact regression of a fully-settled month's real settlement output, per portal (the numbers that reach customers). |

## Golden baselines

`golden/settlements.json` freezes one settled month per invoice-validated portal
(Aguayo, Stafford, Azure Sky, Heart of Texas). Each is recomputed in its own
subprocess (`_settle_worker.py`) because several portals share the package name
`portal` and their own `config.json`. If a **legitimate** data backfill (e.g. a
DST fall-back re-pull) intentionally changes a settled month, rebaseline — never
edit a number to hide an engine regression:

```bash
python tests/regenerate_golden.py
```

## Adding a golden portal

Add an entry to `golden/settlements.json` with the portal's `portal_dir`,
`package`, and a fully-settled `month`; run `regenerate_golden.py` to fill in the
figures. The parametrized test picks it up automatically and skips cleanly if the
portal or its cached data isn't present.
