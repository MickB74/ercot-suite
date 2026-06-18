"""Compatibility shim — the resource-code -> plant-name crosswalk now lives in
ercot_core.plant_names (shared across the monorepo).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ercot_core.plant_names import *  # noqa: F401,F403
from ercot_core.plant_names import (  # noqa: F401
    KNOWN_MAPPINGS, build_crosswalk, load_crosswalk, load_queue,
)
