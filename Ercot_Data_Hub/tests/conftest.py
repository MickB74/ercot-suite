"""Shared test fixtures. Puts the Data Hub root on sys.path so `import ercot_core`
works when pytest is run from anywhere, and exposes a data-lake availability flag
so the golden settlement tests can skip gracefully on a bare checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]      # …/Ercot_Data_Hub
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _data_lake_present() -> bool:
    """True if the cached parquet data lake the settlement engine reads exists."""
    try:
        from ercot_core import hub  # noqa: PLC0415
        root = hub.hub_root()
    except Exception:               # noqa: BLE001
        return False
    # Any of the lake dirs the portals read from.
    candidates = ["data", "data_lake", "cache"]
    return any((root / c).is_dir() for c in candidates)


@pytest.fixture(scope="session")
def data_lake_present() -> bool:
    return _data_lake_present()


requires_data_lake = pytest.mark.skipif(
    not _data_lake_present(),
    reason="cached ERCOT data lake not present — golden settlement tests need it",
)
