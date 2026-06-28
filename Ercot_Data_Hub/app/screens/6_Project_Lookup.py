"""Project Builder — turn a project name into a fully-wired Hub asset.

The on-ramp for standing up a new project the way the Markham and Azure Sky
portals were built: find its ERCOT resource node, pull the market data it
needs, capture its specs, and register it. Once registered, a project shows up
across every analysis page and can power its own settlement portal.

The ERCOT name-matching (catalog + crosswalk) runs on the back end; the page
just surfaces the answer and how confident it is.
"""

from __future__ import annotations

import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import paths, project_lookup  # noqa: E402
from ercot_core.eia_links import EIA_PLANT_URL  # noqa: E402

HUBS = ["North", "Houston", "South", "West", "Pan"]
TECHS = ["Solar", "Wind"]


# --------------------------------------------------------------------------
# Small helpers (kept page-local; nothing here is reused elsewhere)
# --------------------------------------------------------------------------
def _confidence(cand: dict) -> tuple[str, str]:
    """A plain-language match confidence (label, dot) from the back-end score."""
    if "crosswalk:name" in str(cand.get("match", "")):
        return "Strong", "🟢"
    score = int(cand.get("score", 0))
    if score >= 3:
        return "Strong", "🟢"
    if score == 2:
        return "Likely", "🟡"
    return "Possible", "⚪"


def _guess_tech(res: dict, cand: dict) -> str:
    """Best guess at Solar vs Wind from the queue / fyi / catalog hints."""
    blob = " ".join(str(x) for x in [
        cand.get("types"),
        (res.get("queue_matches") or [{}])[0].get("Fuel"),
        (res.get("queue_matches") or [{}])[0].get("Technology"),
        (res.get("queue_matches") or [{}])[0].get("Generation Type"),
        (res.get("ifyi") or {}).get("fuel"),
        res.get("name_used"),
    ]).upper()
    if "WIND" in blob:
        return "Wind"
    return "Solar"  # default; the user can flip it


def _month_ranges(start_ts, end_ts):
    """[(month_start, inclusive_month_end), …] covering [start, end] with no overlap."""
    out = []
    cur = pd.Timestamp(start_ts).normalize()
    end_ts = pd.Timestamp(end_ts).normalize()
    while cur <= end_ts:
        nxt = (cur + pd.offsets.MonthBegin(1)).normalize()
        out.append((cur, min(end_ts, nxt - pd.Timedelta(days=1))))
        cur = nxt
    return out


@st.cache_data(show_spinner=False)
def _eia_candidates(query: str, tech: str, limit: int = 8) -> list[dict]:
    """EIA-860 plants whose name matches `query` — capacity, county, coords.

    The EIA-860 directory is the richest source of capacity/county/lat-lon, so
    it's the natural autofill for projects the ERCOT queue doesn't cover.
    """
    import eia860
    yrs = eia860.available_years("ercot")
    if not yrs or not str(query).strip():
        return []
    df = eia860.load([max(yrs)], "ercot")
    if df.empty:
        return []
    q = re.escape(str(query).strip())
    sub = df[df["plant_name"].astype(str).str.contains(q, case=False, na=False)].copy()
    if sub.empty:
        return []
    if tech:  # prefer same-tech matches, but don't drop everything if none
        same = sub[sub["fuel_category"].astype(str).str.lower() == tech.lower()]
        sub = same if not same.empty else sub
    for c in ("latitude", "longitude", "nameplate_mw"):
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
    g = sub.groupby("plant_id", as_index=False).agg(
        plant_name=("plant_name", "first"), county=("county", "first"),
        lat=("latitude", "first"), lon=("longitude", "first"),
        capacity_mw=("nameplate_mw", "sum"), fuel=("fuel_category", "first"))
    g = g.sort_values("capacity_mw", ascending=False).head(limit)
    out = []
    for _, r in g.iterrows():
        fuel = str(r["fuel"]).lower()
        out.append({
            "plant_name": str(r["plant_name"]), "county": str(r["county"]),
            "lat": None if pd.isna(r["lat"]) else float(r["lat"]),
            "lon": None if pd.isna(r["lon"]) else float(r["lon"]),
            "capacity_mw": 0.0 if pd.isna(r["capacity_mw"]) else float(r["capacity_mw"]),
            "tech": "Solar" if fuel == "solar" else ("Wind" if fuel == "wind" else None),
        })
    return out


