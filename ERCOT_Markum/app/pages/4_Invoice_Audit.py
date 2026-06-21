"""Invoice Audit — reconcile settlement statement(s) against ERCOT-published data.

Provide statements by uploading one or **many** files, picking from a **linked
folder**, or batch-auditing the whole folder. A single file gets a detailed,
interval-level audit with editable column mapping; multiple files produce a
**portfolio table** (one row per statement). Either way the audit reuses the
same engine and cached data as the rest of the portal.
"""

from __future__ import annotations

from pathlib import Path

import _boot  # noqa: F401
import pandas as pd
import streamlit as st

_boot.ensure_hub(st)

from markum import analytics, branding, contract, hub, settings, statement  # noqa: E402

core = hub.core()
INV = core.invoice

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)   # settlement reference (node or a hub)

branding.hero(st, "Invoice Audit",
              "Check settlement statement(s) against ERCOT-published metered data")

st.markdown(
    "Verify statements by **uploading one or several**, picking from a **linked "
    "folder**, or batch-auditing the folder. One file → a detailed interval audit; "
    "many files → a per-statement portfolio table. Messy layouts (preamble rows, "
    "buried headers, footers, multi-sheet) are handled automatically.")


def _read_any(src, name: str):
    """Robustly read a messy statement → (clean DataFrame, detection info)."""
    return statement.load_clean(src, name)


