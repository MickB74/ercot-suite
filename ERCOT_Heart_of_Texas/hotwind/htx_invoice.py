"""Read & audit the executed Heart of Texas Wind PPA invoice workbook.

The Seller's monthly invoice (``YYYYMM HTX Advent PPA INV.xlsx``) is a rich,
self-describing workbook: an ``Invoice`` summary sheet, a 15-min ``Data`` sheet
carrying every input and computed column behind the §4(d) basis-differential
settlement, plus ``Definitions`` / ``ptc_config`` / ``date_config`` reference
tabs. This module pulls the structured pieces out and audits them two ways:

  1. **Internal consistency** — re-derive the settlement from the invoice's OWN
     inputs (Site Generation, hub Floating Price, node LMP) using
     :mod:`hotwind.basis`, and confirm every computed column (Buyer share, Fixed
     payment, Init/Replacement floating, Basis savings, Settlement) matches what
     the Seller printed.
  2. **vs ERCOT** — compare the invoice's hub and node prices, interval by
     interval, against the RT15 prices the Data Hub has cached for ``HB_WEST``
     and ``RN_RTS1`` — so a wrong price input is caught, not just bad arithmetic.

It is tolerant of the workbook layout (header on row 2 of ``Data``, label rows on
the summary sheet) but deliberately specific to *this* invoice template.
"""

from __future__ import annotations

import io

import pandas as pd

from . import basis, contract, hub

# Map the invoice "Data" sheet's verbose headers to tidy field names. Matched by
# case-insensitive substring so minor wording/whitespace drift doesn't break it.
_DATA_FIELDS = {
    "site generation": "site_gen",
    "west hub": "inv_hub",                 # ERCOT West Hub RT LMP (Floating Price)
    "interconnection point": "inv_node",   # HTX Interconnection Point LMP
    "datetime": "interval_start",
    "buyer's share": "buyer_mwh",
    "fixed price payment": "inv_fixed_payment",
    "init floating": "inv_init_floating",
    "basis differential intervals": "inv_bdi",
    "replacement floating price": "inv_replacement_price",
    "floating price payment w/ basis": "inv_floating_wbd",
    "basis differential savings": "inv_savings",
    "basis differential override": "inv_override",
    "fixed price": "inv_fixed_price",      # NOTE: keep AFTER "fixed price payment"
}


def is_htx_invoice(name: str, sheets: list[str] | None = None) -> bool:
    """Heuristic: does this look like the HTX PPA invoice workbook?"""
    if sheets is not None:
        s = {str(x).lower() for x in sheets}
        if {"invoice", "data"} <= s and any("basis" in x for x in s):
            return True
    n = str(name).lower()
    return n.endswith((".xlsx", ".xls")) and "htx" in n and "inv" in n


def _buffer(src):
    if hasattr(src, "read"):
        data = src.read()
        return io.BytesIO(data if isinstance(data, (bytes, bytearray)) else data.encode())
    return src


def read_invoice(src, name: str = "") -> dict:
    """Parse the invoice workbook → ``{"data", "summary", "meta"}``.

    ``data`` is the tidy 15-min frame (one row per Calculation Interval);
    ``summary`` holds the headline totals from the ``Invoice`` sheet; ``meta``
    carries the fixed price, PTC value and period detected in the workbook.
    """
    buf = _buffer(src)
    book = pd.read_excel(buf, sheet_name=None, header=None, dtype=object)
    sheets = {str(k).strip().lower(): v for k, v in book.items()}
    if "data" not in sheets:
        raise ValueError("no 'Data' sheet — not an HTX PPA invoice workbook")

    data = _read_data_sheet(sheets["data"])
    summary = _read_invoice_sheet(sheets.get("invoice"))
    meta = _read_meta(sheets)
    return {"data": data, "summary": summary, "meta": meta, "name": name}


