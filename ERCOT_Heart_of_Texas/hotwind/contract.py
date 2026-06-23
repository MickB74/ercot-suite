"""The single asset + contract definition behind the portal.

Everything that drives the dollar figures lives here so it is configured in one
place and easy to change. The asset facts come from USWTDB + ERCOT SCED data;
the contract terms (structure, strike, floor, volume share) are stored in a
git-ignored ``config.json`` next to the app and edited on the **Contract** page.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── the asset ───────────────────────────────────────────────────────────────
# Heart of Texas Wind — Scout Clean Energy, McCulloch County, TX. 180 MW
# nameplate, 64 turbines, COD 2020. AdventHealth offtakes 90 MW (50%) via VPPA.
#
# ERCOT settlement node ``RN_RTS1`` (a SHARED node — ~1.06M MWh/yr across several
# plants). Heart of Texas is the ``RTS_U1`` resource specifically; the co-located
# RTS2_U1/RTS2_U2 units are a DIFFERENT plant at the same node, so we settle the
# price at RN_RTS1 but sum ONLY RTS_U1 for generation.
#
# Identity confirmed by REsurety CleanSight (node RN_RTS1, 179.88 MW, 64 turbines,
# McCulloch), RTS_U1 annual SCED = 597,102 MWh vs EIA 61032 = 596,000 (0.2%),
# 0.95 interval correlation with the AdventHealth invoice generation, and Dec-2025
# settlement −$291k vs invoice −$300k.
#
# WRONG nodes tried earlier (both reconcile poorly — keep this history):
#   * VENADO_ALL — a different plant; passed the auto-crosswalk on coincidental
#     annual volume but interval corr only 0.32 and settlement off ~15×.
#   * SHANNONW_RN — Shannon Wind, Clay County (EIA 59034), 250 mi away.
ASSET = {
    "project_name": "Heart of Texas Wind",
    "resource_node": "RN_RTS1",
    "sced_units": ["RTS_U1"],
    "capacity_mw": 180.0,
    "tech": "Wind",
    "turbine_model": "GE Mixed Fleet (GE2.82-127 / GE2.72-116 / GE2.5-127)",
    "hub_height_m": 89.0,
    "cut_in_ms": 3.0,
    "rated_ms": 12.5,
    "cut_out_ms": 25.0,
    "hub": "HB_WEST",
    "county": "McCulloch, TX",
    "lat": 31.2433,
    "lon": -99.4076,
    "cod_year": 2020,
    "eia_plant_id": 61032,
    "eia_prime_mover": "WT",
}

# ── default contract terms (seed; overridable in config.json / Contract page) ──
DEFAULT_CONTRACT = {
    "structure": "VPPA / CfD",
    "strike": 35.15,                 # Fixed Price per the executed PPA (Definitions tab)
    "volume_share_pct": 50.0,        # AdventHealth's 90 MW of 180 MW plant
    "settle_at": "hub",
    "settle_point": "HB_WEST",
    "price_floor": 0.0,
    "apply_floor": True,
    # PPA §4: when Floating Price < $0 the Floating Price is set to $0 but the
    # interval STILL settles (offtaker pays full fixed on that MWh). That is
    # settle_below_floor=True with a $0 floor — NOT exclusion of those intervals.
    "settle_below_floor": True,
    "fee_per_mwh": 0.0,
    "counterparty": "AdventHealth",
    "offtaker": "AdventHealth",
    "developer": "Scout Clean Energy",
    "currency": "USD",

    "apply_ceiling": False,
    "price_ceiling": 0.0,
    "exclude_negative_prices": False,
    "escalation_pct": 0.0,
    "escalation_base_year": 0,
    "rec_per_mwh": 0.0,
    "term_start": "",
    "term_end": "",
    "annual_volume_cap_mwh": 0.0,
    "settlement_frequency": "Monthly",
    "notional_type": "As-generated",

    # ── §4(d) Basis Differential mechanism (Definitions tab of the PPA) ──
    # The VPPA settles the Floating Price at the West hub (HB_WEST) but the
    # Facility injects at its node (RN_RTS1). To protect the Seller from the
    # node↔hub basis, any Calculation Interval where the Floating Price exceeds
    # (Interconnection Point LMP + Fixed Price + |PTC Value|) is a "Basis
    # Differential Interval": the Floating Price is *deemed* to equal the node
    # LMP + Fixed Price for that interval. ``ptc_amount`` × qualification ÷
    # (1 − tax rate) is the per-MWh |PTC Value| used in the threshold.
    "apply_basis_differential": True,
    "basis_hub": "HB_WEST",          # where the Floating Price is published
    "ptc_amount": 30.0,              # $/MWh PTC amount (= base × inflation factor)
    "ptc_tax_rate": 0.21,            # combined corporate income tax rate
    "ptc_qualification": 1.0,        # fraction of output qualifying for the PTC
}

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def engine_kwargs(terms: dict) -> dict:
    """Map contract terms to the extra ``compute_settlement`` kwargs."""
    kw: dict = {}
    if terms.get("apply_ceiling") and float(terms.get("price_ceiling", 0) or 0) > 0:
        kw["price_ceiling"] = float(terms["price_ceiling"])
    if terms.get("exclude_negative_prices"):
        kw["exclude_negative"] = True
    if float(terms.get("rec_per_mwh", 0) or 0):
        kw["rec_per_mwh"] = float(terms["rec_per_mwh"])
    esc = float(terms.get("escalation_pct", 0) or 0)
    base = int(terms.get("escalation_base_year", 0) or 0)
    if not base and terms.get("term_start"):
        try:
            base = int(str(terms["term_start"])[:4])
        except ValueError:
            base = 0
    if esc and base:
        kw["escalation_pct"] = esc / 100.0
        kw["escalation_base_year"] = base
    return kw


def load_contract() -> dict:
    """Contract terms: defaults overlaid with anything in ``config.json``."""
    terms = dict(DEFAULT_CONTRACT)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            if isinstance(saved, dict):
                terms.update({k: v for k, v in saved.items() if k in DEFAULT_CONTRACT})
        except Exception:  # noqa: BLE001
            pass
    return terms


def save_contract(terms: dict) -> None:
    """Persist the editable contract terms to ``config.json`` (git-ignored)."""
    clean = {k: terms[k] for k in DEFAULT_CONTRACT if k in terms}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2) + "\n")


def settle_location(terms: dict) -> str:
    """Resolve the settlement reference location from the terms."""
    pt = str(terms.get("settle_point", "") or "").strip()
    if pt:
        return pt
    return ASSET["hub"] if terms.get("settle_at") == "hub" else ASSET["resource_node"]


def is_node_location(location: str) -> bool:
    """True if ``location`` is the plant's own resource node (vs. a trading hub)."""
    return str(location) == ASSET["resource_node"]


