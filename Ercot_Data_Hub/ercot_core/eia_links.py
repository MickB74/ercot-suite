"""Canonical EIA deep-link builder.

One place both the Data Hub and the standalone Grid Monitor format links to a
plant's page in EIA's Electricity Data Browser. Change the URL here and every
app picks it up. Dependency-free on purpose so light callers can import it.
"""

from __future__ import annotations

EIA_PLANT_URL = "https://www.eia.gov/electricity/data/browser/#/plant/{id}"


def eia_plant_url(plant_id) -> str:
    """EIA Electricity Data Browser link for an EIA plant id, or '' if none."""
    return EIA_PLANT_URL.format(id=plant_id) if plant_id else ""
