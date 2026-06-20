"""Hidalgo Mirasole Wind Settlement Portal — router / entry point.

A focused, customer-facing app for one asset (Hidalgo Mirasole Wind). It reuses the
shared ERCOT engine and cached data from the sibling ``Ercot_Data_Hub`` repo;
see :mod:`portal.hub`. This file owns ``st.set_page_config`` for the whole app —
the page scripts must not call it again.

Run:  .venv/bin/streamlit run app/Home.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the ``portal`` package importable however the app is launched.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from portal import contract  # noqa: E402

# Title/icon follow the asset so the same template reads right for solar or wind.
_name = str(contract.ASSET.get("project_name", "Settlement Portal"))
_is_wind = "wind" in str(contract.ASSET.get("tech", "")).lower()
_icon = "🌬️" if _is_wind else "☀️"
st.set_page_config(page_title=f"{_name} · Settlement Portal", page_icon=_icon, layout="wide")

nav = st.navigation({
    _name: [
        st.Page("pages/1_Overview.py", title="Overview", icon="📊", default=True),
        st.Page("pages/2_Past_Settlement.py", title="Past Settlement", icon="🧾"),
        st.Page("pages/3_Future_Bill.py", title="Projected Bill", icon="🔮"),
    ],
    "Audit": [
        st.Page("pages/4_Invoice_Audit.py", title="Invoice Audit", icon="🔍"),
    ],
    "Data": [
        st.Page("pages/6_Update_Data.py", title="Update data", icon="⬇️"),
    ],
    "About": [
        st.Page("pages/5_Contract.py", title="Contract Terms", icon="📄"),
        st.Page("pages/0_How_It_Works.py", title="How it works", icon="📘"),
    ],
})
nav.run()
