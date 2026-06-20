"""Queue Explorer UI — one ``render()`` shared by the standalone app and the
Data Hub screen, so the two never diverge.

It expects the Data Hub on ``sys.path`` (for ``ercot_core``) and the Hub's
``app/`` dir on ``sys.path`` (for the shared ``_common`` / ``_export`` helpers);
both entrypoints do that bootstrap before importing this module.

  - standalone:  ``Ercot Queue/app.py``  (owns ``st.set_page_config``)
  - embedded:    ``Ercot_Data_Hub/app/screens/20_Queue_Explorer.py``
"""

from __future__ import annotations

import pathlib

import pandas as pd
import streamlit as st

import _common
import _export
from ercot_core import paths, queue_search


@st.cache_data(show_spinner="Loading interconnection queue…")
def _load() -> pd.DataFrame:
    return queue_search.unified_queue()


def render() -> None:
    st.title("🔌 Queue Explorer")
    st.caption("Search and analyze the ERCOT interconnection queue, then build a "
               "due-diligence dossier for any project — links, status, dates, "
               "resource-node crosswalk, and Texas county/state filing pointers.")

    df = _load()
    if df.empty:
        _common.empty_state(
            st, "No queue data cached yet.",
            hint="Build the interconnection.fyi superset with "
                 "`python ercot_core/ifyi.py`, and/or fetch the GIS queue "
                 "(`project_lookup.load_full_queue(refresh=True)`).")

    # --- Summary -------------------------------------------------------------
    mw = pd.to_numeric(df["capacity_mw"], errors="coerce")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Projects", f"{len(df):,}")
    c2.metric("Total capacity", f"{mw.sum()/1000:,.1f} GW")
    c3.metric("In current GIS queue", f"{int(df['in_gis'].sum()):,}")
    c4.metric("Counties", f"{df['county'].nunique():,}")
    _common.data_status(st, path=[
        str(paths.IFYI_ERCOT_PARQUET),
        str(paths.INTERCONNECTION_QUEUE_FULL_PARQUET),
    ], rows=len(df))

    tab_search, tab_dossier = st.tabs(["🔎 Search & Analyze", "📋 Project Dossier"])
    with tab_search:
        _render_search(df)
    with tab_dossier:
        _render_dossier(df)


