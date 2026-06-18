"""Standalone Streamlit app for Wind Capture & Revenue.

Run:  streamlit run capture_app.py
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Wind Capture & Revenue", page_icon="💨", layout="wide")

import capture_app_ui  # noqa: E402

capture_app_ui.render()
