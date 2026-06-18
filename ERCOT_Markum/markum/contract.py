"""The single asset + contract definition behind the portal.

Everything that drives the dollar figures lives here so it is configured in one
place and easy to change. The asset facts come from the Hub's curated registry;
the contract terms (structure, strike, floor, volume share) are stored in a
git-ignored ``config.json`` next to the app and edited on the **Contract** page.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── the asset ───────────────────────────────────────────────────────────────
# Markum Solar — ERCOT resource node MRKM_SLR_RN, single SCED unit MRKM_SLR_PV1.
# (Facts mirror the Hub's curated solar registry so the two never drift.)
ASSET = {
    "project_name": "Markum Solar",
    "resource_node": "MRKM_SLR_RN",
    "resource_name": "MRKM_SLR_PV1",
    "capacity_mw": 161.0,
    "tech": "Solar PV",
    "tracking_type": "single_axis",
    "hub": "HB_NORTH",
    "county": "Bosque, TX",                # EIA-860: plant 67580, Bosque County
    "lat": 31.694792,                      # EIA-860 authoritative (COD 2024-11-01)
    "lon": -97.374883,
    "dc_ac_ratio": 1.45,
    # EIA-923 plant identifier for the independent generation cross-check. There
    # is no public ERCOT→EIA crosswalk, so this is supplied by hand once (the
    # plant's EIA ORIS code). Markum Solar Farm = 67580 (matched on EIA-860 name
    # "Markum Solar Farm", 161 MW PV, Bosque County). Overridable via
    # "eia_plant_id" in config.json; None ⇒ the cross-check is disabled.
    "eia_plant_id": 67580,
    "eia_prime_mover": "PV",   # solar PV; None = all prime movers at the plant
}

# ── default contract terms (seed; overridable in config.json / Contract page) ──
DEFAULT_CONTRACT = {
    "structure": "VPPA / CfD",   # "VPPA / CfD" | "Physical PPA" | "Merchant + fee"
    "strike": 35.0,              # $/MWh — the fixed contract price
    "volume_share_pct": 100.0,   # offtaker's pro-rata share of the plant's output
    "settle_at": "node",         # "node" (MRKM_SLR_RN) or "hub" (HB_NORTH)
    "price_floor": 0.0,          # $/MWh; intervals below this don't settle (VPPA norm)
    "apply_floor": True,
    "settle_below_floor": False,  # False: no settlement below floor (most VPPAs)
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
    """Resolve the settlement reference location from the terms."""
    return ASSET["hub"] if terms.get("settle_at") == "hub" else ASSET["resource_node"]


def floor_args(terms: dict) -> tuple[float | None, bool]:
    """(price_floor, settle_below_floor) for ercot_core.settlement.compute_settlement."""
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
    """Human label, e.g. ``"100 MW (62.1% of plant)"``."""
    return (f"{offtake_mw(terms):,.0f} MW "
            f"({float(terms.get('volume_share_pct', 100.0)):.1f}% of plant)")


def eia_plant_id() -> int | None:
    """Markum's EIA-923 plant id for the SCED cross-check, or None if unmapped.

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
