"""Assemble the forward power-price forecast for one ERCOT hub.

Pipeline:
  1. history  -> realized implied-heat-rate buckets (median anchor + samples)
  2. gas      -> traded forward strip ($/MMBtu) over the horizon
  3. model    -> P50 = gas x median heat rate, per month x block
  4. scenarios-> Monte Carlo bands (P10/P50/P90, mean, std)
  5. calibrate-> blend traded ERCOT power futures into the near months
"""

from __future__ import annotations

import pandas as pd

import gas_curve
import heat_rate
import pf_history
import pf_tz
import power_futures
import public_forecasts
import scenarios


def _as_of(asof=None) -> pd.Timestamp:
    if asof is not None:
        return pd.Timestamp(asof)
    return pf_tz.now_central().tz_localize(None).normalize()


def build_inputs(hub: str, asof: pd.Timestamp, horizon_months: int,
                 blocks=("peak", "offpeak", "atc"), gas_override=None,
                 gas_source_label=None, *, aeo_anchor: bool = True,
                 aeo_weight: float = 0.0, scarcity: bool = False
                 ) -> tuple[pd.DataFrame, dict]:
    """Per-(month, block) model inputs: gas, heat-rate stats + samples, t_years.

    ``gas_override`` — optional DataFrame[month, gas] (e.g. the resolved/edited
    in-app strip) used instead of the auto-resolved EIA/manual forward.
    ``gas_source_label`` — label to record in meta when an override is used.
    ``aeo_anchor`` / ``aeo_weight`` — wire the EIA AEO long-term path into the gas
    blend (far-tail anchor + optional mid-curve weight).
    ``scarcity`` — apply the ERCOT CDR reserve-margin upper-tail boost per year.
    """
    rt = pf_history.load_rt15(hub)
    bk = heat_rate.buckets(rt).set_index(["month", "block"])
    if gas_override is not None and not gas_override.empty:
        gas = gas_override.copy()
        gas["month"] = pd.to_datetime(gas["month"])
        gas = gas[["month", "gas"]].sort_values("month").reset_index(drop=True)
        gas_src = gas_source_label or "in-app strip"
    else:
        gas, gas_src = gas_curve.forward_strip(asof, horizon_months,
                                               aeo_anchor=aeo_anchor, aeo_weight=aeo_weight)

    boost_map, scar_meta = public_forecasts.scarcity_by_month(
        [pd.Timestamp(m) for m in gas["month"]], on=scarcity)

    first = pd.Timestamp(asof).normalize().replace(day=1)
    rows = []
    for _, grow in gas.iterrows():
        mo = pd.Timestamp(grow["month"])
        moy = mo.month
        t_years = max((mo.to_period("M") - first.to_period("M")).n, 0) / 12.0
        for block in blocks:
            if (moy, block) not in bk.index:
                continue
            b = bk.loc[(moy, block)]
            rows.append({
                "month": mo, "block": block, "gas": float(grow["gas"]),
                "ihr_p50": float(b["ihr_mean"]) if pd.isna(b["ihr_p50"]) else float(b["ihr_p50"]),
                "ihr_samples": b["samples"], "t_years": t_years,
                "tail_boost": float(boost_map.get(mo, 1.0)),
            })
    inp = pd.DataFrame(rows)
    meta = {"hub": hub, "asof": str(pd.Timestamp(asof).date()),
            "horizon_months": horizon_months, "gas_source": gas_src,
            "history_rows": len(rt), "blocks": list(blocks),
            "aeo_anchor": bool(aeo_anchor), "aeo_weight": float(aeo_weight),
            "scarcity_overlay": scar_meta}
    return inp, meta


def run(hub: str = "HB_NORTH", asof=None, horizon_months: int = 36,
        n_sims: int = 5000, gas_vol: float | None = None, price_cap: float | None = 5000.0,
        fade_months: int = 18, seed: int = 42, gas_override=None,
        gas_source_label=None, *, gas_vol_mode: str = "auto",
        aeo_anchor: bool = True, aeo_weight: float = 0.0,
        scarcity: bool = False) -> tuple[pd.DataFrame, dict]:
    """Full monthly forecast for one hub. Returns (curve, meta).

    ``gas_vol_mode`` — "auto" derives the gas volatility from realized EIA Henry
    Hub history (``public_forecasts.realized_gas_vol``); "fixed" uses ``gas_vol``
    (defaulting to 0.5 if not given). An explicit ``gas_vol`` always wins.

    curve columns: month, block, hub, gas, ihr_p50, t_years, mean, std,
    p5..p95, p50_model, traded, blend_w.
    """
    asof = _as_of(asof)
    if gas_vol is not None:
        vol, vol_src = float(gas_vol), "user"
    elif gas_vol_mode == "auto":
        vol, vol_src = public_forecasts.realized_gas_vol(), "EIA realized (auto)"
    else:
        vol, vol_src = 0.5, "fixed default"

    inp, meta = build_inputs(hub, asof, horizon_months, gas_override=gas_override,
                             gas_source_label=gas_source_label, aeo_anchor=aeo_anchor,
                             aeo_weight=aeo_weight, scarcity=scarcity)
    if inp.empty:
        raise ValueError(f"No model inputs for {hub} (check gas strip / history).")

    sim = scenarios.run(inp, n_sims=n_sims, gas_vol=vol,
                        price_cap=price_cap, seed=seed)
    sim = sim.merge(inp[["month", "block", "ihr_samples"]], on=["month", "block"])
    sim["hub"] = hub
    curve = power_futures.blend(sim.drop(columns=["ihr_samples"]), hub,
                               fade_months=fade_months)
    curve = curve.sort_values(["block", "month"]).reset_index(drop=True)
    meta.update({"n_sims": n_sims, "gas_vol": round(vol, 4), "gas_vol_source": vol_src,
                 "price_cap": price_cap, "fade_months": fade_months, "seed": seed,
                 "traded_calibration": bool(curve["blend_w"].gt(0).any())})
    return curve, meta


def run_many(hubs, asof=None, progress=None, **kw) -> tuple[pd.DataFrame, list[dict]]:
    """Forecast several hubs and stack the curves. Returns (combined_curve, metas).

    ``progress`` — optional callback(i, n, hub) for a UI progress bar.
    """
    asof = _as_of(asof)
    curves, metas = [], []
    hubs = list(hubs)
    for i, hub in enumerate(hubs):
        if progress:
            progress(i, len(hubs), hub)
        curve, meta = run(hub, asof=asof, **kw)
        curves.append(curve)
        metas.append(meta)
    combined = pd.concat(curves, ignore_index=True) if curves else pd.DataFrame()
    return combined, metas


def price_matrix(curve: pd.DataFrame, block: str = "atc", metric: str = "p50") -> pd.DataFrame:
    """Pivot a (multi-hub) curve to a month × hub price table for one block."""
    sub = curve[curve["block"] == block]
    piv = sub.pivot_table(index="month", columns="hub", values=metric)
    piv.index = pd.to_datetime(piv.index).strftime("%Y-%m")
    piv.index.name = "Month"
    return piv
