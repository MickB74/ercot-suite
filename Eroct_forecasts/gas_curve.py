"""Henry Hub natural-gas history (for heat rates) and forward strip (for level).

History  -> realized Henry Hub spot, monthly. Powers the implied-heat-rate
            calc (historical power price / gas). EIA daily series when an API
            key is set, else the bootstrap seed CSV.
Forward  -> the market-traded gas leg of the forecast. Preference order:
            1. manual data/inputs/gas_curve.csv (paste NYMEX NG settlements)
            2. EIA STEO Henry Hub forecast (if API key)
            3. flat hold of the last realized spot (last resort)
"""

from __future__ import annotations

import math

import pandas as pd

import pf_paths

# Long-run Henry Hub anchor ($/MMBtu, de-seasonalized) the curve reverts toward
# beyond the quoted strip, and how fast (months ~ e-folding time). Defaults are
# overridable per call / from the app. ~$4 reflects the post-LNG-export era.
LT_GAS_ANCHOR_DEFAULT = 4.00
REVERT_MONTHS_DEFAULT = 24

EIA_BASE = "https://api.eia.gov/v2"
# Henry Hub Natural Gas Spot Price, Daily ($/MMBtu).
EIA_SPOT_DAILY = "NG.RNGWHHD.D"
# NYMEX Henry Hub futures contracts 1-4, daily settlement ($/MMBtu) — the
# actual traded near strip. Contract N settles ~N months out.
EIA_NYMEX_CONTRACTS = ["NG.RNGC1.D", "NG.RNGC2.D", "NG.RNGC3.D", "NG.RNGC4.D"]
# STEO Henry Hub spot price forecast, monthly ($/MMBtu). The exact mnemonic has
# drifted across EIA releases, so we try candidates and use the first that hits.
# NGHHUUS is the current $/MMBtu series; NGHHMCF ($/mcf) and others are fallbacks.
EIA_STEO_HH_CANDIDATES = ["STEO.NGHHUUS.M", "STEO.NGHHMCF.M", "STEO.NGHHPUS.M"]

# how stale the cached EIA forward can get before an auto-refresh (days)
FORWARD_CACHE_DAYS = 3


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------
def _seed_monthly() -> pd.DataFrame:
    df = pd.read_csv(pf_paths.HENRY_HUB_SEED_CSV, comment="#")
    df["month"] = pd.to_datetime(df["month"])
    return df[["month", "henry_hub"]].sort_values("month").reset_index(drop=True)


def daily_history() -> pd.DataFrame | None:
    """EIA daily Henry Hub spot if cached/fetchable, else None."""
    if pf_paths.HENRY_HUB_DAILY_PARQUET.exists():
        df = pd.read_parquet(pf_paths.HENRY_HUB_DAILY_PARQUET)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    return None


def monthly_history() -> pd.DataFrame:
    """Monthly Henry Hub spot ($/MMBtu): EIA daily->monthly if present, else seed.

    Returns columns: month (period start), henry_hub.
    """
    daily = daily_history()
    if daily is not None and not daily.empty:
        m = daily.set_index("date")["henry_hub"].resample("MS").mean().reset_index()
        m.columns = ["month", "henry_hub"]
        seed = _seed_monthly()
        # Prefer EIA where available; backfill any gap with the seed.
        merged = seed.merge(m, on="month", how="outer", suffixes=("_seed", "_eia"))
        merged["henry_hub"] = merged["henry_hub_eia"].fillna(merged["henry_hub_seed"])
        return merged[["month", "henry_hub"]].dropna().sort_values("month").reset_index(drop=True)
    return _seed_monthly()


