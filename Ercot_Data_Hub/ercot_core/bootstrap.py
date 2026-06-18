"""Make ``ercot_core`` and the dataset modules importable from anywhere.

Dataset scripts keep their original flat intra-package imports (``import
eia923``, ``import resource_catalog`` ...). To run them from the repo root, the
unified Streamlit app, or a subprocess, we put the repo root and every dataset
directory on ``sys.path``. There are no library-module name collisions across
the four datasets (only ``app.py``, which the unified app never imports).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = ROOT / "datasets"
_DATASET_DIRS = [
    DATASETS_DIR / "system_gen_by_fuel",
    DATASETS_DIR / "eia923",
    DATASETS_DIR / "plant_sced",
    DATASETS_DIR / "hub_prices",
    DATASETS_DIR / "solar_forecast",
    DATASETS_DIR / "wind_forecast",
]


def setup_path(*, datasets: bool = True) -> None:
    """Insert the repo root (for ``ercot_core``) and dataset dirs on sys.path."""
    entries = [ROOT]
    if datasets:
        entries += _DATASET_DIRS
    for p in entries:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