@st.cache_data(show_spinner=False)
def _registry_eia_ids(reg_key: tuple) -> dict:
    """project_name -> EIA plant_id. Primary: resource_name -> resource node ->
    EIA id (node_eia.json, built via the station-name→EIA-860 crosswalk). Fallback:
    nearest EIA-860 plant to the registry's authoritative lat/lon with a name check.
    reg_key is a hashable tuple of (project_name, resource_name, lat, lon)."""
    import json
    import math
    import eia860
    out = {}

    # Primary — the curated node→EIA crosswalk.
    try:
        reg_dir = pathlib.Path(paths.__file__).parent / "registry"
        node_eia = json.loads((reg_dir / "node_eia.json").read_text())
        cat = pd.read_parquet(paths.NODE_DATA_DIR.parent / "resource_node_catalog.parquet")
        sced2node = dict(zip(cat["sced_resource_name"], cat["resource_node"]))
    except Exception:
        node_eia, sced2node = {}, {}

    yrs = eia860.available_years("ercot")
    df = eia860.load([max(yrs)], "ercot").dropna(subset=["latitude", "longitude"]) if yrs else pd.DataFrame()
    if not df.empty:
        plants = df.groupby("plant_id", as_index=False).agg(
            name=("plant_name", "first"), lat=("latitude", "first"), lon=("longitude", "first"))
        stop = {"SOLAR", "WIND", "FARM", "PROJECT", "ENERGY", "STORAGE", "LLC", "CENTER",
                "POWER", "PLANT", "BESS", "THE", "OF"}

        def toks(s):
            return {w for w in re.split(r"[^A-Z0-9]+", str(s).upper()) if w and w not in stop and len(w) > 2}
        plants["toks"] = plants["name"].map(toks)

        def hv(a, b, c, d):
            p1, p2 = math.radians(a), math.radians(c); dp = math.radians(c - a); dl = math.radians(d - b)
            x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * 6371 * math.asin(math.sqrt(x))

    for pname, rname, lat, lon in reg_key:
        node = sced2node.get(rname)
        if node and node in node_eia:                       # primary path
            out[pname] = int(node_eia[node]["eia_id"]); continue
        if df.empty or lat is None or lon is None:          # fallback: lat/lon
            continue
        plants["_d"] = plants.apply(lambda r: hv(lat, lon, r["lat"], r["lon"]), axis=1)
        pt = toks(pname or "")
        for _, r in plants.nsmallest(3, "_d").iterrows():
            if r["_d"] <= 0.5 or (r["_d"] <= 12 and (pt & r["toks"])):
                out[pname] = int(r["plant_id"]); break
    return out


def _prefill(res: dict) -> dict:
    """Pull capacity / county / queue id out of the queue or fyi match."""
    qm = (res.get("queue_matches") or [{}])[0]
    fyi = res.get("ifyi") or {}
    cap = qm.get("Capacity (MW)") or fyi.get("capacity_mw")
    try:
        cap = float(cap)
    except (TypeError, ValueError):
        cap = 0.0
    return {
        "capacity_mw": cap,
        "county": str(qm.get("County") or fyi.get("county") or "").strip(),
        "queue_id": str(qm.get("Queue ID") or fyi.get("queue_id") or "").strip(),
    }


# --------------------------------------------------------------------------
# Header + what's already registered
# --------------------------------------------------------------------------
st.title("🏗️ Project Builder")
st.caption("Stand up a new project the way the **Markham** and **Azure Sky** portals were built: "
           "find its ERCOT resource node, pull the market data it needs, capture its specs, and "
           "register it. Registered projects appear across every analysis page (Plant Value, Wind "
           "Capture, PPA Settlement, the forecasts) and can power their own settlement portal.")

