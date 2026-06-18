"""Project → resource node lookup. Paste a project name or ERCOT queue id."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import project_lookup  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🔎 Project → Resource Node")
st.caption("Map an interconnection project (name, or ERCOT queue id like "
           "`21INR0477` / `ercot-21inr0477`) to its ERCOT resource node and units. "
           "Bridges the interconnection queue and the resource-node catalog + "
           "plant-name crosswalk.")

col1, col2 = st.columns([4, 1])
query = col1.text_input("Project name or queue id", placeholder="e.g. Azure Sky  ·  21INR0477")
allow_fetch = not col2.toggle("Offline", value=False,
                              help="Skip downloading the live queue; use cached catalog/crosswalk only.")

if not query:
    st.info("Enter a project name or queue id above.")
    st.stop()


@st.cache_data(show_spinner=True)
def _lookup(q, fetch):
    return project_lookup.lookup(q, allow_fetch=fetch)


res = _lookup(query.strip(), allow_fetch)

qm = res.get("queue_matches", [])
if qm:
    st.subheader("Interconnection queue match (ERCOT live queue)")
    st.dataframe(pd.DataFrame(qm), hide_index=True, use_container_width=True)
if res.get("ifyi"):
    r = res["ifyi"]
    st.subheader("interconnection.fyi match")
    st.markdown(
        f"**{r.get('name')}** — {r.get('fuel')} · {r.get('capacity_mw')} MW · "
        f"{r.get('county')} Co · {r.get('status')}  \n"
        f"POI: {r.get('poi')}  ·  [{r.get('url')}]({r.get('url')})")
if res.get("queue_note"):
    st.info(res["queue_note"])

st.subheader(f"Resource-node candidates · name used: “{res['name_used']}”")
cands = res.get("candidates", [])
if not cands:
    st.error("No matching resource node found. Try a more distinctive name token, "
             "or build the catalog (Node Explorer → Build catalog).")
    st.stop()

rec = res.get("ifyi") or {}
for c in cands:
    av = c["availability"]
    with st.container(border=True):
        st.markdown(f"### `{c['resource_node']}`  &nbsp; <small>match: {c['match']}</small>",
                    unsafe_allow_html=True)
        a, b = st.columns(2)
        a.markdown(f"**Units:** {', '.join(c['units'])}")
        a.markdown(f"**Types:** {', '.join(c['types']) or '—'}")
        b.markdown(f"**Cached price:** {av['price_rows_cached']:,} rows")
        b.markdown(f"**Cached generation:** {av['gen_rows_cached']:,} rows")
        b.markdown(f"**SCED per-unit files:** {av['plant_sced_files']} · "
                   f"in registry: {', '.join(av['units_in_registry']) or 'no'}")
        st.code(f"python datasets/system_gen_by_fuel/pull_nodes.py pull "
                f"--node {c['resource_node']} --start 2026-01-01 --end 2026-03-31",
                language="bash")
        if st.button(f"💾 Save “{res['name_used']}” for these units",
                     key=f"save_{c['resource_node']}"):
            n = project_lookup.persist_to_crosswalk(
                c["units"], res["name_used"], queue_id=rec.get("queue_id"),
                url=rec.get("url"), county=rec.get("county"),
                capacity_mw=rec.get("capacity_mw"))
            st.success(f"Saved {n} unit name(s) to the crosswalk (source 'ifyi'). "
                       "They now show across the hub and survive crosswalk rebuilds.")

st.caption("Tip: the top candidate is usually right; lower-scored ones can be "
           "name-token coincidences. Cross-check against the queue's county/POI.")
