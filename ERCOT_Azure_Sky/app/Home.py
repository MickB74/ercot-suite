"""Azure Sky Wind Settlement Portal — router / entry point.

A focused, customer-facing app for one asset (Azure Sky Wind). It reuses the
shared ERCOT engine and cached data from the sibling ``Ercot_Data_Hub`` repo;
see :mod:`azuresky.hub`. This file owns ``st.set_page_config`` for the whole app
— the page scripts must not call it again.

Run:  .venv/bin/streamlit run app/Home.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the ``azuresky`` package importable however the app is launched.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Azure Sky Wind · Settlement Portal",
                   page_icon="🌬️", layout="wide")

nav = st.navigation({
    "Azure Sky Wind": [
        st.Page("pages/1_Overview.py", title="Overview", icon="📊", default=True),
        st.Page("pages/2_Past_Settlement_Estimate.py", title="Past Settlement Estimate", icon="🧾"),
        st.Page("pages/3_Future_Bill.py", title="Projected Bill", icon="🔮"),
    ],
    "Audit": [
        st.Page("pages/4_Invoice_Audit.py", title="Invoice Audit", icon="🔍"),
        st.Page("pages/7_Hub_vs_Node.py", title="Hub vs Node", icon="📡"),
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