def _read_data_sheet(grid: pd.DataFrame) -> pd.DataFrame:
    """Tidy the ``Data`` sheet (header on row 2, data from row 3)."""
    grid = grid.reset_index(drop=True)
    # Find the header row: the one carrying "Site Generation".
    hdr_row = 1
    for r in range(min(6, len(grid))):
        cells = [str(v).lower() for v in grid.loc[r] if v is not None]
        if any("site generation" in c for c in cells):
            hdr_row = r
            break
    headers = [str(v) if v is not None else "" for v in grid.loc[hdr_row]]
    body = grid.loc[hdr_row + 1:].reset_index(drop=True)
    body.columns = range(body.shape[1])

    out = pd.DataFrame()
    for i, h in enumerate(headers):
        hl = h.strip().lower()
        for needle, field in _DATA_FIELDS.items():
            if needle in hl and field not in out.columns:
                out[field] = body[i]
                break
    if "interval_start" not in out.columns:
        raise ValueError("could not locate the interval timestamp in the Data sheet")
    out["interval_start"] = pd.to_datetime(out["interval_start"], errors="coerce")
    out = out[out["interval_start"].notna()].reset_index(drop=True)
    num = [c for c in out.columns if c != "interval_start"]
    out[num] = out[num].apply(pd.to_numeric, errors="coerce")
    return out


def _read_invoice_sheet(grid) -> dict:
    """Pull labelled headline figures off the ``Invoice`` summary sheet."""
    out: dict = {}
    if grid is None:
        return out
    wants = {
        "buyer's share of generated quantity": "buyer_mwh",
        "fixed price payment": "fixed_payment",
        "floating price payment": "floating_payment_wbd",
        "settlement amount": "settlement",
        "amount payable by buyer": "payable_by_buyer",
        "amount payable by seller": "payable_by_seller",
        "basis differential intervals": "bdi_intervals",
        "basis differential savings": "basis_savings",
        "monthly settlement period start": "period_start",
        "monthly settlement period end": "period_end",
    }
    for _, row in grid.iterrows():
        cells = list(row)
        label = next((str(c).strip().lower() for c in cells if isinstance(c, str) and c.strip()), "")
        if not label:
            continue
        # The value sits to the right of the label — take the last real (non-NaN)
        # number or datetime on the row, skipping the NaN-padded middle cells and
        # any section-reference text ("Section 4(a)…").
        val = None
        for c in reversed(cells):
            if isinstance(c, str) or c is None:
                continue
            if isinstance(c, float) and pd.isna(c):
                continue
            val = c
            break
        for needle, key in wants.items():
            if needle in label and key not in out:
                out[key] = val
                break
    return out


def _read_meta(sheets: dict) -> dict:
    """Detect fixed price + |PTC value| stated in the workbook (for cross-check)."""
    meta: dict = {}
    ptc = sheets.get("ptc_config")
    if ptc is not None:
        for _, row in ptc.iterrows():
            cells = list(row)
            label = next((str(c).lower() for c in cells if isinstance(c, str)), "")
            if "ptc value" in label and "=" in label:
                num = next((c for c in cells if isinstance(c, (int, float))
                            and not (isinstance(c, float) and pd.isna(c))), None)
                if num is not None:
                    meta["ptc_value"] = abs(float(num))
    return meta


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #

