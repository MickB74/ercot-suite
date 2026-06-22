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
# Stafford Solar — ERCOT resource node BUZI_SLR_RN, four SCED units
# (BUZI_SLR_UNIT1..4) making up the ~252 MW plant. Motley County, West Texas
# (ERCOT West hub). The ERCOT/queue codename is "Buzios" (queue 24INR0399) and
# the legal entity is Roaring Springs Solar LLC (f/k/a Stetson Renewables); the
# offtaker (AdventHealth) markets it as "Stafford Solar". COD 2025-10-01.
# (Facts mirror the Hub's curated solar registry so the two never drift.)
ASSET = {
    "project_name": "Stafford Solar",
    "resource_node": "BUZI_SLR_RN",
    "resource_name": "BUZI_SLR_UNIT1",
    # All four SCED units make up the ~252 MW plant; settlement must sum ALL,
    # not just resource_name, or it counts a fraction of the plant.
    "sced_units": ["BUZI_SLR_UNIT1", "BUZI_SLR_UNIT2", "BUZI_SLR_UNIT3", "BUZI_SLR_UNIT4"],
    "capacity_mw": 252.0,
    "tech": "Solar PV",
    "tracking_type": "single_axis",
    "hub": "HB_WEST",
    "county": "Motley",                 # Roaring Springs, Motley County, West TX
    "lat": 33.88,
    "lon": -100.9,
    "dc_ac_ratio": 1.27,
    # EIA-923 plant id for the independent generation cross-check (no public
    # ERCOT→EIA crosswalk, so mapped by hand). Stafford/Roaring Springs =
    # EIA plant 68458 ("Roaring Springs, LLC", 250 MW PV, Motley County).
    # Overridable via "eia_plant_id" in config.json; None ⇒ check disabled.
    "eia_plant_id": 68458,
    "eia_prime_mover": "PV",   # solar PV; None = all prime movers at the plant
    # Hub registry uses STAFFORD_SOLAR_AGG / 250 MW for the TMY cache file;
    # the portal's resource_name is BUZI_SLR_UNIT1 (the SCED unit).
    "tmy_resource_name": "STAFFORD_SOLAR_AGG",
    "tmy_capacity_kw": 317500,
}

# ── default contract terms (seed; overridable in config.json / Contract page) ──
DEFAULT_CONTRACT = {
    "structure": "VPPA / CfD",   # "VPPA / CfD" | "Physical PPA" | "Merchant + fee"
    "strike": 42.55,             # $/MWh — Fixed Price per executed VPPA §1.1 (base; adj via §11.14/§11.15)
    "volume_share_pct": 100.0,   # offtaker's pro-rata share of the plant's output
    "settle_at": "hub",          # invoice settles at HB_WEST
    "settle_point": "HB_WEST",   # invoice Settlement Location
    "price_floor": -3.0,         # $/MWh; invoice floors negative LMP to -3 (then settles)
    "apply_floor": True,
    "settle_below_floor": True,   # invoice settles negative intervals at the -3 floor
    "fee_per_mwh": 0.0,          # only used for "Merchant + fee"
    "counterparty": "AdventHealth",  # label shown on the bill
    "offtaker": "AdventHealth",              # full company name of the PPA buyer
    "developer": "NextEra Energy Resources",  # Roaring Springs Solar LLC (f/k/a Stetson Renewables)
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
    # §3.1(d) Excluded Settlement Intervals: each month the Seller excludes the
    # intervals most favorable to the Buyer (highest offtaker-signed CfD) up to
    # this % of metered output. 0 = off. Stafford = 3% (annual cap, applied monthly).
    "monthly_exclusion_pct": 3.0,
    # Contract term: settlement is clamped to [term_start, term_end] when set.
    "term_start": "2025-10-01",      # COD (15 Contract Years per VPPA §2.1(a))
    "term_end": "2040-09-30",
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
    ``BUZI_SLR_RN`` or any trading hub such as ``HB_SOUTH``). When it's blank,
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
    """Stafford Solar's EIA-923 plant id for the SCED cross-check, or None if unmapped.

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
