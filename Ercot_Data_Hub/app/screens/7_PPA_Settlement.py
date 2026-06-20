"""PPA settlement — actual 15-min generation × market price vs a PPA price.

Guided flow: ① pick asset/units · ② settlement terms · ③ period · then one
button that fetches any missing data from ERCOT and shows the settlement.
"""

from __future__ import annotations

import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import resource_catalog as rc  # noqa: E402
import node_generation as ng  # noqa: E402
import node_prices as npx  # noqa: E402
import pull_nodes as pn  # noqa: E402
import settlement_points as sp  # noqa: E402
from ercot_core import settlement as S  # noqa: E402
from ercot_core import tz  # noqa: E402
from ercot_core import prices as PX  # noqa: E402
from ercot_core import spp_archive as SPP  # noqa: E402
from ercot_core import dam_prices as DAMX  # noqa: E402
from ercot_core import sced_disclosure as SCD  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🧾 PPA Settlement")
st.write("Multiply an asset's **actual 15-min generation** by a **market price** "
         "(its node or a hub) and compare to your **PPA price**.")

with st.expander("How it works", expanded=False):
    st.markdown(
        "1. **Pick the asset** (resource node) and which units count — the "
        "co-located **battery is excluded by default**; toggle it on for a hybrid PPA.\n"
        "2. **Set the terms** — settle at the **node** or a **hub** (RT15 real-time "
        "prices) and enter your **PPA $/MWh**.\n"
        "3. **Pick a period ≥ 60 days back** (SCED generation publishes on a ~60-day lag).\n"
        "4. Press **▶ Run settlement**. Missing data is fetched live from ERCOT "
        "(~a minute the first time), then stored so it's instant next time.")