def refresh_eia(api_key: str | None = None) -> int:
    """Fetch the full EIA daily Henry Hub spot history and cache it. Returns rows."""
    import requests

    key = api_key or pf_paths.eia_api_key()
    if not key:
        raise RuntimeError("No EIA API key. Set eia_api_key in config.json.")
    series, _ = EIA_SPOT_DAILY.rsplit(".", 1)  # "NG.RNGWHHD"
    url = f"{EIA_BASE}/seriesid/{EIA_SPOT_DAILY}"
    rows, offset = [], 0
    while True:
        r = requests.get(url, params={"api_key": key, "length": 5000, "offset": offset}, timeout=60)
        r.raise_for_status()
        data = r.json().get("response", {}).get("data", [])
        if not data:
            break
        rows.extend(data)
        if len(data) < 5000:
            break
        offset += 5000
    if not rows:
        raise RuntimeError("EIA returned no Henry Hub spot data.")
    df = pd.DataFrame(rows)
    df = df.rename(columns={"period": "date", "value": "henry_hub"})
    df["date"] = pd.to_datetime(df["date"])
    df["henry_hub"] = pd.to_numeric(df["henry_hub"], errors="coerce")
    df = df.dropna(subset=["henry_hub"])[["date", "henry_hub"]].sort_values("date")
    pf_paths.ensure_dirs()
    df.to_parquet(pf_paths.HENRY_HUB_DAILY_PARQUET, index=False)
    return len(df)


# --------------------------------------------------------------------------
# forward strip
# --------------------------------------------------------------------------
def _manual_strip() -> pd.DataFrame | None:
    p = pf_paths.GAS_CURVE_CSV
    if not p.exists():
        return None
    df = pd.read_csv(p, comment="#")
    if df.empty or "month" not in df.columns or "gas" not in df.columns:
        return None
    df["month"] = pd.to_datetime(df["month"])
    df["gas"] = pd.to_numeric(df["gas"], errors="coerce")
    df = df.dropna(subset=["gas"])
    return df[["month", "gas"]].sort_values("month").reset_index(drop=True) if not df.empty else None


def _eia_series(series_id: str, api_key: str, length: int = 60) -> pd.DataFrame | None:
    """Fetch one EIA v2 series id -> DataFrame[period(raw str), value]. None on fail."""
    try:
        import requests

        url = f"{EIA_BASE}/seriesid/{series_id}"
        r = requests.get(url, params={"api_key": api_key, "length": length,
                                      "sort[0][column]": "period", "sort[0][direction]": "desc"},
                         timeout=60)
        r.raise_for_status()
        data = r.json().get("response", {}).get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"])[["period", "value"]]
    except Exception:
        return None


_NYMEX_MAX_AGE_DAYS = 30  # reject NYMEX data older than this — series may have gone stale


def _nymex_near_strip(api_key: str, first_month: pd.Timestamp) -> pd.DataFrame | None:
    """Latest NYMEX futures contract 1-4 settlements -> monthly gas, near strip.

    Returns None if the most-recent EIA publication date is older than
    ``_NYMEX_MAX_AGE_DAYS`` days — that means EIA stopped updating the series
    and the prices reflect an old market snapshot, not current forwards.
    """
    rows = []
    for i, sid in enumerate(EIA_NYMEX_CONTRACTS):
        d = _eia_series(sid, api_key, length=5)
        if d is None or d.empty:
            continue
        latest = d.sort_values("period").iloc[-1]
        # Staleness check — EIA RNGC1-4 stopped updating after ~April 2024;
        # using 2-year-old contract prices would badly distort the near curve.
        age_days = (pd.Timestamp.now() - pd.Timestamp(latest["period"])).days
        if age_days > _NYMEX_MAX_AGE_DAYS:
            return None   # whole strip is stale — fall back to STEO
        rows.append({"month": first_month + pd.DateOffset(months=i),
                     "gas": float(latest["value"])})
    return pd.DataFrame(rows) if rows else None


def _steo_forecast(api_key: str) -> pd.DataFrame | None:
    """STEO monthly Henry Hub spot forecast (tries candidate series ids)."""
    for sid in EIA_STEO_HH_CANDIDATES:
        d = _eia_series(sid, api_key, length=60)
        if d is not None and not d.empty:
            d = d.rename(columns={"period": "month", "value": "gas"})
            d["month"] = pd.to_datetime(d["month"])
            return d[["month", "gas"]].sort_values("month").reset_index(drop=True)
    return None


