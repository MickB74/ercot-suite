# ERCOT Price Forecast — Methodology

This document is the citable, full-detail companion to the [README](README.md). It
specifies every data source, equation, and parameter behind the forward power-price
forecast, including the public **EIA** and **ERCOT** forecasts now wired in. Every run
also writes a machine-readable `*.meta.json` recording the exact sources and parameters
used, so any number can be reproduced.

---

## 1. Model overview

The forecast is a **market-implied heat-rate model**:

```
Forward power ($/MWh)  =  Gas forward ($/MMBtu)  ×  Implied heat rate (MMBtu/MWh)  →  Monte Carlo bands
                          └── public, market/EIA ──┘   └── realized from hub history ──┘
```

The gas leg carries the **price level** (liquid, market-traded + public forecasts); the
implied heat rate — `hub price ÷ Henry Hub gas`, bucketed by calendar-month × peak/off-peak
from the hub's own history — carries **ERCOT shape and scarcity**. Forecasting a slow-moving
ratio while letting traded/forecast gas do the heavy lifting is what makes the method robust.

The **P50** is anchored on the *median* heat rate (robust to a single scarcity year); the
**bands** come from the full historical heat-rate spread (including Feb-2021 Uri) crossed with
forward gas-price uncertainty in a Monte Carlo.

---

## 2. Data sources

All sources are free. The EIA key (one-time, free) is stored in `config.json`.

| # | Source | Endpoint / series | Role | Auto? |
|---|--------|-------------------|------|-------|
| 1 | ERCOT RTM hub prices | shared Data Hub lake (`hub_prices`) | history → heat rate | yes |
| 2 | EIA Henry Hub spot (daily) | `NG.RNGWHHD.D` | history → heat rate **and** realized gas vol | yes |
| 3 | EIA NYMEX Henry Hub futures 1–4 | `NG.RNGC1.D … NG.RNGC4.D` | gas **near** strip (traded) | yes |
| 4 | EIA STEO Henry Hub forecast | `STEO.NGHHUUS.M` ($/MMBtu) | gas **mid** curve (~2 yrs) | yes |
| 5 | EIA AEO Henry Hub (long-term) | `aeo/2026` · `prce_hhp_NA_NA_ng_NA_usa_ndlrpmbtu` (nominal $/MMBtu, → 2050) | gas **far-tail anchor** | yes |
| 6 | EIA STEO electricity (retail) | `STEO.ESICUUS.M` (¢/kWh) | **cross-check line only** | yes |
| 7 | ERCOT CDR reserve margins | CDR report (XLSX) → `data/inputs/ercot_cdr.csv` | forward **scarcity** overlay | manual |
| 8 | ICE/CME ERCOT power futures | *(none free)* → `data/inputs/ercot_power_strip.csv` | near-month **traded calibration** | manual |

> **Why some are manual.** ERCOT does **not** publish a free traded forward curve or a
> machine-readable CDR feed, and STEO's NYMEX-implied gas *uncertainty* band is not exposed as
> an open-API series. Those three are therefore manual drop-ins; everything else is automatic.

---

## 3. The gas forward curve

Resolved over the full horizon by `gas_curve.forward_strip()` with this **precedence**:

1. **Manual override** — `data/inputs/gas_curve.csv` (paste live CME/Barchart NG settlements). Wins outright.
2. **Public blend** (default) — built term-by-term:
   - **Near (contracts 1–4, ~4 months):** NYMEX Henry Hub futures settlements — the actual
     traded strip. *Never diluted* by other sources.
   - **Mid (to the end of the STEO horizon, ~2 yrs):** STEO Henry Hub, optionally blended with
     the AEO path by a user weight `w_aeo ∈ [0,1]`:
     `gas(m) = (1 − w_aeo)·STEO(m) + w_aeo·AEO(m)` (default `w_aeo = 0`, i.e. pure STEO).
   - **Far tail (beyond the last quote):** the de-seasonalized level mean-reverts from the last
     quoted level `L₀` toward a long-run **anchor** `A`, with the historical monthly seasonal
     shape re-applied:
     `level(k) = A + (L₀ − A)·e^(−k/τ)`,  `gas(m) = level · seasonal(month)`
     where `k` = months past the last quote and `τ` = reversion months (default 24).
3. **Seasonal hold** — offline / no key: reverts a recent realized level toward `A` with seasonality.

### Long-run anchor `A` — now from EIA AEO
Previously a hardcoded constant ($4.00). Now, when `aeo_anchor=True` (default), `A` is the
**year-varying** EIA AEO reference Henry Hub level for each forecast month (de-seasonalized,
linearly interpolated across AEO years, flat-extrapolated past the AEO horizon). The constant
remains only as a fallback when AEO is unavailable or the toggle is off.

> **AEO scenario.** AEO 2026 is an early release with no plain "Reference" case, so the neutral
> path defaults to the **average of the Low- and High-Economic-Growth cases** (`lm2026`, `hm2026`).
> Override via `config.json` → `"aeo_scenario"` / `"aeo_year"`. The scenario actually used is
> recorded in the gas-source label and `meta`. AEO values are **nominal** $/MMBtu — consistent
> with the nominal NYMEX/STEO strip — so far-dated power reflects EIA's nominal long-run outlook.

