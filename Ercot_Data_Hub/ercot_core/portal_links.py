"""Cross-app links from the single-asset portals back to the shared Data Hub.

A portal is its own Streamlit app on its own port, so it can't ``st.page_link``
into the Hub — it needs a URL. The Hub URL is configurable via ``ERCOT_HUB_URL``
(default ``http://localhost:8501``, the Hub's default local port) so a deployed
setup can point it at the real host. Centralised here so all portals share one
link and one URL convention; the Hub's own pages link internally instead.
"""

from __future__ import annotations

import os

# Stable deep-link path — must match the ``url_path`` set on the Price Coverage
# st.Page in app/Home.py.
COVERAGE_PATH = "price-coverage"


def hub_url(path: str = "") -> str:
    base = (os.environ.get("ERCOT_HUB_URL") or "http://localhost:8501").rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base


def hub_coverage_link(st) -> None:
    """Render a link to the Data Hub's Price Coverage page (price inventory)."""
    st.link_button(
        "📊 See exactly what price data is cached ↗",
        hub_url(COVERAGE_PATH),
        help="Opens the shared ERCOT Data Hub's Price Coverage page — every hub & "
             "resource node, with its date span, row count and freshness.")
    st.caption("Opens the shared ERCOT Data Hub (running locally).")
