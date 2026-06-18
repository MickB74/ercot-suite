"""One quieted gridstatus.Ercot() client for the whole monorepo.

gridstatus logs every MIS download at INFO/DEBUG; we set it to WARNING once,
here, instead of in five different modules.
"""

from __future__ import annotations

import logging

logging.getLogger("gridstatus").setLevel(logging.WARNING)

_iso = None


def ercot():
    """Return a shared gridstatus.Ercot() instance (lazily constructed)."""
    global _iso
    if _iso is None:
        import gridstatus
        _iso = gridstatus.Ercot()
    return _iso
