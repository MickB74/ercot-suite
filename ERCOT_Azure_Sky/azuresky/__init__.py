"""Azure Sky Wind settlement portal — a focused, customer-facing view of one asset.

This package is a thin layer over the shared ERCOT engine and data lake that
live in the sibling ``Ercot_Data_Hub`` repo (``ercot_core``). It does **no**
market math of its own — it reuses :func:`ercot_core.settlement.compute_settlement`
verbatim, so the numbers a customer sees here are identical to the ones produced
in the internal Data Hub. All this layer adds is:

  * :mod:`azuresky.hub`       — locate the Hub, put ``ercot_core`` on the path,
                                read the project's cached HB_NORTH hub prices and
                                aggregate the four VORTEX SCED units into the
                                15-minute generation the engine expects.
  * :mod:`azuresky.contract`  — the one asset + contract definition (strike, etc.)
  * :mod:`azuresky.analytics` — glue that runs the shared engine over that data.
  * :mod:`azuresky.branding`  — shared look-and-feel for the customer pages.

The Azure Sky aggregate (``AZURE_SKY_WIND_AGG``) has no single resource-node
generation series — it is four ERCOT units (``VORTEX_WIND1..4``). It also settles
at its **trading hub** (HB_NORTH), not a resource node. Both facts are handled in
:mod:`azuresky.hub` / :mod:`azuresky.analytics`; the rest of the engine is shared.
"""

from __future__ import annotations

__all__ = ["hub", "contract", "analytics", "branding"]