---

## 4. The implied heat rate

For each historical `(year, month, block)`:
`IHR = mean hub price ($/MWh) ÷ Henry Hub gas ($/MMBtu)`  [MMBtu/MWh].

Pooled across years into a distribution per `(calendar-month, block)`:
- **Median** → the P50 multiplier (one scarcity year cannot move it).
- **Per-year samples** → the empirical spread that drives the scenario tails.
- Thin buckets (`< 2 yrs`) fall back to a block-wide relative dispersion so every bucket carries uncertainty.

Blocks: **peak = ERCOT 5×16** (Mon–Fri HE 7–22, NERC holidays on-peak in v1); off-peak; ATC.

---

## 5. Monte Carlo scenarios

Per `(month, block)`, draw `n` paths of `price = gas × IHR`:

- **Gas** — lognormal around the strip with a √t volatility term structure (martingale-mean
  corrected): `gas = G·exp(−½σ²t + σ√t·Z)`, `Z~N(0,1)`.
- **Heat rate** — lognormal in log-space, **centered on the median**, with log-σ from the
  realized samples (floored for thin buckets).

`price` is clipped to the ERCOT system-wide offer cap (default $5,000/MWh) and floored at 0.

### Gas volatility `σ` — now data-driven
`gas_vol_mode="auto"` (default) sets `σ = public_forecasts.realized_gas_vol()`: the annualized
log-volatility of **monthly-average** Henry Hub returns from cached EIA daily history over a
trailing ~5-year window, clamped to `[0.20, 1.20]`. This replaces the old hardcoded `0.5`.

> **Note:** recent Henry Hub realized vol runs high (~0.7–0.8) because the window spans the
> 2021–2022 price spikes — gas is genuinely one of the most volatile commodities, so the old
> `0.5` was conservative. Switch to **Manual** (or pass `--gas-vol`) to override; the resolved
> value and its source are always recorded in `meta.gas_vol` / `meta.gas_vol_source`.

### ERCOT scarcity overlay (CDR)
When `scarcity=True` **and** `data/inputs/ercot_cdr.csv` is populated, each forecast year's
planning reserve margin `R` produces a **heat-rate upper-tail boost** `b ≥ 1`:

```
b(R) = 1                              if R ≥ target (15%)
b(R) = 1 + max_boost · (target − R)/(target − knee)   for knee(10%) ≤ R < target
b(R) = 1 + max_boost (= 1.6)         if R < knee
```

The boost stretches only the log-deviation **beyond +1σ** of the heat-rate draw, so the
**median (P50) and through ~P84 are unchanged** and only **P90/P95 fatten** — i.e. a tighter
forward grid carries more scarcity-price risk, not a higher base case. Reserve margins come
from the ERCOT CDR (summer peak is the binding season). Verified: with a tight test year the
P50 drift stays under ~2% while P90/P95 widen materially.

---

## 6. Traded power calibration (optional)
If `data/inputs/ercot_power_strip.csv` holds ICE/CME ERCOT hub forwards, near months are blended
toward those traded settlements with a weight decaying linearly to 0 over `fade_months`
(default 18). The whole scenario distribution is shifted by the blend delta so it re-centers on
the traded level without losing its width.

## 7. EIA STEO electricity — cross-check only
The STEO U.S. retail electricity series (¢/kWh → $/MWh) is plotted as a dotted reference line.
It is a **national retail average, not an ERCOT wholesale hub**, so it is **never blended into
the model** — it exists purely to sanity-check the order of magnitude.

---

## 8. Caching & refresh
- Daily Henry Hub, NYMEX+STEO forward, AEO path, and STEO power are cached under `data/gas/`.
- The forward and STEO-power caches refresh when older than ~3 days (and on demand); AEO is
  allowed to be stale (it updates yearly).
- `python cli.py --refresh-gas` refreshes all of them and prints provenance.

## 9. Reproducibility
Every saved forecast writes `forecast_<HUB>_<ASOF>.meta.json` containing: the gas-source label
(with AEO scenario and blend weight), resolved `gas_vol` + its source, the AEO-anchor flag, the
scarcity-overlay state (and CDR vintage), horizon, sims, seed, and price cap. The `.meta.json`
is the authoritative record of how a given curve was produced.

## 10. Limitations
- Gas and heat rate are sampled **independently**; in winter they are positively correlated
  (a correlation knob is a natural next step).
- The AEO anchor is **nominal** and scenario-dependent; far-dated levels inherit AEO's
  macro assumptions.
- The CDR overlay uses **annual** reserve margins applied to all months of that year; it widens
  tails but does not model specific scarcity events.
- There is **no free traded ERCOT forward** — the ERCOT-side signal is fundamentals (CDR) plus
  any manual ICE paste, not a market-priced power curve.
- STEO gas *uncertainty* (NYMEX-implied CI) is not in the open API, so volatility is derived
  from realized EIA history rather than option-implied bands.
