#!/usr/bin/env python
"""Load a REAL wind project (detected USWTDB fleet) and report expected output.

Detects the actual turbine fleet at a coordinate, runs the keyless Wind engine
(Open-Meteo ERA5) for the given weather years with that real fleet, caches the
8760(s), then reports expected energy + capacity factor and — if a hub is given
— capture price, cannibalization and revenue from the price forecast.

Run with the Ercot_Data_Hub venv (has the wind deps):
    .../Ercot_Data_Hub/.venv/bin/python run_project.py --lat 27.596 --lon -97.637 --hub HB_SOUTH
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

HUB = pathlib.Path.home() / "Documents" / "Github" / "Ercot_Data_Hub"
THIS = pathlib.Path(__file__).resolve().parent
CACHE_DIR = HUB / "data" / "wind_forecast"
sys.path.insert(0, str(HUB))                          # ercot_core
sys.path.insert(0, str(HUB / "datasets" / "wind_forecast"))  # wind_power, etc.
sys.path.insert(0, str(THIS))                         # price engine + wind_revenue

os.environ.setdefault("PF_HUB_LAKE_DIR", str(HUB / "data" / "hub_prices"))
os.environ.setdefault("WIND_CACHE_DIR", str(CACHE_DIR))

import pandas as pd  # noqa: E402

import turbine_db as tdb  # noqa: E402
import wind_app_ui as wui  # noqa: E402
import wind_power as wp  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Expected output for a real wind project")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius", type=float, default=10.0, help="USWTDB search radius (km)")
    ap.add_argument("--hub", default=None, help="settlement hub for capture/revenue")
    ap.add_argument("--years", nargs="+", default=["2024", "2025"])
    ap.add_argument("--asof", default="2026-07-01")
    ap.add_argument("--horizon", type=int, default=24)
    args = ap.parse_args()

    fdb = tdb.find_project_near(args.lat, args.lon, radius_km=args.radius)
    if fdb is None:
        print(f"No turbines within {args.radius} km of {args.lat},{args.lon}. "
              "Widen --radius or check the coordinate.")
        return
    fleet = wui._build_fleet(wui._fleet_from_db(fdb), dict(wp.DEFAULT_LOSSES))
    print(f"Detected: {fdb.name} — {fdb.n_turbines} turbines, {fleet.capacity_mw:.0f} MW, "
          f"mean hub {fdb.mean_hub_height_m:.0f} m, at {fdb.lat:.3f},{fdb.lon:.3f} "
          f"({fdb.distance_km} km away)\n")

    wiring = wui.Wiring(get_api_key=lambda: "", save_creds=lambda s: None,
                        cache_dir=CACHE_DIR, sced_loader=None)
    for y in args.years:
        token = f"era5:{y}-01-01:{y}-12-31"
        _, df, _ = wui.run_or_load(wiring, round(fdb.lat, 4), round(fdb.lon, 4), token, fleet, use_wpl=False)
        cf = (df["net_mw"] / fleet.capacity_mw).mean()
        print(f"  {y}: {df['net_mw'].sum():,.0f} MWh ({df['net_mw'].sum()/1000:,.0f} GWh), CF {cf:.1%}")

    if not args.hub:
        print("\n(no --hub given; skipping capture/revenue)")
        return

    import forecast
    import pf_history
    import shape as shaping
    import wind_revenue as wr

    site = next((s for s in wr.list_wind_sites()
                 if abs(s["lat"] - round(fdb.lat, 4)) < 0.01 and abs(s["lon"] - round(fdb.lon, 4)) < 0.01), None)
    shp, meta = wr.load_cf_shape_blended(site["paths"])
    curve, _ = forecast.run(args.hub, asof=args.asof, horizon_months=args.horizon, n_sims=2000)
    p8760 = shaping.build_8760(curve, pf_history.load_rt15(args.hub))
    mo = wr.capture(p8760, shp, fleet.capacity_mw)
    ann = wr.annual(mo)
    print(f"\nExpected market value at {args.hub} ({meta['n_years']}-yr blend, "
          f"{fleet.capacity_mw:.0f} MW):")
    for _, a in ann.iterrows():
        print(f"  {a['year']}: gen {a['gen_gwh']:.0f} GWh · capture ${a['capture_p50']:.1f} "
              f"vs ATC ${a['atc_p50']:.1f} ({a['cannib_pct']:+.0f}%) · "
              f"revenue ${a['revenue_p50_m']:.1f}M")


if __name__ == "__main__":
    main()
