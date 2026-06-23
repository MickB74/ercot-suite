"""Basis Differential — §4(d) settlement & HTX PPA invoice processing.

Two jobs in one place:

  1. **Compute** the contract's §4(d) basis-differential settlement from
     ERCOT-published data — the Floating Price (HB_WEST) floored at $0, with the
     node↔hub basis-differential replacement applied on qualifying intervals —
     and read off the Buyer's settlement, the Basis Differential Intervals, and
     the savings they produced.

  2. **Process an invoice** — load the Seller's monthly HTX PPA invoice workbook
     and reconcile it: re-derive every settled column from the invoice's own
     inputs, confirm the Basis Differential Interval election, and cross-check
     the invoice's hub/node prices against ERCOT's published RT15 prices.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import _boot  # noqa: F401
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_boot.ensure_hub(st)

from hotwind import basis, branding, contract, htx_invoice, hub, settings  # noqa: E402

a = contract.ASSET
NODE = a["resource_node"]
terms = contract.load_contract()
HUB = contract.basis_hub(terms)
PTC = contract.ptc_value(terms)
FIXED = float(terms.get("strike", 0.0))

branding.hero(st, "Basis Differential",
              f"§4(d) settlement · Floating at {HUB.replace('HB_', '')} hub vs {NODE} node")

if not terms.get("apply_basis_differential", True):
    st.warning("The basis-differential mechanism is **disabled** in the contract terms. "
               "Enable it on the Contract page to apply §4(d).")

st.markdown(
    f"A Calculation Interval is a **Basis Differential Interval** when the Floating "
    f"Price (the **{HUB.replace('HB_', '')}** hub LMP) exceeds the **{NODE}** node LMP "
    f"+ the **\\${FIXED:,.2f}** Fixed Price + the **\\${PTC:,.2f}** \\|PTC Value\\|. "
    f"On those intervals the Floating Price is *deemed* equal to **node LMP + Fixed "
    f"Price**, lowering the Floating leg credited to the Buyer (and so raising what "
    f"the Buyer owes the Seller). The Floating Price is floored at \\$0 first.")

tab_calc, tab_audit = st.tabs(["📐 Compute from ERCOT", "🔍 Process an invoice"])

# ───────────────────────────────────────────────────────────────────────────
# TAB 1 — compute the §4(d) settlement from ERCOT-published data
# ───────────────────────────────────────────────────────────────────────────
with tab_calc:
    win_start, win_end = hub.settlement_window(NODE, HUB)
    if win_start is None:
        st.info("No overlapping node + hub price data is cached for this asset yet. "
                "Pull it on the Update Data page.")
    else:
        def _eom(y, m):
            import calendar
            return dt.date(y, m, calendar.monthrange(y, m)[1])

        years = list(range(win_end.year, win_start.year - 1, -1))
        c0, c1, c2 = st.columns([1, 1, 2])
        pmode = c0.radio("Period", ["Month", "Custom"], horizontal=True)
        if pmode == "Month":
            yr = c1.selectbox("Year", years)
            mo = c2.selectbox("Month", list(range(1, 13)), index=max(0, win_end.month - 1),
                              format_func=lambda m: dt.date(2000, m, 1).strftime("%B"))
            start_d, end_d = dt.date(yr, mo, 1), _eom(yr, mo)
        else:
            start_d = c1.date_input("Start", value=win_start, min_value=win_start, max_value=win_end)
            end_d = c2.date_input("End", value=win_end, min_value=win_start, max_value=win_end)
        start_d = max(start_d, win_start)
        end_d = min(end_d, win_end)

        if start_d > end_d:
            st.error(f"Period outside the settled window ({win_start} → {win_end}).")
        else:
            @st.cache_data(show_spinner="Computing §4(d) settlement…", ttl=3600)
            def _settle(s, e, _terms):
                res = basis.settle_from_ercot(s, e, _terms)
                if res is None:
                    return None
                return res["intervals"], res["summary"]

            out = _settle(start_d, end_d, terms)
            if out is None:
                st.info("No settled intervals (need cached generation + node + hub prices).")
            else:
                intervals, s = out
                st.caption(f"Settled **{s['intervals']:,}** intervals · "
                           f"{start_d:%b %d, %Y} → {end_d:%b %d, %Y} · "
                           f"Buyer share {terms.get('volume_share_pct', 100):.0f}%.")
                r = st.columns(4)
                r[0].metric("Buyer's MWh", f"{s['buyer_mwh']:,.0f}")
                r[1].metric("Fixed payment", branding.money(s["fixed_payment"]))
                r[2].metric("Floating payment (w/ basis diff)",
                            branding.money(s["floating_payment_wbd"]))
                r[3].metric("Settlement (Buyer → Seller)",
                            branding.signed_money(s["settlement"]))
                r2 = st.columns(4)
                r2[0].metric("Basis Differential Intervals", f"{s['bdi_intervals']:,}")
                r2[1].metric("Basis savings to Seller", branding.money(s["basis_savings"]))
                r2[2].metric("Init floating (no basis diff)",
                             branding.money(s["init_floating_payment"]))
                r2[3].metric("|PTC Value| threshold add-on", f"${PTC:,.2f}/MWh")

                if s["bdi_intervals"] == 0:
                    st.success("✅ No Basis Differential Intervals in this period — the hub "
                               "Floating Price never exceeded node + fixed + |PTC|, so the "
                               "settlement equals the plain floored-floating CfD.")
                else:
                    st.info(f"⚖️ **{s['bdi_intervals']:,}** Basis Differential Interval(s) "
                            f"saved the Seller **{branding.money(s['basis_savings'])}** vs the "
                            f"plain floored-floating settlement.")

                # Monthly rollup
                mb = basis.monthly_breakdown(intervals)
                if not mb.empty and len(mb) > 1:
                    st.markdown("**Monthly breakdown**")
                    show = mb.rename(columns={
                        "Buyer_MWh": "Buyer MWh", "BDI_intervals": "BDI intervals",
                        "Fixed_payment": "Fixed $", "Floating_payment": "Floating $ (w/ BD)",
                        "Basis_savings": "Basis savings $", "Settlement": "Settlement $"})
                    st.dataframe(show, hide_index=True, use_container_width=True)

                # Basis-differential intervals detail (if any)
                bdi = intervals[intervals["is_bdi"]]
                if not bdi.empty:
                    with st.expander(f"Basis Differential Intervals ({len(bdi):,})"):
                        d = bdi.rename(columns={
                            "interval_start": "Interval (CPT)", "buyer_mwh": "Buyer MWh",
                            "floating_price": "Floating $/MWh", "node_lmp": "Node $/MWh",
                            "replacement_price": "Replacement $/MWh", "basis_savings": "Saved $"})
                        st.dataframe(
                            d[["Interval (CPT)", "Buyer MWh", "Floating $/MWh", "Node $/MWh",
                               "Replacement $/MWh", "Saved $"]],
                            hide_index=True, use_container_width=True, height=320)

                dl = hub.export_block()
                if dl is not None:
                    dl(st, intervals, name=f"htx_basis_differential_{start_d}",
                       title="Heart of Texas Wind — §4(d) basis-differential settlement",
                       meta={"Asset": a["project_name"], "Hub": HUB, "Node": NODE,
                             "Period": f"{start_d} → {end_d}",
                             "Fixed $/MWh": f"${FIXED:,.2f}", "|PTC| $/MWh": f"${PTC:,.2f}",
                             "BDI intervals": f"{s['bdi_intervals']:,}",
                             "Settlement": branding.signed_money_raw(s["settlement"])})

# ───────────────────────────────────────────────────────────────────────────
# TAB 2 — process / audit an HTX PPA invoice workbook
# ───────────────────────────────────────────────────────────────────────────
with tab_audit:
    st.markdown(
        "Load the Seller's monthly **HTX PPA invoice** (`YYYYMM HTX Advent PPA INV.xlsx`). "
        "The audit re-derives every settled column from the invoice's own inputs, confirms "
        "the Basis Differential Interval election, and cross-checks the invoice's hub/node "
        "prices against ERCOT's published RT15 prices.")

    folder = settings.invoice_folder()
    files = [p for p in settings.list_statements(folder)
             if htx_invoice.is_htx_invoice(p.name)] if folder else []

    src = src_name = None
    if files:
        srcmode = st.radio("Invoice source", ["Pick from linked folder", "Upload"],
                           horizontal=True)
    else:
        srcmode = "Upload"
    if srcmode == "Pick from linked folder":
        names = [p.name for p in files]
        pick = st.selectbox("Invoice workbook", names,
                            help="HTX PPA invoice workbooks in your linked folder, newest first.")
        sel = files[names.index(pick)]
        src, src_name = sel, sel.name
    else:
        up = st.file_uploader("HTX PPA invoice (.xlsx)", type=["xlsx", "xls"])
        if up is not None:
            src, src_name = up, up.name

    if src is None:
        st.caption("Tip: link your Box invoices folder on the Invoice Audit page and the "
                   "HTX workbooks will appear here automatically.")
    elif st.button("🔍 Process invoice", type="primary"):
        try:
            parsed = htx_invoice.read_invoice(src, src_name)
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't read **{src_name}** as an HTX PPA invoice: {e}")
            st.stop()
        res = htx_invoice.audit(parsed, terms, check_ercot=True)
        s = res["summary"]
        iv = res["intervals"]

        n_bad = s["n_mismatch"] + s["n_bdi_mismatch"]
        st.subheader("Result")
        if n_bad == 0:
            st.success(
                f"✅ **Invoice ties out.** All {s['intervals']:,} intervals reproduce from "
                f"the invoice's own inputs under §4(d). Settlement **"
                f"{branding.signed_money(s['inv_settlement'])}** "
                f"(Buyer → Seller), {s['inv_bdi_intervals']:,} Basis Differential Interval(s).")
        else:
            st.error(
                f"⚠️ **{n_bad:,} of {s['intervals']:,} intervals flagged** "
                f"({s['n_mismatch']:,} arithmetic, {s['n_bdi_mismatch']:,} BDI-election). "
                f"Invoice settlement **{branding.signed_money(s['inv_settlement'])}** vs "
                f"recomputed **{branding.signed_money(s['calc_settlement'])}** — Δ "
                f"{branding.signed_money(s['settlement_delta'])}.")

        k = st.columns(4)
        k[0].metric("Intervals", f"{s['intervals']:,}")
        k[1].metric("Matched", f"{s['n_match']:,}")
        k[2].metric("BDI (invoice / recomputed)",
                    f"{s['inv_bdi_intervals']:,} / {s['calc_bdi_intervals']:,}")
        k[3].metric("Settlement Δ", branding.signed_money(s["settlement_delta"]))

        # Headline leg reconciliation
        rows = [
            ("Buyer's MWh", s.get("calc_buyer_mwh"), None, "mwh"),
            ("Fixed payment", s["inv_fixed_payment"], s["calc_fixed_payment"], "$"),
            ("Floating payment (w/ basis diff)", s["inv_floating_wbd"], s["calc_floating_wbd"], "$"),
            ("Basis differential savings", s["inv_savings"], s["calc_savings"], "$"),
            ("Settlement (Buyer → Seller)", s["inv_settlement"], s["calc_settlement"], "$"),
        ]
        tbl = []
        for label, inv_v, calc_v, kind in rows:
            d = {"Item": label}
            if kind == "$":
                d["Invoice"] = f"${inv_v:,.2f}"
                d["Recomputed"] = f"${calc_v:,.2f}"
                d["Δ"] = f"${inv_v - calc_v:,.2f}"
            else:
                d["Invoice"] = f"{inv_v:,.1f} MWh"
                d["Recomputed"] = ""
                d["Δ"] = ""
            tbl.append(d)
        st.markdown("**Settlement reconciliation** — invoice vs recomputed from its own inputs")
        st.dataframe(pd.DataFrame(tbl), hide_index=True, use_container_width=True)

        # Contract cross-checks
        checks = []
        checks.append(("✅" if s["strike_ok"] else "⚠️")
                      + f" Fixed price **\\${s.get('invoice_fixed_price') or 0:,.2f}** vs "
                        f"contract strike **\\${s['strike']:,.2f}**")
        ipv = s.get("invoice_ptc_value")
        if ipv is not None:
            ok = abs(ipv - s["ptc_value"]) < 0.01
            checks.append(("✅" if ok else "⚠️")
                          + f" \\|PTC Value\\| **\\${ipv:,.4f}** vs contract **\\${s['ptc_value']:,.4f}**")
        bdi_ok = s["inv_bdi_intervals"] == s["calc_bdi_intervals"]
        checks.append(("✅" if bdi_ok else "⚠️")
                      + f" Basis Differential Intervals: invoice **{s['inv_bdi_intervals']:,}**, "
                        f"recomputed **{s['calc_bdi_intervals']:,}**")
        if "invoice_sheet_settlement" in s:
            sd = s.get("sheet_vs_data_delta", 0.0)
            checks.append(("✅" if abs(sd) < 1 else "⚠️")
                          + f" Invoice summary sheet settlement **\\${s['invoice_sheet_settlement']:,.2f}** "
                            f"vs Data-sheet total (Δ \\${sd:,.2f})")
        if s.get("ercot_checked"):
            hm, ni = s.get("hub_price_matches", 0), s.get("ercot_intervals", 0)
            nm = s.get("node_price_matches", 0)
            hmad, nmad = s.get("hub_price_mad"), s.get("node_price_mad")
            shift = s.get("ercot_time_shift_min", 0)
            ok = ni and (hm / ni > 0.95) and (nm / ni > 0.95)
            checks.append(
                ("✅" if ok else "⚠️")
                + f" ERCOT price cross-check: hub **{hm:,}/{ni:,}** within \\$0.50 "
                  f"(MAD \\${hmad:.2f}), node **{nm:,}/{ni:,}** (MAD \\${nmad:.2f})"
                + (f" · aligned at {shift:+d} min" if shift else ""))
        else:
            checks.append("ℹ️ ERCOT price cross-check unavailable — no cached RT15 price "
                          "for this invoice's dates yet.")
        st.markdown("**Cross-checks**\n\n" + "\n\n".join(f"- {c}" for c in checks))

        # Flagged intervals
        flagged = iv[iv["status"] != "match"]
        if not flagged.empty:
            st.subheader("Flagged intervals")
            cols = [c for c in ["interval_start", "site_gen", "inv_hub", "inv_node",
                                "inv_bdi", "calc_is_bdi", "inv_floating_wbd",
                                "calc_floating_wbd", "d_floating_wbd", "status"]
                    if c in flagged.columns]
            st.dataframe(flagged[cols].head(500), hide_index=True,
                         use_container_width=True, height=320)

        # Optional price-comparison chart
        if {"ercot_hub", "inv_hub"} <= set(iv.columns) and iv["ercot_hub"].notna().any():
            with st.expander("Invoice vs ERCOT prices (interval detail)"):
                d = iv.dropna(subset=["ercot_hub"]).copy()
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=d["interval_start"], y=d["inv_hub"],
                                         name="Invoice hub", line=dict(width=1)))
                fig.add_trace(go.Scatter(x=d["interval_start"], y=d["ercot_hub"],
                                         name="ERCOT hub", line=dict(width=1, dash="dot")))
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0),
                                  legend=dict(orientation="h"),
                                  yaxis_title="$/MWh")
                st.plotly_chart(fig, use_container_width=True)

        dl = hub.export_block()
        if dl is not None:
            dl(st, iv, name=f"htx_invoice_audit_{src_name}",
               title="Heart of Texas Wind — HTX PPA invoice audit",
               meta={"Asset": a["project_name"], "Invoice": src_name,
                     "Intervals": f"{s['intervals']:,}", "Flagged": f"{n_bad:,}",
                     "BDI": f"{s['inv_bdi_intervals']:,}",
                     "Settlement": branding.signed_money_raw(s["inv_settlement"]),
                     "Settlement Δ": branding.signed_money_raw(s["settlement_delta"])})

branding.footer(st)
