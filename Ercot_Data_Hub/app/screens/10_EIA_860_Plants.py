"""EIA-860 — the full ERCOT plant & generator directory (identity, siting, sizing)."""

from __future__ import annotations

import sys
import pathlib
import datetime as _dt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import pydeck as pdk  # noqa: E402
import streamlit as st  # noqa: E402

import eia860  # noqa: E402

# EIA Electricity Data Browser per-plant page (verified: #/plant/<id> resolves to
# that plant, e.g. .../plant/6145 → "Comanche Peak").
EIA_PLANT_URL = "https://www.eia.gov/electricity/data/browser/#/plant/{}"


def _hex_to_rgb(h: str) -> list[int]:
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]

# EIA-860 generator status codes. `status_group` is the coarse bucket (which
# EIA-860 schedule the unit sits in); `status` is the granular code below.
# Proposed codes are ordered by development maturity (least → most built).
STATUS_CODES = {
    "operable": [
        ("OP", "Operating"),
        ("SB", "Standby / backup — available but not normally used"),
        ("OA", "Out of service, expected to return within the year"),
        ("OS", "Out of service, not expected to return within the year"),
    ],
    "proposed": [
        ("P", "Planned — regulatory approvals not yet initiated"),
        ("L", "Regulatory approvals pending (application filed / under review)"),
        ("T", "Regulatory approvals received — not yet under construction"),
        ("U", "Under construction, ≤ 50% complete"),
        ("V", "Under construction, > 50% complete"),
        ("TS", "Construction complete, not yet in commercial operation"),
        ("OT", "Other"),
    ],
    "retired": [
        ("RE", "Retired"),
        ("IP", "Indefinitely postponed / canceled"),
    ],
}
# Flat code -> meaning, for labelling the `status` column in the table.
STATUS_LABELS = {code: meaning for items in STATUS_CODES.values() for code, meaning in items}

# Map colours. Hex strings — st.map reads a per-row colour column directly.
FUEL_COLORS = {
    "Solar": "#F4C20D", "Wind": "#36A2EB", "Gas": "#FF7043", "Other Gas": "#FFA270",
    "Coal": "#8D6E63", "Nuclear": "#AB47BC", "Storage": "#26C6DA", "Hydro": "#5C9DFF",
    "Oil": "#EF5350", "Biomass": "#66BB6A", "Other": "#9E9E9E",
}
STATUS_COLORS = {"operable": "#2ECC71", "proposed": "#F1C40F", "retired": "#7F8C8D"}
STATUS_MAP_LABELS = {"operable": "Operating", "proposed": "Planned / proposed", "retired": "Retired"}
DEFAULT_COLOR = "#9E9E9E"

# Page config is set centrally by the router (app/Home.py).
st.title("🗺️ EIA-860 — ERCOT Plant Directory")
st.caption("Every ERCOT plant/generator from EIA Form 860: identity, county, lat/lon, "
           "nameplate MW, technology/fuel, status, and online (or planned) date — "
           "operable, proposed, and retired.")


@st.cache_data(show_spinner=False)
def load(years):
    return eia860.load(years=list(years), region="ercot")


@st.cache_data(show_spinner="Fetching EIA-860M (monthly) from eia.gov…", ttl=3600)
def load_860m():
    """Current-year ERCOT plants from the monthly EIA-860M (plant-level, no API key)."""
    from ercot_core import reconcile as R
    return R.eia860m_plants()


cached = eia860.available_years(region="ercot")

with st.container(border=True):
    with st.expander("Get / update data", expanded=not cached):
        from ercot_core import tz
        _yr_now = tz.now_central().year
        yr = st.number_input("Year", min_value=2013, max_value=_yr_now,
                              value=_yr_now - 2, step=1)
        force = st.checkbox("Force re-download", value=False)
        if st.button(f"Download & build EIA-860 {int(yr)}"):
            with st.spinner(f"Downloading EIA-860 {int(yr)} from eia.gov…"):
                try:
                    df = eia860.build_year(int(yr), force_download=force)
                    st.success(f"Built {len(df):,} generators for {int(yr)}.")
                    load.clear()
                except Exception as e:
                    st.error(f"Failed: {e}")

if not cached:
    _common.empty_state(
        st, "No EIA-860 data cached yet.",
        hint="Use **Get / update data** in the sidebar, or "
             "`python datasets/eia923/eia860.py 2024`.",
        page="views/home.py", page_label="Go to Control Tower")

