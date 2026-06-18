"""Tiny zero-dependency test runner so these files run under plain ``python``
(pytest is not a project dependency). Each test file calls run(globals()) in its
__main__ block; the same files also work if pytest is ever added.
"""

from __future__ import annotations

import sys
import traceback


def run(ns: dict) -> int:
    tests = [(n, f) for n, f in sorted(ns.items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {exc}")
            traceback.print_exc()
    total = len(tests)
    print(f"\n{total - failed}/{total} passed"
          + (f", {failed} FAILED" if failed else ""))
    return 1 if failed else 0


def main(ns: dict) -> None:
    sys.exit(run(ns))
