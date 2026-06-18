"""Compatibility shim — settlement-point lists now live in
ercot_core.settlement_points (shared across the monorepo).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ercot_core.settlement_points import *  # noqa: F401,F403
from ercot_core.settlement_points import (  # noqa: F401
    LOCATION_TYPES, PRICE_ONLY_TYPES, HUBS, ZONES, locations, refresh,
)
