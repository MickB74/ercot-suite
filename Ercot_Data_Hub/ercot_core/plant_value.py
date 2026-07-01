"""Plant capture-price valuation: solar generation shape × hub price forecast.

Ties together three pieces the Hub already has but never combined:

  * the PVWatts solar engine (``datasets/solar_forecast/solar_pvwatts.py``),
  * the forward price-forecast engine (sibling ``Eroct_forecasts`` repo), and
  * the curated asset registry (``price_settlements/ercot_assets.json``), which
    is the only source that maps a plant to its ERCOT trading *hub*.

The headline metric is the **capture price**: the generation-weighted average
forward price the plant's solar output actually earns at its hub, versus the
flat ATC (all-hours) average. Solar over-produces midday when prices sag, so
capture price typically sits *below* ATC — the "solar capture discount".

Pure-Python (no Streamlit); the page wires the UI around ``value_plant``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

from ercot_core import paths

# Asset-registry hub names → forecast-engine hub codes. PAN/BUSAVG/HUBAVG never
# appear in the curated renewable registry, so they are intentionally absent.
HUB_MAP = {
    "North": "HB_NORTH",
    "Houston": "HB_HOUSTON",
    "South": "HB_SOUTH",
    "West": "HB_WEST",
    # The Panhandle is in the North load zone and ERCOT's HB_PAN settlement point
    # is not carried by the price-forecast engine, so value Panhandle wind against
    # HB_NORTH — the conventional proxy. (Slightly optimistic: it omits Panhandle
    # export congestion, which can depress realized Pan prices vs. North.)
    "Pan": "HB_NORTH",
}


def to_hub_code(asset_hub: str) -> str:
    """Map a registry hub label ("North") to a forecast hub code ("HB_NORTH")."""
    key = str(asset_hub or "").strip().title()
    if key in HUB_MAP:
        return HUB_MAP[key]
    code = str(asset_hub or "").strip().upper()
    if code.startswith("HB_"):
        return code  # already a code
    raise ValueError(f"Unknown hub {asset_hub!r}; expected one of {list(HUB_MAP)}")


def _ensure_engines_on_path() -> None:
    """Put the solar engine + sibling price-forecast engine on sys.path.

    Mirrors the env wiring page 16 does: route the price engine's data lake at
    the Hub's shared ``hub_prices`` store and cache its artifacts under the Hub.
    """
    from ercot_core import bootstrap

    bootstrap.setup_path()  # adds datasets/solar_forecast (→ import solar_pvwatts)

    repo = paths.ROOT.parent / "Eroct_forecasts"
    if not repo.exists():
        raise FileNotFoundError(
            f"Price-forecast engine not found at {repo}. Keep `Eroct_forecasts` "
            "as a sibling of `Ercot_Data_Hub`."
        )
    os.environ.setdefault("PF_DATA", str(paths.DATA / "price_forecast"))
    os.environ.setdefault("PF_HUB_LAKE_DIR", str(paths.HUB_PRICES_DIR))
    s = str(repo)
    if s not in sys.path:
        sys.path.insert(0, s)


def load_assets(tech: str, path: str | Path | None = None) -> list[dict]:
    """Plants of one ``tech`` ("solar"/"wind") from the curated registry.

    Each record carries ``resource_name, capacity_mw, hub, lat, lon, county``
    plus, for solar, optional ``dc_ac_ratio, solar_gcr, tracking_type``.
    """
    p = Path(path or paths.PRICE_SETTLEMENTS_ASSETS)
    if not p.exists():
        raise FileNotFoundError(f"Asset registry not found at {p}")
    raw = json.loads(p.read_text())
    want = str(tech).strip().lower()
    out = []
    for name, rec in raw.items():
        if str(rec.get("tech", "")).strip().lower() != want:
            continue
        r = dict(rec)
        r.setdefault("project_name", name)
        out.append(r)
    out.sort(key=lambda r: str(r.get("project_name") or r.get("resource_name", "")))
    return out


def load_solar_assets(path: str | Path | None = None) -> list[dict]:
    """Solar plants from the curated registry, sorted by project name."""
    return load_assets("solar", path)


def load_wind_assets(path: str | Path | None = None) -> list[dict]:
    """Wind plants from the curated registry, sorted by project name."""
    return load_assets("wind", path)


def system_config_from_asset(asset: dict):
    """Build the PVWatts ``SystemConfig`` from a registry record.

    Single-axis tracking is inferred from ``tracking_type``; ``dc_ac_ratio`` and
    GCR are passed through with sensible solar defaults when absent. (The engine
    hard-codes the tracker max rotation at 60°, so ``solar_max_angle`` is not
    plumbed through here.)
    """
    _ensure_engines_on_path()
    import solar_pvwatts as sf

    tracking = str(asset.get("tracking_type", "") or "").lower()
    is_tracker = any(k in tracking for k in ("axis", "single", "track"))
    ratio = float(asset.get("dc_ac_ratio") or 1.3)
    return sf.SystemConfig(
        capacity_kw_dc=float(asset["capacity_mw"]) * 1000.0 * ratio,
        array_type="1-Axis Tracker" if is_tracker else "Fixed - Open Rack",
        dc_ac_ratio=ratio,
        gcr=float(asset.get("solar_gcr") or 0.35),
    )


def _value_grid(gen_hourly: pd.DataFrame, price_8760: pd.DataFrame, bands: list[str]):
    """(year, month, hour) grid of price bands + bucket generation.

    The typical-year solar calendar won't line up with future price years, so we
    align on a **(month, hour-of-day)** profile: average generation per
    month-hour (``ac_kw`` over 1 h = kWh, /1000 = MWh), average each price band
    per (year, month, hour), and carry the real hour count ``n`` so a bucket's
    total generation = avg × n. Returns the merged DataFrame.
    """
    g = pd.DataFrame({
        "month": gen_hourly.index.month,
        "hour": gen_hourly.index.hour,
        "gen_mwh": gen_hourly["ac_kw"].to_numpy() / 1000.0,
    })
    gen_grid = g.groupby(["month", "hour"], as_index=False)["gen_mwh"].mean()

    p = price_8760.copy()
    ts = pd.to_datetime(p["ts"])
    p["year"] = ts.dt.year
    p["hour"] = ts.dt.hour
    primary = "p50" if "p50" in bands else bands[0]
    price_grid = (p.groupby(["year", "month", "hour"])
                  .agg(n=(primary, "size"), **{b: (b, "mean") for b in bands})
                  .reset_index())

    merged = price_grid.merge(gen_grid, on=["month", "hour"], how="left")
    merged["gen_mwh"] = merged["gen_mwh"].fillna(0.0)
    merged["gen_total"] = merged["gen_mwh"] * merged["n"]  # MWh in that bucket
    return merged


def _aggregate(merged: pd.DataFrame, bands: list[str], keys: list[str]) -> pd.DataFrame:
    """Roll a value grid up to ``keys`` (e.g. ['year'] or ['year','month'])."""
    primary = "p50" if "p50" in bands else bands[0]
    rows = []
    for key_vals, g in merged.groupby(keys):
        key_vals = key_vals if isinstance(key_vals, tuple) else (key_vals,)
        gen_total = float(g["gen_total"].sum())
        n_total = float(g["n"].sum())
        # ATC = hours-weighted average P50 (== simple mean over all hours).
        atc = float((g[primary] * g["n"]).sum() / n_total) if n_total else float("nan")
        row = {k: int(v) for k, v in zip(keys, key_vals)}
        row.update({"atc_p50": atc, "gen_mwh": gen_total, "hours": int(n_total)})
        for b in bands:
            rev = float((g[b] * g["gen_total"]).sum())
            row[f"revenue_{b}"] = rev
            row[f"capture_{b}"] = rev / gen_total if gen_total else float("nan")
        row["capture_ratio"] = (row[f"capture_{primary}"] / atc if atc else float("nan"))
        rows.append(row)
    return pd.DataFrame(rows).sort_values(keys).reset_index(drop=True)


def _clean_bands(price_8760: pd.DataFrame, bands) -> list[str]:
    out = [b for b in bands if b in price_8760.columns]
    if not out:
        raise ValueError("price_8760 carries none of the requested band columns")
    return out


def capture_by_year(gen_hourly: pd.DataFrame, price_8760: pd.DataFrame,
                    *, bands=("p10", "p50", "p90")) -> pd.DataFrame:
    """Generation-weighted capture price + revenue per forecast **year**.

    One row per year: ``atc_p50``, ``capture_{band}``, ``revenue_{band}``,
    ``capture_ratio`` (capture_p50 / atc_p50), ``gen_mwh``, ``hours``.
    """
    bands = _clean_bands(price_8760, bands)
    return _aggregate(_value_grid(gen_hourly, price_8760, bands), bands, ["year"])


def capture_by_month(gen_hourly: pd.DataFrame, price_8760: pd.DataFrame,
                     *, bands=("p10", "p50", "p90")) -> pd.DataFrame:
    """Same as :func:`capture_by_year`, but one row per (year, month)."""
    bands = _clean_bands(price_8760, bands)
    return _aggregate(_value_grid(gen_hourly, price_8760, bands), bands, ["year", "month"])


def add_net_settlement(table: pd.DataFrame, strike: float,
                       *, band: str = "p50", share: float = 1.0) -> pd.DataFrame:
    """Add CfD/swap settlement columns for a fixed PPA ``strike`` ($/MWh).

    ``share`` is the offtaker's contracted fraction of plant output (0–1) — only
    that volume settles under the PPA; the rest is sold merchant.

    Signed **from the offtaker's perspective** (the buyer in a virtual PPA), to
    match the Wind Capture page: net = Σ contracted_gen × (capture − strike) —
    positive ⇒ the offtaker receives (market/capture above strike), negative ⇒
    the offtaker pays (tops the generator up to the strike). This is the negative
    of the generator-centric ``ercot_core.settlement`` / invoice convention. Adds
    ``contracted_mwh``, ``ppa_revenue`` (strike × contracted), ``merchant_value``
    (capture × contracted) and ``net_settlement``.
    """
    out = table.copy()
    vol = out["gen_mwh"] * float(share)
    out["contracted_mwh"] = vol
    out["merchant_value"] = out[f"capture_{band}"] * vol
    out["ppa_revenue"] = float(strike) * vol
    out["net_settlement"] = out["merchant_value"] - out["ppa_revenue"]
    return out


# --- caching --------------------------------------------------------------

def _solar_cache_path(asset: dict, cfg, year: str) -> Path:
    res = str(asset["resource_name"])
    return paths.PLANT_VALUE_DIR / f"gen_{res}_{year}_{int(cfg.capacity_kw_dc)}kw.parquet"


def _load_or_run_solar(asset: dict, cfg, year: str,
                       api_key: str | None, email: str | None,
                       use_cache: bool) -> pd.DataFrame:
    import solar_pvwatts as sf  # engines already on path via caller

    cp = _solar_cache_path(asset, cfg, year)
    if use_cache and cp.exists():
        return pd.read_parquet(cp)

    # EIA-anchored path (opt-in per node, keyless): when this solar node has an
    # EIA-923 anchor, build the typical year from ERA5 + PVWatts and anchor each
    # month's CF to the EIA P50. No NREL/NSRDB key needed, and the seasonal shape
    # + any small irradiance bias are pinned to the plant's realized history.
    from ercot_core import eia_anchor
    _node = asset.get("resource_node") or asset.get("resource_name")
    _targets = eia_anchor.monthly_cf_targets(_node, "p50") if _node else None
    if _targets:
        s_str, e_str, _ = _wind_year_range(year)
        wx = sf.fetch_weather_era5(float(asset["lat"]), float(asset["lon"]), s_str, e_str)
        raw = sf.run_pvwatts(wx, cfg)
        ac_nameplate_mw = cfg.capacity_kw_dc / 1000.0 / cfg.dc_ac_ratio
        cf = (raw["ac_kw"] / 1000.0 / ac_nameplate_mw).clip(lower=0.0) if ac_nameplate_mw else raw["ac_kw"] * 0.0
        cf = _anchor_to_monthly_cf(cf, _targets, {}, clamp=(0.5, 2.0))
        gen = pd.DataFrame({"ac_kw": cf.to_numpy() * ac_nameplate_mw * 1000.0}, index=raw.index)
        try:
            paths.PLANT_VALUE_DIR.mkdir(parents=True, exist_ok=True)
            gen.to_parquet(cp)
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass
        return gen

    if not api_key or not email:
        raise RuntimeError(
            "NREL api_key + email required to fetch solar weather "
            "(set nrel_api_key / nrel_email in config.json or pass them in)."
        )
    wx = sf.fetch_weather(float(asset["lat"]), float(asset["lon"]),
                          api_key, email, year=year)
    gen = sf.run_pvwatts(wx, cfg)
    try:
        paths.PLANT_VALUE_DIR.mkdir(parents=True, exist_ok=True)
        gen.to_parquet(cp)
    except Exception:  # noqa: BLE001 — caching is best-effort
        pass
    return gen


def build_typical_profile(asset: dict, *, api_key: str | None = None,
                          email: str | None = None, force: bool = False):
    """Build (and cache) the typical-year 8,760-h profile a portal expects.

    This is the "plant-value step" the portals need before their calibrated model
    lights up — wire it into a portal's refresh so a new portal is ready without a
    manual Hub run. Caches at the path the portal globs for: wind →
    ``windgen_{resource_node}_…mw``; solar → ``gen_{resource_name}_…kw``. Wind uses
    the keyless ERA5+physics engine (EIA-anchored when available); solar uses the
    keyless ERA5+PVWatts path when the node has an EIA anchor, else needs NREL keys.
    Returns the hourly ``ac_kw`` frame, or None on failure (best-effort).
    """
    _ensure_engines_on_path()
    tech = str(asset.get("tech", "")).lower()
    node = asset.get("resource_node") or asset.get("resource_name")
    try:
        if "solar" in tech or "pv" in tech:
            cfg = system_config_from_asset(asset)
            from ercot_core import eia_anchor  # noqa: PLC0415
            _a = eia_anchor.load(node) if node else None
            _mature = bool(_a) and int(_a.get("n_months", 0)) >= 9
            if _mature or (api_key and email):
                # enough history → EIA-anchored (or NSRDB if keyed)
                return _load_or_run_solar(asset, cfg, "tmy", api_key, email, use_cache=not force)
            # Young/pre-COD plant: a <9-month anchor would be season-skewed
            # (a Feb-COD plant reads winter-only). Use a RAW ERA5+PVWatts typical
            # year instead — the physically-correct full-year shape at nameplate,
            # uncalibrated but seasonally sound. Keyless; every portal gets one.
            import solar_pvwatts as sf  # noqa: PLC0415
            cp = _solar_cache_path(asset, cfg, "tmy")
            if not force and cp.exists():
                return pd.read_parquet(cp)
            s_str, e_str, _ = _wind_year_range("tmy")
            wx = sf.fetch_weather_era5(float(asset["lat"]), float(asset["lon"]), s_str, e_str)
            gen = sf.run_pvwatts(wx, cfg)
            try:
                paths.PLANT_VALUE_DIR.mkdir(parents=True, exist_ok=True)
                gen.to_parquet(cp)
            except Exception:  # noqa: BLE001
                pass
            return gen
        # wind: align the cache filename to the portal's glob (resource_node)
        a2 = dict(asset)
        a2["resource_name"] = asset.get("resource_node") or asset["resource_name"]
        gen, _meta = _load_or_run_wind(a2, "tmy", use_cache=not force)
        return gen
    except Exception:  # noqa: BLE001 — best-effort; portal falls back to history
        return None


def _wind_cache_path(asset: dict, year: str) -> Path:
    res = str(asset["resource_name"])
    return paths.PLANT_VALUE_DIR / f"windgen_{res}_{year}_{int(asset['capacity_mw'])}mw.parquet"


def _build_wind_fleet(asset: dict):
    """Real USWTDB turbine fleet nearest the plant; generic fallback if none.

    Returns ``(FleetConfig, fleet_capacity_mw, fleet_name | None, distance_km | None)``.
    The fleet sets the *shape* (turbine physics); magnitude is scaled to the
    registry nameplate later, so generation never depends on the detected count.
    """
    import turbine_db as tdb
    import wind_power as wp

    f = tdb.find_project_near(float(asset["lat"]), float(asset["lon"]), radius_km=15.0)
    if f is not None and f.segments:
        # USWTDB TurbineSegment → engine TurbineSpec (mirrors the Wind Forecast page).
        segs = [wp.TurbineSpec(
            count=int(s.count), rated_kw=float(s.rated_kw),
            hub_height_m=float(s.hub_height_m), rotor_m=float(s.rotor_m or 120),
            curve_key=getattr(s, "curve_key", "GENERIC_IEC2"),
            label=f"{s.manufacturer} {s.model}".strip() or "turbine",
        ) for s in f.segments if int(s.count) > 0]
        if segs:
            return wp.FleetConfig(segments=segs), float(f.capacity_mw), f.name, round(f.distance_km, 1)
    # Generic fleet sized to the nameplate (≈2.5 MW class turbines).
    n = max(1, int(round(float(asset["capacity_mw"]) / 2.5)))
    fc = wp.FleetConfig(segments=[wp.TurbineSpec(count=n, rated_kw=2500.0)])
    return fc, fc.capacity_mw, None, None


def _wind_year_range(year: str) -> tuple[str, str, str]:
    y = str(year).strip()
    if not y.isdigit():
        y = "2024"  # ERA5 has no TMY; default to a recent complete year
    return f"{y}-01-01", f"{y}-12-31", y


def _sced_actuals_hourly(asset: dict) -> pd.Series | None:
    """Plant-total metered SCED output as an hourly mean-MW series, or None.

    Used to site-anchor the modelled wind profile. The units come from the
    asset's optional ``sced_units`` list — an aggregate resource (e.g.
    ``AZURE_SKY_WIND_AGG``) maps to its constituent SCED units; a single-node
    plant falls back to its own ``resource_name``. Sub-hourly telemetry is
    floored to the hour in **UTC** (DST-safe), averaged per unit, summed across
    units, then expressed in tz-aware Central to align with the model index.
    Returns None when no SCED files are present (most registry plants).
    """
    from ercot_core import tz

    units = [u for u in (asset.get("sced_units") or [asset.get("resource_name")]) if u]
    frames = []
    for u in units:
        for p in sorted(paths.PLANT_DATA_DIR.glob(f"{u}_*.parquet")):
            try:
                frames.append(pd.read_parquet(
                    p, columns=["resource_name", "sced_timestamp",
                                "telemetered_net_output"]))
            except Exception:  # noqa: BLE001 — skip unreadable/short files
                continue
    if not frames:
        return None
    g = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(g["sced_timestamp"])
    ts = ts.dt.tz_convert("UTC") if ts.dt.tz is not None else ts.dt.tz_localize("UTC")
    g = g.assign(_hr=ts.dt.floor("h"))
    per_unit = g.groupby(["_hr", "resource_name"])["telemetered_net_output"].mean()
    plant = per_unit.groupby(level=0).sum()          # sum units → plant MW/hour
    if plant.empty:
        return None
    plant.index = plant.index.tz_convert(tz.CENTRAL)
    plant.name = "actual_mw"
    return plant


def _calendar_anchor(modeled_cf: pd.Series, actual_cf: pd.Series,
                     meta: dict, clamp=(0.3, 5.0)) -> pd.Series:
    """Anchor a typical-year **capacity-factor** profile to metered CF by month.

    Operates on capacity factor (0–1), not raw MW: the modelled series is at the
    detected fleet capacity while metered SCED is at nameplate, so the two are
    only comparable once normalised. Year-agnostic on purpose — the modelled
    weather year (e.g. 2024) and the metered record rarely share a calendar year,
    so a timestamp join would find no overlap. Each modelled month is scaled so
    its mean CF matches the actual mean CF for that calendar month (averaged over
    whatever years of metered data exist); months without actuals fall back to the
    overall ratio. Factors are clamped and recorded in ``meta["sced_anchor"]``.
    """
    m = pd.to_numeric(modeled_cf, errors="coerce").dropna()
    a = pd.to_numeric(actual_cf, errors="coerce").dropna()
    if m.empty or a.empty or m.mean() <= 0:
        return modeled_cf
    lo, hi = clamp
    clip = lambda v: max(lo, min(hi, float(v)))
    m_mon = m.groupby(m.index.month).mean()
    a_mon = a.groupby(a.index.month).mean()
    overall = clip(a.mean() / m.mean())
    factors = {mo: (clip(a_mon[mo] / m_mon[mo]) if mo in a_mon.index and m_mon.get(mo, 0) > 0
                    else overall) for mo in range(1, 13)}
    out = modeled_cf.astype(float) * modeled_cf.index.month.map(factors)
    meta["sced_anchor"] = {"overall_factor": round(overall, 4),
                           "monthly_factors": {k: round(v, 3) for k, v in factors.items()},
                           "actual_hours": int(len(a))}
    return out.clip(lower=0.0, upper=1.0)


def _anchor_to_monthly_cf(modeled_cf: pd.Series, targets: dict, meta: dict,
                          clamp=(0.3, 5.0)) -> pd.Series:
    """Anchor a typical-year CF profile to a per-calendar-month CF target dict.

    Like :func:`_calendar_anchor` but driven by an external monthly CF target
    (e.g. the EIA-923 long-history P50) rather than an hourly metered series.
    Each modelled month is scaled so its mean CF matches the target for that
    calendar month; months without a target use the overall ratio.
    """
    m = pd.to_numeric(modeled_cf, errors="coerce").dropna()
    if m.empty or not targets:
        return modeled_cf
    lo, hi = clamp
    clip = lambda v: max(lo, min(hi, float(v)))
    m_mon = m.groupby(m.index.month).mean()
    overall_t = sum(targets.values()) / len(targets)
    overall = clip(overall_t / m.mean()) if m.mean() > 0 else 1.0
    factors = {mo: (clip(targets[mo] / m_mon[mo])
                    if mo in targets and m_mon.get(mo, 0) > 0 else overall)
               for mo in range(1, 13)}
    out = modeled_cf.astype(float) * modeled_cf.index.month.map(factors)
    meta["eia_anchor"] = {"source": "EIA-923 P50", "overall_factor": round(overall, 4),
                          "monthly_factors": {k: round(v, 3) for k, v in factors.items()}}
    return out.clip(lower=0.0, upper=1.0)


def _load_or_run_wind(asset: dict, year: str, use_cache: bool) -> tuple[pd.DataFrame, dict]:
    """Hourly wind generation (``ac_kw`` indexed by local time) + fleet metadata.

    Builds an ERA5-driven 8760 from the nearest real turbine fleet, expressed as a
    capacity factor and rescaled to the registry nameplate. Fleet metadata is
    recomputed on cache hits (a cheap local USWTDB lookup) so the UI can show it.
    """
    import wind_calibration as cal
    import wind_power as wp

    fc, fcap, fname, fdist = _build_wind_fleet(asset)
    meta = {"fleet_name": fname, "fleet_distance_km": fdist,
            "fleet_capacity_mw": round(fcap, 1), "n_segments": len(fc.segments)}

    cp = _wind_cache_path(asset, year)
    if use_cache and cp.exists():
        return pd.read_parquet(cp), meta

    start, end, _ = _wind_year_range(year)
    wx = wp.fetch_weather_era5(float(asset["lat"]), float(asset["lon"]), start, end)

    # Speed-space bias correction BEFORE the power curve, from the learned
    # per-region seasonal prior (build_ws_scale.py). This fixes ERA5's hub-wind
    # under-resolution physically; because it does the level correction, we drop
    # the crude post-hoc region energy multiplier (use_bias=False) to avoid
    # double-counting, keeping only the SCED hour-shape priors.
    ws_k = cal.ws_scale_for(lat=float(asset["lat"]), lon=float(asset["lon"]),
                            hub_name=asset.get("hub"))
    raw = wp.run_wind(wx, fc, ws_scale=ws_k)
    meta["ws_scale"] = ws_k

    # run_wind returns raw physics; the engine is designed to layer calibration
    # on top (see wind_power's module docstring). (1) The ws_scale prior + SCED
    # hour-shape priors correct the hub-level bias with no actuals. (2) If this
    # plant has metered SCED output, anchor the whole profile to it so the cached
    # typical year reflects realized availability/curtailment rather than raw
    # physics — without it a high-CF North site like Azure reads ~21% not ~39%.
    net = cal.apply_region_priors(raw["net_mw"], capacity_mw=fcap,
                                  lat=float(asset["lat"]), lon=float(asset["lon"]),
                                  hub_name=asset.get("hub"), use_bias=False)
    cf = (net / fcap).clip(lower=0.0) if fcap else net * 0.0
    nameplate_mw = float(asset["capacity_mw"])

    # Prefer the EIA-923 long-history anchor when one exists for this node: it is
    # a clean ~9-yr monthly CF distribution, immune to the partial-unit / recent-
    # window problems of the SCED record (e.g. Mirasole's per-unit SCED files only
    # go back to 2024-25 and under-count the plant). Opt-in per site — nodes with
    # no anchor file fall through to the SCED anchor below, unchanged.
    from ercot_core import eia_anchor
    _node = asset.get("resource_node") or asset.get("resource_name")
    _anchor = eia_anchor.load(_node) if _node else None
    _eia_targets = eia_anchor.monthly_cf_targets(_node, "p50") if _node else None
    if _anchor and _eia_targets and nameplate_mw > 0:
        # Physical-first: lift the raw ERA5 winds by the site's fitted hub-wind
        # bias (×ws_speed_correction) and re-run the power curve, so the bulk
        # under-prediction is corrected through the (nonlinear) curve rather than
        # by an extreme post-hoc scalar. The residual monthly anchor to the EIA
        # P50 is then small. Falls back to a raw scale if no ws correction.
        ws_k = float(_anchor.get("ws_speed_correction") or 1.0)
        if ws_k and ws_k != 1.0:
            wx2 = wx.data.copy()
            wx2["ws10"] *= ws_k
            wx2["ws100"] *= ws_k
            corr = wp.WeatherResult(data=wx2, metadata=wx.metadata, label=wx.label,
                                    latitude=wx.latitude, longitude=wx.longitude,
                                    sources=wx.sources)
            net_c = cal.apply_region_priors(wp.run_wind(corr, fc)["net_mw"],
                                            capacity_mw=fcap, lat=float(asset["lat"]),
                                            lon=float(asset["lon"]), hub_name=asset.get("hub"))
            cf = (net_c / fcap).clip(lower=0.0) if fcap else cf
        meta["eia_ws_correction"] = ws_k
        cf = _anchor_to_monthly_cf(cf, _eia_targets, meta, clamp=(0.5, 2.5))
        gen = pd.DataFrame({"ac_kw": cf.to_numpy() * nameplate_mw * 1000.0}, index=raw.index)
        try:
            paths.PLANT_VALUE_DIR.mkdir(parents=True, exist_ok=True)
            gen.to_parquet(cp)
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass
        return gen, meta

    # Anchor to metered SCED — but only when the metered record is sane. The
    # registry has a few mislabelled/partial wind entries whose SCED reads an
    # implausible CF; anchoring to those would corrupt an otherwise good profile,
    # so gate on hours and a plausible wind CF band and fall back to region priors.
    actual = _sced_actuals_hourly(asset)
    if actual is not None and not actual.empty and nameplate_mw > 0:
        act_cf = (actual / nameplate_mw).clip(lower=0.0)
        hrs, mean_cf = int(len(act_cf)), float(act_cf.mean())
        if hrs >= 2000 and 0.15 <= mean_cf <= 0.60:
            cf = _calendar_anchor(cf, act_cf, meta)
        else:
            meta["sced_anchor_skipped"] = {
                "actual_hours": hrs, "actual_cf": round(mean_cf, 3),
                "reason": "too few hours" if hrs < 2000 else "implausible CF — SCED mapping/data suspect"}
    gen = pd.DataFrame({"ac_kw": cf.to_numpy() * nameplate_mw * 1000.0}, index=raw.index)
    try:
        paths.PLANT_VALUE_DIR.mkdir(parents=True, exist_ok=True)
        gen.to_parquet(cp)
    except Exception:  # noqa: BLE001 — caching is best-effort
        pass
    return gen, meta


def _gen_summary(gen: pd.DataFrame, nameplate_mw: float, cf_label: str) -> dict:
    """Annualized generation summary from an hourly ``ac_kw`` series.

    Annualizes the mean so partial weather windows (ERA5 runs to ~5 days ago)
    still yield a representative annual figure.
    """
    mean_kw = float(gen["ac_kw"].mean()) if len(gen) else float("nan")
    cf = (mean_kw / 1000.0 / nameplate_mw) if nameplate_mw else float("nan")
    return {"annual_mwh": cf * nameplate_mw * 8760.0, "capacity_factor": cf,
            "cf_label": cf_label}


def _value_wind(asset: dict, *, asof=None, horizon_months: int = 36,
                year: str = "2024", n_sims: int = 5000,
                use_cache: bool = True) -> dict:
    """Value one wind plant: ERA5/USWTDB generation × its hub's price forecast.

    Mirrors :func:`value_plant` (solar) and returns the same dict shape, so the
    page renders both with one code path.
    """
    _ensure_engines_on_path()
    import forecast
    import pf_history
    import shape as shaping

    paths.ensure_dirs()
    hub_code = to_hub_code(asset["hub"])
    gen, fleet_meta = _load_or_run_wind(asset, year, use_cache)

    curve, pmeta = forecast.run(hub_code, asof=asof,
                                horizon_months=horizon_months, n_sims=n_sims)
    rt15 = pf_history.load_rt15(hub_code)
    price_8760 = shaping.build_8760(curve, rt15)

    grid = _value_grid(gen, price_8760, ["p10", "p50", "p90"])
    by_year = _aggregate(grid, ["p10", "p50", "p90"], ["year"])
    by_month = _aggregate(grid, ["p10", "p50", "p90"], ["year", "month"])
    _save_result(asset, hub_code, asof, by_year)

    return {
        "asset": asset,
        "hub_code": hub_code,
        "system": None,
        "gen_summary": _gen_summary(gen, float(asset["capacity_mw"]), "net"),
        "fleet_meta": fleet_meta,
        "by_year": by_year,
        "by_month": by_month,
        "price_meta": pmeta,
        "weather_year": year,
    }


def _save_result(asset: dict, hub_code: str, asof, by_year: pd.DataFrame) -> Path:
    res = str(asset["resource_name"])
    tag = str(asof or "today").replace("-", "")
    fp = paths.PLANT_VALUE_DIR / f"value_{res}_{hub_code}_{tag}.parquet"
    try:
        by_year.to_parquet(fp)
    except Exception:  # noqa: BLE001
        pass
    return fp


def value_plant(asset: dict, *, asof=None, horizon_months: int = 36,
                year: str = "tmy", api_key: str | None = None,
                email: str | None = None, n_sims: int = 5000,
                use_cache: bool = True) -> dict:
    """Value one plant: generation shape × its hub's hourly price forecast.

    Dispatches on ``asset["tech"]`` — wind routes to :func:`_value_wind`; solar
    runs PVWatts here. Both return ``{asset, hub_code, gen_summary, by_year,
    by_month, price_meta, weather_year, ...}`` so the page renders either.
    """
    if str(asset.get("tech", "")).strip().lower() == "wind":
        return _value_wind(asset, asof=asof, horizon_months=horizon_months,
                           year=year, n_sims=n_sims, use_cache=use_cache)

    _ensure_engines_on_path()
    import solar_pvwatts as sf
    import forecast
    import pf_history
    import shape as shaping

    paths.ensure_dirs()
    hub_code = to_hub_code(asset["hub"])
    cfg = system_config_from_asset(asset)

    gen = _load_or_run_solar(asset, cfg, year, api_key, email, use_cache)

    curve, pmeta = forecast.run(hub_code, asof=asof,
                                horizon_months=horizon_months, n_sims=n_sims)
    rt15 = pf_history.load_rt15(hub_code)
    price_8760 = shaping.build_8760(curve, rt15)

    grid = _value_grid(gen, price_8760, ["p10", "p50", "p90"])
    by_year = _aggregate(grid, ["p10", "p50", "p90"], ["year"])
    by_month = _aggregate(grid, ["p10", "p50", "p90"], ["year", "month"])
    _save_result(asset, hub_code, asof, by_year)

    ss = sf.summarize(gen, cfg)
    return {
        "asset": asset,
        "hub_code": hub_code,
        "system": cfg,
        "solar_summary": ss,
        "gen_summary": {"annual_mwh": ss["annual_mwh"],
                        "capacity_factor": ss["capacity_factor_dc"], "cf_label": "DC"},
        "by_year": by_year,
        "by_month": by_month,
        "price_meta": pmeta,
        "weather_year": year,
    }
