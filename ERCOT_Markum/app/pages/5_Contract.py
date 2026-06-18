"""Contract Terms — the one place that drives every dollar figure.

Edit and save the contract here; the change flows to Overview, Past Settlement,
Projected Bill, and Invoice Audit. Stored in a git-ignored ``config.json``.
"""

from __future__ import annotations

import _boot  # noqa: F401
import streamlit as st

_boot.ensure_hub(st)

from markum import branding, contract  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET

branding.hero(st, "Contract Terms", "Set the deal — it drives every figure in the portal")

st.subheader("Asset")
c = st.columns(4)
c[0].metric("Project", a["project_name"])
c[1].metric("Capacity", f"{a['capacity_mw']:,.0f} MW")
c[2].metric("ERCOT node", a["resource_node"])
c[3].metric("Hub", a["hub"].replace("HB_", ""))
st.caption(f"{a['tech']} · {a['tracking_type'].replace('_', '-')} tracking · "
           f"{a['county']} · DC/AC {a['dc_ac_ratio']}")

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

    saved = st.form_submit_button("💾 Save contract", type="primary")

if saved:
    contract.save_contract({
        "structure": structure,
        "strike": float(strike),
        "volume_share_pct": contract.share_pct_for_mw(offtake),
        "settle_at": terms.get("settle_at", "node"),
        "price_floor": float(price_floor),
        "apply_floor": bool(apply_floor),
        "settle_below_floor": settle_below.startswith("Still"),
        "fee_per_mwh": float(fee),
        "counterparty": counterparty,
        "currency": terms.get("currency", "USD"),
    })
    st.success(f"Saved — offtake set to **{offtake:,.0f} MW** "
               f"({contract.share_pct_for_mw(offtake):.1f}% of the {cap:,.0f} MW plant). "
               "The new terms apply across all pages.")
    st.cache_data.clear()

if contract.is_placeholder_strike(contract.load_contract()):
    st.warning("The strike is currently **$0** (placeholder). Enter your real contract "
               "price above so the settlement figures mean something.")

branding.footer(st)
