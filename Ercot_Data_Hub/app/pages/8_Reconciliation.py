"""Reconcile ERCOT SCED telemetry vs EIA-923 metered net generation, by month."""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import _export  # noqa: E402
import eia923  # noqa: E402
from ercot_core import reconcile as R  # noqa: E402

# Page config is set centrally by the router (app/Home.py).
st.title("🔁 SCED ↔ EIA-923 Reconciliation")
st.write("Line up a plant's **5-min SCED telemetry** against its **monthly EIA-923 "
         "metered net generation** to see where — and how much — SCED is off.")

with st.expander("Why they differ / how matching works"):
    st.markdown(
        "- **SCED** = ERCOT dispatch *telemetry* (Telemetered Net Output), ~60-day lag. "
        "**EIA-923** = monthly *revenue-meter* net generation, ~6-month lag.\n"
        "- Small, steady gaps are normal (station service, telemetry rounding). Large or "
        "erratic gaps flag real issues (telemetry outages, curtailment, unit/plant boundary).\n"
        "- There's no official ERCOT-resource ↔ EIA-plant map, so resources are matched by "
        "**shared name tokens + fuel** and you can **override** the mapping (it's saved).")

cached_years = eia923.available_years(region="ercot")
if not cached_years:
    st.info("No EIA-923 data cached. Run **Update** for *EIA-923* on the Home page first.")
    st.stop()

with st.sidebar:
    st.header("① Year(s)")
    years = st.multiselect("EIA years", cached_years, default=cached_years[-1:])
    if not years:
        st.warning("Pick at least one year.")
        st.stop()

    st.header("② Plant (EIA)")
    et = R.eia_plant_table(tuple(sorted(years)))
    fuels = ["(all)"] + sorted(et["fuel"].dropna().unique().tolist())
    fsel = st.selectbox("Fuel filter", fuels)
    if fsel != "(all)":
        et = et[et["fuel"] == fsel]
    search = st.text_input("Search plant name").strip()
    if search:
        et = et[et["plant_name"].str.contains(search, case=False, na=False)]
    et = et.reset_index(drop=True)
    if et.empty:
        st.warning("No EIA plants match.")
        st.stop()
    labels = {int(r.plant_id): f"{r.plant_name} · {r.fuel} · {r.mwh:,.0f} MWh (#{int(r.plant_id)})"
              for r in et.itertuples()}
    pid = st.selectbox("EIA plant", list(labels), format_func=lambda x: labels[x])
    prow = et[et["plant_id"] == pid].iloc[0]
    pname, pfuel = prow["plant_name"], prow["fuel"]

    st.header("③ ERCOT resources")
    saved = R.mapped_resources(pid)
    sug = R.suggest_resources(pname, pfuel)
    if saved:
        st.success(f"✓ Auto-mapped from the saved crosswalk · {len(saved)} resource(s).")
    elif not sug.empty:
        st.warning(f"No saved mapping — pre-filled with {len(sug)} auto-suggestion(s); "
                   "review before reconciling.")
        st.caption("Suggested: " + ", ".join(
            f"{r.resource_name} ({r.matched})" for r in sug.itertuples()))
    else:
        st.warning("No saved mapping **and** no matching ERCOT SCED resource — this EIA "
                   "plant may be too small to be SCED-dispatched, so there's nothing to "
                   "reconcile. (Map it on 🧩 Auto-Crosswalk if you think it should match.)")
    import sced_plants as sp
    all_res = sorted(sp.load_registry()["resource_name"].tolist())
    default_res = saved or (sug["resource_name"].tolist() if not sug.empty else [])
    resources = st.multiselect("Resources settled to this plant", all_res, default=default_res,
                               help="Defaults to your saved mapping, else auto-suggested.")
    if st.button("💾 Save mapping"):
        R.save_crosswalk(pid, pname, resources)
        st.success("Mapping saved (used next time + by other tools).")

    tol = st.slider("‘Off’ tolerance (±%)", 1, 50, 10) / 100.0
    run = st.button("▶ Reconcile", type="primary", use_container_width=True)

st.info(f"**{pname}** (#{pid}, {pfuel}) ↔ {', '.join(resources) or '— pick resources —'} · "
        f"years {', '.join(map(str, years))}")

if not run:
    st.caption("Pick the plant + its ERCOT resources, then **Reconcile**.")
    st.stop()
if not resources:
    st.warning("Select at least one ERCOT resource (or add an override).")
    st.stop()

with st.status("Reconciling… (fetching any missing SCED days — cached days are reused)",
               expanded=True) as status:
    res = R.reconcile(pid, pname, resources, tuple(sorted(years)), tolerance=tol)
    status.update(label="Done.", state="complete")
t, s = res["table"], res["summary"]

if s["months_compared"] == 0:
    st.error("No months where both SCED and EIA exist. EIA-923 lags ~6 months and SCED ~60 days "
             "— pick a year where both are published, and make sure resources are mapped.")
    st.stop()

# Summary
op = s["overall_pct_diff"]
c = st.columns(4)
c[0].metric("EIA net gen", f"{s['eia_total_mwh']:,.0f} MWh")
c[1].metric("SCED net gen", f"{s['sced_total_mwh']:,.0f} MWh")
c[2].metric("Overall diff", f"{op*100:+.2f}%" if op is not None else "—",
            help="SCED total vs EIA total over compared months.")
c[3].metric("Months off", f"{s['months_off']} / {s['months_compared']}",
            help=f"|SCED−EIA| > {tol*100:.0f}% of EIA.")

if s["months_off"]:
    st.warning(f"⚠️ SCED is off in **{s['months_off']}** month(s) by more than {tol*100:.0f}%. "
               "See the flagged rows — common causes: telemetry outages, curtailment not in "
               "telemetry, or a unit mapped to the wrong plant.")
else:
    st.success(f"✅ SCED tracks EIA within ±{tol*100:.0f}% in every compared month "
               f"(overall {op*100:+.2f}%).")

# Chart
plot = t.dropna(subset=["eia_mwh", "sced_mwh"])
if not plot.empty:
    fig = go.Figure()
    fig.add_bar(x=plot["month"], y=plot["eia_mwh"], name="EIA-923 (metered)")
    fig.add_bar(x=plot["month"], y=plot["sced_mwh"], name="SCED (telemetry)")
    fig.update_layout(barmode="group", height=380, hovermode="x unified",
                      yaxis_title="MWh", margin=dict(t=20, b=10),
                      legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, use_container_width=True)

# Table
show = t.copy()
show["month"] = pd.to_datetime(show["month"]).dt.strftime("%Y-%m")
show["pct_diff"] = (show["pct_diff"] * 100).round(1)
for col in ["eia_mwh", "sced_mwh", "diff_mwh"]:
    show[col] = show[col].round(0)
show = show.rename(columns={"eia_mwh": "EIA MWh", "sced_mwh": "SCED MWh",
                            "diff_mwh": "Δ MWh", "pct_diff": "Δ %", "flag": "flag"})
st.dataframe(show, hide_index=True, use_container_width=True)
_export.download_block(st, t, name=f"reconcile_{pid}_{'_'.join(map(str, years))}",
                       title="EIA ↔ SCED reconciliation",
                       meta={"Plant ID": pid, "Years": ", ".join(map(str, years)),
                             "Rows": f"{len(t):,}"})

st.caption("Coverage note: SCED months are summed from telemetry actually pulled/cached. A "
           "missing or low SCED month usually means that month wasn't fully fetched — re-run to "
           "fill it. EIA-923 is the metered reference.")
