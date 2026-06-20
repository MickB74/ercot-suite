"""Name resolver — correct ERCOT resource plant-names from interconnection.fyi
by recognizing the SCED code as an abbreviation of the project name (MRKM→Markum)."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import streamlit as st  # noqa: E402

import _export  # noqa: E402
from ercot_core import ifyi, reconcile as R  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🔤 Resource Name Resolver")
st.write("Match each ERCOT SCED code to its authoritative **interconnection.fyi** project "
         "name by treating the code as an abbreviation (subsequence) of the name — e.g. "
         "`MRKM` → *Markum*. Corrected names flow into every tool (reconciliation, lookup, "
         "settlement) and override our derived guesses (but never your manual overrides).")

if ifyi.load_ercot_projects().empty:
    st.info("Run the interconnection.fyi crawl first: `python orchestrate.py update ifyi`.")
    st.stop()

with st.container(border=True):
    st.header("Settings")
    if st.button("▶ Build resolver", type="primary", use_container_width=True):
        with st.spinner("Matching codes to interconnection.fyi names…"):
            st.session_state["resolve"] = R.resolve_names()
    show = st.multiselect("Show confidence", ["high", "medium"], default=["high", "medium"])
    only_diff = st.checkbox("Only proposed changes", value=True)

d = st.session_state.get("resolve")
if d is None or d.empty:
    st.caption("Click **Build resolver** to scan all resources.")
    st.stop()

view = d[d["confidence"].isin(show)]
if only_diff:
    view = view[view["differs"]]

c = st.columns(4)
c[0].metric("Proposals", len(d))
c[1].metric("High", int((d.confidence == "high").sum()), help="single unambiguous project match")
c[2].metric("Name changes", int(d["differs"].sum()))
c[3].metric("High + changed", int(((d.confidence == "high") & d.differs).sum()),
            help="safe to auto-apply")

st.dataframe(view[["resource_name", "current_name", "current_source", "proposed_name",
                   "county", "capacity_mw", "status", "candidates", "confidence"]],
             hide_index=True, use_container_width=True, height=460)

st.divider()
col1, col2 = st.columns([1, 2])
with col1:
    min_conf = st.selectbox("Apply at confidence ≥", ["high", "medium"], index=0)
    if st.button("💾 Apply corrected names", type="primary"):
        n = R.apply_resolved_names(d, min_confidence=min_conf, only_differs=True)
        st.success(f"Applied {n} name correction(s) at ≥{min_conf}. They now appear "
                   "everywhere (rebuild the plant registry / re-run reconciliation to pick up).")
with col2:
    st.caption("**High** = the code is an unambiguous abbreviation of exactly one project "
               "(safe to bulk-apply). **Medium** = several candidate projects share the "
               "abbreviation (e.g. 3 *Markum* projects) — review and pick before applying. "
               "Manual overrides are never touched.")

_export.download_block(st, d, name="name_resolver",
                       title="Name resolver", meta={"Rows": f"{len(d):,}"})
