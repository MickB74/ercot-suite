"""Heart of Texas Wind settlement portal — a focused, customer-facing view of one asset.

This package is a thin layer over the shared ERCOT engine that lives in the
sibling ``Ercot_Data_Hub`` repo (``ercot_core``). It does **no** market math of
its own — it reuses :mod:`ercot_core.settlement` and :mod:`ercot_core.invoice`
verbatim so the numbers a customer sees here are identical to the ones produced
in the internal Data Hub. All this layer adds is:

  * :mod:`hotwind.hub`       — locate the Hub, put ``ercot_core`` on the path,
                              and read Heart of Texas Wind's cached generation / price data.
  * :mod:`hotwind.contract`  — the one asset + contract definition (strike, etc.)
  * :mod:`hotwind.branding`  — shared look-and-feel for the customer pages.
"""

from __future__ import annotations

__all__ = ["hub", "contract", "branding"]
