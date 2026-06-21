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

import sys
from pathlib import Path

import pandas as pd

_THIS = Path(__file__).resolve()


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
                 n_sims: int = 2000, capture_to_hub: float = 1.0) -> pd.DataFrame:
    """Monthly P10/P50/P90 forecast for ``hub`` from now over ``horizon_months``.

    Returns columns ``Month`` ("YYYY-MM"), ``month`` (Timestamp, first-of-month,
    naive Central), ``p10`` / ``p50`` / ``p90`` ($/MWh). All three percentiles
    are multiplied by ``capture_to_hub``, so when that ratio comes from history
    the output is already in capture-price units the Future Bill page expects.

    The engine reports per-block (atc/peak/offpeak); we use ATC — the round-the-
    clock mean — since the portal projections are monthly net P&L.
    """
    asof = str(pd.Timestamp(asof).date() if asof else pd.Timestamp.today().date())
    curve = _load_curve(hub, asof, horizon_months, n_sims)
    atc = curve[curve["block"] == "atc"][["month", "p10", "p50", "p90"]].copy()
    atc["month"] = pd.to_datetime(atc["month"])
    atc = atc.sort_values("month").reset_index(drop=True).head(horizon_months)
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
