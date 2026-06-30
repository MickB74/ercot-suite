"""ERCOT System — demand, generation mix, and the deepening midday price collapse.

A branded, shareable system view (2020–2026): demand growth, the 8× solar surge,
the deepening duck curve, and how that chain erodes solar capture and widens
solar-node basis. Embeds the same self-contained HTML published as the SR Inc.
dashboard so the hub view and the shared artifact stay identical.
"""

from __future__ import annotations

import pathlib

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="ERCOT System", layout="wide")

_HTML = (pathlib.Path(__file__).resolve().parents[1] / "assets"
         / "ercot_system.html").read_text()

st.title("🦆 ERCOT System Outlook")
st.caption("Demand, generation mix, and price-shape 2020–2026 — the system story "
           "behind falling solar capture and widening basis. SR Inc. branded; "
           "data from the Fuel Mix Report + HB_NORTH RT15.")

components.html(_HTML, height=2300, scrolling=True)