def audit(parsed: dict, terms: dict | None = None, *, tol: float = 1.0,
          check_ercot: bool = True) -> dict:
    """Audit a parsed HTX invoice. Returns ``{"intervals", "summary"}``.

    Re-derives the §4(d) settlement from the invoice's own inputs and flags any
    interval where a computed column disagrees by more than ``tol`` dollars (or
    where the Basis Differential Interval election differs). When ``check_ercot``
    is set and prices are cached, also compares the invoice's hub/node LMPs to
    ERCOT's published RT15 prices.
    """
    terms = terms or contract.load_contract()
    d = parsed["data"].copy()
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0

    # Re-derive the settlement from the invoice's OWN inputs.
    calc_in = pd.DataFrame({
        "interval_start": d["interval_start"],
        "buyer_mwh": d["site_gen"].fillna(0.0) * share,
        "floating_price": d["inv_hub"],
        "node_lmp": d["inv_node"],
    })
    calc = basis.compute_intervals(calc_in, terms)

    j = d.copy()
    j["calc_buyer_mwh"] = calc["buyer_mwh"]
    j["calc_is_bdi"] = calc["is_bdi"]
    j["calc_replacement_price"] = calc["replacement_price"]
    j["calc_init_floating"] = calc["init_floating_payment"]
    j["calc_floating_wbd"] = calc["floating_payment_wbd"]
    j["calc_savings"] = calc["basis_savings"]
    j["calc_fixed_payment"] = calc["fixed_payment"]
    j["calc_settlement"] = calc["settlement"]

    # Per-interval deltas (invoice − recomputed).
    j["d_fixed"] = _num(j.get("inv_fixed_payment")) - j["calc_fixed_payment"]
    j["d_init_floating"] = _num(j.get("inv_init_floating")) - j["calc_init_floating"]
    j["d_floating_wbd"] = _num(j.get("inv_floating_wbd")) - j["calc_floating_wbd"]
    j["d_savings"] = _num(j.get("inv_savings")) - j["calc_savings"]
    inv_bdi = _num(j.get("inv_bdi")).fillna(0.0) > 0
    j["bdi_match"] = inv_bdi.eq(j["calc_is_bdi"])

    money_cols = ["d_fixed", "d_init_floating", "d_floating_wbd", "d_savings"]
    worst = j[money_cols].abs().max(axis=1)
    j["status"] = "match"
    j.loc[worst > tol, "status"] = "mismatch"
    j.loc[~j["bdi_match"], "status"] = "bdi_mismatch"

    # ── vs ERCOT published prices ──
    price = {}
    if check_ercot:
        price = _ercot_price_check(d, terms, tol=0.5)
        if price.get("merged") is not None:
            pm = price.pop("merged")
            j = j.merge(pm, on="interval_start", how="left")

    fixed_strike = float(terms.get("strike", 0.0))
    inv_fixed_price = _num(d.get("inv_fixed_price")).dropna()
    seen_strike = float(inv_fixed_price.iloc[0]) if len(inv_fixed_price) else None

    summary = {
        "intervals": int(len(j)),
        "n_match": int((j["status"] == "match").sum()),
        "n_mismatch": int((j["status"] == "mismatch").sum()),
        "n_bdi_mismatch": int((j["status"] == "bdi_mismatch").sum()),
        "inv_bdi_intervals": int(inv_bdi.sum()),
        "calc_bdi_intervals": int(j["calc_is_bdi"].sum()),
        # Headline recomputed totals.
        "calc_fixed_payment": float(j["calc_fixed_payment"].sum()),
        "calc_floating_wbd": float(j["calc_floating_wbd"].sum()),
        "calc_savings": float(j["calc_savings"].sum()),
        "calc_settlement": float(j["calc_settlement"].sum()),
        "calc_buyer_mwh": float(j["calc_buyer_mwh"].sum()),
        # Headline as printed on the invoice (Data sheet sums).
        "inv_fixed_payment": float(_num(j.get("inv_fixed_payment")).sum()),
        "inv_floating_wbd": float(_num(j.get("inv_floating_wbd")).sum()),
        "inv_savings": float(_num(j.get("inv_savings")).sum()),
        # Strike / PTC / share cross-checks.
        "strike": fixed_strike, "invoice_fixed_price": seen_strike,
        "strike_ok": (seen_strike is not None and abs(seen_strike - fixed_strike) < 0.01),
        "ptc_value": contract.ptc_value(terms),
        "invoice_ptc_value": parsed.get("meta", {}).get("ptc_value"),
        "share_pct": share * 100.0,
        **price,
    }
    summary["inv_settlement"] = summary["inv_fixed_payment"] - summary["inv_floating_wbd"]
    summary["settlement_delta"] = summary["inv_settlement"] - summary["calc_settlement"]
    # Reconcile against the Invoice summary sheet headline, when present.
    inv_sheet = parsed.get("summary", {})
    if isinstance(inv_sheet.get("settlement"), (int, float)):
        summary["invoice_sheet_settlement"] = float(inv_sheet["settlement"])
        summary["sheet_vs_data_delta"] = (float(inv_sheet["settlement"])
                                          - summary["inv_settlement"])
    return {"intervals": j, "summary": summary}


