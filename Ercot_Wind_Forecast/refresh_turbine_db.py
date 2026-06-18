"""Refresh the bundled USWTDB turbine database from the public USGS API.

  python3 refresh_turbine_db.py            # whole U.S.  -> reference/uswtdb_us.json
  python3 refresh_turbine_db.py TX         # one state   -> reference/uswtdb_tx.json

The engine prefers the national file when present, else the bundled TX extract.
"""

import sys

import turbine_db as tdb

if __name__ == "__main__":
    state = sys.argv[1].upper() if len(sys.argv) > 1 else None
    print(f"Downloading USWTDB ({state or 'national'}) from USGS…")
    path = tdb.refresh_national(state=state)
    recs = tdb._load_records(path)
    print(f"Wrote {len(recs):,} turbine records → {path}")
