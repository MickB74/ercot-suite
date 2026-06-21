"""Shared sidebar control to flip a settlement page between node and hub.

Every portal resolves its settlement reference through ``contract.settle_location``
(which honors a ``settle_point`` override in the contract terms). This helper
renders a small sidebar radio that lets the viewer switch the reference between
the plant's resource node and its trading hub **for the current page only** — it
overrides ``terms`` in memory and never touches the saved ``config.json``.

Usage in a page (after ``terms`` and ``contract`` are available)::

    from ercot_core import settle_ui
    terms, loc = settle_ui.choose(st, contract, terms)

The returned ``terms`` carries the chosen ``settle_point`` so it flows through
``analytics.settle`` / ``contract.settle_location`` and the price forecast
unchanged; ``loc`` is the resolved location string.
"""

from __future__ import annotations


def _label(loc: str, node: str) -> str:
    if loc == node:
        return f"📍 Plant node · {loc}"
    if str(loc).upper().startswith("HB_"):
        return f"🔌 Trading hub · {loc}"
    return str(loc)


def choose(st, contract, terms: dict, *, key: str = "settle_ref"):
    """Render the Node/Hub settlement toggle; return ``(terms, location)``.

    ``terms`` is returned unchanged when there's only one sensible reference or
    the user keeps the contract default; otherwise a shallow copy with
    ``settle_point`` set to the chosen location is returned.
    """
    a = contract.ASSET
    # The *priced* node — Azure's aggregate resource id has no price of its own,
    # so it exposes ``price_node`` (AZURE_RN); everyone else prices at the node.
    node = a.get("price_node") or a.get("resource_node")
    hub_loc = a.get("hub")
    current = contract.settle_location(terms)

    # Ordered, de-duped options with the contract's current reference first.
    options: list[str] = []
    for loc in (current, node, hub_loc):
        if loc and loc not in options:
            options.append(loc)
    if len(options) < 2:
        return terms, current

    st.sidebar.header("Settlement reference")
    pick = st.sidebar.radio(
        "Settle at",
        options,
        index=0,
        format_func=lambda loc: _label(loc, node),
        key=key,
        help="Flip this page between the plant-node price and the trading-hub "
             "price. View-only — your saved contract isn't changed. Basis (node "
             "minus hub) is what differs between the two.",
    )
    if pick != current:
        terms = {**terms, "settle_point": pick}
    return terms, pick
