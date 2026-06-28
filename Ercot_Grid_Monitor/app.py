"""ERCOT Grid Monitor — a standalone nodal price map + grid-event alerts.

Free, gridstatus-powered recreation of GridStatus.io's Starter-tier price map
and SMS/email alerts. No API key needed for prices.
"""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st

import alerts
import coords
import ercot
import spmap

st.set_page_config(page_title="ERCOT Grid Monitor", page_icon="⚡", layout="wide")

# Diverging price ramp (cheap→expensive): blue → yellow → red.
_RAMP = [
    (0.00, (44, 123, 182)), (0.25, (171, 217, 233)), (0.50, (255, 255, 191)),
    (0.75, (253, 174, 97)), (1.00, (215, 25, 28)),
]


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _price_color(v, vmin, vmax):
    t = 0.5 if vmax <= vmin else min(1.0, max(0.0, (v - vmin) / (vmax - vmin)))
    for (s0, c0), (s1, c1) in zip(_RAMP, _RAMP[1:]):
        if t <= s1:
            local = 0.0 if s1 == s0 else (t - s0) / (s1 - s0)
            return list(_lerp(c0, c1, local)) + [210]
    return list(_RAMP[-1][1]) + [210]


@st.cache_data(show_spinner="Loading prices…", ttl=600)
def _snapshot(location_type, market, locs, start, end):
    """(avg-price-per-location df, source label). Reads the ercot-suite data lake
    first; falls back to a live gridstatus pull for anything it doesn't cover."""
    df, source = ercot.get_prices(list(locs), location_type, market, start, end)
    if df.empty:
        return df, source
    agg = df.groupby("location", as_index=False).agg(avg_spp=("spp", "mean"),
                                                      min_spp=("spp", "min"),
                                                      max_spp=("spp", "max"),
                                                      n=("spp", "size"))
    return agg, source


st.title("⚡ ERCOT Grid Monitor")
st.caption("Nodal price map + grid-event alerts — free, gridstatus-powered. "
           "A self-hosted take on GridStatus.io's Starter tier.")

tab_map, tab_alerts = st.tabs(["📍 Price Map", "🔔 Alerts"])

