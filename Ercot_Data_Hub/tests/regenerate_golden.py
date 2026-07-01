"""Regenerate the golden settlement baselines from the current engine + data lake.

Run this ONLY after a legitimate data backfill (e.g. a DST fall-back re-pull) has
intentionally changed a settled month — never to paper over an engine regression.

    python tests/regenerate_golden.py

Rewrites the mwh/net_cfd of every entry in golden/settlements.json in place,
using tests/_settle_worker.py (one subprocess per portal).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden" / "settlements.json"
WORKER = HERE / "_settle_worker.py"


def main() -> None:
    spec = json.loads(GOLDEN.read_text())
    for bl in spec["baselines"]:
        proc = subprocess.run(
            [sys.executable, str(WORKER), bl["portal_dir"], bl["package"], bl["month"]],
            capture_output=True, text=True, timeout=600,
        )
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
        got = json.loads(line)
        if "skip" in got:
            print(f"SKIP {bl['portal_dir']} {bl['month']}: {got['skip']}")
            continue
        bl["mwh"], bl["net_cfd"] = got["mwh"], got["net_cfd"]
        print(f"OK   {bl['portal_dir']} {bl['month']}: "
              f"MWh={got['mwh']} net_cfd={got['net_cfd']} (rows={got.get('rows')})")
    GOLDEN.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"\nWrote {GOLDEN}")


if __name__ == "__main__":
    main()
