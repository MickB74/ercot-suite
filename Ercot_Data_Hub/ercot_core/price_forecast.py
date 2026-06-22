"""Price-forecast shim: wraps the standalone Eroct_forecasts engine.

The Eroct_forecasts repo (sibling of Ercot_Data_Hub) produces a monthly
power-price forecast for ERCOT hubs via market-implied heat-rate × gas strip
+ Monte Carlo. This module gives the portals one import to:

  • locate the sibling repo and put it on ``sys.path``
  • return a tidy ``month / p10 / p50 / p90`` DataFrame for one hub
  • scale that hub forecast to the asset's *capture* price via a historical ratio

Disk-cached forecasts under ``Eroct_forecasts/data/forecasts/`` are used when
the (hub, asof) pair already exists — a fresh Monte Carlo run takes seconds, a
cache hit is instant.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_THIS = Path(__file__).resolve()
_FLEET_CAPTURE_PATH = _THIS.parent / "registry" / "fleet_capture_ratios.json"


def fleet_capture_ratios(hub: str) -> dict[int, float]:
    """Load precomputed fleet solar capture-to-hub ratios for ``hub``.

    Returns ``{cal_month: ratio}`` from the curated JSON file, or an empty
    dict if the file is missing or the hub isn't covered.
    """
    try:
        data = json.loads(_FLEET_CAPTURE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    raw = data.get(hub, {})
    return {int(k): float(v) for k, v in raw.items()}


def _engine_root() -> Path:
    """Find the sibling Eroct_forecasts repo. Raises if not present."""
    candidates = [
        _THIS.parents[2] / "Eroct_forecasts",  # ercot-suite/Eroct_forecasts
        Path.home() / "Documents" / "Github" / "ercot-suite" / "Eroct_forecasts",
    ]
    for c in candidates:
        if (c / "forecast.py").exists():
            return c
    raise FileNotFoundError(
        "Could not find Eroct_forecasts/forecast.py. Tried:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def _wire_engine() -> None:
    p = str(_engine_root())
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_curve(hub: str, asof: str, horizon_months: int, n_sims: int) -> pd.DataFrame:
    """Cached load or fresh Monte Carlo. Returns the full forecast curve."""
    _wire_engine()
    import forecast_store  # type: ignore  # noqa: PLC0415
    try:
        curve, _meta = forecast_store.load(hub, asof)
        return curve
    except (FileNotFoundError, OSError):
        pass
    import forecast as _engine  # type: ignore  # noqa: PLC0415
    curve, meta = _engine.run(hub=hub, asof=asof,
                              horizon_months=horizon_months, n_sims=n_sims)
    try:
        forecast_store.save(curve, meta)
    except Exception:  # noqa: BLE001 — save is opportunistic
        pass
    return curve


def monthly_band(hub: str, *, asof=None, horizon_months: int = 12,
                 n_sims: int = 2000,
                 capture_to_hub: float | dict[int, float] = 1.0) -> pd.DataFrame:
    """Monthly P10/P50/P90 forecast for ``hub`` from now over ``horizon_months``.

    Returns columns ``Month`` ("YYYY-MM"), ``month`` (Timestamp, first-of-month,
    naive Central), ``p10`` / ``p50`` / ``p90`` ($/MWh). All three percentiles
    are multiplied by ``capture_to_hub``, so when that ratio comes from history
    the output is already in capture-price units the Future Bill page expects.

    ``capture_to_hub`` can be a single float (applied to every month) or a
    ``{calendar_month: ratio}`` dict for per-month capture adjustment. Months
    missing from the dict fall back to the median of the available ratios.

    The engine reports per-block (atc/peak/offpeak); we use ATC — the round-the-
    clock mean — since the portal projections are monthly net P&L.
    """
    asof = str(pd.Timestamp(asof).date() if asof else pd.Timestamp.today().date())
    curve = _load_curve(hub, asof, horizon_months, n_sims)
    atc = curve[curve["block"] == "atc"][["month", "p10", "p50", "p90"]].copy()
    atc["month"] = pd.to_datetime(atc["month"])
    atc = atc.sort_values("month").reset_index(drop=True).head(horizon_months)
    if isinstance(capture_to_hub, dict) and capture_to_hub:
        import statistics
        fallback = statistics.median(capture_to_hub.values())
        ratios = atc["month"].dt.month.map(
            lambda cm: capture_to_hub.get(cm, fallback))
        for col in ("p10", "p50", "p90"):
            atc[col] = atc[col] * ratios
    else:
        ratio = float(capture_to_hub) if capture_to_hub else 1.0
        for col in ("p10", "p50", "p90"):
            atc[col] = atc[col] * ratio
    atc["Month"] = atc["month"].dt.strftime("%Y-%m")
    return atc[["Month", "month", "p10", "p50", "p90"]]


def capture_to_hub_ratio(monthly_breakdown: pd.DataFrame,
                          hub_intervals: pd.DataFrame,
                          *, price_col: str = "spp") -> float:
    """Historical mean(capture $/MWh) ÷ mean(hub time-weighted $/MWh).

    ``monthly_breakdown`` is the portal's per-month frame with ``Market_value``
    and ``MWh``; ``hub_intervals`` is the 15-min hub price history over the same
    span. Returns 1.0 when either side is missing or zero.

    The denominator is a simple time-mean (hub ATC = what the forecast also
    reports), not MWh-weighted. So multiplying the forecast ATC P50 by this
    ratio yields the expected per-MWh price the offtaker actually realizes — it
    folds in node-to-hub basis *and* the asset's generation timing.
    """
    if monthly_breakdown is None or monthly_breakdown.empty:
        return 1.0
    needed = {"Market_value", "MWh"}
    if not needed.issubset(monthly_breakdown.columns):
        return 1.0
    mwh = float(monthly_breakdown["MWh"].sum())
    if mwh <= 0:
        return 1.0
    capture = float(monthly_breakdown["Market_value"].sum()) / mwh
    if hub_intervals is None or hub_intervals.empty or price_col not in hub_intervals.columns:
        return 1.0
    hub_atc = float(pd.to_numeric(hub_intervals[price_col], errors="coerce").mean())
    if not hub_atc or hub_atc <= 0:
        return 1.0
    return capture / hub_atc


def capture_to_hub_monthly(monthly_breakdown: pd.DataFrame,
                            hub_intervals: pd.DataFrame,
                            *, price_col: str = "spp",
                            cal_months: pd.Series | None = None,
                            fleet_fallback: dict[int, float] | None = None,
                            ) -> dict[int, float]:
    """Per-calendar-month capture-to-hub ratios.

    Like :func:`capture_to_hub_ratio` but returns ``{cal_month: ratio}`` so
    summer and spring aren't blended into one number. Each month's capture
    (market_value / MWh) is divided by that month's hub ATC.

    ``cal_months`` is a Series aligned to ``monthly_breakdown`` giving the
    calendar month (1–12) for each row. If not provided, falls back to
    ``monthly_breakdown["cal_month"]``.

    ``fleet_fallback`` is an optional ``{cal_month: ratio}`` dict from a
    broader fleet of similar assets (e.g. all solar in the same hub zone).
    Months 1–12 not observed in ``monthly_breakdown`` are filled from this
    dict, so newly-online assets get seasonally-shaped capture from day one
    instead of a flat median.

    Returns an empty dict (which ``monthly_band`` treats as 1.0 everywhere)
    when inputs are missing.
    """
    if monthly_breakdown is None or monthly_breakdown.empty:
        return {}
    needed = {"Market_value", "MWh"}
    if not needed.issubset(monthly_breakdown.columns):
        return {}
    if cal_months is None:
        if "cal_month" not in monthly_breakdown.columns:
            return {}
        cal_months = monthly_breakdown["cal_month"]

    if hub_intervals is None or hub_intervals.empty or price_col not in hub_intervals.columns:
        return {}
    hp = pd.to_numeric(hub_intervals[price_col], errors="coerce")
    ts_col = next((c for c in ("interval_start", "timestamp", "datetime")
                   if c in hub_intervals.columns), None)
    if ts_col:
        hub_month = pd.to_datetime(hub_intervals[ts_col]).dt.month
    elif hasattr(hub_intervals.index, "month"):
        hub_month = hub_intervals.index.month
    else:
        return {}
    hub_atc_by_month = hp.groupby(hub_month).mean()

    mb = monthly_breakdown.copy()
    mb["_cm"] = cal_months.values
    grp = mb.groupby("_cm").agg(MWh=("MWh", "sum"), MV=("Market_value", "sum"))

    ratios: dict[int, float] = {}
    for cm in grp.index:
        mwh = float(grp.at[cm, "MWh"])
        mv = float(grp.at[cm, "MV"])
        hatc = float(hub_atc_by_month.get(cm, 0))
        if mwh > 0 and hatc > 0:
            ratios[int(cm)] = (mv / mwh) / hatc
    if fleet_fallback:
        for cm in range(1, 13):
            if cm not in ratios and cm in fleet_fallback:
                ratios[cm] = fleet_fallback[cm]
    return ratios