def _arrow_safe(df):
    """Stringify object columns so Streamlit's Arrow renderer can't choke on a
    mixed-type column (common in raw statement grids)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda v: "" if (v is None or (isinstance(v, float) and pd.isna(v)))
                                else str(v))
    return out


# ── linked statement folder ──────────────────────────────────────────────────
folder = settings.invoice_folder()
files = settings.list_statements(folder)
cur = settings.get_invoice_folder_str()
hdr = (f"📁 Linked statement folder · {len(files)} file(s)" if folder is not None
       else "📁 Link a statement folder")
with st.expander(hdr, expanded=folder is None and not cur):
    new = st.text_input(
        "Folder path", value=cur,
        placeholder=str(Path.home() / "Markum Solar" / "statements"),
        help="Link a folder of settlement statements so you can pick or batch-audit "
             "them without uploading each one. Stored locally in settings.json.")
    b1, b2 = st.columns(2)
    if b1.button("🔗 Link / update folder", type="primary"):
        settings.set_invoice_folder(new)
        st.rerun()
    if cur and b2.button("Unlink"):
        settings.set_invoice_folder("")
        st.rerun()
    if cur and folder is None:
        st.warning(f"`{cur}` isn't a folder I can read — check the path.")
    elif folder is not None and not files:
        st.caption(f"Linked `{folder}` — no CSV/Excel statements found in it yet.")
    elif folder is not None:
        st.caption(f"Linked `{folder}` — {len(files)} statement file(s).")


# ── audit controls (apply to both single and batch) ─────────────────────────
st.subheader("What does this statement bill?")
audit_basis = st.radio(
    "Audit basis",
    ["VPPA net settlement — (price − strike) × volume",
     "Energy value — price × volume (gross invoice)"],
    label_visibility="collapsed",
    help="Most VPPA/CfD statements report a **net settlement** (the difference "
         "amount), not a gross energy invoice. Net is the usual choice. Use "
         "‘energy value’ only for a plain price×volume invoice.")
net_mode = audit_basis.startswith("VPPA")

oc = st.columns(2)
vol_source = oc[0].radio(
    "Volume that settles", ["Statement's own volume", "ERCOT metered generation"],
    help="‘Metered generation’ also checks the *quantity* against ERCOT SCED; "
         "‘statement’s own’ checks the money math against the volume the statement "
         "asserts. (SCED is always shown for comparison in net mode.)")
volume_source = "metered" if vol_source.startswith("ERCOT") else "invoice"

stmt_sign = "auto"
neg_treatment = "full"
neg_floor = float(terms.get("price_floor", 0.0))
if net_mode:
    sign_choice = oc[1].radio(
        "Statement sign convention",
        ["Auto-detect (recommended)",
         "Offtaker-signed (positive = you receive)",
         "Generator-signed (positive = generator receives)"],
        help="Auto-detect picks whichever fits ERCOT data — many statements are "
             "generator-signed. Override here if needed.")
    stmt_sign = ("offtaker" if sign_choice.startswith("Offtaker")
                 else "generator" if sign_choice.startswith("Generator") else "auto")
    neg_choice = st.radio(
        "Negative-price treatment",
        ["Floor floating price at $0 (typical VPPA)",
         "Settle the real price (no floor)",
         "Curtail (no settlement when price < $0)"],
        horizontal=True,
        help="How negative RT prices settle. Most VPPAs **floor the floating price "
             "at $0** — the offtaker isn't charged below it. Match this to your "
             "PPA's negative-price clause.")
    neg_treatment = ("floor" if neg_choice.startswith("Floor")
                     else "curtail" if neg_choice.startswith("Curtail") else "full")


def _audit_one(raw, sinfo):
    """Auto-map and audit one parsed statement → the engine's result dict.

    Used for batch (no interactive mapping). Raises ValueError with a clear
    message on the common failure modes so the batch can record a per-file note.
    """
    mp = statement.refine_mapping(raw.columns, dict(INV.suggest_mapping(raw.columns)))
    if net_mode:
        vmap = statement.vppa_mapping(raw.columns)
        for k in ("price_col", "volume_col", "amount_col"):
            if vmap.get(k):
                mp[k] = vmap[k]
    if sinfo.get("time_col"):
        mp["time_col"] = sinfo["time_col"]
    for k in ("time_col", "price_col", "volume_col", "amount_col"):
        if mp.get(k) == "(none)":
            mp[k] = None
    mp.setdefault("volume_unit", "MWh")
    if not mp.get("time_col"):
        raise ValueError("no timestamp column detected")
    raw2, _ = statement.drop_unparseable_times(raw, mp["time_col"])
    if raw2.empty:
        raise ValueError("no parseable interval timestamps")
    inv = INV.load_invoice(raw2, mp)
    lo, hi = inv["interval_start"].min(), inv["interval_start"].max()
    start = pd.Timestamp(lo).tz_localize(None).normalize() - pd.Timedelta(days=1)
    end = pd.Timestamp(hi).tz_localize(None).normalize() + pd.Timedelta(days=2)
    price = hub.settlement_prices(loc, start, end)
    if price.empty:
        raise ValueError("no cached ERCOT price for these dates")
    basis = analytics.pick_time_basis(raw2, mp, price, loc)   # ending vs beginning
    if basis != mp.get("time_basis"):
        mp["time_basis"] = basis
        inv = INV.load_invoice(raw2, mp)
    gen = (hub.generation(a["resource_node"], start, end)
           if (net_mode or volume_source == "metered") else None)
    if net_mode:
        return analytics.audit_net_settlement(
            inv, price, terms, location=loc, resource_node=a["resource_node"],
            units=a.get("sced_units") or [a["resource_name"]], gen_df=gen, volume_basis=volume_source, sign=stmt_sign,
            neg_treatment=neg_treatment, neg_floor=neg_floor)
    return INV.reconcile(inv, price_df=price, gen_df=gen, location=loc, market="RT15",
                         resource_node=a["resource_node"], units=a.get("sced_units") or [a["resource_name"]],
                         volume_source=volume_source)


def _run_batch(items):
    """Audit a list of (label, source) statements → a portfolio table."""
    st.subheader(f"Portfolio audit — {len(items)} statement(s)")
    st.caption(("Net-settlement basis. " if net_mode else "Energy-invoice basis. ")
               + "Each file is auto-mapped; open a row for interval detail. Use "
                 "single-file mode for manual column mapping.")
    if not st.button("🔍 Audit all", type="primary"):
        return

    rows, details = [], {}
    prog = st.progress(0.0, text="Auditing…")
    for i, (label, src) in enumerate(items):
        try:
            if _is_pdf(label):   # monthly summary invoice (no interval detail)
                summ = statement.read_pdf_summary(src, label)
                if not all(k in summ for k in ("volume_mwh", "net_total")):
                    rows.append({"Statement": label, "Status": "PDF — unrecognised layout"})
                    prog.progress((i + 1) / len(items)); continue
                rr = analytics.audit_summary(summ, terms, resource_node=a["resource_node"],
                                             units=a.get("sced_units") or [a["resource_name"]])
                ev = rr.get("volume_pct")
                rows.append({"Statement": label,
                             "Status": "PDF summary" + (f" · EIA Δ {ev:+.1f}%" if ev is not None else ""),
                             "Statement net $": (round(rr["offtaker_net"], 2)
                                                 if rr.get("offtaker_net") is not None else None),
                             "Expected net $": None, "Net Δ $": None, "Net Δ %": None,
                             "Bill MWh": round(rr["volume_mwh"], 1) if rr.get("volume_mwh") else None,
                             "EIA-923 MWh": round(rr["eia_volume"], 1) if rr.get("eia_volume") else None,
                             "EIA Δ %": round(ev, 2) if ev is not None else None,
                             "Flagged": None})
                prog.progress((i + 1) / len(items)); continue
            raw, sinfo = _read_any(src, label)
            res = _audit_one(raw, sinfo)
            s = res["summary"]
            details[label] = res["intervals"]
            status = "✅ ties out" if s["n_flagged"] == 0 else f"⚠ {s['n_flagged']} flagged"
            if net_mode:
                rows.append({"Statement": label, "Status": status,
                             "Statement net $": round(s["invoiced_total"], 2),
                             "Expected net $": round(s["expected_total"], 2),
                             "Net Δ $": round(s["variance"], 2),
                             "Net Δ %": round(s["variance_pct"], 2),
                             "Bill MWh": round(s.get("inv_volume_matched", 0.0), 1),
                             "EIA-923 MWh": (round(s["eia_volume_total"], 1)
                                             if s.get("eia_volume_total") else None),
                             "EIA Δ %": (round(s["eia_volume_pct"], 2)
                                         if s.get("eia_volume_total") else None),
                             "Flagged": s["n_flagged"]})
            else:
                rows.append({"Statement": label, "Status": status,
                             "Intervals": s["intervals"], "Flagged": s["n_flagged"],
                             "Variance $": round(s["variance"], 2)})
        except Exception as e:  # noqa: BLE001 — one bad file shouldn't stop the batch
            rows.append({"Statement": label, "Status": f"error: {e}"})
        prog.progress((i + 1) / len(items), text=f"Audited {i + 1}/{len(items)}")
    prog.empty()

    summ = pd.DataFrame(rows)
    n_ok = int(summ["Status"].fillna("").str.startswith("✅").sum())
    n_flag = int(summ["Status"].fillna("").str.startswith("⚠").sum())
    if n_flag:
        st.error(f"⚠️ {n_flag} of {len(items)} statement(s) have flagged intervals; "
                 f"{n_ok} tie out.")
    else:
        st.success(f"✅ All {len(items)} statement(s) tie out (or were skipped).")
    if net_mode and "Net Δ $" in summ.columns:
        tot = pd.to_numeric(summ["Net Δ $"], errors="coerce").sum()
        st.metric("Portfolio net variance (Σ across statements)", branding.signed_money(tot))

    st.dataframe(_arrow_safe(summ), hide_index=True, use_container_width=True)

    for label, iv in details.items():
        fl = iv[iv["status"] != "match"] if "status" in iv.columns else iv
        with st.expander(f"{label} — {len(fl)} flagged interval(s)"):
            st.dataframe(_arrow_safe((fl if not fl.empty else iv).head(500)),
                         hide_index=True, use_container_width=True, height=300)

    dl = hub.export_block()
    if dl is not None:
        dl(st, summ, name="markum_portfolio_audit",
           title="Markum Solar — portfolio invoice audit",
           meta={"Asset": a["project_name"], "Settles at": loc,
                 "Statements": str(len(items)),
                 "Basis": ("net settlement" if net_mode else "energy value"),
                 "Tie out": f"{n_ok}/{len(items)}"})


def _is_pdf(name):
    return str(name).lower().endswith(".pdf")


def _pdf_audit(src, name):
    """Audit a PDF **summary invoice** (monthly totals — no interval detail)."""
    st.subheader(f"PDF summary invoice — {name}")
    summ = statement.read_pdf_summary(src, name)
    need = ["volume_mwh", "fixed_rate", "floating_rate", "net_total"]
    if not all(k in summ for k in need):
        st.error("Couldn't read this PDF's invoice layout. The PDF invoices are monthly "
                 "summaries — for a full interval-level audit, upload the **Excel/CSV** "
                 "statement for this month instead.")
        st.caption(f"Recognised fields: {', '.join(summ) if summ else 'none'}")
        return
    r = analytics.audit_summary(summ, terms, resource_node=a["resource_node"], units=a.get("sced_units") or [a["resource_name"]])
    st.caption("PDF invoices are **monthly summaries** (no interval detail). This validates the "
               "strike, the invoice arithmetic, and the billed volume against EIA-923 — for "
               "interval-level price/net checks, use the Excel/CSV statement.")
    k = st.columns(4)
    k[0].metric("Period", f"{r.get('period_start','?')} – {r.get('period_end','?')}")
    k[1].metric("Billed volume", f"{r['volume_mwh']:,.1f} MWh")
    k[2].metric("Floating / Fixed", f"${r['floating_rate']:,.2f} / ${r['fixed_rate']:,.2f}")
    k[3].metric("Net to offtaker", branding.signed_money_raw(r['offtaker_net'] or 0))

    st.markdown("**Checks**")
    st.markdown(("- ✅ " if r["strike_ok"] else "- ⚠️ ")
                + f"Fixed rate **${r['fixed_rate']:,.2f}** vs contract strike "
                  f"**${r['strike']:,.2f}**" + ("" if r["strike_ok"] else " — mismatch"))
    if "arithmetic_delta" in r:
        ad = r["arithmetic_delta"]
        st.markdown(("- ✅ " if abs(ad) < 1 else "- ⚠️ ")
                    + f"Invoice arithmetic (fixed + floating vs total) — Δ ${ad:,.2f}")
    if "eia_volume" in r:
        vp = r["volume_pct"]
        st.markdown((f"- {'✅' if abs(vp) < 2 else '⚠️'} Billed volume **{r['volume_mwh']:,.0f} MWh** "
                     f"vs EIA-923 (your {contract.offtake_label(terms)}) "
                     f"**{r['eia_volume']:,.0f} MWh** — Δ {r['volume_delta']:+,.0f} MWh ({vp:+.2f}%)"))
    else:
        st.markdown("- ℹ️ EIA-923 volume check unavailable for this month (not yet published, "
                    "or for this period).")


# ── choose the statement source ──────────────────────────────────────────────
if files:
    mode = st.radio("Statement source",
                    ["Upload statement(s)", "Pick from linked folder",
                     "Batch-audit all in folder"],
                    horizontal=True)
else:
    mode = "Upload statement(s)"

raw = sinfo = src_label = None
if mode == "Batch-audit all in folder":
    _run_batch([(p.name, p) for p in files])
    branding.footer(st)
    st.stop()
elif mode == "Pick from linked folder":
    names = [p.name for p in files]
    pick = st.selectbox("Statement file", names, help="Files in the linked folder, newest first.")
    sel = files[names.index(pick)]
    if _is_pdf(sel.name):
        _pdf_audit(sel, sel.name)
        branding.footer(st)
        st.stop()
    raw, sinfo = _read_any(sel, sel.name)
    src_label = sel.name
else:
    ups = st.file_uploader("Statement file(s) — CSV, Excel, or PDF",
                           type=["csv", "xlsx", "xls", "pdf"], accept_multiple_files=True)
    if not ups:
        st.caption("Tip: upload **one** file for a detailed audit (Excel/CSV → interval-level; "
                   "PDF → monthly summary), or **several** for a portfolio table. Or link a folder above.")
        branding.footer(st)
        st.stop()
    if len(ups) > 1:
        _run_batch([(u.name, u) for u in ups])
        branding.footer(st)
        st.stop()
    if _is_pdf(ups[0].name):
        _pdf_audit(ups[0], ups[0].name)
        branding.footer(st)
        st.stop()
    raw, sinfo = _read_any(ups[0], ups[0].name)
    src_label = ups[0].name

# ── single-statement detailed audit ──────────────────────────────────────────
st.write(f"**{src_label}** · {len(raw):,} rows · columns: {', '.join(map(str, raw.columns))}")
if sinfo.get("method") == "detected":
    bits = [f"found the header{'' if sinfo.get('header_row') is None else f' on row {sinfo['header_row'] + 1}'}",
            f"**{sinfo['n_rows']:,}** interval rows"]
    if sinfo.get("n_skipped"):
        bits.append(f"skipped **{sinfo['n_skipped']:,}** preamble/footer row(s)")
    if sinfo.get("sheets", 1) > 1:
        bits.append(f"scanned {sinfo['sheets']} sheets")
    st.caption("🧭 Auto-detected layout — " + " · ".join(bits)
               + (f" · timestamp column **{sinfo['time_col']}**" if sinfo.get("time_col") else "")
               + ". Adjust the mapping below if anything looks off.")
with st.expander("Preview statement"):
    st.dataframe(_arrow_safe(raw.head(50)), use_container_width=True, hide_index=True)

# ── column mapping (auto-suggested, user-correctable) ───────────────────────
guess = statement.refine_mapping(raw.columns, dict(INV.suggest_mapping(raw.columns)))
if net_mode:
    vmap = statement.vppa_mapping(raw.columns)
    for _k in ("price_col", "volume_col", "amount_col"):
        if vmap.get(_k):
            guess[_k] = vmap[_k]
if sinfo.get("time_col"):
    guess["time_col"] = sinfo["time_col"]
cols = ["(none)"] + list(map(str, raw.columns))


def _idx(val):
    return cols.index(str(val)) if (val is not None and str(val) in cols) else 0


st.subheader("Map the columns")
c1, c2, c3 = st.columns(3)
m = {}
m["time_col"] = c1.selectbox("Interval timestamp", cols, index=_idx(guess.get("time_col")))
m["price_col"] = c2.selectbox("Market price ($/MWh)" if net_mode else "Price ($/MWh)",
                              cols, index=_idx(guess.get("price_col")),
                              help="The floating / RT market price the contract settles against."
                              if net_mode else None)
m["volume_col"] = c3.selectbox("Generation volume (MWh)" if net_mode else "Volume (MWh)",
                               cols, index=_idx(guess.get("volume_col")))
c4, c5, c6 = st.columns(3)
m["amount_col"] = c4.selectbox("Net settlement ($)" if net_mode else "Amount ($)",
                               cols, index=_idx(guess.get("amount_col")),
                               help="The statement's net settlement / CfD amount column "
                               "(not the per-MWh rate, not the running cumulative)."
                               if net_mode else None)
_basis_choice = c5.radio("Timestamp labels the interval's…",
                         ["auto-detect", "ending", "beginning"], index=0, horizontal=True,
                         help="Auto-detect aligns the bill's price to ERCOT and picks the "
                              "convention that matches — some months flip ending/beginning.")
m["time_basis"] = "ending" if _basis_choice == "auto-detect" else _basis_choice
m["interval"] = c6.radio("Interval length", ["15min", "hour"],
                         index=0 if guess.get("interval", "15min") == "15min" else 1,
                         horizontal=True)
for k in ("time_col", "price_col", "volume_col", "amount_col"):
    if m[k] == "(none)":
        m[k] = None
m["volume_unit"] = "MWh"

if not m["time_col"]:
    st.warning("Pick the interval-timestamp column to continue.")
    st.stop()
if not st.button("🔍 Audit statement", type="primary"):
    st.stop()

# Safety net: drop stray non-timestamp rows so the strict parse can't be sunk.
raw, _dropped = statement.drop_unparseable_times(raw, m["time_col"])
if raw.empty:
    st.error(f"The mapped timestamp column **{m['time_col']}** has no parseable dates. "
             "Pick the column that holds the interval timestamps.")
    st.stop()
try:
    inv = INV.load_invoice(raw, m)
except Exception as e:  # noqa: BLE001
    st.error(f"Couldn't read the statement with that mapping: {e}")
    st.stop()

lo = inv["interval_start"].min()
hi = inv["interval_start"].max()
start = pd.Timestamp(lo).tz_localize(None).normalize() - pd.Timedelta(days=1)
end_excl = pd.Timestamp(hi).tz_localize(None).normalize() + pd.Timedelta(days=2)

price_df = hub.settlement_prices(loc, start, end_excl)
gen_df = (hub.generation(a["resource_node"], start, end_excl)
          if (net_mode or volume_source == "metered") else None)

if price_df.empty:
    st.error(f"No cached ERCOT price covers this statement's dates for {loc} — the "
             "internal Data Hub may need to pull that window first.")
    st.stop()

if _basis_choice == "auto-detect":
    _b = analytics.pick_time_basis(raw, m, price_df, loc)
    if _b != m["time_basis"]:
        m["time_basis"] = _b
        inv = INV.load_invoice(raw, m)
    st.caption(f"🕐 Timestamp basis auto-detected: **interval-{m['time_basis']}**.")

# ── VPPA net-settlement audit (the bill's bottom line) ──────────────────────
if net_mode:
    res = analytics.audit_net_settlement(
        inv, price_df, terms, location=loc, resource_node=a["resource_node"],
        units=a.get("sced_units") or [a["resource_name"]], gen_df=gen_df, volume_basis=volume_source, sign=stmt_sign,
        neg_treatment=neg_treatment, neg_floor=neg_floor)
    s = res["summary"]
    intervals = res["intervals"]
    var = s["variance"]
    n_flagged = s["n_flagged"]
    basis_note = ("ERCOT metered generation" if volume_source == "metered"
                  else "the statement's own volume")
    st.subheader("Result")
    if n_flagged == 0:
        st.success(
            f"✅ **Net settlement ties out.** All {s['intervals']:,} intervals match "
            f"**(RT price − \\${terms['strike']:,.2f} strike) × {basis_note}**. "
            f"Statement net **{branding.signed_money(s['invoiced_total'])}** vs expected "
            f"**{branding.signed_money(s['expected_total'])}**.")
    else:
        st.error(
            f"⚠️ **{n_flagged:,} of {s['intervals']:,} intervals flagged.** Statement net "
            f"**{branding.signed_money(s['invoiced_total'])}** vs expected "
            f"**{branding.signed_money(s['expected_total'])}** — variance "
            f"**{branding.signed_money(var)}** ({s['variance_pct']:+.2f}%).")

    k = st.columns(4)
    k[0].metric("Intervals", f"{s['intervals']:,}")
    k[1].metric("Matched", f"{s['n_match']:,}")
    k[2].metric("Flagged", f"{n_flagged:,}")
    k[3].metric("Net variance", branding.signed_money(var),
                delta=f"{s['variance_pct']:+.2f}%",
                delta_color=("off" if abs(var) < 1 else "inverse"))
    st.caption(f"Read as **{s.get('sign')}-signed**, volume from **{basis_note}**, "
               f"negative prices **{neg_treatment}**."
               + ("  ·  Status: " + " · ".join(
                   f"{kk.replace('_', ' ')}: {vv:,}" for kk, vv in s["status_counts"].items())
                  if s.get("status_counts") else ""))

    # EIA-923 — the authoritative settlement-meter volume (monthly).
    if s.get("eia_volume_total"):
        eb, et_, ed, ep = (s["eia_volume_bill"], s["eia_volume_total"],
                           s["eia_volume_delta"], s["eia_volume_pct"])
        tie = "✅ ties out" if abs(ep) < 1.0 else "⚠ review"
        st.caption(f"🔢 **EIA-923 volume check** ({s.get('eia_months', 0)} month(s)) — bill "
                   f"**{eb:,.1f} MWh** vs EIA-923 net generation (your "
                   f"{contract.offtake_label(terms)}) **{et_:,.1f} MWh** · Δ **{ed:+,.1f} MWh** "
                   f"({ep:+.2f}%) {tie}. *Authoritative settlement-meter source.*")
        etbl = res.get("eia_table")
        if etbl is not None and len(etbl) > 1:
            with st.expander("EIA-923 monthly volume detail"):
                show = etbl.assign(eia_share_mwh=etbl["eia_share_mwh"]).rename(columns={
                    "year": "Year", "month": "Month", "bill_mwh": "Bill MWh",
                    "eia_share_mwh": "EIA-923 MWh (your share)", "delta_mwh": "Δ MWh", "pct": "Δ %"})
                st.dataframe(show[["Year", "Month", "Bill MWh", "EIA-923 MWh (your share)",
                                   "Δ MWh", "Δ %"]], hide_index=True, use_container_width=True)
    elif s.get("eia_plant_id"):
        st.caption("🔢 EIA-923 volume check unavailable — EIA hasn't published net "
                   "generation for this statement's month(s) yet (~6-month lag).")

    # ERCOT SCED telemetry — a real-time approximation (not settlement quality).
    sced_tot = s.get("sced_volume_total") or 0.0
    if sced_tot:
        iv_m = s.get("inv_volume_matched") or 0.0
        vd = s.get("volume_delta") or 0.0
        cov = f"{s.get('sced_intervals', 0):,} of {s['intervals']:,} intervals"
        miss = s.get("sced_missing", 0)
        st.caption(f"📦 SCED telemetry check (approx., on {cov}) — bill **{iv_m:,.1f} MWh** "
                   f"vs ERCOT SCED real-time telemetry **{sced_tot:,.1f} MWh** · "
                   f"Δ **{vd:+,.1f} MWh** ({(vd / sced_tot * 100):+.2f}%)."
                   + (f"  ⚠ {miss:,} intervals lack telemetry." if miss else "")
                   + "  *SCED telemetry runs below settlement-quality meter; use EIA-923 above as the authority.*")

    view_cols = [c for c in ["interval_start", "inv_price", "exp_price", "price_delta",
                             "inv_volume_mwh", "sced_volume_mwh", "vol_delta",
                             "inv_net", "exp_net", "net_delta", "status"]
                 if c in intervals.columns]
    disp = intervals[view_cols].rename(columns={
        "interval_start": "Interval (CPT)", "inv_price": "Bill price $/MWh",
        "exp_price": "ERCOT price $/MWh", "price_delta": "Δ price",
        "inv_volume_mwh": "Bill MWh", "sced_volume_mwh": "SCED MWh (telem.)", "vol_delta": "Δ MWh",
        "inv_net": "Statement net $", "exp_net": "Expected net $", "net_delta": "Δ net $"})
    flagged = disp[intervals["status"] != "match"]
    if not flagged.empty:
        st.subheader("Flagged intervals")
        st.dataframe(flagged, use_container_width=True, hide_index=True, height=360)
    with st.expander("All reconciled intervals"):
        st.dataframe(disp, use_container_width=True, hide_index=True, height=400)

    download_block = hub.export_block()
    if download_block is not None:
        download_block(
            st, intervals, name=f"markum_net_settlement_audit_{pd.Timestamp(lo).date()}",
            title="Markum Solar — net settlement audit",
            meta={"Asset": a["project_name"], "Settles at": loc, "Statement": src_label or "",
                  "Strike": f"${terms['strike']:,.2f}/MWh", "Volume basis": volume_source,
                  "Sign": s.get("sign"), "Neg-price": neg_treatment,
                  "Intervals": f"{s['intervals']:,}", "Flagged": f"{n_flagged:,}",
                  "Net variance": branding.signed_money_raw(var)})
    else:
        st.download_button("⬇ Download net-settlement audit CSV",
                           intervals.to_csv(index=False).encode("utf-8"),
                           file_name="markum_net_settlement_audit.csv", mime="text/csv")
    branding.footer(st)
    st.stop()

# ── gross energy-invoice reconcile (amount = price × volume) ────────────────
res = INV.reconcile(inv, price_df=price_df, gen_df=gen_df, location=loc, market="RT15",
                    resource_node=a["resource_node"], units=a.get("sced_units") or [a["resource_name"]],
                    volume_source=volume_source)
s = res["summary"]
intervals = res["intervals"]
var = s["variance"]
n_flagged = s["n_flagged"]
st.subheader("Result")
if n_flagged == 0:
    st.success(f"✅ **Ties out.** All {s['intervals']:,} intervals match ERCOT-published "
               f"data within tolerance. Invoiced **\\${s['invoiced_total']:,.2f}** vs expected "
               f"**\\${s['expected_total']:,.2f}**.")
else:
    st.error(f"⚠️ **{n_flagged:,} of {s['intervals']:,} intervals flagged.** "
             f"Invoiced **\\${s['invoiced_total']:,.2f}** vs expected "
             f"**\\${s['expected_total']:,.2f}** — variance **{branding.signed_money(var)}** "
             f"({s['variance_pct']:+.2f}%).")

k = st.columns(4)
k[0].metric("Intervals", f"{s['intervals']:,}")
k[1].metric("Matched", f"{s['n_match']:,}")
k[2].metric("Flagged", f"{n_flagged:,}")
k[3].metric("Variance", branding.signed_money(var),
            delta=f"{s['variance_pct']:+.2f}%",
            delta_color=("off" if abs(var) < 1 else "inverse"))
if s.get("status_counts"):
    st.caption("Status breakdown: " + " · ".join(
        f"{kk.replace('_', ' ')}: {vv:,}" for kk, vv in s["status_counts"].items()))

flagged = intervals[intervals["status"] != "match"]
if not flagged.empty:
    st.subheader("Flagged intervals")
    st.dataframe(flagged, use_container_width=True, hide_index=True, height=360)
with st.expander("All reconciled intervals"):
    st.dataframe(intervals, use_container_width=True, hide_index=True, height=400)

download_block = hub.export_block()
if download_block is not None:
    download_block(
        st, intervals, name=f"markum_invoice_audit_{pd.Timestamp(lo).date()}",
        title="Markum Solar — invoice audit",
        meta={"Asset": a["project_name"], "Settles at": loc, "Statement": src_label or "",
              "Volume basis": volume_source, "Intervals": f"{s['intervals']:,}",
              "Flagged": f"{n_flagged:,}", "Variance": branding.signed_money_raw(var)})
else:
    st.download_button("⬇ Download reconciliation CSV",
                       intervals.to_csv(index=False).encode("utf-8"),
                       file_name="markum_invoice_audit.csv", mime="text/csv")

branding.footer(st)