def refresh_forward(api_key: str | None = None, *, horizon_months: int = 60) -> pd.DataFrame:
    """Fetch and cache the EIA gas forward (NYMEX near 1-4 + STEO beyond).

    NYMEX contract settlements are the actual traded near strip and win for the
    months they cover; STEO fills further out. Cached to GAS_FORWARD_PARQUET.
    """
    key = api_key or pf_paths.eia_api_key()
    if not key:
        raise RuntimeError("No EIA API key. Set eia_api_key in config.json or enter it in the app.")

    # anchor the near strip to the latest spot month
    hist = monthly_history()
    first = hist["month"].max() + pd.offsets.MonthBegin(1)

    nymex = _nymex_near_strip(key, first)
    steo = _steo_forecast(key)
    parts = [p for p in (nymex, steo) if p is not None and not p.empty]
    if not parts:
        raise RuntimeError("EIA returned no NYMEX or STEO gas data (check the key / series ids).")

    # NYMEX wins on overlap (drop STEO months covered by NYMEX), then concat.
    combined = pd.concat(parts, ignore_index=True)
    src = pd.Series(["nymex"] * (len(nymex) if nymex is not None else 0)
                    + ["steo"] * (len(steo) if steo is not None else 0))
    combined["_src"] = src.to_numpy()
    combined = (combined.sort_values(["month", "_src"])  # nymex < steo alphabetically
                .drop_duplicates("month", keep="first")
                .drop(columns="_src").sort_values("month").reset_index(drop=True))
    pf_paths.ensure_dirs()
    combined.to_parquet(pf_paths.GAS_FORWARD_PARQUET, index=False)
    return combined


def _cached_forward(max_age_days: int = FORWARD_CACHE_DAYS) -> pd.DataFrame | None:
    p = pf_paths.GAS_FORWARD_PARQUET
    if not p.exists():
        return None
    age_days = (pd.Timestamp.now() - pd.Timestamp(p.stat().st_mtime, unit="s")).days
    if age_days > max_age_days:
        return None
    df = pd.read_parquet(p)
    df["month"] = pd.to_datetime(df["month"])
    return df


def _seasonal_factors() -> dict:
    """Month-of-year Henry Hub shape (mean 1.0) from realized history.

    Uses the median per calendar month so a single spike year (Feb-2021 Uri, the
    2022 run-up) doesn't distort the seasonal shape."""
    hist = monthly_history().copy()
    hist["moy"] = hist["month"].dt.month
    seas = hist.groupby("moy")["henry_hub"].median()
    seas = seas / seas.mean()
    return {int(m): float(v) for m, v in seas.items()}