def _read(template, key_col, locs, start, end_excl):
    frames = []
    for year in range(start.year, end_excl.year + 1):
        path = pn._path(template, year)
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        df = df[df[key_col].isin(locs)]
        df = df[(df["interval_start"] >= start) & (df["interval_start"] < end_excl)]
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ─────────────────────────── sidebar: inputs ───────────────────────────────
with st.container(border=True):
    if not os.path.exists(rc.CATALOG_PATH):
        st.warning("No resource-node catalog yet — open **Node Explorer** and click "
                   "**Build catalog** first.")
        st.stop()

    # Universal plant (shared with Plant Value / Wind Capture) → resolve to its
    # ERCOT resource node via the crosswalk and default the picker to it.
    _uplant = _common.universal_plant_picker(st)
    _pref_node, _pref_msg = None, None
    if _uplant:
        try:
            from ercot_core import project_lookup as PL
            _cand = PL.candidate_nodes(_uplant.get("project_name") or _uplant["resource_name"])
            if _cand is not None and not _cand.empty:
                _pref_node = str(_cand.iloc[0]["resource_node"])
        except Exception:  # noqa: BLE001
            _pref_node = None
        _nm = _uplant.get("project_name", _uplant["resource_name"])
        _pref_msg = (f"🌎 **{_nm}** → node **{_pref_node}**." if _pref_node
                     else f"🌎 **{_nm}** — couldn't auto-resolve to an ERCOT node; pick one below.")

    st.header("① Asset")
    query = st.text_input("Search node", placeholder="e.g. AZURE, FRYE, RNCH")
    cat = rc.load_catalog()
    all_nodes = sorted(cat["resource_node"].dropna().astype(str).unique().tolist())
    opts = all_nodes
    if query:
        matched = sorted(rc.search(query)["resource_node"].dropna().astype(str).unique().tolist())
        if matched:
            opts = matched
        else:
            st.caption(f"No nodes match “{query}” — showing all.")
    if _pref_msg:
        st.caption(_pref_msg)
    # Default to the universal plant's node when not actively searching.
    _nidx = opts.index(_pref_node) if (_pref_node in opts and not query) else 0
    node = st.selectbox("Resource node", opts, index=_nidx)

    sel_units = None
    units_df = S.node_units(node) if node else None
    if units_df is not None and not units_df.empty:
        storage = units_df.loc[units_df["is_storage"], "resource_name"].tolist()
        gen_units = units_df.loc[~units_df["is_storage"], "resource_name"].tolist()
        all_units = units_df["resource_name"].tolist()
        include_batt = st.toggle("Include co-located battery", value=False,
                                 help="Off: exclude storage (generation PPA). On: include it (hybrid PPA).")
        sel_units = st.multiselect(
            "Units settled", all_units, default=(all_units if include_batt else gen_units),
            format_func=lambda u: f"🔋 {u} (battery)" if u in storage else u)
        if storage and not include_batt:
            st.caption(f"Battery excluded: {', '.join(storage)}")

    scale_pct = st.number_input(
        "Output scaling (%)", min_value=0.0, value=100.0, step=5.0,
        help="Scale the asset's actual 15-min output. 100 = as-metered. 50 = a 50% "
             "pro-rata PPA share. 150 = model a 1.5× larger plant with the same shape.")
    cap_on = st.checkbox(
        "Cap at a contracted MW", value=False,
        help="Limit each 15-min interval to a contracted capacity (as-available up to "
             "the cap). Applied after scaling.")
    mw_cap = st.number_input("Contracted MW", min_value=0.0, value=100.0, step=10.0) \
        if cap_on else None
    mw_scale = scale_pct / 100.0
    if mw_scale != 1.0 or mw_cap is not None:
        bits = []
        if mw_scale != 1.0:
            bits.append(f"scaled to {scale_pct:g}%")
        if mw_cap is not None:
            bits.append(f"capped at {mw_cap:g} MW")
        st.caption("⚙️ Output " + " · ".join(bits) + " — not as-metered.")

    st.header("② Settlement terms")
    ref_kind = st.radio(
        "Settle at", ["Node SPP", "Trading Hub"], index=1, horizontal=True,
        help="Hub RT15 reads from the complete local store (instant). Node/zone RT15 "
             "comes from the ERCOT API — fast for the last ~80 days, slower (archive "
             "download) for older windows, and needs API credentials.")
    if ref_kind == "Trading Hub":
        hub = st.selectbox("Hub", sp.HUBS, index=sp.HUBS.index("HB_WEST"))
        hub_loc = hub
        ref_location = hub
    else:
        hub = st.selectbox("Also show basis vs hub", ["(none)"] + sp.HUBS)
        hub_loc = None if hub == "(none)" else hub
        ref_location = node
    # Settlement is RT15 (real-time 15-min SPP). DAM is intentionally not offered:
    # the hub store is RT15-only and historical Day-Ahead prices aren't available.
    market = "RT15"
    st.caption("Settled on **RT15** (real-time 15-min SPP).")
    ppa = st.number_input("PPA price ($/MWh)", value=30.0, step=1.0)
    floor_on = st.checkbox(
        "Apply a price floor", value=True,
        help="Standard VPPA lever for negative/low prices. Default: a $0 floor where "
             "nothing settles below it (unsettled below floor). Uncheck for no floor at all "
             "(negatives settle at the raw price).")
    if floor_on:
        price_floor = st.number_input("Floor ($/MWh)", value=0.0, step=1.0)
        settle_below_floor = st.radio(
            "Below the floor…", ["No settlement (unsettled below floor)",
                                 "PPA still settles (market floored at the floor)"],
            index=0,
            help="Most VPPAs suspend settlement when price < floor — no money changes "
                 "hands. Choose the second option to keep paying the PPA with the market "
                 "leg clipped to the floor.").startswith("PPA")
    else:
        price_floor, settle_below_floor = None, False

    st.header("③ Period")
    start_d, end_d = _common.period_picker(st, key="settle", default_mode="Month")
    st.caption(f"**{start_d} → {end_d}** · pick **Month/Quarter/Year** for quick spans "
               "or **Custom days** for exact dates. SCED needs dates ≥60 days back.")

# ─────────────────────────── main: clamp, status, run ──────────────────────
_common.assumptions_bar(st, {"🏷️ Asset": (_uplant.get("project_name") or _uplant.get("resource_name"))
                             if _uplant else "—", "📅": f"{start_d} → {end_d}"})

if start_d > end_d:
    st.error(f"Start ({start_d}) is after End ({end_d}). Fix the period above.")
    st.stop()

