"""Validate an ERCOT invoice / settlement statement against cached prices.

Upload any invoice that has an interval timestamp plus some of {price $/MWh,
volume MWh, amount $}. The page maps its columns, lifts both the invoice and our
cached prices to tz-aware Central (DST-correct — the November fall-back hour is
matched on the absolute instant, not the repeated wall-clock label), and shows a
per-interval reconciliation with the dollar variance. See ercot_core.invoice.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import invoice as INV  # noqa: E402
from ercot_core import prices as PX  # noqa: E402
from ercot_core import dam_prices as DAMX  # noqa: E402
from ercot_core import settlement as S  # noqa: E402
from ercot_core import paths, tz  # noqa: E402
from ercot_core.settlement_points import HUBS  # noqa: E402

_STATUS_COLOR = {
    "match": "#1b5e20",
    "price_mismatch": "#b71c1c",
    "volume_mismatch": "#e65100",
    "amount_mismatch": "#b71c1c",
    "missing_in_invoice": "#4527a0",
    "extra_in_invoice": "#37474f",
}
_NONE = "— none —"

st.title("🧾 Invoice Validation")
st.caption("Reconcile an uploaded ERCOT invoice / settlement statement against "
           "cached hub prices (× metered generation), interval by interval, in "
           "Central Prevailing Time with DST handled.")

up = st.file_uploader("Invoice file (CSV or Excel)", type=["csv", "xlsx", "xls"])
if up is None:
    _common.empty_state(
        st, "Upload an invoice to begin.",
        hint="Any CSV/Excel with an interval timestamp column and at least one of "
             "price ($/MWh), volume (MWh), or amount ($). ERCOT settlement "
             "extracts work directly.", stop=True)


@st.cache_data(show_spinner=False)
def _read(name: str, data: bytes) -> pd.DataFrame:
    import io
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data))
    return pd.read_csv(io.BytesIO(data))


raw = _read(up.name, up.getvalue())
st.write(f"**{len(raw):,}** rows · **{len(raw.columns)}** columns")
st.dataframe(raw.head(8), use_container_width=True)

guess = INV.suggest_mapping(raw.columns)
cols = list(raw.columns)


def _sel(label, role, *, required=False, help=None):
    options = ([] if required else [_NONE]) + cols
    default = guess.get(role)
    idx = options.index(default) if default in options else 0
    val = st.selectbox(label, options, index=idx, key=f"map_{role}", help=help)
    return None if val == _NONE else val


with st.sidebar:
    st.header("1 · Map columns")
    time_col = _sel("Interval timestamp", "time_col", required=True)
    time_basis = st.radio("Timestamp marks the interval's…", ["ending", "beginning"],
                          index=0 if guess["time_basis"] == "ending" else 1,
                          horizontal=True,
                          help="ERCOT settlement labels are interval-ENDING.")
    interval = st.radio("Interval length", ["15min", "hour"],
                        index=0 if guess["interval"] == "15min" else 1, horizontal=True)
    dst_flag_col = _sel("DST / repeated-hour flag (optional)", "dst_flag_col",
                        help="Exactly disambiguates the November fall-back hour. "
                             "Without it, the duplicated hour is inferred.")
    price_col = _sel("Price $/MWh", "price_col")
    volume_col = _sel("Volume", "volume_col")
    volume_unit = st.radio("Volume unit", ["MWh", "MW"], index=0, horizontal=True,
                           disabled=volume_col is None)
    amount_col = _sel("Amount $", "amount_col")
    location_col = _sel("Location / settlement point (optional)", "location_col")

    st.header("2 · Reference price")
    market = st.radio("Market", ["RT15", "DAM"], horizontal=True,
                      help="RT15 = real-time 15-min hub SPP (local store). "
                           "DAM = day-ahead hourly (local store).")
    location = st.selectbox("Settlement point (hub)", HUBS,
                            index=HUBS.index("HB_HUBAVG") if "HB_HUBAVG" in HUBS else 0)

    st.header("3 · Volume basis")
    vsrc = st.radio("Validate volume against", ["invoice (price/amount only)",
                                                "metered generation"],
                    help="'metered' also checks the billed quantity against this "
                         "hub's SCED generation at a resource node.")
    volume_source = "metered" if vsrc.startswith("metered") else "invoice"
    resource_node = None
    if volume_source == "metered":
        resource_node = st.text_input("Resource node (for metered generation)",
                                      value=location)

    st.header("4 · Tolerance")
    abs_tol = st.number_input("Absolute ($, $/MWh, MWh)", value=0.01, min_value=0.0,
                              step=0.01, format="%.2f")
    rel_tol = st.number_input("Relative (%)", value=0.5, min_value=0.0, step=0.1,
                              format="%.2f") / 100.0
    run = st.button("▶ Validate", type="primary", use_container_width=True)

mapping = {
    "time_col": time_col, "time_basis": time_basis, "interval": interval,
    "dst_flag_col": dst_flag_col, "location_col": location_col,
    "price_col": price_col, "volume_col": volume_col, "volume_unit": volume_unit,
    "amount_col": amount_col,
}

if not run:
    st.info("Map the columns and pick a reference price on the left, then press "
            "**▶ Validate**.")
    st.stop()

try:
    inv = INV.load_invoice(raw, mapping)
except ValueError as e:
    st.error(f"Could not read the invoice: {e}")
    st.stop()

# Pull cached reference prices over the invoice's window (+ a one-interval pad).
lo = inv["interval_start"].min().tz_convert(tz.CENTRAL).tz_localize(None)
hi = inv["interval_start"].max().tz_convert(tz.CENTRAL).tz_localize(None)
start = pd.Timestamp(lo) - pd.Timedelta(hours=1)
end_excl = pd.Timestamp(hi) + pd.Timedelta(hours=2)

if market == "DAM":
    price_df = DAMX.dam_store_prices([location], start, end_excl)
    price_store = DAMX.DAM_STORE
else:
    price_df = PX.hub_store_prices([location], start, end_excl)
    price_store = paths.HUB_PRICES_PARQUET

if price_df.empty:
    _common.empty_state(
        st, f"No cached **{market}** price for **{location}** over "
            f"{lo.date()} → {hi.date()}.",
        hint="Update Hub prices / DAM on the Control Tower so this window is "
             "covered, then re-validate.", stop=True)

gen_df = None
if volume_source == "metered":
    ng = paths.NODE_DATA_DIR
    files = sorted(ng.glob("node_generation_*.parquet")) if ng.exists() else []
    if files:
        gen_df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if gen_df is None or gen_df.empty:
        st.warning("No cached node generation found — falling back to invoice volume.")
        volume_source = "invoice"

res = INV.reconcile(inv, price_df=price_df, gen_df=gen_df, location=location,
                    market=market, resource_node=resource_node,
                    volume_source=volume_source, abs_tol=abs_tol, rel_tol=rel_tol)
d, s = res["intervals"], res["summary"]

# ── results ────────────────────────────────────────────────────────────────
st.subheader("Result")
c = st.columns(4)
c[0].metric("Intervals", f"{s['intervals']:,}")
c[1].metric("Matched", f"{s['n_match']:,}", help="Within tolerance on every "
            "compared field.")
c[2].metric("Flagged", f"{s['n_flagged']:,}",
            delta=None if not s["n_flagged"] else "needs review",
            delta_color="inverse")
c[3].metric("$ Variance (inv − exp)", f"${s['variance']:+,.2f}",
            delta=f"{s['variance_pct']:+.2f}%", delta_color="inverse",
            help="Invoiced − expected, signed from the payer's (offtaker's) side: "
                 "positive ⇒ you were overbilled (charged more than market × volume); "
                 "negative ⇒ underbilled (in your favor).")

cc = st.columns(2)
cc[0].metric("Invoiced total", f"${s['invoiced_total']:,.2f}")
cc[1].metric("Expected total", f"${s['expected_total']:,.2f}",
             help=f"Volume ({s['volume_source']}) × {market} SPP at {location}.")

_var = s["variance"]
if abs(_var) < 0.005:
    st.caption("✅ **Offtaker view:** the invoice matches expected (within rounding).")
elif _var > 0:
    st.caption(f"🔴 **Offtaker view: overbilled** — the invoice charges **\\${_var:,.2f}** "
               "more than expected; as the offtaker you'd overpay by this much.")
else:
    st.caption(f"🟢 **Offtaker view: underbilled** — the invoice charges **\\${abs(_var):,.2f}** "
               "less than expected; in your favor as the offtaker.")

if s["status_counts"]:
    st.caption("  ·  ".join(f"**{k}**: {v}" for k, v in sorted(s["status_counts"].items())))

flagged_only = st.toggle("Show flagged intervals only", value=bool(s["n_flagged"]))
view = d[d["status"] != "match"] if flagged_only else d


def _style(col: pd.Series):
    return [f"color: {_STATUS_COLOR.get(v, '')}; font-weight: 600" for v in col]


def _style_offtaker_delta(col: pd.Series):
    # Offtaker view: positive delta = overbilled (you overpay) → red;
    # negative = underbilled (in your favor) → green.
    out = []
    for v in col:
        if pd.isna(v) or abs(v) < 0.005:
            out.append("")
        else:
            out.append(f"color: {'#d23f31' if v > 0 else '#34a853'}; font-weight: 600")
    return out


money = {c: "${:,.2f}" for c in ("inv_price", "exp_price", "price_delta",
                                 "inv_amount", "exp_amount", "amount_delta")
         if c in view.columns}
qty = {c: "{:,.3f}" for c in ("inv_volume_mwh", "metered_mwh", "volume_delta")
       if c in view.columns}
styler = view.style.apply(_style, subset=["status"]).format({**money, **qty})
# Red = overbilled, green = in the offtaker's favor — on the $ that actually bills.
for _dcol in ("amount_delta", "price_delta"):
    if _dcol in view.columns:
        styler = styler.apply(_style_offtaker_delta, subset=[_dcol])
st.dataframe(styler, use_container_width=True, height=460)

_export.download_block(
    st, d, name=f"invoice_reconciliation_{location}_{market}",
    title=f"Invoice reconciliation — {location} ({market})",
    meta={"Location": location, "Market": market, "Intervals": f"{s['intervals']:,}",
          "$ Variance (inv − exp)": f"${s['variance']:+,.2f}"})

_common.data_status(st, path=price_store, rows=len(price_df),
                    span=(start.date(), end_excl.date()))