with st.container(border=True):
    st.header("Filters")
    year = st.selectbox("Vintage year", cached, index=len(cached) - 1)
    df = load((year,)).copy()
    # Spell out the EIA status code so it's both a column and a filter dimension.
    df["status_full"] = (df["status"].astype(str).str.strip().str.upper()
                         .map(STATUS_LABELS).fillna(df["status"]))
    sg = st.multiselect("Status group", sorted(df["status_group"].dropna().unique()),
                        default=["operable"])
    det_opts = sorted(df.loc[df["status_group"].isin(sg), "status_full"].dropna().unique())
    det_sel = st.multiselect("Detailed status", det_opts, default=det_opts,
                             help="The granular EIA-860 status, scoped to the status group(s) "
                                  "above. See the “What the status codes mean” reference.")
    fuels_sel = st.multiselect("Fuel", sorted(df["fuel_category"].dropna().unique()),
                               default=sorted(df["fuel_category"].dropna().unique()))
    county = st.text_input("County contains").strip().lower()
    name = st.text_input("Plant name contains").strip().lower()
    cmin = float(pd.to_numeric(df["nameplate_mw"], errors="coerce").fillna(0).min())
    cmax = float(pd.to_numeric(df["nameplate_mw"], errors="coerce").fillna(0).max())
    cap = st.slider("Nameplate MW range", 0.0, round(cmax, 0), (0.0, round(cmax, 0)))

f = df[df["status_group"].isin(sg) & df["status_full"].isin(det_sel)
       & df["fuel_category"].isin(fuels_sel)]
if county:
    f = f[f["county"].fillna("").str.lower().str.contains(county)]
if name:
    f = f[f["plant_name"].fillna("").str.lower().str.contains(name)]
f = f[pd.to_numeric(f["nameplate_mw"], errors="coerce").fillna(0).between(cap[0], cap[1])]

# Is this vintage EIA's preliminary Early Release (vs. the final annual file)?
is_er = "release" in df.columns and df["release"].astype(str).eq("early").any()

# Show status_full right after the raw status code; drop the constant `release` col.
f = f.copy()
cols = [c for c in f.columns if c != "release"]
cols.insert(cols.index("status") + 1, cols.pop(cols.index("status_full")))
f = f[cols]

vint = f"vintage {year}" + (" · Early Release" if is_er else "")
_common.data_status(st, rows=len(f), span=(vint, f"{len(df):,} generators total"))
if is_er:
    st.caption("⚠️ This is EIA's preliminary **Early Release** — figures are revised in the "
               "final annual file (published ~late the following year). Fine for scouting; "
               "treat as provisional.")

c = st.columns(4)
c[0].metric("Plants", f"{f['plant_id'].nunique():,}")
c[1].metric("Generators", f"{len(f):,}")
c[2].metric("Nameplate", f"{f['nameplate_mw'].sum():,.0f} MW")
top = f.groupby("fuel_category")["nameplate_mw"].sum().idxmax() if not f.empty else "—"
c[3].metric("Top fuel (MW)", top)

with st.expander("ℹ️ What the status codes mean"):
    st.caption("**status_group** is the coarse bucket (the EIA-860 schedule a unit sits in). "
               "**status** is the granular EIA code. Proposed codes run least → most built.")
    legend = pd.DataFrame(
        [(grp, code, meaning) for grp, items in STATUS_CODES.items()
         for code, meaning in items],
        columns=["status_group", "status", "meaning"])
    st.dataframe(legend, hide_index=True, use_container_width=True)

tab_tbl, tab_map, tab_mix, tab_860m = st.tabs(
    ["Generators", "Map", "Capacity mix", "🆕 Recent (860M)"])

with tab_tbl:
    show = f.sort_values("nameplate_mw", ascending=False).copy()
    show["eia_url"] = (EIA_PLANT_URL.rsplit("{", 1)[0]
                       + show["plant_id"].astype("Int64").astype(str))
    st.dataframe(show, hide_index=True, use_container_width=True, height=520,
                 column_config={"eia_url": st.column_config.LinkColumn(
                     "EIA page", display_text="Open ↗",
                     help="Open this plant on the EIA Electricity Data Browser.")})
    _export.download_block(st, show, name=f"eia860_ercot_{year}",
                           title=f"EIA-860 ERCOT plants — {year}",
                           meta={"Year": year, "Rows": f"{len(f):,}"})

