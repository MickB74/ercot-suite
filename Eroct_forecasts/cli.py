#!/usr/bin/env python
"""Run an ERCOT price forecast from the terminal.

Examples:
    python cli.py --hub HB_NORTH --horizon 36
    python cli.py --hub HB_HOUSTON --asof 2026-07-01 --horizon 24 --shape
    python cli.py --refresh-gas          # pull EIA Henry Hub daily history
"""

from __future__ import annotations

import argparse

import pandas as pd

import forecast
import forecast_store
import gas_curve
import pf_history
import pf_paths
import public_forecasts
import shape as shaping


def main() -> None:
    ap = argparse.ArgumentParser(description="ERCOT forward price forecast")
    ap.add_argument("--hub", nargs="+", default=["HB_NORTH"], choices=pf_history.HUBS,
                    metavar="HUB", help="one or more hubs (space-separated)")
    ap.add_argument("--all-hubs", action="store_true", help="forecast every hub")
    ap.add_argument("--block", default="atc", choices=["atc", "peak", "offpeak"],
                    help="block shown in the hub × month matrix (default atc)")
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--horizon", type=int, default=36, help="months")
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--gas-vol", type=float, default=None,
                    help="annualized gas log-vol override (default: auto from EIA history)")
    ap.add_argument("--gas-vol-mode", choices=["auto", "fixed"], default="auto",
                    help="auto = data-driven vol from EIA Henry Hub history; fixed = 0.5")
    ap.add_argument("--aeo-weight", type=float, default=0.0,
                    help="weight on the EIA AEO long-term path in the gas mid-curve (0-1)")
    ap.add_argument("--no-aeo-anchor", action="store_true",
                    help="disable the EIA AEO far-tail anchor (use the flat constant instead)")
    ap.add_argument("--scarcity", action="store_true",
                    help="apply the ERCOT CDR reserve-margin scarcity tail boost")
    ap.add_argument("--price-cap", type=float, default=5000.0)
    ap.add_argument("--fade-months", type=int, default=18, help="power-futures blend fade")
    ap.add_argument("--shape", action="store_true", help="also build the 8760 hourly curve")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--refresh-gas", action="store_true", help="pull EIA Henry Hub daily, then exit")
    ap.add_argument("--backtest", action="store_true", help="walk-forward skill/calibration report, then exit")
    args = ap.parse_args()

    pf_paths.ensure_dirs()
    if args.refresh_gas:
        n = gas_curve.refresh_eia()
        print(f"Cached {n:,} EIA Henry Hub daily rows -> {pf_paths.HENRY_HUB_DAILY_PARQUET}")
        fwd = gas_curve.refresh_forward()
        print(f"Cached EIA forward ({len(fwd)} months, NYMEX 1-4 + STEO) "
              f"-> {pf_paths.GAS_FORWARD_PARQUET}")
        print(fwd.assign(month=lambda d: d.month.dt.strftime("%Y-%m")).head(12).round(2).to_string(index=False))
        try:
            aeo = public_forecasts.refresh_aeo()
            print(f"\nCached EIA AEO long-term gas ({len(aeo)} yrs, "
                  f"{aeo.attrs.get('scenario', 'ref')}) -> {pf_paths.AEO_GAS_PARQUET}")
            print(aeo.head(6).round(2).to_string(index=False))
        except Exception as e:
            print(f"\nAEO refresh skipped: {e}")
        pw = public_forecasts.eia_steo_power()
        if pw is not None and not pw.empty:
            print(f"\nCached EIA STEO power cross-check ({pw['_series'].iloc[0]}) "
                  f"-> {pf_paths.STEO_POWER_PARQUET}")
        print(f"\nData-driven gas vol (EIA realized): {public_forecasts.realized_gas_vol():.0%}")
        return

    if args.backtest:
        import backtest as bt
        for hub in (list(pf_history.HUBS) if args.all_hubs else args.hub):
            df, s = bt.report(hub, horizon_months=args.horizon)
            ov = s["overall"]
            print(f"\n=== {hub} backtest ({ov.get('n',0)} points; gas held at realized) ===")
            print(f"  P50 bias: {ov['bias_%']:+.1f}%  |  MAPE: {ov['mape_%']:.1f}%  |  RMSE: ${ov['rmse_$']:.0f}")
            print(f"  coverage P10–P90: {ov['coverage80']:.0%} (target 80%)  |  "
                  f"realized below P50: {ov['pit_below50']:.0%} (target 50%)")
            print(s["by_horizon"][["n", "bias_%", "mape_%", "coverage80"]].round(1).to_string())
        return

    hubs = list(pf_history.HUBS) if args.all_hubs else args.hub
    curve, metas = forecast.run_many(
        hubs, asof=args.asof, horizon_months=args.horizon, n_sims=args.sims,
        gas_vol=args.gas_vol, gas_vol_mode=args.gas_vol_mode, price_cap=args.price_cap,
        fade_months=args.fade_months, aeo_anchor=not args.no_aeo_anchor,
        aeo_weight=args.aeo_weight, scarcity=args.scarcity,
        progress=lambda i, n, h: print(f"  [{i + 1}/{n}] {h}…"),
    )
    meta = metas[0]
    print("\nForecast:", {k: meta[k] for k in ("asof", "horizon_months", "gas_source",
                                              "gas_vol", "gas_vol_source",
                                              "traded_calibration")},
          "| hubs:", ", ".join(hubs))
    print("ERCOT scarcity overlay:", meta["scarcity_overlay"])

    mat = forecast.price_matrix(curve, block=args.block, metric="p50").round(0)
    print(f"\nHub × month {args.block.upper()} P50 ($/MWh):\n{mat.to_string()}")

    if not args.no_save:
        print("\nSaved:")
        for hub, m in zip(hubs, metas):
            hourly = None
            if args.shape:
                hourly = shaping.build_8760(curve[curve.hub == hub], pf_history.load_rt15(hub))
            paths = forecast_store.save(curve[curve.hub == hub], m, hourly)
            print(f"  {hub}: {paths['csv']}")


if __name__ == "__main__":
    main()
