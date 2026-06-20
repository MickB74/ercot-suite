"""Project Hub — data-quality index for every project loaded into the suite.

Surfaces, for all registry assets: metadata completeness, source verification
(EIA-923 crosswalk + cached SCED actuals), calibration/model-readiness
(typical-year profile + plant value), and which tools consume each project.

Reads the same assessment used by the standalone ``Ercot_Project Hub/build_hub.py``
generator, so the page and the on-disk index never diverge.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
# The Project Hub lives as a sibling folder of Ercot_Data_Hub (name has a space,
# but the module file does not, so it imports fine once the dir is on the path).
HUB_DIR = pathlib.Path(__file__).resolve().parents[3] / "Ercot_Project Hub"
sys.path.insert(0, str(HUB_DIR))

from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import build_hub  # noqa: E402

st.title("🗂️ Project Hub")
st.caption("What projects are loaded into the suite and how good the data behind "
           "each one is. Auto-derived from the asset registry and the live data "
           "lake — completeness, verification, calibration, and coverage.")

# EIA Electricity Data Browser per-plant page (for the clickable EIA column).
EIA_PLANT_URL = "https://www.eia.gov/electricity/data/browser/#/plant/"


def _eia_url(v):
    """EIA plant-page URL from an eia_plant_id, or None if unmapped."""
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return EIA_PLANT_URL + str(int(float(v)))
    except (ValueError, TypeError):
        return None


@st.cache_data(show_spinner="Assessing projects…")
def _load():
    rows = build_hub.collect_rows()
    df = pd.DataFrame(rows)
    by_tech = pd.DataFrame(build_hub.rollup(rows, "tech"))
    by_hub = pd.DataFrame(build_hub.rollup(rows, "hub"))
    return df, by_tech, by_hub


try:
    df, by_tech, by_hub = _load()
except FileNotFoundError as e:
    st.error(f"Could not load the asset registry: {e}")
    st.stop()

# --- Summary metrics ---------------------------------------------------------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Projects", len(df))
c2.metric("Avg quality", f"{df['overall_score'].mean():.0f}/100")
c3.metric("EIA crosswalk", f"{int(df['eia_crosswalk'].sum())}/{len(df)}")
c4.metric("SCED actuals", f"{int(df['sced_actuals'].sum())}/{len(df)}")
c5.metric("Plant value", f"{int(df['plant_value'].sum())}/{len(df)}")

grade_counts = df["grade"].value_counts().reindex(list("ABCDF")).fillna(0).astype(int)
st.caption("Grade distribution — " +
           " · ".join(f"**{g}**: {grade_counts[g]}" for g in "ABCDF"))

# --- Rollups -----------------------------------------------------------------
t1, t2 = st.columns(2)
with t1:
    st.subheader("By technology")
    st.dataframe(by_tech, hide_index=True, use_container_width=True)
with t2:
    st.subheader("By hub")
    st.dataframe(by_hub, hide_index=True, use_container_width=True)

# --- Filters -----------------------------------------------------------------
st.subheader("All projects")
f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 2.4])
techs = f1.multiselect("Tech", sorted(df["tech"].dropna().unique()))
hubs = f2.multiselect("Hub", sorted(df["hub"].dropna().unique()))
grades = f3.multiselect("Grade", list("ABCDF"))
query = f4.text_input("Search", placeholder="project or resource name")

view = df.copy()
if techs:
    view = view[view["tech"].isin(techs)]
if hubs:
    view = view[view["hub"].isin(hubs)]
if grades:
    view = view[view["grade"].isin(grades)]
if query:
    q = query.strip().lower()
    view = view[view["project"].str.lower().str.contains(q) |
                view["resource_name"].str.lower().str.contains(q)]

view = view.sort_values("overall_score", ascending=False)

# Present coverage/missing_fields (lists) as readable strings.
display = view.assign(
    coverage=view["coverage"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x),
    missing_fields=view["missing_fields"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else x),
    eia_url=view["eia_plant_id"].apply(_eia_url),
)
cols = ["project", "tech", "capacity_mw", "hub", "grade", "overall_score",
        "completeness_pct", "verification_score", "calibration_score",
        "eia_crosswalk", "eia_url", "sced_actuals", "plant_value", "portal",
        "missing_fields", "coverage"]

st.dataframe(
    display[cols],
    hide_index=True,
    use_container_width=True,
    height=520,
    column_config={
        "project": "Project",
        "capacity_mw": st.column_config.NumberColumn("MW", format="%g"),
        "grade": "Grade",
        "overall_score": st.column_config.ProgressColumn(
            "Overall", min_value=0, max_value=100, format="%g"),
        "completeness_pct": st.column_config.NumberColumn("Complete", format="%g%%"),
        "verification_score": st.column_config.NumberColumn("Verify", format="%g"),
        "calibration_score": st.column_config.NumberColumn("Calib", format="%g"),
        "eia_crosswalk": st.column_config.CheckboxColumn("Crosswalk"),
        "eia_url": st.column_config.LinkColumn(
            "EIA", display_text="Open ↗",
            help="Open this plant on the EIA Electricity Data Browser (blank if unmapped)."),
        "sced_actuals": st.column_config.CheckboxColumn("SCED"),
        "plant_value": st.column_config.CheckboxColumn("Valued"),
        "missing_fields": "Missing fields",
        "coverage": "Consumed by",
    },
)
st.caption(f"Showing {len(view)} of {len(df)} projects.")

_export.download_block(
    st, display[cols], name="project_hub_data_quality",
    title="ERCOT Project Hub — data quality",
    meta={"Projects": len(view), "Avg quality": f"{view['overall_score'].mean():.0f}/100"
          if len(view) else "—"})

st.divider()
st.caption("Scores: **completeness** = expected metadata fields present (tech-aware) · "
           "**verification** = EIA-923 crosswalk + cached SCED actuals + location "
           "confidence · **calibration** = typical-year gen profile + computed plant "
           "value. Regenerate the on-disk index with "
           "`python3 \"Ercot_Project Hub/build_hub.py\"`.")
