"""Queue Explorer — search, analyze & run due diligence on the ERCOT queue.

A UI over ``ercot_core.queue_search`` (the merged GIS + interconnection.fyi queue)
and ``ercot_core.tx_filings`` (Texas county/state filing links + DD checklists).

The page body lives in ``queue_page.render()`` in the sibling ``Ercot Queue``
folder, shared with that folder's standalone ``app.py`` so the embedded page and
the double-click app never diverge (same pattern as 19_Project_Hub importing
``build_hub`` from ``Ercot_Project Hub``).
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common/_export)
# The standalone Queue tool is a sibling folder of Ercot_Data_Hub (name has a
# space, but queue_page.py does not, so it imports fine once on the path).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "Ercot Queue"))

from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402,F401  (path bootstrap + shared helpers)

import queue_page  # noqa: E402

queue_page.render()
