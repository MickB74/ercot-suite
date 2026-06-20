"""Ownership / offtaker overlay for the ERCOT interconnection queue.

The queue itself only carries the *interconnecting entity* — the project LLC
(e.g. "Azure Sky Wind LLC"). It says nothing about **who owns that LLC** (the
developer / sponsor / parent) or **whether an offtake deal (VPPA/PPA) has been
announced**. Neither fact exists in any cached source (GIS, interconnection.fyi,
or the curated asset registry).

This module is a small, hand-curated overlay that supplies exactly those two
facts, keyed by normalized Queue ID, and left-joins them onto the unified queue
so the CLI, the Streamlit page, and the dossier all surface them. Every value
carries a free-text **source** (a URL or short note) and an ``updated`` date so
nothing is taken on faith — blanks mean "not researched / nothing found", never
a guess.

File: ``ercot_core/registry/queue_ownership.json`` — a dict::

    {
      "21INR0477": {
        "project_name": "Azure Sky Wind",
        "owners": "Acme Renewables (parent), Foo Capital (tax equity)",
        "owner_source": "https://… press release, 2024-03",
        "vppa": "250 MW VPPA with BigCo (announced 2024-06)",
        "vppa_source": "https://…",
        "updated": "2026-06-20"
      },
      ...
    }

Records that could not be tied to a Queue ID are stored under a ``"NAME:<slug>"``
key; ``annotate`` still matches them to the queue by normalized project name.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from ercot_core import paths

# Columns this overlay contributes to the unified queue.
COLUMNS = ["owners", "owner_source", "vppa", "vppa_source", "ownership_updated"]

_OVERLAY = paths.QUEUE_OWNERSHIP_JSON


def _norm_id(s) -> str:
    return re.sub(r"^ERCOT[-_ ]", "", str(s).strip().upper())


def _norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def name_key(name: str) -> str:
    """The ``NAME:<slug>`` key used for records with no resolvable Queue ID."""
    return "NAME:" + _norm_name(name)


def load() -> dict:
    """The overlay as a ``{key: record}`` dict (``{}`` if the file is absent)."""
    if not _OVERLAY.exists():
        return {}
    try:
        return json.loads(_OVERLAY.read_text())
    except Exception:  # noqa: BLE001 — a corrupt overlay must not break the queue
        return {}


def save(data: dict) -> str:
    """Write the whole overlay back to disk (sorted for stable diffs)."""
    _OVERLAY.parent.mkdir(parents=True, exist_ok=True)
    _OVERLAY.write_text(json.dumps(dict(sorted(data.items())), indent=2))
    return str(_OVERLAY)


def get(queue_id: str | None = None, project_name: str | None = None) -> dict:
    """Look up one record by Queue ID first, then by project-name slug."""
    data = load()
    if queue_id:
        rec = data.get(_norm_id(queue_id))
        if rec:
            return rec
    if project_name:
        return data.get(name_key(project_name), {})
    return {}


def upsert(record: dict, *, queue_id: str | None = None,
           project_name: str | None = None, updated: str | None = None) -> str:
    """Insert/merge one ownership record. Keyed by Queue ID when given, else by
    project-name slug. Empty incoming fields don't clobber existing values.

    ``updated`` should be an ISO date string supplied by the caller (the engine
    has no clock of its own); it is stored verbatim as ``ownership_updated``.
    """
    if not queue_id and not project_name:
        raise ValueError("upsert needs a queue_id or a project_name")
    data = load()
    key = _norm_id(queue_id) if queue_id else name_key(project_name)
    clean = {k: v for k, v in record.items() if str(v).strip() not in ("", "None", "nan")}
    if project_name:
        clean.setdefault("project_name", project_name)
    if updated:
        clean["ownership_updated"] = updated
    data[key] = {**data.get(key, {}), **clean}
    return save(data)


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Left-join the overlay's owner/VPPA fields onto a unified-queue frame.

    Matches on normalized Queue ID first; any still-unmatched rows are filled by
    normalized project name (covering ``NAME:`` records). The output always has
    every :data:`COLUMNS` column, populated with ``None`` where nothing is known.
    """
    out = df.copy()
    for c in COLUMNS:
        out[c] = None
    data = load()
    if out.empty or not data:
        return out

    by_id, by_name = {}, {}
    for key, rec in data.items():
        slim = {c: rec.get(c) for c in COLUMNS if rec.get(c)}
        if not slim:
            continue
        if key.startswith("NAME:"):
            by_name[key[len("NAME:"):]] = slim
        else:
            by_id[key] = slim
            if rec.get("project_name"):  # also index by name as a softer fallback
                by_name.setdefault(_norm_name(rec["project_name"]), slim)

    ids = out["queue_id"].map(_norm_id)
    names = out["project_name"].map(_norm_name)
    for i, (qid, nm) in enumerate(zip(ids, names)):
        rec = by_id.get(qid) or by_name.get(nm)
        if not rec:
            continue
        for c, v in rec.items():
            out.iat[i, out.columns.get_loc(c)] = v
    return out
