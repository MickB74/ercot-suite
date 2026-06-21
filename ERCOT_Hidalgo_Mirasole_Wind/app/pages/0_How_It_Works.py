"""How it works — data sources, methodology, and assumptions behind this portal.

Tech-aware: the generation/weather section reads the asset's technology from
``contract.ASSET`` so the same page reads correctly for a solar or a wind asset.
"""

from __future__ import annotations

import _boot  # noqa: F401
import streamlit as st

_boot.ensure_hub(st)

from portal import branding, contract  # noqa: E402

a = contract.ASSET
terms = contract.load_contract()
is_wind = "wind" in str(a.get("tech", "")).lower()
node = a.get("resource_node", "")
strike = float(terms.get("strike", 0.0))
share_pct = float(terms.get("volume_share_pct", 100.0))

branding.hero(st, "How this portal works",
              "Where the numbers come from, how generation and price are estimated, "
              "and the assumptions behind every figure.")

st.markdown(
    f"This is a focused settlement portal for **{a.get('project_name')}** "
    f"({a.get('capacity_mw', 0):,.0f} MW {a.get('tech')}, ERCOT node `{node}`), settling a "
    f"**{terms.get('structure')}** at a **${strike:,.2f}/MWh** strike. It reuses the shared "
    "ERCOT Data Hub's engine and the market data the Hub has already pulled and cached — "
    "there's no live fetch here, so it loads instantly and works offline.")

st.subheader("📥 Where the data comes from")
xcheck = (f"**EIA-923** monthly net generation (plant {a.get('eia_plant_id')}) as an "
          "independent sanity-check against the ERCOT SCED totals."
          if a.get("eia_plant_id")
          else "An **EIA-923** cross-check becomes available once an EIA plant id is mapped "
               "to this asset (not set yet).")
st.markdown(
    f"""
- **Metered generation** — ERCOT **SCED telemetered net output** (15-minute) for node
  `{node}`: the plant's actual output as reported by ERCOT. It publishes on a **~60-day
  lag**, so the most recent ~2 months can't be settled yet.
- **Prices** — ERCOT **Real-Time 15-minute Settlement Point Prices (RT15)** at the
  settlement reference, pulled from the **ERCOT public API** (recent live window +
  historical archive) and cached in the Hub.
- **Cross-check** — {xcheck}
""")

st.subheader("🧾 Past Settlement — actual, not modelled")
floor_txt = ("A **price floor** applies: intervals below it don't settle (typical VPPA)."
             if terms.get("apply_floor", True) else "No price floor is applied.")
st.markdown(
    f"""
Every interval with **both** real metered output and a real RT15 price is settled as:

> **metered MWh × (RT15 price − ${strike:,.2f} strike) × {share_pct:.0f}% volume share**

{floor_txt} Positive = the offtaker **receives**; negative = the offtaker **pays**. These
are real figures — the only limitation is ERCOT's ~60-day publication lag.
""")

st.subheader("🔮 Projected Bill — an estimate")
if is_wind:
    st.markdown(
        """
**Generation** is projected from the project's **historical metered shape** — the average
MWh for each calendar month across all settled history (at your volume share), carried
forward. Where a **calibrated typical-year wind model** has been cached for the plant, it
can also be selected as the basis (a weather-typical profile rescaled to the plant's real
metered output, capturing availability/curtailment/losses). **Annual degradation** is
configurable and set **low by default** — wind turbines show little systematic output
decline; raise it only to stress wear or availability.
""")
else:
    st.markdown(
        """
**Generation** offers up to three bases: **Historical shape** (mean MWh per calendar month
from metered history); **Calibrated model** (a PVWatts typical-meteorological-year solar
profile from NSRDB weather, rescaled to match this plant's real metered output — capturing
actual availability, curtailment and losses); or **Physical model** (raw PVWatts TMY).
**Annual degradation** defaults to the **solar-PV norm (~0.5%/yr)**.
""")
st.markdown(
    """
**Price** defaults to a **P10/P50/P90 forward forecast** (market-implied heat-rate × gas
strip, capture-adjusted to the settlement hub), shown as a shaded band around the P50
central estimate. If the forecast is unavailable you can override it with a **flat manual
price** (defaulting to the trailing capture price from history) and a **± sensitivity band**
that shows how the bill swings if prices land higher or lower. The projection is
*expected MWh × (price − strike)*, month by month.
""")

st.subheader("⚠️ What this is *not*")
st.markdown(
    """
- The **Projected Bill** is a planning figure, **not an invoice** — generation is modelled
  and the price is your assumption.
- Nothing here fetches live ERCOT data; it reads what the Hub has cached. Refresh the Hub
  to extend the settle-able window.
- **Invoice Audit** is where you reconcile a counterparty's statement against these
  independently-computed figures.
""")

st.caption(f"Asset: {a.get('project_name')} · node {node} · {a.get('capacity_mw', 0):,.0f} MW "
           f"{a.get('tech')} · settles at {contract.settle_location(terms)}.")