with tab_map:
    geo = f.dropna(subset=["latitude", "longitude"]).copy()
    geo["latitude"] = pd.to_numeric(geo["latitude"], errors="coerce")
    geo["longitude"] = pd.to_numeric(geo["longitude"], errors="coerce")
    geo = geo.dropna(subset=["latitude", "longitude"])
    if geo.empty:
        st.caption("No coordinates for this selection.")
    else:
        color_by = st.radio("Colour by", ["Fuel type", "Status"], horizontal=True,
                            help="Fuel type → one colour per technology. "
                                 "Status → operating vs planned vs retired.")
        if color_by == "Fuel type":
            palette, key, label_of = FUEL_COLORS, "fuel_category", (lambda v: v)
        else:
            palette, key, label_of = STATUS_COLORS, "status_group", STATUS_MAP_LABELS.get
        geo["_color"] = geo[key].astype(str).map(palette).fillna(DEFAULT_COLOR)
        # deck.gl colour accessor must reference ONE column of [r,g,b,a] lists —
        # mixing column names with a literal alpha silently kills the layer.
        geo["_fill"] = geo["_color"].map(lambda h: _hex_to_rgb(h) + [200])
        # Bigger dots for bigger plants so the map reads at a glance.
        geo["nameplate_mw"] = pd.to_numeric(geo["nameplate_mw"], errors="coerce").fillna(0)
        geo["_size"] = (800 + geo["nameplate_mw"] * 12).clip(upper=9000)

        layer = pdk.Layer(
            "ScatterplotLayer", id="plants", data=geo,
            get_position=["longitude", "latitude"],
            get_fill_color="_fill",
            get_radius="_size", radius_min_pixels=4, radius_max_pixels=22,
            pickable=True, auto_highlight=True)
        deck = pdk.Deck(
            layers=[layer], map_style=None,
            initial_view_state=pdk.ViewState(latitude=31.2, longitude=-99.3, zoom=4.6),
            tooltip={"html": "<b>{plant_name}</b><br/>{fuel_category} · {nameplate_mw} MW · "
                             "{status_full}<br/><i>click to open its EIA page</i>"})
        ev = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object",
                             key="eia_map", use_container_width=True)

        # Legend — only the categories actually on the map, with their counts.
        present = [v for v in palette if (geo[key].astype(str) == v).any()]
        swatches = "&nbsp;&nbsp;".join(
            f"<span style='color:{palette[v]};font-size:1.3em'>●</span> {label_of(v)} "
            f"({int((geo[key].astype(str) == v).sum()):,})" for v in present)
        st.markdown(swatches, unsafe_allow_html=True)

        # Clicking a dot selects it → surface a link to that plant's EIA page.
        sel = getattr(ev, "selection", None)
        objs = (sel.get("objects") if isinstance(sel, dict)
                else getattr(sel, "objects", None)) or {}
        picked = objs.get("plants", []) if isinstance(objs, dict) else []
        if picked:
            p = picked[0]
            pid = int(p["plant_id"])
            st.markdown(f"📍 **{p['plant_name']}** · {p.get('fuel_category')} · "
                        f"{p.get('nameplate_mw')} MW &nbsp; "
                        f"[Open on EIA ↗]({EIA_PLANT_URL.format(pid)})")
        else:
            st.caption(f"{geo['plant_id'].nunique():,} plants · dot size ∝ MW · "
                       "click a dot to open its EIA page.")

with tab_mix:
    if f.empty:
        mix = pd.DataFrame()
    else:
        mix = (f.groupby(["fuel_category", "status_group"])["nameplate_mw"].sum()
               .unstack("status_group").fillna(0))
        # Sort by 'operable' when present, else by total capacity across the
        # selected groups (avoids sort_values(by=None) when operable is filtered out).
        sort_col = "operable" if "operable" in mix.columns else None
        mix = (mix.sort_values(by=sort_col, ascending=False) if sort_col
               else mix.loc[mix.sum(axis=1).sort_values(ascending=False).index])
    st.bar_chart(mix)

with tab_860m:
    st.caption("The annual EIA-860 above is the latest *final* vintage, so it lags ~18 months. "
               "**EIA-860M** is the *monthly* inventory — it catches plants too new for the "
               "annual file. It's plant-level (no per-generator or lat/lon detail) and needs "
               "network, but no API key.")
    if st.button("🔄 Refresh from eia.gov", help="Re-fetch the latest monthly file."):
        load_860m.clear()
    m = load_860m()
    if m.empty:
        st.warning("Couldn't fetch EIA-860M (needs network, and the newest file lags ~2 months). "
                   "Try **Refresh** again in a moment.")
    else:
        through = m["online"].max()
        only_new = st.checkbox(f"Only plants online after the {year} vintage", value=True,
                               help="The gap the annual file misses — i.e. the current-year adds.")
        view = m[m["online"] > pd.Timestamp(f"{int(year)}-12-31")] if only_new else m
        _common.data_status(st, rows=len(view),
                            span=("EIA-860M", f"current through {through:%b %Y}"))
        st.dataframe(view.sort_values("online", ascending=False), hide_index=True,
                     use_container_width=True, height=480)
        _export.download_block(st, view, name="eia860m_ercot_recent",
                               title="EIA-860M ERCOT plants (monthly)",
                               meta={"Through": f"{through:%b %Y}", "Rows": f"{len(view):,}"})
        st.caption("Tip: pick a plant here, then register it in **Project Builder** (resolve its "
                   "ERCOT node + add coordinates the forecasts need).")

st.caption("This directory is the key to matching ERCOT SCED resources to EIA plants "
           "(by county + capacity + online date) — used by the Reconciliation pages.")
