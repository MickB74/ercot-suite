"""Compute one portal's settlement for one month and print {mwh, net_cfd} as JSON.

Run as a subprocess so each portal gets a clean interpreter — several portals
share the package name ``portal`` and their own ``config.json``, so they cannot
be imported into the same process. Mirrors how the portals actually run.

    python tests/_settle_worker.py <portal_dir> <package> <YYYY-MM>
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]      # …/ercot-suite
HUB = Path(__file__).resolve().parents[1]         # …/Ercot_Data_Hub


def main() -> None:
    portal_dir, package, ym = sys.argv[1], sys.argv[2], sys.argv[3]
    pdir = SUITE / portal_dir
    if not (pdir / package).is_dir():
        print(json.dumps({"skip": f"package {package} not found in {portal_dir}"})); return
    sys.path.insert(0, str(HUB))
    sys.path.insert(0, str(pdir))
    try:
        analytics = importlib.import_module(f"{package}.analytics")
        contract = importlib.import_module(f"{package}.contract")
    except Exception as exc:                       # noqa: BLE001
        print(json.dumps({"skip": f"import failed: {exc}"})); return

    start = dt.date.fromisoformat(ym + "-01")
    end = (start.replace(day=28) + dt.timedelta(days=4))
    end = end - dt.timedelta(days=end.day)
    try:
        res = analytics.settle(start, end, contract.load_contract())
    except Exception as exc:                       # noqa: BLE001
        print(json.dumps({"skip": f"settle failed: {exc}"})); return
    if not res or res["intervals"].empty:
        print(json.dumps({"skip": f"no cached data for {ym}"})); return
    iv = res["intervals"]
    print(json.dumps({
        "mwh": round(float(iv["mwh"].sum()), 4),
        "net_cfd": round(float(iv["cfd"].sum()), 4),
        "rows": int(len(iv)),
    }))


if __name__ == "__main__":
    main()
