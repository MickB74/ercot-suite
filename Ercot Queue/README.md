# Ercot Queue — interconnection-queue search, analytics & due diligence

A robust way to **search and analyze the ERCOT interconnection queue**, pull
**links and information** for any project, and assemble a **due-diligence
dossier** — including the Texas **county/state filing** pointers you need to run
diligence on a renewable or storage project.

Three front-ends, **one engine** — a CLI, a standalone Streamlit app, and an
embedded Data Hub page all sit on the same `ercot_core` modules:

| Front-end | File | Launch |
| --- | --- | --- |
| Standalone app | `app.py` | double-click **`Open ERCOT Queue.command`** |
| Data Hub page | `Ercot_Data_Hub/app/screens/20_Queue_Explorer.py` | Build a Project → Queue Explorer |
| CLI | `ercot_queue.py` | `python ercot_queue.py …` |

| Module | Role |
| --- | --- |
| `ercot_core/queue_search.py` | merge the two queue sources, search, rollup analytics, per-project dossier |
| `ercot_core/tx_filings.py` | Texas county/state filing deep-links + tech-aware DD checklists |
| `queue_page.py` | the Streamlit UI (`render()`) shared by `app.py` and the Data Hub page |

### Streamlit app

Double-click **`Open ERCOT Queue.command`** (macOS), or run it manually:

```bash
cd "Ercot Queue"
../Ercot_Data_Hub/.venv/bin/streamlit run app.py
```

The app **reuses the Data Hub's virtual environment, engine, and data lake** — no
duplicate install, and the queue always matches the Hub. (First run sets up the
Hub venv automatically if it doesn't exist yet.) Two tabs: **Search & Analyze**
(filters + rollup table/chart + export) and **Project Dossier** (info, links,
crosswalk, registry match, DD checklist).

## What it knows

Two complementary sources the suite already caches in the data lake (no new
credentials needed):

- **ERCOT GIS report** — the authoritative *current* snapshot (~1.8k rows): clean
  Fuel / Technology / Capacity / Interconnecting Entity / POI, Status ∈ {Active,
  Completed}. Drops long-operational projects.
- **interconnection.fyi** — a superset (~3.3k) that keeps Withdrawn / Suspended /
  Operational projects and adds queue & completion **dates** and a canonical
  **URL** per project.

`unified_queue()` merges them on a normalized Queue ID — GIS wins for the clean
fuel/technology/capacity fields; interconnection.fyi supplies the dates, URL, and
richer lifecycle status. **Cache-first and offline by default.**

## Usage

```bash
cd "Ercot Queue"
PY=../Ercot_Data_Hub/.venv/bin/python   # any env with pandas + the hub on the path

# ── search ───────────────────────────────────────────────────────────────
$PY ercot_queue.py search "azure sky"
$PY ercot_queue.py search --county Pecos --fuel Solar --status Active --limit 10
$PY ercot_queue.py search --min-mw 200 --technology Battery --in-gis
$PY ercot_queue.py search "wind" --county Haskell --json

# ── analyze (rollups: projects, total MW, median MW) ──────────────────────
$PY ercot_queue.py stats --by county --fuel Solar --status Active
$PY ercot_queue.py stats --by fuel  --status Active
$PY ercot_queue.py stats --by entity --county Pecos

# ── one project: links + info + due diligence ─────────────────────────────
$PY ercot_queue.py dossier 21INR0477
$PY ercot_queue.py dossier "Azure Sky Solar"
$PY ercot_queue.py links "Markham Solar" --county Bosque   # just the filing links
```

Add `--json` to any command for machine-readable output, and `--fetch` to let the
GIS source download if its cached parquet is missing.

### Search filters

`--county · --fuel · --technology · --status · --entity · --min-mw · --max-mw ·
--in-gis` (only projects in the current GIS queue) · `--sort <col> · --asc ·
--limit N`. Free-text (positional) matches across name, entity, county, queue id,
and POI. All filters are case-insensitive substrings and AND together.

## The dossier (`dossier <queue-id | name>`)

Assembles everything for one project:

1. **Queue record** — fuel/tech, capacity, lifecycle status (flags projects not
   in the current GIS queue), county, entity, POI, queue/COD dates, project URL.
   Technology is **inferred from the name** when the source columns are blank, so
   the checklist and tech-specific links still apply.
2. **Curated-registry match** — if the project is one of the suite's analyzable
   assets, surfaces the resource name so you can jump to Plant Value / settlement.
3. **Resource-node crosswalk** — candidate ERCOT resource node(s) + how much
   price/gen/SCED data is already cached for them (via `ercot_core.project_lookup`).
4. **Filing / due-diligence links** — authoritative Texas sources (below).
5. **DD checklist** — base items + wind/solar/storage-specific items.

## County & state filings (`ercot_core/tx_filings.py`)

There is **no single API** for "county filings." Diligence in Texas is spread
across several public systems, each searchable by project name / developer entity
/ county. The tool emits a labeled set of **deep-links** (`↗` direct URL where the
portal is stable, `🔎` a pre-filled search otherwise):

| Source | What you find |
| --- | --- |
| **PUC Interchange** | CCN, transmission & interconnection dockets |
| **Comptroller Ch. 313 / Ch. 403 JETI** | school-tax limitation agreements (the headline state incentive) |
| **County Appraisal District (CAD)** | parcel ownership, valuations, Ch. 312 abatements |
| **County Clerk** | recorded leases/easements, road-use & decommissioning agreements, liens |
| **Commissioners Court** | county Ch. 312 abatements, road-use agreements |
| **FAA OE/AAA** | obstruction determinations (wind — a hard gate) |
| **USFWS IPaC** | protected-species / habitat screen |
| **TCEQ** | air, construction stormwater, water rights |
| **Comptroller / SOSDirect** | developer entity standing, ownership chain, registered agent |

~30 of the highest-activity ERCOT counties have curated CAD URLs; every other
county falls back to a reliable search link (a wrong specific URL is worse than a
search). The checklist adds **FAA + avian/noise** for wind, **glare +
decommissioning** for solar, and **NFPA 855 fire-code + SARA Tier II** for
storage.

## Notes & limits

- Refresh the interconnection.fyi superset with
  `python ../Ercot_Data_Hub/ercot_core/ifyi.py` (polite, throttled, resumable).
  The GIS snapshot refreshes via `--fetch` (or `project_lookup.load_full_queue(refresh=True)`).
- The filing links are **navigational aids**, not a filings feed — they take you
  to the right portal pre-scoped; the actual document search happens there.
- Curated-registry / resource-node matching is name-token based; treat
  cross-tech or fuzzy matches as leads to confirm, not facts.
