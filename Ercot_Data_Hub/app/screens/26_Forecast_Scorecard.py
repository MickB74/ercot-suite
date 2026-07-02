"""Forecast Scorecard — per-asset expected-vs-actual generation bias over time.

Reads the scorecard written by ``ercot-suite/forecast_scorecard.py`` (anchor-
grounded expected monthly generation vs actual SCED metered, per asset) and
trends the bias, so a systematic level error surfaces as a number instead of
needing someone to eyeball a chart. Includes the plausibility-guard flags.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
from ercot_core import paths  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]        # …/Ercot_Data_Hub
SUITE = REPO.parent                                        # …/ercot-suite
SCRIPT = SUITE / "forecast_scorecard.py"
PYEXE = REPO / ".venv" / "bin" / "python"
SC_DIR = paths.DATA / "scorecard"

st.title("📋 Forecast Scorecard")
st.caption(
    "Anchor-grounded **expected** monthly generation (EIA-923 P50 CF × the "
    "anchor's own nameplate × contract share) vs **actual** SCED metered output, "
    "per asset. Median bias ≈ 0% means the forecast *level* is right; large or "
    "one-sided bias flags an asset to investigate.")

if st.button("🔄 Re-run scorecard now", help="Recompute from the latest data lake"):
    with st.spinner("Running forecast_scorecard.py …"):
        r = subprocess.run([str(PYEXE), str(SCRIPT)], capture_output=True, text=True)
    if r.returncode == 0:
        st.success("Scorecard refreshed.")
    else:
        st.error(f"Run failed:\n{r.stderr[-1500:]}")

summary_p = SC_DIR / "forecast_scorecard_summary.csv"
detail_p = SC_DIR / "forecast_scorecard.csv"
if not summary_p.exists() or not detail_p.exists():
    st.info("No scorecard yet — click **Re-run scorecard now**, or run "
            "`forecast_scorecard.py` from the suite root.")
    st.stop()

sm = pd.read_csv(summary_p)
det = pd.read_csv(detail_p)

# ── headline KPIs ────────────────────────────────────────────────────────────
scored = sm[sm["n_months"].fillna(0) > 0]
flagged = sm[sm["flags"].fillna("ok") != "ok"]
worst = (scored.loc[scored["median_bias_%"].abs().idxmax()]
         if len(scored) and scored["median_bias_%"].notna().any() else None)
c = st.columns(4)
c[0].metric("Assets scored", int((sm["n_months"].fillna(0) > 0).sum()))
c[1].metric("Guard flags", int(len(flagged)))
c[2].metric("Assets |bias|>10%",
            int((scored["median_bias_%"].abs() > 10).sum()))
c[3].metric("Worst median bias",
            f"{worst['median_bias_%']:+.0f}%" if worst is not None else "—",
            delta=(worst["asset"] if worst is not None else None), delta_color="off")

# ── summary table ────────────────────────────────────────────────────────────
st.subheader("Per-asset summary")
show = sm.copy()
for col in ("median_bias_%", "mape_%"):
    if col in show:
        show[col] = show[col].map(lambda v: "" if pd.isna(v) else f"{v:+.1f}")
show["n_months"] = show["n_months"].map(lambda v: "" if pd.isna(v) else f"{int(v)}")


def _hl(row):
    f = str(row.get("flags", "ok"))
    try:
        b = abs(float(sm.loc[row.name, "median_bias_%"]))
    except (TypeError, ValueError):
        b = 0.0
    if b > 10:
        return ["background-color: #fde2e1"] * len(row)      # systematic bias
    if f != "ok" and "validated" not in f:
        return ["background-color: #fff3cd"] * len(row)       # unvalidated / hygiene
    return [""] * len(row)


st.dataframe(show.style.apply(_hl, axis=1), use_container_width=True, hide_index=True)
st.caption("🔴 systematic level bias >10% (investigate) · 🟡 guard flag "
           "(data-hygiene / unvalidated) · unshaded = grounding validated against actuals.")

# ── per-asset bias over time ─────────────────────────────────────────────────
st.subheader("Bias over time")
assets = sorted(det["asset"].unique())
default = worst["asset"] if worst is not None and worst["asset"] in assets else assets[0]
pick = st.selectbox("Asset", assets, index=assets.index(default))
d = det[det["asset"] == pick].sort_values("month")

fig = go.Figure()
fig.add_bar(x=d["month"], y=d["expected_mwh"], name="Expected (anchor P50)",
            marker_color="#88a918", opacity=0.55)
fig.add_bar(x=d["month"], y=d["actual_mwh"], name="Actual (SCED)",
            marker_color="#1f77b4", opacity=0.85)
fig.update_layout(barmode="group", height=340, hovermode="x unified",
                  margin=dict(t=20, b=10), legend=dict(orientation="h", y=1.15),
                  yaxis_title="MWh")
st.plotly_chart(fig, use_container_width=True)

fig2 = go.Figure()
colors = ["#d62728" if abs(v) > 10 else "#888" for v in d["bias_pct"]]
fig2.add_bar(x=d["month"], y=d["bias_pct"], marker_color=colors,
             hovertemplate="%{x}<br>bias %{y:+.0f}%<extra></extra>")
fig2.add_hline(y=0, line_color="#333")
fig2.update_layout(height=240, margin=dict(t=10, b=10),
                   yaxis_title="Expected − Actual (%)")
st.plotly_chart(fig2, use_container_width=True)

med = d["bias_pct"].median()
st.caption(f"**{pick}** — median bias **{med:+.1f}%** over {len(d)} months. "
           "Early ramp months (plant coming online) can show large one-off bias; "
           "the median is robust to those.")