# ============================================================================
def _render_search(df: pd.DataFrame) -> None:
    with _common.controls_panel(st, "⚙️ Filters", expanded=True):
        r1 = st.columns([2.5, 1.4, 1.4])
        text = r1[0].text_input("Search", placeholder="name · entity · county · queue id · POI")
        fuels = r1[1].multiselect("Fuel", sorted(df["fuel"].dropna().unique()))
        statuses = r1[2].multiselect("Status", sorted(df["status"].dropna().unique()))
        r2 = st.columns([1.6, 1.6, 1.2, 1.2, 1.0])
        counties = r2[0].multiselect("County", sorted(df["county"].dropna().unique()))
        tech = r2[1].text_input("Technology contains", placeholder="e.g. Battery, Solar")
        min_mw = r2[2].number_input("Min MW", value=0.0, step=50.0)
        max_mw = r2[3].number_input("Max MW", value=0.0, step=50.0, help="0 = no upper limit")
        in_gis_only = r2[4].toggle("In GIS only", value=False,
                                   help="Only projects in the current ERCOT GIS report")

    view = df.copy()
    if text:
        hay = (view["project_name"].astype(str) + "|" + view["entity"].astype(str) + "|"
               + view["county"].astype(str) + "|" + view["queue_id"].astype(str) + "|"
               + view["poi"].astype(str))
        view = view[hay.str.contains(text, case=False, na=False, regex=False)]
    if fuels:
        view = view[view["fuel"].isin(fuels)]
    if statuses:
        view = view[view["status"].isin(statuses)]
    if counties:
        view = view[view["county"].isin(counties)]
    if tech:
        view = view[view["technology"].astype(str).str.contains(tech, case=False, na=False)
                    | view["gen_type"].astype(str).str.contains(tech, case=False, na=False)]
    vmw = pd.to_numeric(view["capacity_mw"], errors="coerce")
    if min_mw > 0:
        view = view[vmw >= min_mw]
    if max_mw > 0:
        view = view[pd.to_numeric(view["capacity_mw"], errors="coerce") <= max_mw]
    if in_gis_only:
        view = view[view["in_gis"]]

    view = view.assign(_s=pd.to_numeric(view["capacity_mw"], errors="coerce")).sort_values(
        "_s", ascending=False, na_position="last").drop(columns="_s").reset_index(drop=True)

    vmw = pd.to_numeric(view["capacity_mw"], errors="coerce")
    m1, m2, m3 = st.columns(3)
    m1.metric("Matches", f"{len(view):,}")
    m2.metric("Capacity", f"{vmw.sum()/1000:,.2f} GW")
    m3.metric("Median size", f"{vmw.median():,.0f} MW" if len(view) else "—")

    cols = ["queue_id", "project_name", "fuel", "technology", "capacity_mw",
            "county", "entity", "status", "queue_date", "proposed_completion",
            "in_gis", "url"]
    st.dataframe(
        view[cols], hide_index=True, use_container_width=True, height=460,
        column_config={
            "queue_id": "Queue ID", "project_name": "Project", "fuel": "Fuel",
            "technology": "Technology",
            "capacity_mw": st.column_config.NumberColumn("MW", format="%.1f"),
            "county": "County", "entity": "Entity", "status": "Status",
            "queue_date": "Queued", "proposed_completion": "Proposed COD",
            "in_gis": st.column_config.CheckboxColumn("GIS"),
            "url": st.column_config.LinkColumn("Link", display_text="Open ↗"),
        })
    st.caption(f"Showing {len(view):,} of {len(df):,} projects.")

    _export.download_block(
        st, view[cols], name="ercot_queue_search",
        title="ERCOT interconnection queue — search results",
        meta={"Matches": len(view), "Capacity (GW)": f"{vmw.sum()/1000:,.2f}"})

    # --- Rollup --------------------------------------------------------------
    st.subheader("📊 Rollup")
    by = st.selectbox("Group by", ["fuel", "technology", "status", "county", "entity"], index=0)
    if not view.empty:
        g = view.assign(mw=vmw).groupby(view[by].fillna("(unknown)").astype(str))
        roll = g.agg(projects=("queue_id", "count"), total_mw=("mw", "sum"),
                     median_mw=("mw", "median")).reset_index()
        roll = roll.sort_values("total_mw", ascending=False).reset_index(drop=True)
        roll["total_mw"] = roll["total_mw"].round(1)
        roll["median_mw"] = roll["median_mw"].round(1)
        gc1, gc2 = st.columns([1.4, 1])
        gc1.dataframe(roll, hide_index=True, use_container_width=True, height=360,
                      column_config={by: by.title(), "projects": "Projects",
                                     "total_mw": st.column_config.NumberColumn("Total MW", format="%.0f"),
                                     "median_mw": st.column_config.NumberColumn("Median MW", format="%.0f")})
        gc2.bar_chart(roll.head(15).set_index(by)["total_mw"])


