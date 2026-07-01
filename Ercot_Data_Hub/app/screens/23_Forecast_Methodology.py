"""Forecast Methodology — a shareable visual walkthrough of how the forecasts work.

A client-facing explainer (hosted as a Claude artifact) covering the price model
(market-implied heat rate × gas strip + Monte Carlo) and the weather engines
(USWTDB wind ensemble, PVWatts solar), with a live hub picker, the source list,
and the traded-price calibration logic. This page is just the door to it.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: F401,E402  (path bootstrap)

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

# The published explainer artifact (price + weather forecast methodology portal).
EXPLAINER_URL = "https://claude.ai/code/artifact/bb4a11b4-b25d-4c8b-a9cb-147112bd8a7c"

st.title("📖 Forecast Methodology")
st.caption("A shareable, client-facing walkthrough of how the ERCOT price and "
           "weather forecasts are built — the logic behind every number on the "
           "Price, Solar, and Wind Forecast pages.")

st.link_button("Open the methodology portal  ↗", EXPLAINER_URL,
               type="primary", use_container_width=False)

# ── forward price → capture → scarcity → cap (SR-branded, self-contained) ─────
st.subheader("Forward price & capture — how the projected bill is built")
_meth = (pathlib.Path(__file__).resolve().parents[1] / "assets"
         / "price_methodology.html").read_text()
components.html(_meth, height=1500, scrolling=True)

st.divider()

c1, c2 = st.columns(2)
with c1:
    st.subheader("⚡ Prices")
    st.markdown(
        "- **Market-implied heat-rate model** — `power = gas strip × implied "
        "heat rate`, with Monte Carlo P10/P50/P90 bands.\n"
        "- **Live hub picker** — switch between HB_NORTH / HOUSTON / SOUTH / WEST "
        "/ PAN / averages; the curve and write-up update.\n"
        "- **Sources** — every input (EIA NYMEX, STEO, AEO; ERCOT CDR) with "
        "click-through links and auto/paid labels.\n"
        "- **Calibration** — how a pasted ICE/Nodal strip blends into the near "
        "months and fades to the model.")
with c2:
    st.subheader("🌦️ Weather")
    st.markdown(
        "- **Wind** — real USWTDB turbine fleet, measured shear & air density, "
        "ERA5 + multi-model NWP ensemble, ERCOT/SCED calibration.\n"
        "- **Solar** — NREL PVWatts on NSRDB; TMY expected year vs. actual-year "
        "backcast.\n"
        "- **Settlement** — weather × the 8,760-hour price strip = capture price "
        "behind every project portal's Future Bill.")

st.info("Opens in a new tab. The portal is a self-contained page you can share "
        "with offtakers and colleagues. Figures reflect the run it was published "
        "from — the live numbers are always on the **Price Forecast** page.")