def forward_strip(asof: pd.Timestamp, horizon_months: int, *,
                  auto_fetch: bool = True, lt_anchor: float | None = None,
                  revert_months: int = REVERT_MONTHS_DEFAULT,
                  aeo_anchor: bool = True, aeo_weight: float = 0.0
                  ) -> tuple[pd.DataFrame, str]:
    """Monthly gas forward over [asof_month .. asof_month+horizon). (df, source).

    Precedence:
      1. manual gas_curve.csv override (only if it has real rows)
      2. fresh EIA cache (NYMEX 1-4 + STEO), auto-refreshed when stale & keyed
      3. seed-history seasonal hold (no key / offline) -- last resort

    Public-forecast blend (selectable):
      * NYMEX near contracts (1-4) are always pure traded — never diluted.
      * In the STEO mid-range, the level is blended ``(1-aeo_weight)`` × STEO/market
        + ``aeo_weight`` × the EIA AEO long-term path (when AEO is available).
      * Beyond the last quoted month the de-seasonalized level mean-reverts toward
        the **AEO year-varying anchor** (``aeo_anchor=True``) instead of the flat
        ``lt_anchor`` constant, over ~``revert_months``, with seasonality re-applied.

    df columns: month, gas. Always spans the full horizon.
    """
    import public_forecasts  # lazy to avoid an import cycle

    asof = pd.Timestamp(asof)
    first = asof.normalize().replace(day=1)
    months = pd.date_range(first, periods=horizon_months, freq="MS")
    target = pd.DataFrame({"month": months})

    src = _manual_strip()
    source = "manual gas_curve.csv"

    if src is None or src.empty:
        src = _cached_forward()
        source = "EIA (NYMEX futures + STEO)"
        if (src is None or src.empty) and auto_fetch and pf_paths.eia_api_key():
            try:
                src = refresh_forward(horizon_months=max(horizon_months, 24))
                source = "EIA (NYMEX futures + STEO, fresh)"
            except Exception as e:  # offline / bad key -> fall through to seasonal
                source = f"EIA fetch failed ({e}); seasonal hold"
                src = None

    if src is None or src.empty:
        return _seasonal_hold(target, lt_anchor, revert_months), (
            source if "failed" in source
            else "seasonal hold (no EIA key — add one to use live futures)")

    seas = _seasonal_factors()

    def _aeo_month(m: pd.Timestamp) -> float | None:
        if not aeo_anchor:
            return None
        a = public_forecasts.aeo_anchor_for(m)
        return a[0] * seas.get(int(m.month), 1.0) if a is not None else None

    out = target.merge(src, on="month", how="left")
    last_obs = pd.Timestamp(src["month"].max())
    within = out["month"] <= last_obs
    out.loc[within, "gas"] = out.loc[within, "gas"].interpolate().ffill().bfill()

    # NYMEX near contracts (the first ~4 quoted months) stay pure traded.
    nymex_last = first + pd.DateOffset(months=len(EIA_NYMEX_CONTRACTS) - 1)
    aeo_used = False
    if aeo_weight > 0 and aeo_anchor:
        wa = min(max(float(aeo_weight), 0.0), 1.0)
        mid = within & (out["month"] > nymex_last)
        for idx in out.index[mid]:
            m = pd.Timestamp(out.at[idx, "month"])
            av = _aeo_month(m)
            if av is not None:
                out.at[idx, "gas"] = (1 - wa) * float(out.at[idx, "gas"]) + wa * av
                aeo_used = True

    beyond = out["month"] > last_obs
    if beyond.any():
        const_anchor = LT_GAS_ANCHOR_DEFAULT if lt_anchor is None else float(lt_anchor)
        last_val = float(src.sort_values("month")["gas"].iloc[-1])
        last_level = last_val / seas.get(int(last_obs.month), 1.0)
        anchor_lab = f"${const_anchor:.2f}"
        for idx in out.index[beyond]:
            m = pd.Timestamp(out.at[idx, "month"])
            a = public_forecasts.aeo_anchor_for(m) if aeo_anchor else None
            anchor_level = a[0] if a is not None else const_anchor
            if a is not None:
                aeo_used = True
                anchor_lab = a[1]
            k = (m.to_period("M") - last_obs.to_period("M")).n
            w = math.exp(-k / max(float(revert_months), 1e-6))
            level = anchor_level + (last_level - anchor_level) * w
            out.at[idx, "gas"] = level * seas.get(int(m.month), 1.0)
        source += f" · mean-reversion → {anchor_lab} beyond {last_obs.strftime('%Y-%m')}"
    if aeo_used and aeo_weight > 0:
        source += f" · STEO/AEO blend (aeo_w={aeo_weight:.0%})"
    return out, source


def _seasonal_hold(target: pd.DataFrame, lt_anchor: float | None = None,
                   revert_months: int = REVERT_MONTHS_DEFAULT) -> pd.DataFrame:
    """Offline fallback (no EIA): seasonal shape on a level that reverts from the
    recent realized level toward the long-term anchor."""
    hist = monthly_history()
    seas = _seasonal_factors()
    last_month = pd.Timestamp(hist["month"].max())
    last_level = hist["henry_hub"].iloc[-3:].mean()
    anchor = LT_GAS_ANCHOR_DEFAULT if lt_anchor is None else float(lt_anchor)
    out = target.copy()
    vals = []
    for m in out["month"]:
        m = pd.Timestamp(m)
        k = max((m.to_period("M") - last_month.to_period("M")).n, 0)
        w = math.exp(-k / max(float(revert_months), 1e-6))
        level = anchor + (last_level - anchor) * w
        vals.append(level * seas.get(int(m.month), 1.0))
    out["gas"] = vals
    return out
