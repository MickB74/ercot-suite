"""Standalone Streamlit app for the ERCOT price forecast.

Run:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="ERCOT Price Forecast", page_icon="⚡", layout="wide")

import pf_app_ui  # noqa: E402

pf_app_ui.render()
