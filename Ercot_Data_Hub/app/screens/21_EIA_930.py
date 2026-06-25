"""EIA-930 — net generation by balancing authority (near-real-time sanity check).

EIA's Hourly Electric Grid Monitor publishes hourly net generation per balancing
authority with a ~1-day lag. This page totals it by **Day / Week / Month** and
charts one line per BA — a fast, independent cross-check on system generation
(it is BA-level, not plant-level; use EIA-923 / SCED for per-plant work).
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402
import _export  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from ercot_core import paths  # noqa: E402

# EIA-930 also reports region / interconnect roll-ups under the same "respondent"
# field. Hidden by default so the BA view isn't double-counted in one chart.
REGION_AGG = {"US48", "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE",
              "NW", "NY", "SE", "SW", "TEN", "TEX"}
GRANULARITY = {"Day": "D", "Week": "W-MON", "Month": "MS"}

st.title("🛰️ EIA-930 Generation by Balancing Authority")
st.caption("Hourly net generation from EIA's Hourly Electric Grid Monitor (Form "
           "EIA-930), ~1-day lag. Totalled by day/week/month. BA-level sanity "
           "check — not plant-level (use EIA-923 / SCED for assets).")


@st.cache_data(show_spinner=False)
def load() -> pd.DataFrame:
    if not paths.EIA930_REGION_PARQUET.exists():
        return pd.DataFrame()
    df = pd.read_parquet(paths.EIA930_REGION_PARQUET)
    df["period"] = pd.to_datetime(df["period"])
    return df


df = load()
if df.empty:
    _common.empty_state(
        st,
        "No EIA-930 data cached yet.",
        hint="Pull it from the Hub root:\n\n"
             "    cd Ercot_Data_Hub && ./.venv/bin/python orchestrate.py update eia930\n\n"
             "(needs the free `eia_api_key` in config.json).")
    st.stop()

# ── filters ──────────────────────────────────────────────────────────────────
labels = (df.drop_duplicates("respondent")
            .set_index("respondent")["respondent_name"].to_dict())
totals = df.groupby("respondent")["value_mwh"].sum().sort_values(ascending=False)

c = st.columns([1.2, 1.4, 2.2])
gran = c[0].radio("Total by", list(GRANULARITY), horizontal=True)
dmin, dmax = df["period"].min().date(), df["period"].max().date()
rng = c[1].date_input("Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax)
show_agg = c[0].checkbox("Include EIA region aggregates (US48, TEX, …)", value=False)

ba_pool = [r for r in totals.index if show_agg or r not in REGION_AGG]
opt_label = lambda r: f"{r} — {labels.get(r, r)}"  # noqa: E731
default = [r for r in ("ERCO",) if r in ba_pool] or ba_pool[:1]
picks = c[2].multiselect("Balancing authorities", ba_pool, default=default,
                         format_func=opt_label)
if not picks:
    st.info("Pick at least one balancing authority."); st.stop()
if isinstance(rng, tuple) and len(rng) == 2:
    lo, hi = rng
else:
    lo, hi = dmin, dmax

# ── aggregate to the chosen granularity ──────────────────────────────────────
mask = (df["respondent"].isin(picks)
        & (df["period"].dt.date >= lo) & (df["period"].dt.date <= hi))
sub = df.loc[mask, ["period", "respondent", "value_mwh"]]
if sub.empty:
    st.warning("No data in that window for the selected BAs."); st.stop()

rule = GRANULARITY[gran]
grp = sub.set_index("period").groupby("respondent")["value_mwh"].resample(rule)
agg = grp.sum().reset_index()
agg["hours"] = grp.count().reset_index(drop=True)        # hours of data in each bucket

# A leading/trailing bucket is "partial" when it holds fewer hours than the
# CALENDAR length of that period (current day/week/month still in progress, or
# the range starts mid bucket). Summing those makes the line cliff to near-zero
# — so trim the series to the span of complete buckets. Expected hours are
# calendar-derived (months vary 28–31 days), not a global max, so short months
# aren't mistaken for partial. Tolerance of 1h absorbs a DST hour / dropped row.
if rule == "D":
    expected = pd.Series(24, index=agg.index)
elif rule.startswith("W"):
    expected = pd.Series(168, index=agg.index)
else:  # month-start
    expected = agg["period"].dt.days_in_month * 24
complete = agg.loc[agg["hours"] >= expected - 1, "period"]
n_partial = 0
if not complete.empty:
    lo_p, hi_p = complete.min(), complete.max()
    n_partial = int((agg["period"] < lo_p).sum() + (agg["period"] > hi_p).sum())
    agg = agg[(agg["period"] >= lo_p) & (agg["period"] <= hi_p)]
agg["label"] = agg["respondent"].map(opt_label)
max_ts = df["period"].max()

# ── KPIs (lead BA = highest total over the complete buckets) ─────────────────
win_tot = agg.groupby("respondent")["value_mwh"].sum().sort_values(ascending=False)
if win_tot.empty:
    st.warning("Only a partial period in that window — widen the range."); st.stop()
lead = win_tot.index[0]
lead_series = agg[agg["respondent"] == lead].set_index("period")["value_mwh"]
per = gran.lower()
_dfmt = "%Y-%m" if rule == "MS" else "%Y-%m-%d"
_when = lambda ts: ts.strftime(_dfmt)  # noqa: E731

st.caption(f"Headline figures for **{lead}** — {labels.get(lead, lead)} "
           f"(largest of the selected BAs). Per-{per} totals over complete buckets.")
r1 = st.columns(3)
r1[0].metric(f"Total · {len(lead_series)} {per}s", f"{win_tot.loc[lead]/1e6:,.1f} TWh")
r1[1].metric(f"Avg / {per}", f"{lead_series.mean()/1e3:,.0f} GWh")
r1[2].metric("Data through", f"{dmax}")
r2 = st.columns(3)
if len(lead_series):
    pk, tr = lead_series.idxmax(), lead_series.idxmin()
    r2[0].metric(f"Peak {per} · {_when(pk)}", f"{lead_series.max()/1e3:,.0f} GWh")
    r2[1].metric(f"Min {per} · {_when(tr)}", f"{lead_series.min()/1e3:,.0f} GWh")
    r2[2].metric(f"Latest {per} · {_when(lead_series.index[-1])}",
                 f"{lead_series.iloc[-1]/1e3:,.0f} GWh")

# ── chart ────────────────────────────────────────────────────────────────────
fig = px.line(agg, x="period", y="value_mwh", color="label",
              labels={"period": "", "value_mwh": "Net generation (MWh)", "label": "BA"},
              title=f"Net generation by balancing authority — {gran.lower()} totals")
fig.update_layout(legend_title_text="", hovermode="x unified",
                  margin=dict(t=50, b=10, l=10, r=10))
fig.update_traces(hovertemplate="%{y:,.0f} MWh")
st.plotly_chart(fig, use_container_width=True)

if n_partial:
    st.caption(f"⏳ Latest hourly data: {max_ts:%Y-%m-%d %H:00}. "
               f"{n_partial} partial {gran.lower()} bucket(s) at the edge of the "
               "window are excluded from totals (still in progress).")

_common.data_status(st, path=paths.EIA930_REGION_PARQUET, rows=len(df),
                    span=(dmin, dmax), fresh_within_days=3)

# ── table + export ───────────────────────────────────────────────────────────
with st.expander("Data table"):
    wide = (agg.pivot(index="period", columns="respondent", values="value_mwh")
               .sort_index())
    wide.columns = [opt_label(c).split(" — ")[0] for c in wide.columns]  # BA code headers
    idx_fmt = "{:%Y-%m}" if rule == "MS" else "{:%Y-%m-%d}"
    st.caption(f"Net generation, MWh — {gran.lower()} totals by balancing authority.")
    styled = (wide.style
                  .format("{:,.0f} MWh", na_rep="—")
                  .format_index(idx_fmt, axis=0))
    st.dataframe(styled, use_container_width=True, height=360)
_export.download_block(
    st, agg.rename(columns={"value_mwh": "net_generation_mwh"}),
    name=f"eia930_{gran.lower()}_{lo}_{hi}",
    title="EIA-930 net generation by balancing authority",
    meta={"Granularity": gran, "Window": f"{lo} → {hi}",
          "BAs": ", ".join(picks), "Source": "EIA-930 Hourly Grid Monitor"})