# Generation (and node prices) only exist up to the ~60-day SCED lag. Clamp the
# working window to what can exist; remember what was requested.
req_start_d, req_end_d = start_d, end_d
avail_end = SCD.latest_available_date()
clamped = end_d > avail_end
if clamped:
    end_d = avail_end
if start_d > end_d:
    st.error(f"Your whole period is inside the ~60-day SCED lag — no generation is "
             f"published yet (available through ~{avail_end}). Pick an earlier period.")
    st.stop()
start = pd.Timestamp(start_d)
end_excl = pd.Timestamp(end_d) + pd.Timedelta(days=1)
period_days = (end_d - start_d).days + 1

units_label = ", ".join(sel_units) if sel_units else "all units"
adj_bits = []
if mw_scale != 1.0:
    adj_bits.append(f"scaled to {scale_pct:g}%")
if mw_cap is not None:
    adj_bits.append(f"capped at {mw_cap:g} MW")
adj_label = f" · output **{' · '.join(adj_bits)}**" if adj_bits else ""
st.info(f"**{node}** · units: {units_label}{adj_label} · settle at **{ref_location}** "
        f"({market}) · PPA **\\${ppa:,.2f}/MWh** · requested **{req_start_d} → {req_end_d}**"
        + (f"  \n⏳ Generation is only published through **~{avail_end}**, so the settlement "
           f"window is **{start_d} → {end_d}**." if clamped else ""))

ref_type = "Trading Hub" if ref_kind == "Trading Hub" else "Resource Node"
_now = tz.now_central().tz_localize(None)  # ERCOT "today" for the archive-window test
_archive_window = (pd.Timestamp(start_d) < _now - pd.Timedelta(days=80))


def _price_for(loc, ltype, fetch):
    if market == "RT15" and ltype == "Trading Hub":          # complete RT store
        df = PX.hub_store_prices([loc], start, end_excl)
        if not df.empty:
            return df
    if market == "DAM" and ltype == "Trading Hub":           # DAM hub store
        df = DAMX.dam_store_prices([loc], start, end_excl)
        if not df.empty:
            return df
    df = _read(pn.PRICE_TEMPLATE, "location", [loc], start, end_excl)  # cached
    if not df.empty:
        df = df[df["market"] == market]
    if not df.empty:
        return df
    if fetch:
        try:
            if market == "RT15":      # ERCOT API: live (recent) + archive (older, slow)
                got = SPP.fetch_rtm_spp([loc], start_d, end_d, location_type=ltype, log=lambda m: None)
            else:                     # DAM — ERCOT API (hourly, long retention)
                got = DAMX.fetch_dam_spp([loc], start_d, end_d, location_type=ltype, log=lambda m: None)
        except Exception as e:
            st.warning(f"{market} price fetch failed for {loc}: {e}")
            got = pd.DataFrame()
        if not got.empty:
            pn._merge_save(got, pn.PRICE_TEMPLATE, pn.PRICE_KEY)
        return got
    return pd.DataFrame()


def _days(df, col="interval_start"):
    return pd.to_datetime(df[col]).dt.normalize().nunique() if (df is not None and not df.empty) else 0


gen_df = _read(pn.GEN_TEMPLATE, "resource_node", [node], start, end_excl)
ref_cached = _price_for(ref_location, ref_type, fetch=False)
hub_src = " (full local store)" if (market == "RT15" and ref_type == "Trading Hub") else ""

s1, s2 = st.columns(2)
s1.markdown(f"**Generation cached:** {_days(gen_df)} / {period_days} days")
s2.markdown(f"**{ref_location} {market} price cached:** {_days(ref_cached)} / {period_days} days{hub_src}")

run = st.button("▶ Run settlement", type="primary", use_container_width=True)
auto = st.checkbox("Fetch missing data from ERCOT to fill the whole period (live)", value=True,
                   help="Fills any days in the period that aren't cached. Node prices older "
                        "than ~80 days download from the ERCOT archive and can take several minutes.")

if sel_units is not None and len(sel_units) == 0:
    st.warning("Select at least one unit in the sidebar.")
    st.stop()

