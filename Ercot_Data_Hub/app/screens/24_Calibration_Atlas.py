"""Calibration Atlas — fleet-wide EIA-923 model calibration (wind vs solar).

A branded, shareable summary of the realized-output calibration: how far ERA5
physics under-predicts each of 176 ERCOT plants, the Texas bias map, and per-asset
portal impact. The page embeds the same self-contained HTML published as the
SR Inc. dashboard, so the hub view and the shared artifact stay identical.
"""

from __future__ import annotations

import pathlib

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Calibration Atlas", layout="wide")

_HTML = (pathlib.Path(__file__).resolve().parents[1] / "assets"
         / "calibration_atlas.html").read_text()

st.title("📐 Calibration Atlas")
st.caption("Fleet-wide EIA-923 calibration of the ERA5 generation model — wind vs "
           "solar, the Texas bias map, and portal impact. SR Inc. branded; the "
           "shareable artifact mirrors this page. Rebuild data via "
           "`eia_anchor` / `build_full_wind_sweep.py`.")

components.html(_HTML, height=2700, scrolling=True)
