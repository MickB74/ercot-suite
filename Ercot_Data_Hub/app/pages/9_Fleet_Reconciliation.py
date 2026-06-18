"""Fleet batch reconciliation — run every saved SCED↔EIA mapping and flag the
plants where SCED is off."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import _export  # noqa: E402
import eia923  # noqa: E402
from ercot_core import reconcile as R  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🛰️ Fleet Reconciliation")
st.write("Run **every saved SCED↔EIA mapping** at once and see which plants are off. "
         "Add/curate mappings on the **🔁 Reconciliation** page (they're saved and reused here).")

xwalk = R.load_crosswalk()
n_saved = len(xwalk)

if n_saved == 0:
    st.info("No saved mappings yet. Go to **🔁 Reconciliation**, match a plant's ERCOT "
            "resources, and click **💾 Save mapping**. Saved plants show up here.")
    st.stop()

st.caption(f"{n_saved} saved mapping(s): " + ", ".join(xwalk["eia_plant_name"].astype(str).head(12))
           + (" …" if n_saved > 12 else ""))

cached_years = eia923.available_years(region="ercot")
with st.sidebar:
    st.header("Settings")
    years = st.multiselect("EIA years", cached_years, default=cached_years[-1:])
    tol = st.slider("‘Off’ tolerance (±%)", 1, 50, 10) / 100.0
    run = st.button("▶ Run fleet reconciliation", type="primary", use_container_width=True)
    st.caption("Fetches any missing SCED months per plant (cached days reused). "
               "First run over a new year can take a while.")

if not years:
    st.warning("Pick at least one year.")
    st.stop()
if not run:
    st.caption("Set years + tolerance, then **Run fleet reconciliation**.")
    st.stop()

prog = st.progress(0.0, text="Starting…")


def _cb(i, total, name):
    prog.progress(min((i + 1) / max(total, 1), 1.0), text=f"Reconciling {name} ({i + 1}/{total})…")


df = R.batch_reconcile(tuple(sorted(years)), tolerance=tol, allow_fetch=True, progress=_cb)
prog.empty()

if df.empty:
    st.warning("Nothing reconciled.")
    st.stop()

off = df[df["status"] == "⚠ off"]
no_ov = df[df["status"] == "no overlap"]
errs = df[df["status"].str.startswith("error", na=False)]

c = st.columns(4)
c[0].metric("Plants", len(df))
c[1].metric("SCED off", len(off), help=f"Diverge > {tol*100:.0f}% in ≥1 month")
c[2].metric("No overlap", len(no_ov), help="No month with both SCED and EIA")
c[3].metric("Errors", len(errs))

if len(off):
    st.warning("⚠️ SCED is off for: " + ", ".join(off["plant"].astype(str).tolist()))
else:
    st.success("✅ No plant exceeds the tolerance in any compared month.")

show = df.copy()
show["overall_pct"] = (show["overall_pct"] * 100).round(1)
for col in ("eia_mwh", "sced_mwh"):
    show[col] = show[col].round(0)
show = show.rename(columns={"plant_id": "EIA #", "plant": "Plant", "resources": "Resources",
                            "months": "Months", "eia_mwh": "EIA MWh", "sced_mwh": "SCED MWh",
                            "overall_pct": "Overall Δ%", "months_off": "Months off",
                            "status": "Status"})
st.dataframe(show, hide_index=True, use_container_width=True,
             column_config={"Overall Δ%": st.column_config.NumberColumn(format="%.1f%%")})

_export.download_block(st, df, name=f"fleet_reconcile_{'_'.join(map(str, years))}",
                       title="Fleet reconciliation",
                       meta={"Years": ", ".join(map(str, years)), "Rows": f"{len(df):,}"})
st.caption("Sorted worst-divergence first. Drill into any plant on the 🔁 Reconciliation page "
           "for the month-by-month breakdown.")
