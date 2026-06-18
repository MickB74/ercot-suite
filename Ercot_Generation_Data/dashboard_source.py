"""Provisional supplement: ERCOT real-time Fuel Mix dashboard.

Direct from ERCOT (no credentials), pulled via the same JSON the public
dashboard uses (https://www.ercot.com/api/1/services/read/dashboards/
fuel-mix.json), wrapped by gridstatus. All fuels, 5-minute telemetry, but only
the current + previous day (~2 days). We resample to 15-minute interval means
to match the report grid, map to the canonical taxonomy, and tag every row
PROVISIONAL so the Fuel Mix Report replaces it later.

Coarseness caveats (documented, intentional):
  - dashboard "Natural Gas" -> canonical "Gas" (combines Gas + Gas-CC)
  - dashboard "Other" includes biomass (no separate Biomass row)
"""

from __future__ import annotations

import pandas as pd

import ercot_fuels as F


def fetch_recent(fetched_at: pd.Timestamp | None = None) -> pd.DataFrame:
    """Fetch the last ~2 days of all-fuel 5-min telemetry, resampled to 15-min."""
    import gridstatus

    fetched_at = fetched_at or pd.Timestamp.now(tz="UTC")
    iso = gridstatus.Ercot()
    wide = iso.get_fuel_mix("today")  # tz-aware US/Central, 5-min
    try:
        prev = iso.get_fuel_mix("yesterday")
        wide = pd.concat([prev, wide], ignore_index=True)
    except Exception:
        pass  # yesterday occasionally unavailable; today alone is fine

    if wide is None or wide.empty:
        return pd.DataFrame(columns=F.SCHEMA_COLUMNS)

    wide = wide.drop_duplicates(subset=["Time"]).sort_values("Time")

    # Bucket 5-min samples into the 15-min interval they START in (floor),
    # using naive CPT clock time to match the report representation.
    import tzutil
    local = tzutil.to_naive_central(wide["Time"])
    wide = wide.assign(interval_start=local.dt.floor("15min"))

    fuel_cols = [c for c in F.DASHBOARD_FUEL_MAP if c in wide.columns]
    agg = wide.groupby("interval_start")[fuel_cols].mean().reset_index()

    long = agg.melt(
        id_vars="interval_start",
        value_vars=fuel_cols,
        var_name="dash_fuel",
        value_name="mw",
    ).dropna(subset=["mw"])

    long["fuel"] = long["dash_fuel"].map(F.DASHBOARD_FUEL_MAP)
    long["interval_end"] = long["interval_start"] + pd.Timedelta(minutes=15)
    long["settlement_type"] = F.ST_PROVISIONAL
    long["source"] = F.SOURCE_DASHBOARD
    long["fetched_at"] = fetched_at

    # Drop the trailing partial interval (fewer than 3 of the expected 5-min
    # samples) so we don't store an under-averaged bucket.
    counts = wide.groupby("interval_start").size()
    full = counts[counts >= 3].index
    long = long[long["interval_start"].isin(full)]

    long = long[long["fuel"].isin(F.CANONICAL_FUELS)]
    return F.finalize(long)


if __name__ == "__main__":
    out = fetch_recent()
    if out.empty:
        print("no dashboard data")
    else:
        print(f"{len(out):,} rows | "
              f"{out['interval_start'].min()} -> {out['interval_start'].max()}")
        print("fuels:", sorted(out["fuel"].unique()))
