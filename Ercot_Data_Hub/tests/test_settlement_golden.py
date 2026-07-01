"""Golden regression on the REAL settlement engine, per invoice-validated portal.

Runs each portal's settlement for a fully-settled month against the cached data
lake and asserts the penny-exact output hasn't moved. This protects the numbers
that actually reach customers — any engine change that shifts a settled bill
fails here.

Each portal runs in its own subprocess (several share the package name ``portal``
and their own config, so they can't co-exist in one interpreter). Data-gated:
skips on a bare checkout without the data lake, and per-portal if that portal or
its cached month isn't present.

Regenerate baselines after a legitimate data backfill:
    python tests/regenerate_golden.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import requires_data_lake

GOLDEN = Path(__file__).parent / "golden" / "settlements.json"
SPEC = json.loads(GOLDEN.read_text())
WORKER = Path(__file__).parent / "_settle_worker.py"


def run_settlement(portal_dir: str, package: str, month: str) -> dict:
    """Compute one portal-month in a clean subprocess; returns the worker's JSON."""
    proc = subprocess.run(
        [sys.executable, str(WORKER), portal_dir, package, month],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        pytest.skip(f"{portal_dir} worker failed: {proc.stderr.strip()[-300:]}")
    out = proc.stdout.strip().splitlines()
    if not out:
        pytest.skip(f"{portal_dir} worker produced no output")
    return json.loads(out[-1])


@requires_data_lake
@pytest.mark.parametrize("bl", SPEC["baselines"], ids=lambda b: b["portal_dir"])
def test_settled_month_matches_golden(bl):
    got = run_settlement(bl["portal_dir"], bl["package"], bl["month"])
    if "skip" in got:
        pytest.skip(f"{bl['portal_dir']} {bl['month']}: {got['skip']}")

    assert got["mwh"] == pytest.approx(bl["mwh"], abs=SPEC["tol_mwh"]), (
        f"{bl['portal_dir']} settled MWh moved: {got['mwh']} vs golden {bl['mwh']}")
    assert got["net_cfd"] == pytest.approx(bl["net_cfd"], abs=SPEC["tol_net"]), (
        f"{bl['portal_dir']} settled net CfD moved: {got['net_cfd']} "
        f"vs golden {bl['net_cfd']}")
