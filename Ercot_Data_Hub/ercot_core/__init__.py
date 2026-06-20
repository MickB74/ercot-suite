"""ercot_core — shared library for the ERCOT Data Hub monorepo.

One place for the things the four datasets used to each reinvent:
  - paths          unified data lake layout (data/<dataset>/...)
  - credentials    one ERCOT Public API credential store (config.json + env)
  - gridstatus_client  one shared gridstatus.Ercot() instance, quieted
  - settlement_points  the canonical hub / zone / location-type lists
  - fuels          the canonical fuel taxonomy + provenance merge engine,
                   plus the EIA-923 and SCED fuel-code crosswalks
  - sced_disclosure  ONE 60-day SCED disclosure download + shared daily cache
                   (previously downloaded twice — by plant_sced and system_gen)
  - plant_names    resource-code -> human plant-name crosswalk
  - tz             canonical US/Central timezone helpers (DST-correct), the one
                   place naive-Central <-> tz-aware <-> UTC conversion lives
  - project_lookup interconnection queue / name -> resource node, curated registry
  - ifyi           interconnection.fyi client (queue id -> canonical name + dates)
  - queue_search   search / analyze the merged queue + per-project DD dossier
  - tx_filings     Texas county/state filing deep-links + DD checklists
"""

from __future__ import annotations

__all__ = [
    "paths",
    "credentials",
    "gridstatus_client",
    "settlement_points",
    "fuels",
    "sced_disclosure",
    "plant_names",
    "tz",
    "project_lookup",
    "ifyi",
    "queue_search",
    "tx_filings",
    "weather_forecast",
    "gen_forecast",
    "near_term_bill",
]
