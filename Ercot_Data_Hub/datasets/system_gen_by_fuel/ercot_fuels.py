"""Compatibility shim — the fuel taxonomy + provenance engine now lives in
ercot_core.fuels (shared across the monorepo). Kept so the original
``import ercot_fuels as F`` call sites in this dataset still work.
"""

from __future__ import annotations

import os
import sys

# Put the repo root on sys.path so ``ercot_core`` resolves whether this module
# is run as a script, imported by the unified app, or run via subprocess.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ercot_core.fuels import *  # noqa: F401,F403
from ercot_core.fuels import (  # noqa: F401  (explicit for names tools may miss)
    CANONICAL_FUELS, REPORT_FUEL_RENAME, DASHBOARD_FUEL_MAP, SCHEMA_COLUMNS,
    KEY_COLUMNS, SOURCE_FUEL_MIX_REPORT, SOURCE_DASHBOARD, SOURCE_API,
    ST_FINAL, ST_PROVISIONAL, source_priority, finalize, merge_with_provenance,
    to_utc,
)
