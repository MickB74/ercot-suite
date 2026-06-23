"""§4(d) Basis Differential settlement for Heart of Texas Wind.

The VPPA settles the Floating Price at the **West hub** (``HB_WEST``) while the
Facility actually injects at its **node** (``RN_RTS1``). To shield the Seller
from the node↔hub basis, the PPA's Definitions tab defines a *Basis Differential
Interval*: any 15-min Calculation Interval where

    Floating Price (hub LMP)  >  Interconnection Point LMP (node)
                                 + Fixed Price
                                 + |PTC Value|

For such intervals the Seller may elect to have the Floating Price *deemed* equal
to ``node LMP + Fixed Price`` (a "Settled Basis Differential Interval"), which
lowers the Floating leg the Buyer is credited and so raises the net amount the
Buyer owes the Seller. The saving is ``original − replacement`` floating payment.

This module reproduces that mechanism interval-by-interval. The Floating Price is
floored at $0 first (the PPA's negative-price term), then the basis-differential
replacement is applied. Verified to the penny against the executed AdventHealth
invoices for Dec-2025 (0 BDI intervals) and Jan-2026 (15 BDI intervals, $5,374.99
saved).

The math is intentionally self-contained (not in the shared ``ercot_core``
engine) because the §4(d) mechanism is specific to this contract; the engine's
generic ``basis`` output is just ``Σ mwh × (node − hub)``, a different quantity.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import contract, hub


# Output column order — mirrors the invoice's "Data" sheet so an audit lines up
# field-for-field with what the Seller sent.
COLUMNS = [
    "interval_start", "buyer_mwh", "floating_price", "node_lmp", "fixed_price",
    "ptc_value", "is_bdi", "replacement_price",
    "init_floating_payment", "floating_payment_wbd", "basis_savings",
    "fixed_payment", "settlement",
]


def compute_intervals(df: pd.DataFrame, terms: dict | None = None) -> pd.DataFrame:
    """Apply the §4(d) mechanism to a merged interval frame.

    ``df`` must carry ``interval_start``, ``buyer_mwh`` (the Buyer's allocated
    quantity, MWh), ``floating_price`` (hub LMP $/MWh) and ``node_lmp`` ($/MWh).
    Returns a copy with every settled column in :data:`COLUMNS`.
    """
    terms = terms or contract.load_contract()
    fixed = float(terms.get("strike", 0.0))
    ptc = contract.ptc_value(terms) if terms.get("apply_basis_differential", True) else 0.0
    floor, settle_below = contract.floor_args(terms)
    apply_bdi = bool(terms.get("apply_basis_differential", True))

    d = df.copy()
    d["buyer_mwh"] = pd.to_numeric(d["buyer_mwh"], errors="coerce").fillna(0.0)
    hub_p = pd.to_numeric(d["floating_price"], errors="coerce")
    node_p = pd.to_numeric(d["node_lmp"], errors="coerce")

    # Floating Price floored at $0 (PPA negative-price term) before §4(d).
    if floor is not None and settle_below:
        eff_float = hub_p.clip(lower=floor)
    elif floor is not None:               # "no electrons sold" below floor
        eff_float = hub_p.where(hub_p >= floor)
    else:
        eff_float = hub_p

    d["fixed_price"] = fixed
    d["ptc_value"] = ptc
    # Basis Differential Interval test uses the RAW hub Floating Price.
    d["is_bdi"] = apply_bdi & (hub_p > (node_p + fixed + ptc))
    # Replacement Floating Price: node LMP + Fixed on BDI intervals, else the
    # (floored) Floating Price. The replacement leg is also floored at $0.
    repl = eff_float.copy()
    bdi_price = (node_p + fixed)
    if floor is not None:
        bdi_price = bdi_price.clip(lower=floor)
    repl = repl.where(~d["is_bdi"], bdi_price)

    d["node_lmp"] = node_p
    d["floating_price"] = eff_float
    d["replacement_price"] = repl
    d["init_floating_payment"] = d["buyer_mwh"] * eff_float
    d["floating_payment_wbd"] = d["buyer_mwh"] * repl
    d["basis_savings"] = d["init_floating_payment"] - d["floating_payment_wbd"]
    d["fixed_payment"] = d["buyer_mwh"] * fixed
    # Settlement (offtaker/Buyer pays Seller when positive) = Fixed − Floating.
    d["settlement"] = d["fixed_payment"] - d["floating_payment_wbd"]
    return d[[c for c in COLUMNS if c in d.columns]]


def summarize(intervals: pd.DataFrame) -> dict:
    """Monthly-style totals for a computed §4(d) interval frame."""
    if intervals is None or intervals.empty:
        return {"intervals": 0, "buyer_mwh": 0.0, "bdi_intervals": 0,
                "fixed_payment": 0.0, "init_floating_payment": 0.0,
                "floating_payment_wbd": 0.0, "basis_savings": 0.0, "settlement": 0.0}
    d = intervals
    return {
        "intervals": int(len(d)),
        "buyer_mwh": float(d["buyer_mwh"].sum()),
        "bdi_intervals": int(d["is_bdi"].sum()),
        "fixed_payment": float(d["fixed_payment"].sum()),
        "init_floating_payment": float(d["init_floating_payment"].sum()),
        "floating_payment_wbd": float(d["floating_payment_wbd"].sum()),
        "basis_savings": float(d["basis_savings"].sum()),
        "settlement": float(d["settlement"].sum()),
    }


# --------------------------------------------------------------------------- #
# Settle from ERCOT-published data (the portal's cached node + hub prices)
# --------------------------------------------------------------------------- #

def settle_from_ercot(start_date: dt.date, end_date: dt.date,
                      terms: dict | None = None) -> dict | None:
    """§4(d) settlement over [start, end] from ERCOT cached data, or None.

    Uses the plant's metered generation (``RTS_U1`` × Buyer share) and the
    real-time node (``RN_RTS1``) and hub (``HB_WEST``) RT15 prices the Data Hub
    has cached. This is the independent ("what the bill *should* be") view.
    """
    terms = terms or contract.load_contract()
    a = contract.ASSET
    node = a["resource_node"]
    hub_loc = contract.basis_hub(terms)
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0

    start = pd.Timestamp(start_date)
    end_excl = pd.Timestamp(end_date) + pd.Timedelta(days=1)

    gen_df = hub.generation(node, start, end_excl)
    node_df = hub.node_prices(node, start, end_excl)
    hub_df = hub.hub_prices(hub_loc, start, end_excl)
    if gen_df.empty or node_df.empty or hub_df.empty:
        return None

    INV = hub.core().invoice
    vol = INV.expected_volume(gen_df, node, units=a.get("sced_units") or [a["resource_name"]],
                              mw_scale=share).rename(columns={"metered_mwh": "buyer_mwh"})
    npr = INV.expected_prices(node_df, node, "RT15").rename(columns={"exp_price": "node_lmp"})
    hpr = INV.expected_prices(hub_df, hub_loc, "RT15").rename(columns={"exp_price": "floating_price"})

    def _key(s):
        return pd.to_datetime(s).dt.tz_localize(None).dt.round("min")

    m = vol.copy()
    m["_key"] = _key(m["interval_start"])
    for frame in (npr, hpr):
        f = frame.copy()
        f["_key"] = _key(f["interval_start"])
        m = m.merge(f.drop(columns=["interval_start"]), on="_key", how="left")
    m = m.dropna(subset=["node_lmp", "floating_price"]).drop(columns=["_key"])
    if m.empty:
        return None

    intervals = compute_intervals(m, terms)
    return {"intervals": intervals, "summary": summarize(intervals),
            "node": node, "hub": hub_loc}


def monthly_breakdown(intervals: pd.DataFrame) -> pd.DataFrame:
    """Per-month §4(d) rollup: MWh, BDI intervals, savings, settlement."""
    if intervals is None or intervals.empty:
        return pd.DataFrame()
    d = intervals.copy()
    d["Month"] = pd.to_datetime(d["interval_start"]).dt.to_period("M").astype(str)
    g = d.groupby("Month").agg(
        Buyer_MWh=("buyer_mwh", "sum"),
        BDI_intervals=("is_bdi", "sum"),
        Fixed_payment=("fixed_payment", "sum"),
        Floating_payment=("floating_payment_wbd", "sum"),
        Basis_savings=("basis_savings", "sum"),
        Settlement=("settlement", "sum"),
    ).reset_index()
    return g
