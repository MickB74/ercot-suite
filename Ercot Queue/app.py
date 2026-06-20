"""Standalone ERCOT Queue Explorer — double-click ``Open ERCOT Queue.command``
or run:  ../Ercot_Data_Hub/.venv/bin/streamlit run app.py

Reuses the Data Hub's engine (``ercot_core``), shared UI helpers (``_common`` /
``_export``), and data lake — no duplicate venv or data. The page UI itself lives
in ``queue_page.render()``, which the Data Hub screen also calls, so the
standalone app and the embedded page never diverge.
"""

from __future__ import annotations

import os
import sys

import streamlit as st

# Locate the sibling Data Hub and put it (for ercot_core) and its app/ dir (for
# the shared _common / _export helpers) on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_HUB = os.path.join(os.path.dirname(_HERE), "Ercot_Data_Hub")
for p in (_HERE, _HUB, os.path.join(_HUB, "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

# This standalone app OWNS the page config (the embedded screen does not).
st.set_page_config(page_title="ERCOT Queue Explorer", page_icon="🔌",
                   layout="wide", initial_sidebar_state="collapsed")

from ercot_core import paths  # noqa: E402

paths.ensure_dirs()

import queue_page  # noqa: E402

queue_page.render()