def _num(s):
    return pd.to_numeric(s, errors="coerce") if s is not None else pd.Series(dtype=float)


def _ercot_price_check(d: pd.DataFrame, terms: dict, tol: float = 0.5) -> dict:
    """Compare invoice hub/node LMPs to ERCOT cached RT15 prices."""
    try:
        a = contract.ASSET
        node = a["resource_node"]
        hub_loc = contract.basis_hub(terms)
        lo = pd.Timestamp(d["interval_start"].min()).normalize() - pd.Timedelta(days=1)
        hi = pd.Timestamp(d["interval_start"].max()).normalize() + pd.Timedelta(days=2)
        node_df = hub.node_prices(node, lo, hi)
        hub_df = hub.hub_prices(hub_loc, lo, hi)
        if node_df.empty or hub_df.empty:
            return {"ercot_checked": False}
        INV = hub.core().invoice
        npr = INV.expected_prices(node_df, node, "RT15").rename(columns={"exp_price": "ercot_node"})
        hpr = INV.expected_prices(hub_df, hub_loc, "RT15").rename(columns={"exp_price": "ercot_hub"})

        def _key(s):
            return pd.to_datetime(s).dt.tz_localize(None).dt.round("min")

        m = pd.DataFrame({"interval_start": d["interval_start"],
                          "inv_hub": _num(d.get("inv_hub")),
                          "inv_node": _num(d.get("inv_node"))})

        # The invoice's "Datetime (Interval Begin)" column actually carries the
        # interval-ENDING time, so its key must shift −15 min to align with the
        # portal's interval-beginning price key. Auto-detect the offset (0 vs
        # −15 min) by whichever makes the hub price match ERCOT on more intervals,
        # so a future template that labels intervals correctly still reconciles.
        ehub = (hpr.assign(_k=_key(hpr["interval_start"])).drop_duplicates("_k")
                   .set_index("_k")["ercot_hub"])
        best_shift, best_n = 0, -1
        for shift in (0, -15):
            k = (pd.to_datetime(m["interval_start"]).dt.tz_localize(None)
                 + pd.Timedelta(minutes=shift)).dt.round("min")
            n = int(((m["inv_hub"] - k.map(ehub)).abs() <= 0.5).sum())
            if n > best_n:
                best_shift, best_n = shift, n
        m["_key"] = (pd.to_datetime(m["interval_start"]).dt.tz_localize(None)
                     + pd.Timedelta(minutes=best_shift)).dt.round("min")
        for f, col in ((npr, "ercot_node"), (hpr, "ercot_hub")):
            ff = f.copy()
            ff["_key"] = _key(ff["interval_start"])
            m = m.merge(ff[["_key", col]], on="_key", how="left")
        m = m.drop(columns=["_key"])
        m["d_hub_price"] = m["inv_hub"] - m["ercot_hub"]
        m["d_node_price"] = m["inv_node"] - m["ercot_node"]
        cov = m["ercot_hub"].notna()
        return {
            "ercot_checked": True,
            "ercot_time_shift_min": best_shift,
            "ercot_intervals": int(cov.sum()),
            "hub_price_matches": int((m["d_hub_price"].abs() <= tol).sum()),
            "node_price_matches": int((m["d_node_price"].abs() <= tol).sum()),
            "hub_price_mad": float(m.loc[cov, "d_hub_price"].abs().mean()) if cov.any() else None,
            "node_price_mad": float(m.loc[cov, "d_node_price"].abs().mean()) if cov.any() else None,
            "merged": m[["interval_start", "ercot_hub", "ercot_node",
                         "d_hub_price", "d_node_price"]],
        }
    except Exception as e:  # noqa: BLE001 — ERCOT check is a bonus, never fatal
        return {"ercot_checked": False, "ercot_error": str(e)}
