"""Per-asset SCED→EIA generation uplift factors.

SCED telemetry systematically **under-reads WIND** net generation versus EIA-923
revenue meters (measured EIA/SCED ≈ 1.03–1.13 across the wind fleet; solar
≈ 1.00). The real ERCOT market — and the actual VPPA invoice — settle on the
revenue meter, so a settlement estimate built on raw SCED under-states wind
volume and $. This registry holds a per-asset multiplicative uplift, built by
``build_sced_uplift.py`` from the SCED↔EIA overlap and re-fit as data lands;
:func:`ercot_core.settlement.compute_settlement` applies it so wind settlement
matches the EIA/meter truth. Solar factors are 1.0 (no-op).

Registry format (``data/sced_uplift.json``):
    {"AGUAYO_UNIT1": {"factor": 1.10, "tech": "wind", "n_months": 24,
                      "span": "2024-01..2025-12", "method": "median EIA/SCED"}, ...}
"""

from __future__ import annotations

import json
from pathlib import Path

# Version-controlled curated registry (like ercot_assets.json), NOT the gitignored
# data lake — so the uplift travels with the code and is active on any checkout.
REGISTRY_PATH = Path(__file__).resolve().parent / "registry" / "sced_uplift.json"

# Clamp: SCED under-reads wind, so the uplift is ≥ 1.0; cap at 1.5 to fence off a
# bad fit (e.g. a near-zero-SCED ramp month) from ballooning a settlement.
MIN_FACTOR, MAX_FACTOR = 1.0, 1.5

_cache: dict = {}
_cache_mtime: float | None = None


def load() -> dict:
    """The registry dict (empty if the file is missing/unreadable). mtime-cached."""
    global _cache, _cache_mtime
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache_mtime:
        try:
            _cache = json.loads(REGISTRY_PATH.read_text())
        except (ValueError, OSError):
            _cache = {}
        _cache_mtime = mtime
    return _cache


def factor(node: str | None, default: float = 1.0) -> float:
    """Multiplicative SCED→EIA uplift for ``node`` — 1.0 when absent (solar/new).

    Clamped to [MIN_FACTOR, MAX_FACTOR]. A registry entry with factor 1.0 (solar)
    is a transparent no-op.
    """
    if not node:
        return default
    rec = load().get(str(node))
    if not isinstance(rec, dict):
        return default
    try:
        f = float(rec.get("factor", default))
    except (TypeError, ValueError):
        return default
    if f != f or f <= 0:            # NaN / nonpositive → no-op
        return default
    return max(MIN_FACTOR, min(MAX_FACTOR, f))
