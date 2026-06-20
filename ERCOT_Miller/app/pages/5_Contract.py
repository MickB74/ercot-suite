"""Contract Terms — the one place that drives every dollar figure.

Edit and save the contract here; the change flows to Overview, Past Settlement,
Projected Bill, and Invoice Audit. Stored in a git-ignored ``config.json``.
"""

from __future__ import annotations

import _boot  # noqa: F401
import streamlit as st

_boot.ensure_hub(st)

from portal import branding, contract, hub  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET

branding.hero(st, "Contract Terms", "Set the deal — it drives every figure in the portal")

st.subheader("Asset")
c = st.columns(4)
c[0].metric("Project", a["project_name"])
c[1].metric("Capacity", f"{a['capacity_mw']:,.0f} MW")
c[2].metric("ERCOT node", a["resource_node"])
c[3].metric("Settles at", contract.settle_location(terms).replace("HB_", ""),
            help=f"Default is the plant node {a['resource_node']}; "
                 "change the settlement reference in the Contract form below.")
if "wind" in str(a.get("tech", "")).lower():
    _bits = [a["tech"], a.get("turbine_model"), a["county"],
             (f"{a['hub_height_m']:.0f} m hub" if a.get("hub_height_m") else None)]
    st.caption(" · ".join(str(b) for b in _bits if b))
else:
    st.caption(f"{a['tech']} · {str(a.get('tracking_type', '')).replace('_', '-')} tracking · "
               f"{a['county']} · DC/AC {a.get('dc_ac_ratio')}")

st.divider()
st.subheader("Contract")

with st.form("contract"):
    c1, c2 = st.columns(2)
    structures = ["VPPA / CfD", "Physical PPA", "Merchant + fee"]
    structure = c1.selectbox(
        "Structure", structures,
        index=structures.index(terms["structure"]) if terms["structure"] in structures else 0,
        help="VPPA/CfD: settle the difference market − strike. Physical PPA: pay strike "
             "per MWh. Merchant + fee: market revenue ± a management fee.")
    counterparty = c2.text_input("Counterparty label", value=terms.get("counterparty", "Customer"))

    # Settlement reference: the plant's own node, or any trading hub with cached
    # RT15 prices. The node reads the node-price lake; hubs read the rich hub store.
    locs = list(hub.available_locations()) or [a["resource_node"]]
    cur_loc = contract.settle_location(terms)
    if cur_loc not in locs:
        locs = [cur_loc] + locs
    settle_point = c1.selectbox(
        "Settlement location", locs, index=locs.index(cur_loc),
        format_func=lambda p: (
            f"{a['resource_node']} (plant node)" if p == a["resource_node"]
            else p.replace("HB_", "") + " (hub)"),
        help="Where the contract settles. Defaults to the plant's own node "
             f"({a['resource_node']}); switch it to a trading hub (e.g. South) to "
             "settle against hub price instead of nodal. Only locations with cached "
             "RT15 prices are listed.")
    c2.caption(f"Plant node: **{a['resource_node']}** · asset hub: "
               f"**{a['hub'].replace('HB_', '')}** · {len(locs)} location(s) available")

    c3, c4 = st.columns(2)
    strike = c3.number_input("Strike / contract price ($/MWh)",
                             value=float(terms["strike"]), step=1.0,
                             help="The fixed contract price. This is the single most "
                                  "important input — set it to your real contract price.")
    cap = float(a["capacity_mw"])
    cur_mw = contract.offtake_mw(terms)
    offtake = c4.number_input(
        "Offtake (MW)", min_value=0.0, max_value=cap, value=round(cur_mw, 1), step=1.0,
        help=f"Your contracted MW slice of the {cap:,.0f} MW plant. Settles pro-rata: "
             f"this ÷ {cap:,.0f} MW of every interval's metered output "
             f"(currently ≈ {contract.share_pct_for_mw(cur_mw):.1f}% of plant). "
             "Set it to the whole plant for a 100% offtake.")
    c4.caption(f"Whole plant = {cap:,.0f} MW (100% share)")

    fee = st.number_input("Management fee ($/MWh, ‘Merchant + fee’ only)",
                          value=float(terms.get("fee_per_mwh", 0.0)), step=0.5)

    st.markdown("**Price floor** — the standard VPPA lever for negative/low prices.")
    c5, c6 = st.columns(2)
    apply_floor = c5.checkbox("Apply a price floor", value=bool(terms.get("apply_floor", True)))
    price_floor = c6.number_input("Floor ($/MWh)", value=float(terms.get("price_floor", 0.0)),
                                  step=1.0, disabled=not apply_floor)
    settle_below = st.radio(
        "When the market price is below the floor…",
        ["No settlement (energy not sold below the floor)",
         "Still settle (market leg clipped to the floor)"],
        index=1 if terms.get("settle_below_floor") else 0,
        disabled=not apply_floor,
        help="Most VPPAs suspend settlement below the floor — no money changes hands "
             "on those intervals.")

    st.markdown("**Advanced VPPA terms** — optional; all off / neutral by default.")
    with st.expander("Collar · negative prices · escalation · RECs · term · volume"):
        ac1, ac2 = st.columns(2)
        apply_ceiling = ac1.checkbox("Apply a price ceiling (collar cap)",
                                     value=bool(terms.get("apply_ceiling", False)),
                                     help="Upper rail of a collar — caps the settled market price.")
        price_ceiling = ac2.number_input("Ceiling ($/MWh)",
                                         value=float(terms.get("price_ceiling", 0.0)), step=5.0)
        exclude_neg = st.checkbox(
            "Exclude negative-price intervals (no settlement when RT price < $0)",
            value=bool(terms.get("exclude_negative_prices", False)),
            help="A common VPPA term, separate from the floor.")
        es1, es2 = st.columns(2)
        escalation = es1.number_input("Strike escalation (%/yr)",
                                      value=float(terms.get("escalation_pct", 0.0)), step=0.25,
                                      help="Strike steps up this % per year from the base year.")
        esc_base = es2.number_input("Escalation base year (0 = term-start year)",
                                    value=int(terms.get("escalation_base_year", 0) or 0),
                                    step=1, format="%d")
        rec = st.number_input("REC / green-attribute value ($/MWh)",
                              value=float(terms.get("rec_per_mwh", 0.0)), step=0.5,
                              help="Added to the offtaker's net (+ receive, − pay).")
        tm1, tm2 = st.columns(2)
        term_start = tm1.text_input("Term start (YYYY-MM-DD)", value=str(terms.get("term_start", "")))
        term_end = tm2.text_input("Term end (YYYY-MM-DD)", value=str(terms.get("term_end", "")))
        st.caption("⬇ Recorded for reference — **not yet applied** to the interval math:")
        rc1, rc2, rc3 = st.columns(3)
        ann_cap = rc1.number_input("Annual volume cap (MWh, 0 = none)",
                                   value=float(terms.get("annual_volume_cap_mwh", 0.0)), step=1000.0)
        _freqs = ["Monthly", "Quarterly", "Annual"]
        freq = rc2.selectbox("Settlement frequency", _freqs,
                             index=_freqs.index(terms.get("settlement_frequency", "Monthly"))
                             if terms.get("settlement_frequency", "Monthly") in _freqs else 0)
        _nots = ["As-generated", "Fixed shape"]
        notional = rc3.selectbox("Notional type", _nots,
                                 index=_nots.index(terms.get("notional_type", "As-generated"))
                                 if terms.get("notional_type", "As-generated") in _nots else 0)

    saved = st.form_submit_button("💾 Save contract", type="primary")

