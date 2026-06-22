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
# Millers Branch Solar — ERCOT resource node MLB_SLR_RN, three PVGR units
# (MLB_SLR_SOLAR1/2/3). Haskell County (ERCOT North). Single-axis tracking PV.
# EIA-860 "Millers Branch Solar" plant 69101. The full multi-phase plant (~318 MW
# AC as SOLAR3 ramps in through 2026). NOTE: this portal was originally pointed at
# MIL_MILG1_2 = "R W Miller", a NATURAL GAS plant (ERCOT resource_type GSREH/
# SCGT90) — wrong node; corrected here to the real solar node MLB_SLR_RN.
ASSET = {
    "project_name": "Millers Branch Solar",
    "resource_node": "MLB_SLR_RN",
    "resource_name": "MLB_SLR_SOLAR1",
    # All three PVGR units make up the plant; settlement must sum ALL,
    # not just resource_name, or it counts a fraction of the plant.
    "sced_units": ["MLB_SLR_SOLAR1", "MLB_SLR_SOLAR2", "MLB_SLR_SOLAR3"],
    "capacity_mw": 318.0,
    "tech": "Solar PV",
    "tracking_type": "single_axis",
    "hub": "HB_NORTH",                 # Haskell County = ERCOT North
    "county": "Haskell",
    "lat": 33.221320,                  # EIA-860 plant 69101 (Millers Branch Solar)
    "lon": -99.586520,
    "dc_ac_ratio": 1.3,
    # EIA-860 "Millers Branch Solar" = plant 69101 (aggregates all PV phases).
    # Left disabled until the node↔EIA phase mapping is confirmed.
    "eia_plant_id": None,
    "eia_prime_mover": "PV",
    "tmy_resource_name": "MILLERS_BRANCH_2",
    "tmy_capacity_kw": 65000,
}

# ── default contract terms (seed; overridable in config.json / Contract page) ──
DEFAULT_CONTRACT = {
    "structure": "VPPA / CfD",   # "VPPA / CfD" | "Physical PPA" | "Merchant + fee"
    "strike": 35.0,              # $/MWh — the fixed contract price
    "volume_share_pct": 100.0,   # offtaker's pro-rata share of the plant's output
    "settle_at": "node",         # legacy flag; superseded by settle_point (kept for back-compat)
    "settle_point": "",          # settlement reference, e.g. "HB_SOUTH"; "" ⇒ the node/settle_at default
    "price_floor": 0.0,          # $/MWh; intervals below this don't settle (VPPA norm)
    "apply_floor": True,
    "settle_below_floor": False,  # False: no settlement below floor (most VPPAs)
    "fee_per_mwh": 0.0,          # only used for "Merchant + fee"
    "counterparty": "Customer",  # label shown on the bill
    "offtaker": "",              # full company name of the VPPA buyer
    "developer": "",             # developer / entity above the project SPV
    "currency": "USD",

    # ── extended VPPA levers (all OFF / neutral by default) ──────────────────
    # Price ceiling (upper rail of a collar): cap the settled market price.
    "apply_ceiling": False,
    "price_ceiling": 0.0,
    # Negative-price exclusion: no settlement when RT price < $0 (common term).
    "exclude_negative_prices": False,
    # Strike escalation: strike steps up this %/yr from the base year.
    "escalation_pct": 0.0,           # %/yr (e.g. 2.0); 0 = flat
    "escalation_base_year": 0,       # 0 ⇒ use term-start year, else this year
    # REC / green-attribute value to the offtaker, $/MWh (+ receive, − pay).
    "rec_per_mwh": 0.0,
    # Contract term: settlement is clamped to [term_start, term_end] when set.
    "term_start": "",                # "YYYY-MM-DD"
    "term_end": "",
    # ── recorded for the record, NOT yet applied to interval math ────────────
    "annual_volume_cap_mwh": 0.0,    # 0 = no cap
    "settlement_frequency": "Monthly",
    "notional_type": "As-generated",  # vs "Fixed shape"
}

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def engine_kwargs(terms: dict) -> dict:
    """Map contract terms to the extra ``compute_settlement`` kwargs.

    Returns only the levers that are switched ON, so settlement stays identical to
    the base CfD until a term is enabled. Escalation needs a base year — it uses the
    explicit one, else the term-start year; if neither, escalation stays off.
    """
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
        except Exception:  # noqa: BLE001 — a broken config should not break the app
            pass
    return terms


def save_contract(terms: dict) -> None:
    """Persist the editable contract terms to ``config.json`` (git-ignored)."""
    clean = {k: terms[k] for k in DEFAULT_CONTRACT if k in terms}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2) + "\n")


def settle_location(terms: dict) -> str:
    """Resolve the settlement reference location from the terms.

    Honors an explicit ``settle_point`` (set on the Contract page — the node
    ``MIL_MILG1_2`` or any trading hub such as ``HB_SOUTH``). When it's blank,
    falls back to the legacy ``settle_at`` node/hub flag. The chosen point must
    have cached RT15 prices (see :func:`portal.hub.available_locations`).
    """
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
    """Miller's EIA-923 plant id for the SCED cross-check, or None if unmapped.

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