# =========================================================================== #
# Price map
# =========================================================================== #
with tab_map:
    with st.container(border=True):
        c1, c2 = st.columns(2)
        location_type = c1.radio("Settlement-point type",
                                  ["Trading Hub", "Load Zone", "Resource Node"],
                                  horizontal=True,
                                  help="Resource Node = individual plant settlement "
                                       "points cached in the suite node-price lake.")
        market = c2.radio("Market", ["RT15", "DAM"], horizontal=True,
                          help="RT15 = real-time 15-min; DAM = day-ahead hourly.")
        today = pd.Timestamp.now(tz="US/Central").date()
        mode = st.radio("Date range", ["Custom", "Month", "Year"], horizontal=True,
                        help="Month/Year pull the whole period from the suite data lake. "
                             "Custom is best for recent dates (live gridstatus).")
        if mode == "Month":
            _MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            yc, mc = st.columns(2)
            yr = yc.selectbox("Year", list(range(today.year, 2009, -1)), key="m_year")
            mo = mc.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                              format_func=lambda i: _MONTHS[i - 1], key="m_month")
            start_d = pd.Timestamp(yr, mo, 1).date()
            end_d = (pd.Timestamp(yr, mo, 1) + pd.offsets.MonthEnd(1)).date()
            end_d = min(end_d, today)
        elif mode == "Year":
            yr = st.selectbox("Year", list(range(today.year, 2009, -1)), key="y_year")
            start_d = pd.Timestamp(yr, 1, 1).date()
            end_d = min(pd.Timestamp(yr, 12, 31).date(), today)
        else:
            d1, d2 = st.columns(2)
            start_d = d1.date_input("From", value=today - pd.Timedelta(days=1), key="m_from")
            end_d = d2.date_input("To", value=today, key="m_to",
                                  help="gridstatus serves recent dates; keep it near today.")
        st.caption(f"Window: **{start_d} → {end_d}**")
        go = st.button("Load price map", type="primary")

    cf = coords.coords_frame(location_type)
    if location_type == "Resource Node":
        st.caption(f"📍 {len(cf)} resource nodes on the map "
                   f"(actual plant sites; only nodes cached in the suite lake).")
    else:
        st.caption(f"📍 {len(cf)} {location_type.lower()}s on the map "
                   f"(representative regional centroids).")

    if go:
        prices, source = _snapshot(location_type, market, tuple(cf["location"]),
                                   str(start_d), str(end_d))
        if prices.empty:
            st.warning("No prices for that window. Try a more recent range "
                       "(gridstatus only serves recent SPP).")
        else:
            st.caption(f"📦 Source: **{source}**" + (
                "" if source == "data lake"
                else " — not in the suite data lake for this type/market/window."))
            geo = cf.merge(prices, on="location", how="inner").dropna(subset=["avg_spp"])
            # Default the colour scale to the actual spread of THIS data so the full
            # blue→red ramp is used and cheap vs. expensive locations are easy to tell
            # apart. (Anchoring at $0 flattens everything to one colour when prices
            # are clustered, e.g. all hubs $23–$34.) Percentiles ignore a lone outlier;
            # fall back to true min/max when the spread is tiny.
            lo = float(geo["avg_spp"].quantile(0.05))
            hi = float(geo["avg_spp"].quantile(0.95))
            if hi - lo < 1.0:
                lo, hi = float(geo["avg_spp"].min()), float(geo["avg_spp"].max())
            cc1, cc2 = st.columns(2)
            vmin = cc1.number_input("Colour scale min ($/MWh)", value=round(lo, 1),
                                    help="Locations at or below this are fully blue.")
            vmax = cc2.number_input("Colour scale max ($/MWh)", value=round(max(hi, lo + 1), 1),
                                    help="Locations at or above this are fully red.")
            geo["_fill"] = geo["avg_spp"].map(lambda v: _price_color(v, vmin, vmax))
            geo["price_label"] = geo["avg_spp"].map(lambda v: f"${v:,.2f}")
            geo["min_label"] = geo["min_spp"].map(lambda v: f"${v:,.2f}")
            geo["max_label"] = geo["max_spp"].map(lambda v: f"${v:,.2f}")
            if location_type == "Resource Node":
                geo["name"] = geo["location"].map(
                    lambda loc: coords.NODE_COORDS.get(loc, (0, 0, loc))[2])
                # Authoritative substation + load zone from NP4-160-SG.
                attrs = spmap.node_attrs()
                geo["substation"] = geo["location"].map(
                    lambda loc: (attrs.get(loc) or {}).get("substation") or "—")
                geo["zone"] = geo["location"].map(
                    lambda loc: (attrs.get(loc) or {}).get("load_zone") or "—")
                geo["trust"] = geo["location"].map(
                    lambda loc: (coords.NODE_META.get(loc) or {}).get("trust") or "—")
                geo["gen✓"] = geo["location"].map(
                    lambda loc: bool((coords.NODE_META.get(loc) or {}).get("gen_confirmed")))
            else:
                geo["name"] = geo["location"]

            unit = {"Trading Hub": "hubs", "Load Zone": "zones",
                    "Resource Node": "nodes"}[location_type]
            cheap = geo.loc[geo["avg_spp"].idxmin()]
            pricey = geo.loc[geo["avg_spp"].idxmax()]
            m = st.columns(4)
            m[0].metric(f"{unit.capitalize()}", f"{len(geo):,}")
            m[1].metric(f"Cheapest · {cheap['name']}", f"${cheap['avg_spp']:,.2f}",
                        help=f"Lowest-average {unit[:-1]} over the window.")
            m[2].metric(f"Avg of {unit}", f"${geo['avg_spp'].mean():,.2f}")
            m[3].metric(f"Priciest · {pricey['name']}", f"${pricey['avg_spp']:,.2f}",
                        help=f"Highest-average {unit[:-1]} over the window.")

            layer = pdk.Layer(
                "ScatterplotLayer", id="prices", data=geo,
                get_position=["longitude", "latitude"], get_fill_color="_fill",
                get_radius=1600, radius_min_pixels=6, radius_max_pixels=28,
                pickable=True, auto_highlight=True)
            deck = pdk.Deck(
                layers=[layer], map_style=None,
                initial_view_state=pdk.ViewState(latitude=31.2, longitude=-99.3, zoom=4.7),
                tooltip={"html": "<b>{name}</b><br/>"
                                 "<span style='opacity:0.7'>{location}</span><br/>"
                                 + ("{substation} · {zone}<br/>"
                                    if location_type == "Resource Node" else "")
                                 + "Avg {price_label} /MWh<br/>"
                                   "High {max_label} · Low {min_label}"})
            st.pydeck_chart(deck, use_container_width=True)

            stops = ", ".join(f"rgb{_RAMP[i][1]}" for i in range(len(_RAMP)))
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;font-size:0.85em'>"
                f"<span>${vmin:,.0f}</span>"
                f"<div style='flex:1;height:12px;border-radius:6px;"
                f"background:linear-gradient(to right, {stops})'></div>"
                f"<span>${vmax:,.0f}</span></div>", unsafe_allow_html=True)

            st.subheader(f"By {unit[:-1]}")
            by = geo.sort_values("avg_spp", ascending=False)
            chart_key = "name" if location_type == "Resource Node" else "location"
            st.bar_chart(by.set_index(chart_key)["avg_spp"],
                         x_label=location_type, y_label="Avg $/MWh", horizontal=True)

            tbl_cols = (["name", "location", "substation", "zone", "trust", "gen✓",
                         "avg_spp", "min_spp", "max_spp", "n"]
                        if location_type == "Resource Node"
                        else ["location", "avg_spp", "min_spp", "max_spp", "n"])
            show = (by[tbl_cols]
                    .rename(columns={"name": "plant", "location": location_type,
                                     "substation": "substation", "zone": "load zone",
                                     "avg_spp": "avg $/MWh",
                                     "min_spp": "low $/MWh", "max_spp": "high $/MWh",
                                     "n": "intervals"}))
            st.dataframe(show, hide_index=True, use_container_width=True,
                         column_config={
                             "avg $/MWh": st.column_config.NumberColumn(format="$%.2f"),
                             "low $/MWh": st.column_config.NumberColumn(format="$%.2f"),
                             "high $/MWh": st.column_config.NumberColumn(format="$%.2f"),
                         })
            st.download_button("⬇ Download CSV", show.to_csv(index=False).encode(),
                               file_name=f"ercot_price_map_{location_type.replace(' ', '_')}.csv")
    else:
        st.info("Pick a type, market and window, then **Load price map**.")

    # Full ERCOT resource-node reference (NP4-160-SG) — all ~1,024 nodes with
    # their authoritative substation / load zone, whether or not we can plot them.
    with st.expander("📖 All ERCOT resource nodes (NP4-160 mapping)"):
        ref = spmap.load_mapping()
        if ref.empty:
            st.caption("Mapping unavailable (needs one ERCOT fetch).")
        else:
            plotted = set(coords.NODES)
            ref = ref.assign(plant=ref["node"].map(coords.NODE_NAMES).fillna("—"),
                             mapped=ref["node"].isin(plotted))
            q = st.text_input("Filter (node / substation / zone)", key="np4_q").strip().upper()
            view = ref
            if q:
                mask = (ref["node"].str.upper().str.contains(q, na=False)
                        | ref["plant"].astype(str).str.upper().str.contains(q, na=False)
                        | ref["substation"].astype(str).str.upper().str.contains(q, na=False)
                        | ref["load_zone"].astype(str).str.upper().str.contains(q, na=False))
                view = ref[mask]
            st.caption(f"{len(ref):,} resource nodes · {ref['mapped'].sum()} plotted on the map above "
                       f"· published {ref['publish_date'].iloc[0]}")
            st.dataframe(
                view[["node", "plant", "substation", "load_zone", "kv", "electrical_bus",
                      "psse_bus", "mapped"]].sort_values("node"),
                hide_index=True, use_container_width=True,
                column_config={"mapped": st.column_config.CheckboxColumn("on map")})
            st.download_button("⬇ Download full node mapping (CSV)",
                               ref.to_csv(index=False).encode(),
                               file_name="ercot_resource_node_mapping_NP4-160.csv")

