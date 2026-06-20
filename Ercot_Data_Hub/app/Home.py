"""ERCOT Data Hub — router / entry point.

Defines one grouped sidebar navigation across all pages so the workflow is
legible: **Start Here** (refresh data) → **Explore** → **Analyze**, with the
occasional mapping/name-resolution utilities tucked into **Tools** at the
bottom. This script owns ``st.set_page_config`` for the whole app; the
individual page scripts must NOT call it again.

Run:  .venv/bin/streamlit run app/Home.py
"""

from __future__ import annotations

import sys as _sys
import pathlib as _pathlib

# Ensure this dir (for _common) and the repo root are importable, regardless of
# how the app is launched (streamlit run, AppTest, subprocess).
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

import _common  # noqa: F401,E402  (path bootstrap — must import first)

import streamlit as st  # noqa: E402

from ercot_core import paths  # noqa: E402

st.set_page_config(page_title="ERCOT Data Hub", page_icon="⚡", layout="wide",
                   initial_sidebar_state="expanded")
paths.ensure_dirs()

# --------------------------------------------------------------------------
# Grouped navigation. Sections trace the natural workflow so a first-time user
# knows where to start and what feeds what. Titles/icons live here now that the
# pages no longer call st.set_page_config.
# --------------------------------------------------------------------------
# Page scripts live in app/screens/ (NOT app/pages/): a folder literally named
# "pages" makes Streamlit auto-generate a second, flat sidebar nav that conflicts
# with this grouped st.navigation. Renaming it leaves this as the only nav.
P = "screens"  # page files keep their numeric filenames; titles set explicitly here

nav = st.navigation({
    "Start Here": [
        st.Page("views/home.py", title="Control Tower", icon="⚡", default=True),
        st.Page(f"{P}/0_API_Keys.py", title="API Keys", icon="🔑"),
    ],
    "Explore": [
        st.Page(f"{P}/1_System_Generation.py", title="System Generation", icon="🔥"),
        st.Page(f"{P}/2_Hub_Prices.py", title="Hub Prices", icon="💵"),
        st.Page(f"{P}/3_Plant_SCED.py", title="Plant SCED", icon="🏭"),
        st.Page(f"{P}/5_Node_Explorer.py", title="Node Explorer", icon="📈"),
        st.Page(f"{P}/4_EIA_923.py", title="EIA-923 Generation", icon="📅"),
        st.Page(f"{P}/10_EIA_860_Plants.py", title="EIA-860 Plants", icon="🗺️"),
    ],
    "Forecasts": [
        st.Page(f"{P}/13_Solar_Forecast.py", title="Solar Forecast", icon="☀️"),
        st.Page(f"{P}/14_Wind_Forecast.py", title="Wind Forecast", icon="🌬️"),
        st.Page(f"{P}/16_Price_Forecast.py", title="Price Forecast", icon="📉"),
        st.Page(f"{P}/17_Plant_Value.py", title="Predicted Solar Settlement", icon="🔆"),
        st.Page(f"{P}/18_Wind_Capture.py", title="Predicted Wind Settlement", icon="💨"),
    ],
    "Analyze": [
        st.Page(f"{P}/7_PPA_Settlement.py", title="PPA Settlement", icon="🧾"),
        st.Page(f"{P}/15_Invoice_Validation.py", title="Invoice Validation", icon="✅"),
        st.Page(f"{P}/8_Reconciliation.py", title="Reconciliation", icon="🔁"),
        st.Page(f"{P}/9_Fleet_Reconciliation.py", title="Fleet Reconciliation", icon="🛰️"),
    ],
    # Onboard a new project and manage the registry it feeds.
    "Build a Project": [
        st.Page(f"{P}/20_Queue_Explorer.py", title="Queue Explorer", icon="🔌"),
        st.Page(f"{P}/6_Project_Lookup.py", title="Project Builder", icon="🏗️"),
        st.Page(f"{P}/19_Project_Hub.py", title="Project Hub", icon="🗂️"),
    ],
    # Back-end name/EIA matching that feeds the registry & reconciliation. Rarely
    # needed by hand, so kept last and out of the main workflow.
    "Crosswalk Tools": [
        st.Page(f"{P}/12_Name_Resolver.py", title="Name Resolver", icon="🔤"),
        st.Page(f"{P}/11_Auto_Crosswalk.py", title="Auto-Crosswalk", icon="🧩"),
    ],
})

nav.run()
