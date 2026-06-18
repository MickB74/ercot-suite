"""Auto-crosswalk — match every ERCOT SCED resource to its EIA-860 plant by
fuel + name + county + capacity, then bulk-save high-confidence maps for use by
the reconciliation pages."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import streamlit as st  # noqa: E402

import _export  # noqa: E402
import eia860  # noqa: E402
from ercot_core import reconcile as R  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🧩 SCED ↔ EIA Auto-Crosswalk")
st.write("Match every ERCOT SCED resource to its **EIA-860** plant using **fuel + shared "
         "name tokens + county + nameplate capacity**. The joint county+capacity key catches "
         "plants whose names differ (e.g. *Markham* ↔ *Markum*). Review, then save the "
         "matches you trust — they feed the **🔁 / 🛰️ Reconciliation** pages.")

years860 = eia860.available_years(region="ercot")
if not years860:
    st.info("Build the **EIA-860** directory first (🗺️ EIA-860 Plants → Get/update data, "
            "or `python orchestrate.py update eia860`).")
    st.stop()

with st.sidebar:
    st.header("Settings")
    years = st.multiselect("EIA-860 vintage(s)", years860, default=years860[-1:])
    cap_tol = st.slider("Capacity match tolerance (±%)", 5, 50, 15) / 100.0
    use_860m = st.checkbox(
        "Supplement with EIA-860M (monthly)", value=False,
        help="Pull the monthly EIA-860M generator file (no API key) to add plants too "
             "new for the cached annual 860, and use each plant's EIA entity name as an "
             "extra match signal. Adds a network fetch at build time.")
    run = st.button("▶ Build auto-crosswalk", type="primary", use_container_width=True)

if not years:
    st.warning("Pick at least one EIA-860 vintage.")
    st.stop()
if not run and "axw" not in st.session_state:
    st.caption("Set the vintage + tolerance, then **Build auto-crosswalk**.")
    st.stop()

if run:
    spin = "Matching SCED resources to EIA-860" + ("(+860M)…" if use_860m else "…")
    with st.spinner(spin):
        st.session_state["axw"] = R.auto_crosswalk(tuple(sorted(years)), cap_tol=cap_tol,
                                                   use_860m=use_860m)

x = st.session_state.get("axw")
if x is None or x.empty:
    st.warning("No matches produced.")
    st.stop()

counts = x["confidence"].value_counts().to_dict()
c = st.columns(4)
c[0].metric("Matched resources", len(x))
c[1].metric("High", counts.get("high", 0), help="name + county + capacity agree")
c[2].metric("Medium", counts.get("medium", 0))
c[3].metric("Low", counts.get("low", 0), help="name-only (review before trusting)")

show_conf = st.multiselect("Show confidence", ["high", "medium", "low"], default=["high", "medium"])
view = x[x["confidence"].isin(show_conf)]
st.dataframe(view, hide_index=True, use_container_width=True, height=460,
             column_config={"eia_plant_id": st.column_config.NumberColumn("EIA #", format="%d")})

st.divider()
col1, col2 = st.columns([1, 2])
with col1:
    min_conf = st.selectbox("Save matches at confidence ≥", ["high", "medium", "low"], index=0)
    if st.button("💾 Save to reconciliation crosswalk", type="primary"):
        n = R.save_auto_matches(x, min_confidence=min_conf)
        st.success(f"Saved {n} plant mapping(s) at ≥{min_conf} confidence. "
                   "They now appear on the Reconciliation + Fleet pages.")
with col2:
    st.caption("High = name+county+capacity all agree (safe to bulk-save). Medium = two of "
               "three. Low = name-only (often right, but eyeball first). You can always refine "
               "or override any single plant on the 🔁 Reconciliation page.")

_export.download_block(st, x, name="auto_crosswalk",
                       title="Auto crosswalk (EIA ↔ ERCOT)", meta={"Rows": f"{len(x):,}"})