if saved:
    contract.save_contract({
        "structure": structure,
        "strike": float(strike),
        "volume_share_pct": contract.share_pct_for_mw(offtake),
        # Store "" when the choice is the plant node so the default tracks the
        # asset; otherwise store the chosen point. Keep settle_at in sync for
        # back-compat (hub vs node) with anything that still reads it.
        "settle_at": "hub" if str(settle_point).upper().startswith("HB_") else "node",
        "settle_point": "" if settle_point == a["resource_node"] else settle_point,
        "price_floor": float(price_floor),
        "apply_floor": bool(apply_floor),
        "settle_below_floor": settle_below.startswith("Still"),
        "fee_per_mwh": float(fee),
        "counterparty": counterparty,
        "currency": terms.get("currency", "USD"),
        # extended VPPA levers
        "apply_ceiling": bool(apply_ceiling),
        "price_ceiling": float(price_ceiling),
        "exclude_negative_prices": bool(exclude_neg),
        "escalation_pct": float(escalation),
        "escalation_base_year": int(esc_base),
        "rec_per_mwh": float(rec),
        "term_start": term_start.strip(),
        "term_end": term_end.strip(),
        "annual_volume_cap_mwh": float(ann_cap),
        "settlement_frequency": freq,
        "notional_type": notional,
    })
    st.success(f"Saved — offtake set to **{offtake:,.0f} MW** "
               f"({contract.share_pct_for_mw(offtake):.1f}% of the {cap:,.0f} MW plant), "
               f"settling at **{settle_point}**. The new terms apply across all pages.")
    st.cache_data.clear()

if contract.is_placeholder_strike(contract.load_contract()):
    st.warning("The strike is currently **$0** (placeholder). Enter your real contract "
               "price above so the settlement figures mean something.")

branding.footer(st)
