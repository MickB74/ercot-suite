"""Per-page bootstrap: put the repo root on sys.path and verify the Hub link.

Every page imports this first. ``ensure_hub(st)`` returns the ``ercot_core``
package or renders a friendly error and stops the page if the shared Data Hub
can't be located.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ensure_hub(st):
    """Return ercot_core, or show a clear setup message and stop the page."""
    from azuresky import hub  # noqa: PLC0415
    try:
        return hub.core()
    except FileNotFoundError as e:
        st.error("⚙️ **Data source not connected**")
        st.caption(str(e))
        st.stop()