registered = project_lookup.registered_projects()
with st.expander(f"📋 {len(registered)} projects already registered", expanded=False):
    if registered:
        reg_key = tuple((r.get("project_name"), r.get("resource_name"),
                         r.get("lat"), r.get("lon")) for r in registered)
        eia_ids = _registry_eia_ids(reg_key)
        reg_df = pd.DataFrame([{
            "Project": r.get("project_name"),
            "Tech": r.get("tech"),
            "MW": r.get("capacity_mw"),
            "Hub": r.get("hub"),
            "County": r.get("county"),
            "Resource node": r.get("resource_name"),
            "EIA": (EIA_PLANT_URL.format(id=eia_ids[r.get("project_name")])
                    if r.get("project_name") in eia_ids else None),
        } for r in registered])
        st.dataframe(reg_df, hide_index=True, use_container_width=True,
                     column_config={"EIA": st.column_config.LinkColumn("EIA", display_text="EIA ↗")})
        st.caption("Re-entering an existing project name below lets you update its specs.")
    else:
        st.info("No projects registered yet.")

st.divider()

# --------------------------------------------------------------------------
# Step 1 — Find the project
# --------------------------------------------------------------------------
st.header("1 · Find the project")


@st.cache_data(show_spinner=False)
def _project_options() -> list[str]:
    """Searchable list: registered projects + every ERCOT queue project name."""
    names = {r["project_name"] for r in project_lookup.registered_projects()
             if r.get("project_name")}
    try:
        names.update(pd.read_parquet(paths.IFYI_ERCOT_PARQUET)["name"].dropna().astype(str))
    except Exception:
        pass
    return sorted(names, key=str.lower)


col1, col2 = st.columns([4, 1])
query = col1.selectbox(
    "Project name or ERCOT queue ID",
    options=_project_options(), index=None,
    placeholder="Search projects… or type a name / queue ID (e.g. 21INR0477)",
    accept_new_options=True,
    help="Pick a project from the list, or type its name or ERCOT interconnection "
         "queue ID (e.g. 21INR0477 / ercot-21inr0477).")
offline = col2.toggle("Offline", value=False,
                      help="Skip the live ERCOT queue lookup; use cached data only.")

if not query:
    st.info("Choose or type a project name or queue ID to begin.")
    st.stop()


@st.cache_data(show_spinner="Searching ERCOT…")
def _lookup(q, fetch):
    return project_lookup.lookup(q, allow_fetch=fetch)


res = _lookup(query.strip(), not offline)
name_used = res["name_used"]

# What ERCOT knows about the project (queue / fyi), shown plainly.
qm = res.get("queue_matches", [])
fyi = res.get("ifyi")
if qm or fyi:
    with st.container(border=True):
        st.markdown(f"**ERCOT identifies this as:** {name_used}")
        if fyi:
            st.markdown(
                f"{fyi.get('fuel', '—')} · {fyi.get('capacity_mw', '—')} MW · "
                f"{fyi.get('county', '—')} County · status: {fyi.get('status', '—')}  \n"
                f"Point of interconnection: {fyi.get('poi', '—')}"
                + (f"  ·  [project page]({fyi['url']})" if fyi.get("url") else ""))
        if qm:
            st.dataframe(pd.DataFrame(qm), hide_index=True, use_container_width=True)
if res.get("queue_note"):
    st.caption(res["queue_note"])

# --------------------------------------------------------------------------
# Step 2 — Confirm the ERCOT resource node + get its market data
# --------------------------------------------------------------------------
st.divider()
st.header("2 · Confirm its ERCOT resource node")

cands = res.get("candidates", [])
if not cands:
    st.error(f"No ERCOT resource node matched “{name_used}”. Try a more distinctive part of the "
             "name, or build the node catalog first (Node Explorer → Build catalog).")
    st.stop()

labels = []
for c in cands:
    lab, dot = _confidence(c)
    labels.append(f"{dot} {c['resource_node']}  ·  {lab} match")
pick = st.radio("Which node is this project?", range(len(cands)),
                format_func=lambda i: labels[i],
                help="The top option is usually right. Cross-check the units and county "
                     "against what ERCOT reported above.")
