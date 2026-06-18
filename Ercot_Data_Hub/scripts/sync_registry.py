#!/usr/bin/env python3
"""Refresh the vendored curated asset registry from the price_settlements repo.

The Hub ships a copy of ``ercot_assets.json`` under
``ercot_core/registry/`` so it has no hard dependency on a sibling checkout
(see ercot_core/paths.py). price_settlements remains the source of truth; run
this when you have both repos locally and want to pull its latest registry in.

Usage:
    python scripts/sync_registry.py                 # auto-find sibling repo
    python scripts/sync_registry.py /path/to/ercot_assets.json
    ERCOT_PRICE_SETTLEMENTS=/path/to/repo python scripts/sync_registry.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "ercot_core" / "registry" / "ercot_assets.json"


def _find_source(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).expanduser()
    env_repo = os.environ.get("ERCOT_PRICE_SETTLEMENTS")
    if env_repo:
        return Path(env_repo).expanduser() / "ercot_assets.json"
    return ROOT.parent / "price_settlements" / "ercot_assets.json"


def main(argv: list[str]) -> int:
    src = _find_source(argv)
    if not src.exists():
        print(f"source registry not found: {src}", file=sys.stderr)
        print("pass a path, set ERCOT_PRICE_SETTLEMENTS, or clone price_settlements as a sibling.",
              file=sys.stderr)
        return 1
    # Validate it parses and is non-empty before overwriting the vendored copy.
    records = json.loads(src.read_text())
    if not isinstance(records, dict) or not records:
        print(f"source registry looks empty/invalid: {src}", file=sys.stderr)
        return 1
    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, VENDORED)
    print(f"synced {len(records)} records: {src} -> {VENDORED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
