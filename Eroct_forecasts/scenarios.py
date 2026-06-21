"""Monte Carlo price scenarios — std dev and P10/P50/P90 bands.

Two stochastic drivers per forecast month/block:

  * Gas price  — lognormal around the traded strip, with a sqrt(time) volatility
                 term structure (near months tight, far months wide). The strip
                 is the market's expectation; this layer adds forward-price
                 uncertainty on top of it.
  * Heat rate  — lognormal in log-space, calibrated to the realized per-year
                 samples for that (calendar-month, block). The median anchors
                 the central case; the log-space spread (fattened by scarcity
                 years like Feb-2021 Uri) drives the upper tail.

price = gas x heat_rate, optionally capped at the ERCOT system offer cap. The
heat rate is gas-normalized by construction, so the product reconstructs a price
consistent with the strip's gas level at that month's realized heat-rate regime
-- gas moves are not double-counted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_CAP_DEFAULT = 5000.0   # ERCOT system-wide offer cap ($/MWh), 2024+ HCAP
PCT = [5, 10, 25, 50, 75, 90, 95]


def _lognorm_from_samples(samples: np.ndarray, rng, n: int, *, floor_cv: float = 0.10,
                          tail_boost: float = 1.0):
    """Sample a lognormal calibrated to realized heat-rate samples.

    Centered on the *median* (robust to a single scarcity year), with log-space
    sigma from the samples but floored so thin buckets keep some spread.

    ``tail_boost`` (>=1.0) widens **only the far-upper** tail: the log-deviation
    *beyond +1σ* is stretched by the boost, while everything from the lower tail
    through ~P84 (median, P75) is left untouched. Driven by the ERCOT reserve-
    margin scarcity overlay so tight forward years carry a fatter P90/P95 without
    moving the central P50.
    """
    s = np.asarray(samples, dtype=float)
    s = s[s > 0]
    if s.size == 0:
        return np.full(n, np.nan)
    med = float(np.median(s))
    logs = np.log(s)
    sigma = float(np.std(logs, ddof=1)) if s.size > 1 else floor_cv
    sigma = max(sigma, floor_cv)
    z = rng.normal(0.0, sigma, n)
    if tail_boost and tail_boost > 1.0:
        excess = np.maximum(z - sigma, 0.0)          # only draws beyond +1σ
        z = z + (float(tail_boost) - 1.0) * excess   # stretch the far-upper tail
    return med * np.exp(z)


def simulate_month(gas_central: float, ihr_samples, t_years: float, *,
                   rng, n: int, gas_vol: float, price_cap: float | None,
                   tail_boost: float = 1.0) -> np.ndarray:
    """Vector of n simulated prices for one month/block."""
    # Cumulative log-vol, capped so the P10/P90 band stays realistic at long horizons.
    # Spot gas vol (~76%) is appropriate near-term but mean-reversion limits long-run
    # uncertainty: beyond ~1.5 years, gas prices don't keep diverging at the spot rate.
    # GV_MAX ≈ 0.8 ↔ P90/P50 ≈ 2.8x — comparable to ~2σ in the annual gas distribution.
    GV_MAX = 0.80
    gv = min(gas_vol * np.sqrt(max(t_years, 1e-6)), GV_MAX)
    # Median-preserving: gas_central anchors the P50 at every horizon.
    # mean(gas_t) = gas_central * exp(+0.5*gv²) > forward, but P50 = forward exactly.
    # (Mean-preserving -0.5*gv² is correct for derivatives pricing but collapses the
    # Monte Carlo median to ~40% of forward at 3 yrs × 76% vol — wrong for planning.)
    gas = gas_central * np.exp(rng.normal(0.0, gv, n))  # median-preserving
    ihr = _lognorm_from_samples(ihr_samples, rng, n, tail_boost=tail_boost)
    price = gas * ihr
    if price_cap:
        price = np.minimum(price, price_cap)
    return np.maximum(price, 0.0)


def summarize(sims: np.ndarray) -> dict:
    out = {"mean": float(np.mean(sims)), "std": float(np.std(sims, ddof=1))}
    for p in PCT:
        out[f"p{p}"] = float(np.percentile(sims, p))
    return out


def run(curve_inputs: pd.DataFrame, *, n_sims: int = 5000, gas_vol: float = 0.5,
        price_cap: float | None = PRICE_CAP_DEFAULT, seed: int = 42) -> pd.DataFrame:
    """Monte Carlo over every (month, block) row of ``curve_inputs``.

    ``curve_inputs`` columns required: month, block, gas, ihr_samples (array),
    t_years. Returns the same rows with mean/std/p5..p95 columns added.
    """
    rng = np.random.default_rng(seed)
    recs = []
    for _, row in curve_inputs.iterrows():
        sims = simulate_month(
            float(row["gas"]), row["ihr_samples"], float(row["t_years"]),
            rng=rng, n=n_sims, gas_vol=gas_vol, price_cap=price_cap,
            tail_boost=float(row.get("tail_boost", 1.0)),
        )
        rec = {k: row[k] for k in ("month", "block", "gas", "ihr_p50", "t_years")
               if k in row}
        rec.update(summarize(sims))
        recs.append(rec)
    return pd.DataFrame(recs)