cand = cands[pick]
node = cand["resource_node"]
units = cand["units"]
av = cand["availability"]

a, b = st.columns(2)
a.markdown(f"**Resource node:** `{node}`")
a.markdown(f"**Generating units:** {', '.join(units)}")
a.markdown(f"**Type:** {', '.join(cand['types']) or '—'}")

# Market-data readiness — the data a portal actually consumes.
def _row(ok: bool, label: str, detail: str) -> str:
    return f"{'✅' if ok else '⬜'} **{label}** — {detail}"

have_price = av["price_rows_cached"] > 0
have_gen = av["gen_rows_cached"] > 0
have_sced = av["plant_sced_files"] > 0
b.markdown("**Market data cached for this node**")
b.markdown(_row(have_price, "Prices", f"{av['price_rows_cached']:,} rows"))
b.markdown(_row(have_gen, "Generation", f"{av['gen_rows_cached']:,} rows"))
b.markdown(_row(have_sced, "Per-unit SCED", f"{av['plant_sced_files']} file(s)"))

with st.expander("Why this node matched"):
    st.caption("Back-end match signals (catalog name overlap + plant-name crosswalk). "
               "You don't need these — they're shown only to sanity-check the pick.")
    st.code(cand.get("match", "—"))

# Pull missing market data straight from ERCOT (same engine as Node Explorer).
if not (have_price and have_gen):
    st.markdown("**Pull market data from ERCOT**")
    st.caption("Settlement-point prices (RT15) and SCED generation for this node. "
               "Historical node prices come from the ERCOT API (needs credentials on the "
               "API Keys page; the archive can be slow). Generation lags ~60 days.")
    import datetime as _dt
    today = _dt.date.today()
    pc1, pc2, pc3 = st.columns([2, 2, 1])
    start_d = pc1.date_input("Start", value=today - _dt.timedelta(days=425), key="pull_start")
    end_d = pc2.date_input("End", value=today - _dt.timedelta(days=75), key="pull_end")
    pc3.write("")
    if pc3.button("⬇️ Pull", type="primary", use_container_width=True):
        try:
            import node_generation as ng
            import pull_nodes as pn
            from ercot_core import spp_archive as SPP
            fetched_at = pd.Timestamp.now(tz="UTC")

            # Fetch a month at a time so we can show real progress (and save
            # incrementally) instead of one long, opaque blocking call. Prices use
            # the ERCOT API archive (spp_archive) — the public gridstatus feed only
            # retains recent SPP files, so it can't serve a node's price history.
            chunks = _month_ranges(pd.Timestamp(start_d), pd.Timestamp(end_d))
            total = max(1, len(chunks) * 2)  # prices + generation per month
            bar = st.progress(0.0, text="Starting…")
            done = 0
            for cs, ce in chunks:
                tag = f"{cs:%b %Y}"
                bar.progress(done / total, text=f"Prices · {tag}")
                price = SPP.fetch_rtm_spp([node], cs, ce, location_type="Resource Node",
                                          log=lambda *a: None)
                pn._merge_save(price, pn.PRICE_TEMPLATE, pn.PRICE_KEY)
                done += 1
                bar.progress(done / total, text=f"Generation · {tag} (~60-day lag)")
                gen = ng.fetch_generation([node], cs, ce, fetched_at=fetched_at, verbose=False)
                pn._merge_save(gen, pn.GEN_TEMPLATE, pn.GEN_KEY)
                done += 1
            bar.progress(1.0, text="Done")
            st.cache_data.clear()
            st.success("Pulled and stored. Readiness updated.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001 — surface the failure to the user
            st.error(f"Pull failed: {exc}")
else:
    st.success("This node already has cached prices and generation — ready to analyze.")

# --------------------------------------------------------------------------
# Step 3 — Register the project
# --------------------------------------------------------------------------
st.divider()
st.header("3 · Register the project")
st.caption("Saving writes the project to the curated asset registry "
           "(`ercot_assets.json`). That's what makes it selectable across the Hub and "
           "available to build a portal on. Coordinates power the solar/wind forecasts, "
           "so fill them in when you can.")

pf = _prefill(res)
existing = project_lookup.load_registry().get(name_used, {})  # editing an existing entry?

# The form is session-state-backed so the EIA-860 lookup can populate it.
_toks = project_lookup.tokens(name_used)
_default_q = max(_toks, key=len) if _toks else name_used
defaults = {
    "pb_name": str(existing.get("project_name") or name_used),
    "pb_tech": (str(existing.get("tech")) if existing.get("tech") in TECHS
                else _guess_tech(res, cand)),
    "pb_hub": str(existing.get("hub")) if str(existing.get("hub")) in HUBS else "North",
    "pb_cap": float(existing.get("capacity_mw") or pf["capacity_mw"]),
    "pb_county": str(existing.get("county") or pf["county"]),
    "pb_qid": str(existing.get("queue_id") or pf["queue_id"]),
    "pb_lat": float(existing.get("lat") or 0.0),
    "pb_lon": float(existing.get("lon") or 0.0),
    "pb_eia_q": _default_q,
    "pb_track": "fixed" if str(existing.get("tracking_type", "single_axis")) == "fixed" else "single_axis",
    "pb_dcac": float(existing.get("dc_ac_ratio") or 1.3),
    "pb_tmanuf": str(existing.get("turbine_manuf") or ""),
    "pb_tmodel": str(existing.get("turbine_model") or ""),
    "pb_hh": float(existing.get("hub_height_m") or 0.0),
    "pb_rd": float(existing.get("rotor_diameter_m") or 0.0),
}
# On a genuine project change, drop the old field state so defaults take effect.
# Otherwise keep edits — but still re-seed any keys Streamlit garbage-collected
# (e.g. after navigating away and back), so reads below never KeyError.
ctx = f"{name_used}|{node}"
if st.session_state.get("pb_ctx") != ctx:
    for k in defaults:
        st.session_state.pop(k, None)
    st.session_state["pb_ctx"] = ctx
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# --- Autofill from EIA-860 -------------------------------------------------
with st.container(border=True):
    st.markdown("**🔎 Autofill from EIA-860**")
    st.caption("Search the EIA-860 directory and copy a plant's capacity, county and "
               "coordinates into the form below.")
    st.text_input("Search by plant name", key="pb_eia_q")
    eia_cands = _eia_candidates(st.session_state["pb_eia_q"], st.session_state["pb_tech"])
    if not eia_cands:
        st.caption("No EIA-860 plant matched. Try a different name, or build the directory "
                   "on the **EIA-860 Plants** page if it's empty.")
    else:
        def _eia_label(c):
            loc = f"({c['lat']:.3f}, {c['lon']:.3f})" if c.get("lat") else "no coords"
            return f"{c['plant_name']} — {c['county']} Co · {c['capacity_mw']:,.0f} MW · {loc}"
        i = st.selectbox("Matching EIA-860 plant", range(len(eia_cands)),
                         format_func=lambda j: _eia_label(eia_cands[j]), key="pb_eia_pick")
        if st.button("⬅ Use these details", key="pb_eia_apply"):
            p = eia_cands[i]
            st.session_state["pb_cap"] = float(p.get("capacity_mw") or 0.0)
            st.session_state["pb_county"] = str(p.get("county") or "")
            if p.get("lat") is not None:
                st.session_state["pb_lat"] = float(p["lat"])
            if p.get("lon") is not None:
                st.session_state["pb_lon"] = float(p["lon"])
            if p.get("tech") in TECHS:
                st.session_state["pb_tech"] = p["tech"]
            st.toast(f"Filled from {p['plant_name']}")
            st.rerun()

c1, c2, c3 = st.columns(3)
project_name = c1.text_input("Project name", key="pb_name")
tech = c2.selectbox("Technology", TECHS, key="pb_tech")
hub = c3.selectbox("Trading hub", HUBS, key="pb_hub",
                   help="The ERCOT hub the project settles against — drives capture price.")

c4, c5, c6 = st.columns(3)
capacity_mw = c4.number_input("Capacity (MW)", min_value=0.0, step=1.0, key="pb_cap")
county = c5.text_input("County", key="pb_county")
queue_id = c6.text_input("ERCOT queue ID", key="pb_qid")

c7, c8 = st.columns(2)
lat = c7.number_input("Latitude", format="%.5f", key="pb_lat")
lon = c8.number_input("Longitude", format="%.5f", key="pb_lon")
if (lat == 0.0 or lon == 0.0):
    st.caption("⚠️ Coordinates are blank — the solar/wind forecast pages need them. "
               "Use the EIA-860 autofill above, or enter them manually.")

# Tech-specific specs.
with st.expander(f"{tech} specs (optional, improves forecast accuracy)", expanded=False):
    if tech == "Solar":
        s1, s2 = st.columns(2)
        tracking_type = s1.selectbox("Tracking", ["single_axis", "fixed"], key="pb_track")
        dc_ac_ratio = s2.number_input("DC/AC ratio", min_value=0.0, step=0.01, key="pb_dcac")
        wind_specs = {}
        solar_specs = {"tracking_type": tracking_type,
                       "dc_ac_ratio": dc_ac_ratio if dc_ac_ratio > 0 else None}
    else:
        w1, w2 = st.columns(2)
        turbine_manuf = w1.text_input("Turbine manufacturer", key="pb_tmanuf")
        turbine_model = w2.text_input("Turbine model", key="pb_tmodel")
        w3, w4 = st.columns(2)
        hub_height_m = w3.number_input("Hub height (m)", min_value=0.0, step=1.0, key="pb_hh")
        rotor_diameter_m = w4.number_input("Rotor diameter (m)", min_value=0.0, step=1.0, key="pb_rd")
        solar_specs = {}
        wind_specs = {
            "turbine_manuf": turbine_manuf or None,
            "turbine_model": turbine_model or None,
            "hub_height_m": hub_height_m if hub_height_m > 0 else None,
            "rotor_diameter_m": rotor_diameter_m if rotor_diameter_m > 0 else None,
        }

notes = st.text_area("Notes (optional)", value=str(existing.get("notes") or ""),
                     placeholder="e.g. pre-operational, COD ~2027; resource_name is a placeholder; …")

verb = "Update" if existing else "Register"
if st.button(f"💾 {verb} “{project_name}”", type="primary"):
    record = {
        "resource_name": node,
        "project_name": project_name,
        "tech": tech,
        "capacity_mw": capacity_mw if capacity_mw > 0 else None,
        "hub": hub,
        "county": county or None,
        "lat": lat if lat != 0.0 else None,
        "lon": lon if lon != 0.0 else None,
        "queue_id": queue_id or None,
        "notes": notes or None,
        **solar_specs, **wind_specs,
    }
    # Aggregate nodes carry the underlying SCED units; single-unit nodes don't.
    if len(units) > 1 or units != [node]:
        record["sced_units"] = units

    path = project_lookup.upsert_asset(project_name, record)
    # Quietly keep the name crosswalk in sync so other tools resolve this node too.
    try:
        project_lookup.persist_to_crosswalk(units, project_name, queue_id=queue_id or None,
                                            county=county or None,
                                            capacity_mw=capacity_mw or None)
    except Exception:  # noqa: BLE001 — crosswalk is a nicety, not required
        pass

    st.success(f"✓ {verb}d **{project_name}**.")
    st.markdown(
        "It now appears in the Universal plant picker across **Plant Value**, **Wind Capture**, "
        "**PPA Settlement**, and the **forecasts**.")
    st.caption(f"Saved to {path}")
    st.cache_data.clear()

# --------------------------------------------------------------------------
# Step 4 — Stand up a settlement portal (once registered)
# --------------------------------------------------------------------------
saved_asset = project_lookup.load_registry().get(project_name)
if saved_asset:
    from ercot_core import portal_scaffold  # noqa: E402
    st.divider()
    st.header("4 · Build a settlement portal")

    def _launch(path):
        """Start a portal and remember its run info for the rest of the session."""
        try:
            inf = portal_scaffold.launch_portal(path)
            st.session_state.setdefault("portal_runs", {})[str(path)] = inf
            st.toast(f"Launching on {inf['url']} …")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Launch failed: {exc}")

    def _stop(path):
        inf = st.session_state.get("portal_runs", {}).get(str(path))
        if inf:
            portal_scaffold.stop_portal(port=inf.get("port"), pid=inf.get("pid"))
            st.session_state["portal_runs"].pop(str(path), None)
            st.toast("Stopped.")

    runs = st.session_state.setdefault("portal_runs", {})

    def _run_controls(container, path, kid):
        """Show 🟢 running + ■ Stop, or ▶ Launch.

        ``kid`` is a unique key seed for this call site (the same portal can appear
        in both the list and the just-built row, so keys must differ).

        We trust the session launch record rather than probing the port live: a
        freshly-spawned Streamlit takes a few seconds to bind, so an immediate
        ``lsof`` check would race and prune the entry right after launch (looking
        like nothing happened). Stale entries are cleared by ■ Stop.
        """
        inf = runs.get(str(path))
        if inf:
            if container.button("■ Stop", key=f"stop_{kid}"):
                _stop(path)
                st.rerun()
            return inf["url"]
        if container.button("▶ Launch", key=f"launch_{kid}"):
            _launch(path)
            st.rerun()
        return None

    # Existing portals (incl. the Markham / Azure Sky originals) — launch/stop any.
    portals = portal_scaffold.list_portals()
    with st.expander(f"📂 {len(portals)} existing portals", expanded=False):
        if not portals:
            st.caption("None yet — create one below.")
        for p in portals:
            c1, c2 = st.columns([4, 1])
            url = _run_controls(c2, p["path"], f"list_{p['name']}")
            sub = f"`{p['node'] or '—'}`  ·  {p['path']}"
            c1.markdown(f"**{p['name']}**  \n{sub}"
                        + (f"  \n🟢 running: [{url}]({url})" if url else ""))

    st.caption("Generate a standalone customer portal for this asset — same shape as the "
               "**Markham** and **Azure Sky** portals. It clones the template, points it at this "
               "node, and bakes in the contract. It reads the data this Hub already pulled, so it "
               "runs offline as a sibling folder.")

    dest_preview = portal_scaffold.portal_dest(project_name)
    pcol1, pcol2 = st.columns(2)
    p_strike = pcol1.number_input("Contract strike ($/MWh)", min_value=0.0,
                                  value=float(saved_asset.get("strike") or 35.0), step=1.0,
                                  key="pb_portal_strike")
    p_struct = pcol2.selectbox("Structure", ["VPPA / CfD", "Physical PPA", "Merchant + fee"],
                               key="pb_portal_struct")
    st.caption(f"Will create: `{dest_preview}`")
    overwrite = False
    if dest_preview.exists():
        st.warning(f"`{dest_preview.name}` already exists. Creating will **overwrite** it "
                   "(config.json/branding edits there would be lost).")
        overwrite = st.checkbox("Overwrite if it exists", value=False, key="pb_portal_overwrite")

    if st.button(f"🚀 Create portal for “{project_name}”", type="primary", key="pb_make_portal"):
        try:
            info = portal_scaffold.create_portal(saved_asset, strike=p_strike,
                                                 structure=p_struct, overwrite=overwrite)
            st.session_state["pb_last_portal"] = info["path"]
            st.success(f"✓ Portal created at `{info['path']}`")
        except FileExistsError as exc:
            st.warning(f"{exc}")
        except Exception as exc:  # noqa: BLE001 — surface scaffolding failures
            st.error(f"Portal creation failed: {exc}")

    # One-click launch/stop for the portal just created this session.
    last = st.session_state.get("pb_last_portal")
    if last and pathlib.Path(last).exists():
        lc1, lc2 = st.columns([3, 1])
        last_url = _run_controls(lc2, last, f"last_{pathlib.Path(last).name}")
        lc1.markdown(f"Just built: **{pathlib.Path(last).name}**"
                     + (f" · 🟢 running: [{last_url}]({last_url})" if last_url else ""))
        st.caption("Launch starts it with this Hub's venv on a free port (8600+); give it ~5s, "
                   "then click the link. ■ Stop shuts it down. Branding/contract live in the "
                   "new folder.")