# ============================================================================
def _render_dossier(df: pd.DataFrame) -> None:
    from ercot_core import tx_filings  # noqa: F401 (kept symmetric with engine)

    q = st.text_input("Project (queue id or name)", value="",
                      placeholder="e.g. 21INR0477 or “Azure Sky Solar”",
                      key="dossier_query")
    chosen = q
    if q and queue_search._norm_id(q) not in set(df["queue_id"]):
        hits = df[df["project_name"].astype(str).str.contains(q, case=False, na=False)]
        if len(hits) > 1:
            opts = (hits.assign(_s=pd.to_numeric(hits["capacity_mw"], errors="coerce"))
                    .sort_values("_s", ascending=False))
            labels = {f"{r.queue_id} — {r.project_name} ({r.capacity_mw:.0f} MW, {r.county})":
                      r.queue_id for r in opts.itertuples()}
            pick = st.selectbox(f"{len(hits)} name matches — choose one", list(labels))
            chosen = labels[pick]

    if not q:
        st.info("Enter a queue id or project name above to build a dossier.")
        return

    d = queue_search.dossier(chosen)
    rec = d.get("record")

    if not d["found"]:
        st.warning(f"No queue record found for “{q}”. Showing generic filing "
                   "links + checklist below.")
    else:
        st.markdown(f"### {rec.get('project_name','?')}  ·  `{rec.get('queue_id','?')}`")
        badge = "🟢 in current GIS queue" if rec.get("in_gis") else "⚪ not in current GIS queue"
        st.caption(badge + (f"  ·  registry asset: **{d['registry_match'].get('resource_name')}**"
                            if d.get("registry_match") else ""))
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Capacity", f"{rec.get('capacity_mw') or 0:,.1f} MW")
        i2.metric("Status", str(rec.get("status") or "?"))
        i3.metric("Fuel / Tech", f"{rec.get('fuel') or d.get('inferred_tech') or '?'}")
        i4.metric("County", str(rec.get("county") or "?"))

        info = {
            "Interconnecting entity": rec.get("entity"),
            "POI": rec.get("poi"),
            "Technology": rec.get("technology") or rec.get("gen_type")
            or (f"{d.get('inferred_tech')} (inferred)" if d.get("inferred_tech") else None),
            "Queue date": rec.get("queue_date"),
            "Proposed COD": rec.get("proposed_completion"),
            "Actual COD": rec.get("actual_completion"),
        }
        st.dataframe(pd.DataFrame(
            [(k, v) for k, v in info.items() if v not in (None, "", "None")],
            columns=["Field", "Value"]), hide_index=True, use_container_width=True)
        if rec.get("url"):
            st.markdown(f"🔗 [interconnection.fyi project page]({rec['url']})")

        cw = d.get("crosswalk") or {}
        cands = cw.get("candidates") or []
        if cands:
            with st.expander(f"🔁 Resource-node crosswalk — {len(cands)} candidate(s)", expanded=False):
                rows = []
                for c in cands:
                    av = c.get("availability", {})
                    rows.append({
                        "Resource node": c["resource_node"],
                        "Units": ", ".join(c["units"]),
                        "Match": c["match"],
                        "Price rows": av.get("price_rows_cached", 0),
                        "Gen rows": av.get("gen_rows_cached", 0),
                        "SCED files": av.get("plant_sced_files", 0),
                        "In registry": ", ".join(av.get("units_in_registry") or []) or "—",
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        elif cw.get("queue_note"):
            st.caption(cw["queue_note"])

    st.subheader("🔗 Filing & due-diligence links")
    st.caption("↗ = direct portal · 🔎 = pre-scoped search (open, then search the "
               "project/entity name). These are navigational aids, not a filings feed.")
    links_df = pd.DataFrame(d["filing_links"])
    links_df["kind"] = links_df["kind"].map({"direct": "↗ direct", "search": "🔎 search"})
    st.dataframe(
        links_df[["label", "kind", "note", "url"]], hide_index=True, use_container_width=True,
        column_config={"label": "Source", "kind": "Type", "note": "What to look for",
                       "url": st.column_config.LinkColumn("Open", display_text="Open ↗")})

    st.subheader("✅ Due-diligence checklist")
    chk = pd.DataFrame(d["dd_checklist"]).rename(columns={"area": "Area", "item": "What to verify"})
    st.dataframe(chk, hide_index=True, use_container_width=True)

    _export.download_block(
        st, links_df[["label", "kind", "note", "url"]],
        name=f"dd_links_{(rec or {}).get('queue_id','project')}",
        title=f"Due-diligence links — {(rec or {}).get('project_name', q)}")