def floor_args(terms: dict) -> tuple[float | None, bool]:
    """(price_floor, settle_below_floor) for ercot_core.settlement.compute_settlement."""
    if not terms.get("apply_floor", True):
        return None, False
    return float(terms.get("price_floor", 0.0)), bool(terms.get("settle_below_floor", False))


def is_placeholder_strike(terms: dict) -> bool:
    return float(terms.get("strike", 0.0)) <= 0.0


def offtake_mw(terms: dict) -> float:
    """The contracted offtake in MW = pro-rata share x plant capacity."""
    return float(terms.get("volume_share_pct", 100.0)) / 100.0 * float(ASSET["capacity_mw"])


def share_pct_for_mw(mw: float) -> float:
    """Convert a MW offtake to the stored pro-rata ``volume_share_pct`` (0-100)."""
    cap = float(ASSET["capacity_mw"])
    return max(0.0, min(100.0, (float(mw) / cap * 100.0) if cap else 0.0))


def offtake_label(terms: dict) -> str:
    """Human label, e.g. ``"90 MW (50.0% of plant)"``."""
    return (f"{offtake_mw(terms):,.0f} MW "
            f"({float(terms.get('volume_share_pct', 100.0)):.1f}% of plant)")


def ptc_value(terms: dict) -> float:
    """The |PTC Value| ($/MWh) used in the §4(d) basis-differential threshold.

    Per the PPA's PTC support tab: ``PTC Value = (PTC Amount × qualification) /
    (1 − combined tax rate)``. Returned as a positive number (the threshold uses
    the absolute value). Matches the executed invoice's −$37.9747/MWh at the
    seed inputs ($30 amount, 21% tax, 100% qualification).
    """
    amount = float(terms.get("ptc_amount", 0.0) or 0.0)
    tax = float(terms.get("ptc_tax_rate", 0.0) or 0.0)
    qual = float(terms.get("ptc_qualification", 1.0) or 0.0)
    if amount <= 0 or tax >= 1.0:
        return 0.0
    return (amount * qual) / (1.0 - tax)


def basis_hub(terms: dict) -> str:
    """Hub where the Floating Price is published for the basis-differential test."""
    return str(terms.get("basis_hub") or ASSET["hub"])


def eia_plant_id() -> int | None:
    """Heart of Texas Wind's EIA-923 plant id for the SCED cross-check, or None."""
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
