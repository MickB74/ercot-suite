"""Generation Analysis — production patterns, capacity factor, and trends."""

from __future__ import annotations

import _boot  # noqa: F401
import streamlit as st

_boot.ensure_hub(st)

from azuresky import analytics, branding, contract, hub  # noqa: E402
from ercot_core import generation_analysis  # noqa: E402
from ercot_core import settle_ui  # noqa: E402

terms = contract.load_contract()
a = contract.ASSET
loc = contract.settle_location(terms)
terms, loc = settle_ui.choose(st, contract, terms)

win_start, win_end = hub.settlement_window(a["resource_node"], loc)
if win_start is None:
    st.info("No data available yet for this asset.")
    st.stop()

generation_analysis.render(
    st,
    a=a,
    hub=hub,
    analytics=analytics,
    branding=branding,
    contract=contract,
    terms=terms,
    win_start=win_start,
    win_end=win_end,
)
