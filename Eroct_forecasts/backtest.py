"""Walk-forward backtest & scenario-calibration harness.

For each historical *as-of* date we train the heat-rate buckets on data **strictly
before** that date, then predict the following months and score against what
actually settled. This measures the part of the model we built — the heat-rate
multiplier and the Monte-Carlo bands — holding gas at its realized value
(perfect-foresight gas), since we don't have historical gas *forward* curves to
replay. So a clean read here = "given gas, the heat-rate model and its P10/P90
are well calibrated"; total live error additionally carries gas-forecast error.

Metrics:
  * P50 bias / MAE / MAPE / RMSE        — central-forecast accuracy
  * coverage80  (target 0.80)           — share of realized in [P10, P90]
  * coverage50  (target 0.50)           — share of realized in [P25, P75]
  * pit_below50 (target 0.50)           — share of realized below P50 (median bias)
All reported overall, by horizon bucket (1-3 / 4-6 / 7-12 mo), and by block.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import gas_curve
import heat_rate
import pf_history
import scenarios

BLOCKS = ("peak", "offpeak", "atc")


def _gas_by_month() -> dict:
    g = gas_curve.monthly_history()
    return {(int(m.year), int(m.month)): float(v)
            for m, v in zip(g["month"], g["henry_hub"])}


def run_backtest(hub: str = "HB_NORTH", *, asof_start="2023-01-01",
                 asof_step_months: int = 3, horizon_months: int = 12,
                 n_sims: int = 2000, price_cap: float | None = 5000.0,
                 min_train_years: int = 2, seed: int = 7) -> pd.DataFrame:
    """One row per (as_of, target_month, block) forecast-vs-realized point."""
    rt = pf_history.load_rt15(hub)
    realized = pf_history.monthly_block_mean(rt)            # year, month, block, price
    rmap = {(int(r.year), int(r.month), r.block): float(r.price)
            for r in realized.itertuples()}
    gas = _gas_by_month()
    rng = np.random.default_rng(seed)

    last_ts = rt["ts"].max().tz_localize(None).normalize().replace(day=1)
    last_real = pd.Timestamp(last_ts) - pd.offsets.MonthBegin(1)   # last full realized month
    asofs = pd.date_range(pd.Timestamp(asof_start), last_real, freq=f"{asof_step_months}MS")

    rows = []
    for asof in asofs:
        train = rt[rt["ts"] < pd.Timestamp(asof, tz=rt["ts"].dt.tz)]
        if train["ts"].dt.year.nunique() < min_train_years:
            continue
        bk = heat_rate.buckets(train).set_index(["month", "block"])
        for step in range(horizon_months):
            tgt = (asof.to_period("M") + step).to_timestamp()
            if tgt > last_real:
                break
            ykey = (tgt.year, tgt.month)
            if ykey not in gas:
                continue
            for block in BLOCKS:
                if (tgt.month, block) not in bk.index:
                    continue
                real = rmap.get((tgt.year, tgt.month, block))
                if real is None:
                    continue
                b = bk.loc[(tgt.month, block)]
                sims = scenarios.simulate_month(
                    gas[ykey], b["samples"], 0.0, rng=rng, n=n_sims,
                    gas_vol=0.0, price_cap=price_cap)   # gas known -> gas_vol 0
                s = scenarios.summarize(sims)
                rows.append({
                    "asof": asof.strftime("%Y-%m"), "target": tgt.strftime("%Y-%m"),
                    "step": step + 1, "block": block, "realized": real,
                    "p10": s["p10"], "p25": s["p25"], "p50": s["p50"],
                    "p75": s["p75"], "p90": s["p90"], "mean": s["mean"],
                })
    return pd.DataFrame(rows)


def _metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    err = df["p50"] - df["realized"]
    denom = df["realized"].replace(0, np.nan).abs()
    merr = (df["mean"] - df["realized"]) if "mean" in df.columns else err
    return {
        "n": len(df),
        "bias_$": float(err.mean()),
        "bias_%": float((err / denom).mean() * 100),
        "meanbias_%": float((merr / denom).mean() * 100),
        "mae_$": float(err.abs().mean()),
        "mape_%": float((err.abs() / denom).mean() * 100),
        "rmse_$": float(np.sqrt((err ** 2).mean())),
        "coverage80": float(((df["realized"] >= df["p10"]) & (df["realized"] <= df["p90"])).mean()),
        "coverage50": float(((df["realized"] >= df["p25"]) & (df["realized"] <= df["p75"])).mean()),
        "pit_below50": float((df["realized"] < df["p50"]).mean()),
    }


def summarize(df: pd.DataFrame) -> dict:
    """Overall + by-horizon-bucket + by-block metric tables."""
    if df.empty:
        return {"overall": {}, "by_horizon": pd.DataFrame(), "by_block": pd.DataFrame()}
    d = df.copy()
    bucket = pd.cut(d["step"], [0, 3, 6, 12], labels=["1-3 mo", "4-6 mo", "7-12 mo"])
    by_h = pd.DataFrame({k: _metrics(g) for k, g in d.groupby(bucket, observed=True)}).T
    by_b = pd.DataFrame({k: _metrics(g) for k, g in d.groupby("block")}).T
    return {"overall": _metrics(d), "by_horizon": by_h, "by_block": by_b}


def report(hub: str = "HB_NORTH", **kw) -> tuple[pd.DataFrame, dict]:
    df = run_backtest(hub, **kw)
    return df, summarize(df)