# Signature of everything that affects the numbers (NOT the perspective toggle),
# so the result is recomputed only on Run / input change — toggling perspective
# just re-renders the stored result.
_sig = (node, tuple(sel_units or []), ref_location, market, float(ppa), str(start_d),
        str(end_d), price_floor, settle_below_floor, mw_scale, mw_cap, hub_loc)

if run:
    # Fill the WHOLE window when fetching — don't stop at partial cached data.
    if auto:
        with st.status("Fetching from ERCOT to fill the period…", expanded=True) as status:
            st.write(f"Generation {start_d} → {end_d} (cached days reused)…")
            g = ng.fetch_generation([node], start, pd.Timestamp(end_d), verbose=False)
            pn._merge_save(g, pn.GEN_TEMPLATE, pn.GEN_KEY)
            if ref_type == "Resource Node" and _archive_window:
                st.write(f"{ref_location} {market} price — older dates download from the archive, "
                         "this can take several minutes…")
            else:
                st.write(f"{ref_location} {market} price…")
            _price_for(ref_location, ref_type, fetch=True)
            status.update(label="Fetch complete.", state="complete")
        gen_df = _read(pn.GEN_TEMPLATE, "resource_node", [node], start, end_excl)

    frames = [_price_for(ref_location, ref_type, fetch=False)]  # ref already filled above
    if hub_loc:  # basis legs: best-effort from store/cache
        if node != ref_location:
            frames.append(_price_for(node, "Resource Node", fetch=False))
        if hub_loc != ref_location:
            frames.append(_price_for(hub_loc, "Trading Hub", fetch=False))
    price_df = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()

    if gen_df.empty:
        st.error("No generation for this node/window — the dates may be inside the 60-day lag, "
                 "or this node has no SCED output. Try an earlier window.")
        st.stop()
    if price_df.empty or price_df[(price_df["location"] == ref_location) & (price_df["market"] == market)].empty:
        st.error(f"No **{market}** price available at **{ref_location}** for this window.  \n"
                 "• Hub **RT15** comes from the local store — if your window predates it, update "
                 "**Hub prices** on the Home page.  \n"
                 "• **Node/zone RT15** comes from the ERCOT API (needs credentials; older windows "
                 "download from the archive and can take several minutes — make sure **Fetch** is on).")
        st.stop()

    res = S.compute_settlement(gen_df, price_df, node, ppa, ref_location, market=market,
                               node_location=node, hub_location=hub_loc, units=sel_units,
                               price_floor=price_floor, settle_below_floor=settle_below_floor,
                               mw_scale=mw_scale, mw_cap=mw_cap)
    d = res["intervals"]
    if d.empty:
        st.warning("No overlapping generation + price intervals. Widen the window.")
        st.stop()
    ref_now = price_df[(price_df["location"] == ref_location) & (price_df["market"] == market)]
    st.session_state["settle"] = {
        "sig": _sig, "d": d, "s": res["summary"],
        "covered_days": _days(d),
        "cov_min": pd.to_datetime(d["interval_start"]).min().date(),
        "cov_max": pd.to_datetime(d["interval_start"]).max().date(),
        "gen_days": _days(gen_df),
        "ref_days": _days(ref_cached if not ref_cached.empty else ref_now),
    }

state = st.session_state.get("settle")
if state is None:
    st.caption("Set the options on the left, then press **▶ Run settlement**.")
    st.stop()
if state["sig"] != _sig:
    st.info("Inputs changed — press **▶ Run settlement** to update the numbers.")
    st.stop()
d, s = state["d"], state["s"]
covered_days, cov_min, cov_max = state["covered_days"], state["cov_min"], state["cov_max"]
gen_days, ref_days = state["gen_days"], state["ref_days"]
if covered_days < period_days:
    limiter = ("generation" if gen_days < ref_days else
               f"{ref_location} price" if ref_days < gen_days else "data")
    st.warning(
        f"⚠️ **Partial coverage:** this settles **{covered_days} of {period_days} days** "
        f"({cov_min} → {cov_max}) — the numbers below cover only those days, not the full "
        f"period.  \nGeneration present for **{gen_days}** day(s); {ref_location} {market} "
        f"price for **{ref_days}** day(s). The shorter side (**{limiter}**) is the limit. "
        f"Re-run with **Fetch** on to fill gaps"
        + (" — node prices for older dates download from the archive and can be slow." if _archive_window else "."))