# =========================================================================== #
# Alerts
# =========================================================================== #
with tab_alerts:
    cfg = alerts.load_config()
    rules = alerts.load_rules(cfg)
    all_locs = coords.HUBS + coords.ZONES

    st.caption("Get an email or text when an ERCOT price goes above or below a "
               "level you set. No data setup needed — prices come live from ERCOT.")

    # ---- Your alerts ------------------------------------------------------- #
    st.subheader("Your alerts")
    if not rules:
        st.info("No alerts yet. Add one below 👇")
    for i, r in enumerate(rules):
        direction = "rises above" if r.op in (">", ">=") else "drops below"
        when = "real-time" if r.metric == "rt_price" else "day-ahead"
        c = st.columns([0.62, 0.16, 0.12])
        c[0].markdown(
            f"{'🔔' if r.enabled else '🔕'} When **{coords.LABELS.get(r.location, r.location)}** "
            f"{when} price **{direction} ${r.threshold:,.0f}/MWh**")
        new_on = c[1].toggle("On", value=r.enabled, key=f"on_{i}")
        if new_on != r.enabled:
            cfg["rules"][i]["enabled"] = new_on
            alerts.save_config(cfg)
            st.rerun()
        if c[2].button("Remove", key=f"rm_{i}"):
            cfg["rules"].pop(i)
            alerts.save_config(cfg)
            st.rerun()

    # ---- Add an alert ------------------------------------------------------ #
    with st.expander("➕ Add an alert", expanded=not rules):
        f = st.columns([0.4, 0.25, 0.35])
        loc = f[0].selectbox("Price at", all_locs, format_func=coords.label,
                             index=all_locs.index("HB_HUBAVG"))
        direction = f[1].selectbox("Alert me when it", ["rises above", "drops below"])
        price = f[2].number_input("this price ($/MWh)", value=500.0, step=25.0)
        g = st.columns([0.5, 0.5])
        market = g[0].selectbox("Market", ["Real-time (15-min)", "Day-ahead"])
        if g[1].button("Add alert", type="primary"):
            op = ">" if direction == "rises above" else "<"
            metric = "rt_price" if market.startswith("Real") else "dam_price"
            ltype = "Trading Hub" if loc.startswith("HB_") else "Load Zone"
            rid = f"{loc}_{'gt' if op == '>' else 'lt'}_{int(price)}"
            ids = {x.get("id") for x in cfg.get("rules", [])}
            n = 2
            base = rid
            while rid in ids:
                rid = f"{base}_{n}"; n += 1
            cfg.setdefault("rules", []).append({
                "id": rid,
                "label": f"{coords.LABELS.get(loc, loc)} {direction} ${int(price)}",
                "metric": metric, "location": loc, "location_type": ltype,
                "op": op, "threshold": price, "cooldown_min": 60, "enabled": True,
            })
            alerts.save_config(cfg)
            st.success("Alert added.")
            st.rerun()

    # ---- Check now --------------------------------------------------------- #
    st.subheader("Check prices now")
    if rules:
        if st.button("🔄 Check my alerts against current prices", type="primary"):
            with st.spinner("Getting latest prices…"):
                st.session_state["ev"] = alerts.evaluate(rules)
        ev = st.session_state.get("ev")
        if ev:
            for res in ev:
                r = res["rule"]
                name = coords.LABELS.get(r.location, r.location)
                if res["error"]:
                    st.warning(f"⚠️ {name}: couldn't get a price right now.")
                elif res["value"] is None:
                    st.warning(f"⚠️ {name}: no recent price available.")
                elif res["triggered"]:
                    st.error(f"🔴 {name}: **${res['value']:,.2f}/MWh** — alert condition met!")
                else:
                    st.success(f"🟢 {name}: ${res['value']:,.2f}/MWh — all clear.")
    else:
        st.caption("Add an alert first.")

    # ---- Where to send ----------------------------------------------------- #
    st.subheader("Where to send alerts")
    email = cfg.get("email", {})
    sms = cfg.get("sms", {})
    sent_state = []
    if email.get("enabled"):
        sent_state.append(f"📧 email → {email.get('to')}")
    if sms.get("enabled"):
        sent_state.append(f"📱 text → {sms.get('to')}")
    st.caption("Currently sending to: " + (", ".join(sent_state) if sent_state
               else "**nobody yet** — set up email or text below."))

    with st.expander("📧 Email setup (Gmail or any SMTP)"):
        e_on = st.checkbox("Send me email alerts", value=bool(email.get("enabled")))
        e_to = st.text_input("Send alerts to (email address)",
                             value=((email.get("to") or [""])[0] if isinstance(email.get("to"), list)
                                    else email.get("to", "")))
        e_user = st.text_input("Your Gmail address (the account that sends)",
                               value=email.get("username", ""))
        e_pass = st.text_input("Gmail app password", type="password",
                               value=email.get("password", ""),
                               help="Google Account → Security → App passwords. Not your normal password.")
        with st.popover("Using a non-Gmail provider?"):
            e_host = st.text_input("SMTP host", value=email.get("host", "smtp.gmail.com"))
            e_port = st.number_input("SMTP port", value=int(email.get("port", 465)))
        if st.button("Save email settings"):
            cfg["email"] = {"enabled": e_on, "host": e_host, "port": int(e_port),
                            "username": e_user, "password": e_pass,
                            "from": e_user, "to": [e_to] if e_to else []}
            alerts.save_config(cfg)
            st.success("Saved.")
            st.rerun()

    with st.expander("📱 Text/SMS setup (Twilio)"):
        s_on = st.checkbox("Send me text alerts", value=bool(sms.get("enabled")))
        s_to = st.text_input("Send texts to (your phone, +1…)",
                             value=((sms.get("to") or [""])[0] if isinstance(sms.get("to"), list)
                                    else sms.get("to", "")))
        s_sid = st.text_input("Twilio Account SID", value=sms.get("account_sid", ""))
        s_tok = st.text_input("Twilio Auth Token", type="password",
                              value=sms.get("auth_token", ""))
        s_from = st.text_input("Your Twilio phone number (+1…)", value=sms.get("from", ""))
        if st.button("Save text settings"):
            cfg["sms"] = {"enabled": s_on, "account_sid": s_sid, "auth_token": s_tok,
                          "from": s_from, "to": [s_to] if s_to else []}
            alerts.save_config(cfg)
            st.success("Saved.")
            st.rerun()

    if st.button("✉️ Send a test alert now"):
        if not (email.get("enabled") or sms.get("enabled")):
            st.warning("Turn on email or text above first.")
        else:
            with st.spinner("Sending…"):
                ok_e, det_e = alerts.send_email(email, "ERCOT test alert",
                                                "✅ This is a test from ERCOT Grid Monitor.")
                ok_s, det_s = alerts.send_sms(sms, "✅ ERCOT Grid Monitor test alert.")
            msgs = [d for d in (det_e if email.get("enabled") else None,
                                det_s if sms.get("enabled") else None) if d]
            (st.success if (ok_e or ok_s) else st.error)("; ".join(msgs))

    st.caption("To check automatically around the clock, run **Install Alerts "
               "Schedule.command** in the app folder (checks every 15 min).")
