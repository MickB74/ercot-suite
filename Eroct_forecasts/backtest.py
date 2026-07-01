"""Walk-forward backtest & scenario-calibration harness.

For each historical *as-of* date we train the heat-rate buckets on data **strictly
before** that date, then predict the following months and score against what
actually settled.

``gas_mode`` selects what the forecast assumes about gas (we don't store historical
gas *forward* curves to replay, so the live modes use a naive forward proxy):

  * ``perfect``     — hold gas at its realized value. Isolates the part of the
                      model we built: the heat-rate multiplier and the MC bands.
                      A clean read = "given gas, the heat-rate model and its
                      P10/P90 are well calibrated."
  * ``persistence`` — naive forward = last spot known at as-of, plus a sqrt(t)
                      gas band. Live-like: central error now carries gas-forecast
                      error and the band widens with horizon.
  * ``seasonal``    — persistence seeded with the same month a year earlier.

Use :func:`compare_gas_modes` to see the gap between perfect and live skill.

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


# Spot Henry Hub log-vol (annualized). Used for the horizon term-structure of the
# gas Monte-Carlo band in live-like mode; simulate_month scales it by sqrt(t) and
# caps it (GV_MAX) so far months don't diverge unrealistically.
GAS_SPOT_VOL = 0.76


def _gas_forecast(gas: dict, asof: pd.Timestamp, tgt: pd.Timestamp,
                  mode: str) -> float | None:
    """Gas level a forecaster could have used at ``asof`` for target ``tgt``.

    * ``perfect``     — realized gas that settled in the target month (the
                        original harness behaviour: isolates heat-rate skill).
    * ``persistence`` — last monthly Henry Hub known strictly before ``asof``
                        (naive forward = today's spot; carries gas-forecast error).
    * ``seasonal``    — same calendar month one year before ``asof`` if known,
                        else falls back to persistence (captures winter/summer
                        gas seasonality that flat persistence misses).
    """
    if mode == "perfect":
        return gas.get((tgt.year, tgt.month))
    # Latest realized month strictly before the as-of date.
    known = [(y, m) for (y, m) in gas if pd.Timestamp(y, m, 1) < asof]
    if not known:
        return None
    last_key = max(known)
    if mode == "seasonal":
        prior = pd.Timestamp(asof.year - 1, tgt.month, 1)
        if prior < asof and (prior.year, prior.month) in gas:
            return gas[(prior.year, prior.month)]
    return gas[last_key]


def run_backtest(hub: str = "HB_NORTH", *, asof_start="2023-01-01",
                 asof_step_months: int = 3, horizon_months: int = 12,
                 n_sims: int = 2000, price_cap: float | None = 5000.0,
                 min_train_years: int = 2, seed: int = 7,
                 gas_mode: str = "perfect",
                 gas_vol: float | None = None) -> pd.DataFrame:
    """One row per (as_of, target_month, block) forecast-vs-realized point.

    ``gas_mode`` controls how gas enters the forecast:

    * ``perfect``     — realized gas, no gas band (``gas_vol`` -> 0). Grades the
                        heat-rate model and its P10/P90 alone.
    * ``persistence`` — naive forward (last spot known at as-of) + a sqrt(t) gas
                        band. Live-like: central error now carries gas-forecast
                        error and bands widen with horizon.
    * ``seasonal``    — like persistence but seeds the level with the same month
                        a year earlier (gas seasonality).

    ``gas_vol`` overrides the annualized gas vol used for the band; defaults to 0
    for ``perfect`` and :data:`GAS_SPOT_VOL` otherwise.
    """
    if gas_mode not in ("perfect", "persistence", "seasonal"):
        raise ValueError(f"unknown gas_mode {gas_mode!r}")
    if gas_vol is None:
        gas_vol = 0.0 if gas_mode == "perfect" else GAS_SPOT_VOL

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
            gas_hat = _gas_forecast(gas, asof, tgt, gas_mode)
            if gas_hat is None:
                continue
            # Horizon in years for the sqrt(t) gas band (0 in perfect mode).
            t_years = 0.0 if gas_mode == "perfect" else step / 12.0
            for block in BLOCKS:
                if (tgt.month, block) not in bk.index:
                    continue
                real = rmap.get((tgt.year, tgt.month, block))
                if real is None:
                    continue
                b = bk.loc[(tgt.month, block)]
                sims = scenarios.simulate_month(
                    gas_hat, b["samples"], t_years, rng=rng, n=n_sims,
                    gas_vol=gas_vol, price_cap=price_cap)
                s = scenarios.summarize(sims)
                rows.append({
                    "asof": asof.strftime("%Y-%m"), "target": tgt.strftime("%Y-%m"),
                    "step": step + 1, "block": block, "realized": real,
                    "gas_hat": gas_hat, "gas_real": gas.get(ykey, float("nan")),
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


def compare_gas_modes(hub: str = "HB_NORTH",
                      modes=("perfect", "persistence", "seasonal"),
                      **kw) -> pd.DataFrame:
    """Overall skill under each gas assumption, side by side.

    The gap between ``perfect`` and ``persistence``/``seasonal`` is the error the
    gas forecast adds on top of the heat-rate model — i.e. the difference between
    "given gas, how good is the model" and "how good is the live forward".
    """
    out = {}
    for m in modes:
        df = run_backtest(hub, gas_mode=m, **kw)
        met = _metrics(df)
        if met:
            out[m] = met
    return pd.DataFrame(out).T