# ─────────────────────────── results ───────────────────────────────────────
mwh, cap = s["total_mwh"], s["capture_price"]
gross = s["ppa_revenue"]          # Σ gen × PPA strike  (PPA cost to buyer = revenue to seller)
mktrev = s["merchant_revenue"]    # Σ gen × market price
net = s["cfd_settlement"]         # offtaker frame: mktrev − gross = Σ gen × (market − strike)

persp = st.radio("Perspective", ["Buyer (offtaker)", "Generator (seller)"], horizontal=True,
                 help="Same cash flows, framed from your side of the PPA. Buyer = you pay the "
                      "strike for the energy and the CfD settles vs market.")
buyer = persp.startswith("Buyer")
if price_floor is None:
    floor_note = ""
elif s.get("settle_below_floor"):
    floor_note = (f" Market price floored at **\\${price_floor:,.2f}** in "
                  f"**{s.get('floored_intervals', 0):,}** interval(s) (PPA still settles there).")
else:
    floor_note = (f" **No settlement** below **\\${price_floor:,.2f}** — "
                  f"**{s.get('excluded_intervals', 0):,}** interval(s) / "
                  f"**{s.get('excluded_mwh', 0):,.0f} MWh** unsettled (price < floor).")

head = (f"Over **{cov_min} → {cov_max}** ({covered_days} day(s) with data), **{node}** "
        f"({units_label}) produced **{mwh:,.0f} MWh**, captured at **\\${cap:,.2f}/MWh** "
        f"({ref_location} {market}).")

if buyer:
    who = "the **offtaker receives**" if net >= 0 else "the **offtaker pays**"
    st.success(head + f" At a **\\${ppa:,.2f}** PPA your **gross PPA cost** is "
               f"**\\${gross:,.0f}**; the energy's **market value** is **\\${mktrev:,.0f}**, so "
               f"the CfD/swap settles **\\${abs(net):,.0f}** — {who}.{floor_note}")
    m = st.columns(4)
    m[0].metric("Generation", f"{mwh:,.0f} MWh")
    m[1].metric("Capture price", f"${cap:,.2f}/MWh", help="Generation-weighted market price")
    m[2].metric("PPA cost (gross)", f"${gross:,.0f}",
                help="Σ gen × PPA price — what you pay for the energy at the strike.")
    m[3].metric("Market revenue", f"${mktrev:,.0f}",
                help="Σ gen × market price — value of the energy at market (offsets your cost).")
    m2 = st.columns(4)
    m2[0].metric("Net CfD settlement (to offtaker)", f"${net:,.0f}",
                 delta=("offtaker receives" if net >= 0 else "offtaker pays"),
                 delta_color=("normal" if net >= 0 else "inverse"),
                 help="Σ gen × (market − PPA). Positive ⇒ you (offtaker) receive from the "
                      "generator (market above strike); negative ⇒ you top them up to the strike.")
    m2[1].metric("Effective cost $/MWh", f"${(gross/mwh if mwh else 0):,.2f}",
                 help="Under a fixed PPA you pay the strike per MWh.")
else:
    gnet = -net   # generator sign: positive ⇒ generator receives
    who = "the generator **receives**" if gnet >= 0 else "the generator **pays**"
    st.success(head + f" Against a **\\${ppa:,.2f}** PPA, as-generated **revenue** is "
               f"**\\${gross:,.0f}** vs **merchant \\${mktrev:,.0f}**; the CfD/swap true-up is "
               f"**\\${abs(net):,.0f}** — {who}.{floor_note}")
    m = st.columns(4)
    m[0].metric("Generation", f"{mwh:,.0f} MWh")
    m[1].metric("Capture price", f"${cap:,.2f}/MWh", help="Generation-weighted market price")
    m[2].metric("Merchant revenue", f"${mktrev:,.0f}", help="Σ gen × market price")
    m[3].metric("PPA revenue", f"${gross:,.0f}", help="Σ gen × PPA price")
    m2 = st.columns(4)
    m2[0].metric("CfD / swap", f"${gnet:,.0f}",
                 delta=("generator receives" if gnet >= 0 else "generator pays"),
                 help="Σ gen × (PPA − market). Positive ⇒ generator receives from offtaker.")
    m2[1].metric("Effective $/MWh", f"${(gross/mwh if mwh else 0):,.2f}",
                 help="Under a fixed PPA the generator nets the PPA price.")

