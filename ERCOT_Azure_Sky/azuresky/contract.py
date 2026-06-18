"""The single asset + contract definition behind the portal.

Everything that drives the dollar figures lives here so it is configured in one
place and easy to change. The asset facts mirror the Hub's curated wind registry
(``ercot_assets.json`` / ``wind_calibration.json``); the contract terms
(structure, strike, floor, volume share) are stored in a git-ignored
``config.json`` next to the app and edited on the **Contract** page.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── the asset ───────────────────────────────────────────────────────────────
# Azure Sky Wind — an ERCOT aggregate of four SCED units, settling at HB_NORTH.
# (Facts mirror the Hub's curated wind registry so the two never drift.)
ASSET = {
    "project_name": "Azure Sky Wind",
    # The aggregate resource id. There is no node-level generation series for it;
    # generation is summed from the four units below (see azuresky.hub).
    "resource_node": "AZURE_SKY_WIND_AGG",
    "units": ["VORTEX_WIND1", "VORTEX_WIND2", "VORTEX_WIND3", "VORTEX_WIND4"],
    "capacity_mw": 350.0,
    "tech": "Wind",
    "turbine_model": "Nordex N149/4.X (Mixed Fleet)",
    "hub_height_m": 105.0,
    "hub": "HB_NORTH",                     # the trading hub it settles at
    "county": "Throckmorton, TX",
    "lat": 33.1534,
    "lon": -99.2847,
    "cod_year": 2021,
    # EIA-923 plant identifier for the independent generation cross-check (the
    # authoritative settlement-quality meter). Azure Sky Wind = EIA plant 64164
    # ("Azure Sky Wind Project, LLC Hybrid"); the bill's plant generation matches
    # this plant's WIND (prime mover WT) net generation to the MWh. The plant is
    # co-located wind+battery, so the cross-check filters to the wind prime mover.
    # Overridable via "eia_plant_id" in config.json. None ⇒ check disabled.
    "eia_plant_id": 64164,
    "eia_prime_mover": "WT",   # wind turbine; None = all prime movers at the plant
}

# ── default contract terms (seed; overridable in config.json / Contract page) ──
# Seeded to Azure Sky's deal: a $17.34/MWh VPPA/CfD, 100% share, that curtails at
# negative prices (no settlement when HB_NORTH RT15 < $0 — "no electrons sold").
DEFAULT_CONTRACT = {
    "structure": "VPPA / CfD",   # "VPPA / CfD" | "Physical PPA" | "Merchant + fee"
    "strike": 17.34,             # $/MWh — the fixed contract price
    "volume_share_pct": 100.0,   # offtaker's pro-rata share of the plant's output
    "settle_at": "hub",          # the aggregate settles at HB_NORTH (no node price)
    "price_floor": 0.0,          # $/MWh; intervals below this don't settle
    "apply_floor": True,         # ON ⇒ curtailment at negative prices (the default)
    "settle_below_floor": False,  # False: no settlement below floor (curtail)
    "fee_per_mwh": 0.0,          # only used for "Merchant + fee"
    "counterparty": "Customer",  # label shown on the bill
    "currency": "USD",
}

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_contract() -> dict:
    """Contract terms: defaults overlaid with anything in ``config.json``."""
    terms = dict(DEFAULT_CONTRACT)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            if isinstance(saved, dict):
                terms.update({k: v for k, v in saved.items() if k in DEFAULT_CONTRACT})
        except Exception:  # noqa: BLE001 — a broken config should not break the app
            pass
    return terms


def save_contract(terms: dict) -> None:
    """Persist the editable contract terms to ``config.json`` (git-ignored)."""
    clean = {k: terms[k] for k in DEFAULT_CONTRACT if k in terms}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2) + "\n")


def settle_location(terms: dict) -> str:
    """Resolve the settlement reference location. The aggregate settles at the hub."""
    return ASSET["hub"]


def floor_args(terms: dict) -> tuple[float | None, bool]:
    """(price_floor, settle_below_floor) for ercot_core.settlement.compute_settlement.

    For wind this lever doubles as the **curtail-at-negative-prices** switch:
    ``apply_floor`` ON with a $0 floor and no settle-below means intervals where
    HB_NORTH is below $0 don't settle (the standard wind VPPA treatment).
    """
    if not terms.get("apply_floor", True):
        return None, False
    return float(terms.get("price_floor", 0.0)), bool(terms.get("settle_below_floor", False))


def is_placeholder_strike(terms: dict) -> bool:
    return float(terms.get("strike", 0.0)) <= 0.0


def offtake_mw(terms: dict) -> float:
    """The contracted offtake in MW = pro-rata share × plant capacity.

    The stored lever is ``volume_share_pct`` (a pro-rata slice of every interval,
    fed to the engine as ``mw_scale``); this expresses it in MW for display and
    for the MW-denominated input on the Contract page.
    """
    return float(terms.get("volume_share_pct", 100.0)) / 100.0 * float(ASSET["capacity_mw"])


def share_pct_for_mw(mw: float) -> float:
    """Convert a MW offtake to the stored pro-rata ``volume_share_pct`` (0–100)."""
    cap = float(ASSET["capacity_mw"])
    return max(0.0, min(100.0, (float(mw) / cap * 100.0) if cap else 0.0))


def offtake_label(terms: dict) -> str:
    """Human label, e.g. ``"100 MW (28.6% of plant)"``."""
    return (f"{offtake_mw(terms):,.0f} MW "
            f"({float(terms.get('volume_share_pct', 100.0)):.1f}% of plant)")


def eia_plant_id() -> int | None:
    """Azure's EIA-923 plant id for the SCED cross-check, or None if unmapped.

    Reads ``config.json`` first (so it can be set without touching code), then
    falls back to the ASSET default. No public ERCOT→EIA crosswalk exists, so
    this is a human-supplied mapping; until it's set the cross-check is disabled.
    """
    val = ASSET.get("eia_plant_id")
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            if isinstance(saved, dict) and saved.get("eia_plant_id") not in (None, ""):
                val = saved["eia_plant_id"]
        except Exception:  # noqa: BLE001
            pass
    try:
        return int(val) if val not in (None, "") else None
    except (TypeError, ValueError):
        return None
