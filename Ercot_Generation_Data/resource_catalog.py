"""Searchable catalog of ERCOT resource nodes.

A *resource node* is a settlement point tied to one or more generating units.
ERCOT's Resource Node-to-Unit mapping (gridstatus `get_resource_node_to_unit`)
links each node to its units:

    Resource Node      Unit Substation   Unit Name
    7RNCHSLR_ALL       7RNCHSLR          UNIT1 / UNIT2 / UNIT3

The 60-day SCED disclosure names each generator `{Unit Substation}_{Unit Name}`
(e.g. `7RNCHSLR_UNIT1`). So we derive `sced_resource_name` for every unit, which
is the join key to pull a node's generation. The node name itself is the
settlement point used to pull the node's price.

Build once, then search offline:

    python resource_catalog.py --build
    python resource_catalog.py LOS              # name substring search
    python resource_catalog.py --type WIND      # by resource type (needs --build --with-types)
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

CATALOG_PATH = "resource_node_catalog.parquet"


def _sced_name(substation: str, unit: str) -> str:
    return f"{str(substation).strip()}_{str(unit).strip()}"


def build_catalog(date: str | None = None, with_types: bool = False) -> pd.DataFrame:
    """Build the resource-node catalog from ERCOT's node->unit mapping.

    `with_types` enriches each unit with its SCED `Resource Type` by pulling one
    SCED disclosure day (>60 days old; a heavier download).
    """
    import gridstatus

    iso = gridstatus.Ercot()
    map_date = date or (pd.Timestamp.now(tz="US/Central") - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    print(f"[catalog] fetching resource-node-to-unit mapping ({map_date})...")
    m = iso.get_resource_node_to_unit(date=map_date)
    m.columns = [c.strip() for c in m.columns]

    cat = m.rename(columns={
        "Resource Node": "resource_node",
        "Unit Substation": "unit_substation",
        "Unit Name": "unit_name",
    })[["resource_node", "unit_substation", "unit_name"]].copy()
    cat["sced_resource_name"] = [
        _sced_name(s, u) for s, u in zip(cat["unit_substation"], cat["unit_name"])
    ]
    cat["resource_type"] = pd.NA

    if with_types:
        sced_day = (pd.Timestamp.now(tz="US/Central") - pd.Timedelta(days=65)).strftime("%Y-%m-%d")
        print(f"[catalog] enriching with SCED resource types ({sced_day}, heavy)...")
        try:
            data = iso.get_60_day_sced_disclosure(date=sced_day)
            gen = data["sced_gen_resource"]
            gen.columns = [c.strip() for c in gen.columns]
            types = (gen[["Resource Name", "Resource Type"]]
                     .drop_duplicates("Resource Name")
                     .set_index("Resource Name")["Resource Type"])
            cat["resource_type"] = cat["sced_resource_name"].map(types).astype("object")
        except Exception as e:
            print(f"[catalog] type enrichment failed (kept blank): {e}")

    cat = cat.sort_values(["resource_node", "unit_name"]).reset_index(drop=True)
    cat.to_parquet(CATALOG_PATH, index=False)
    print(f"[catalog] saved {len(cat):,} unit rows "
          f"({cat['resource_node'].nunique():,} nodes) -> {CATALOG_PATH}")
    return cat


def load_catalog() -> pd.DataFrame:
    if not os.path.exists(CATALOG_PATH):
        raise FileNotFoundError(
            f"{CATALOG_PATH} not found — build it first: python resource_catalog.py --build"
        )
    return pd.read_parquet(CATALOG_PATH)


def search(query: str | None = None, rtype: str | None = None) -> pd.DataFrame:
    """Search the catalog by name substring and/or resource type.

    Matches `query` (case-insensitive) against node / substation / unit / sced name.
    """
    cat = load_catalog()
    df = cat
    if query:
        q = query.upper()
        mask = (
            df["resource_node"].str.upper().str.contains(q, na=False)
            | df["unit_substation"].str.upper().str.contains(q, na=False)
            | df["unit_name"].str.upper().str.contains(q, na=False)
            | df["sced_resource_name"].str.upper().str.contains(q, na=False)
        )
        df = df[mask]
    if rtype:
        df = df[df["resource_type"].astype(str).str.upper() == rtype.upper()]
    return df.reset_index(drop=True)


def nodes_for(query: str | None = None, rtype: str | None = None) -> list[str]:
    """Distinct resource-node names matching a search (for the pullers)."""
    return sorted(search(query, rtype)["resource_node"].unique().tolist())


def sced_names_for(resource_node: str) -> list[str]:
    """The SCED resource names (units) that make up a resource node."""
    cat = load_catalog()
    return sorted(
        cat.loc[cat["resource_node"] == resource_node, "sced_resource_name"].unique().tolist()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="name substring to search for")
    ap.add_argument("--build", action="store_true", help="(re)build the catalog from ERCOT")
    ap.add_argument("--with-types", action="store_true", help="enrich with SCED resource types (heavy)")
    ap.add_argument("--type", dest="rtype", help="filter by resource type, e.g. WIND/PVGR/PWRSTR")
    ap.add_argument("--nodes-only", action="store_true", help="print just distinct node names")
    args = ap.parse_args()

    if args.build:
        build_catalog(with_types=args.with_types)
        if not args.query and not args.rtype:
            return 0

    try:
        res = search(args.query, args.rtype)
    except FileNotFoundError as e:
        print(e)
        return 1

    if res.empty:
        print("No matches.")
        return 0

    if args.nodes_only:
        for n in sorted(res["resource_node"].unique()):
            print(n)
    else:
        print(f"{len(res)} unit rows | {res['resource_node'].nunique()} nodes\n")
        with pd.option_context("display.max_rows", 60, "display.width", 120):
            print(res.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