if "basis_settlement" in s:
    m2[2].metric("Basis (node − hub)", f"${s['basis_settlement']:,.0f}",
                 help=f"Σ gen × ({node} − {hub_loc}) {market}: locational congestion.")
m2[3].metric("Intervals", f"{s['intervals']:,}", help="15-min intervals that settled.")

# Unsettled volume due to the price floor (no-settlement-below mode).
excl_mwh = s.get("excluded_mwh", 0.0) or 0.0
if excl_mwh > 0:
    gross_gen = mwh + excl_mwh                       # settled + unsettled = total produced
    pct = (excl_mwh / gross_gen * 100) if gross_gen else 0.0
    m3 = st.columns(4)
    m3[0].metric("Unsettled (price < floor)", f"{excl_mwh:,.0f} MWh",
                 delta=f"{pct:.1f}% of output", delta_color="off",
                 help=f"Generation in intervals where {ref_location} {market} was below the "
                      f"${price_floor:,.2f} floor — the VPPA does not settle on it. The energy "
                      f"may still have been produced and sold into the real-time market; it "
                      f"just isn't part of the swap.")
    m3[1].metric("Unsettled intervals", f"{s.get('excluded_intervals', 0):,}",
                 help="15-min intervals excluded by the floor (price < floor).")
    m3[2].metric("Total output", f"{gross_gen:,.0f} MWh",
                 help="Settled + unsettled = all metered generation in the window.")
    lost = excl_mwh * ppa
    m3[3].metric("PPA value forgone", f"${lost:,.0f}",
                 help=f"Unsettled MWh × \\${ppa:,.2f} PPA — strike revenue not earned on the "
                      f"sub-floor hours (vs. settling them).")

with st.expander("Which number is my PPA?"):
    st.markdown(
        "**Buyer (offtaker):**\n"
        "- **PPA cost (gross)** = Σ gen × strike — what you owe for the energy.\n"
        "- **Market revenue** = what that energy is worth at market (offsets the cost).\n"
        "- **Net CfD settlement (to offtaker)** = (market − strike) × gen. Positive = you "
        "receive; negative = you pay. (All settlement on this app is signed this way.)\n\n"
        "**Generator (seller):** the same numbers with the CfD sign flipped — positive CfD = "
        "generator receives.\n\n"
        "Add the **Basis** line when you settle at a hub but generate at a node (congestion exposure).")

d = d.sort_values("interval_start")
cost_lbl = "PPA cost (cum $)" if buyer else "PPA revenue (cum $)"
mkt_lbl = "Market value (cum $)" if buyer else "Merchant (cum $)"
d[mkt_lbl] = d["merchant"].cumsum()
d[cost_lbl] = d["ppa_revenue"].cumsum()
fig = go.Figure()
for col in (mkt_lbl, cost_lbl):
    fig.add_trace(go.Scatter(x=d["interval_start"], y=d[col], name=col, mode="lines"))
fig.update_layout(height=380, hovermode="x unified", margin=dict(t=20, b=10),
                  yaxis_title="Cumulative $", legend=dict(orientation="h", y=1.02))
st.plotly_chart(fig, use_container_width=True)

with st.expander("Interval detail"):
    cols = ["interval_start", "mw", "mwh"]
    cols += [c for c in ("price_raw", "price") if c in d.columns]
    cols += ["merchant", "ppa_revenue", "cfd"]
    cols += [c for c in ("node_price", "hub_price", "basis") if c in d.columns]
    st.dataframe(d[cols], hide_index=True, use_container_width=True, height=360)
_export.download_block(
    st, d, name=f"settlement_{node}_{ref_location}_{market}_{start_d}_{end_d}",
    title=f"PPA settlement — {node} ({ref_location} {market})",
    meta={"Node": node, "Settles at": f"{ref_location} {market}", "PPA": f"${ppa:,.2f}/MWh",
          "Period": f"{start_d} → {end_d}", "Net to offtaker": f"${net:,.0f}"})
