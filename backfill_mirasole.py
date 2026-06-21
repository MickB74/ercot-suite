"""Backfill Hidalgo Los Mirasoles (MIRASOLE_GEN) history to 2020.

Prices: ERCOT API archive (RTM SPP at the node) — available back to 2020.
Generation: 60-Day SCED Disclosure — only retained ~2024+, so we grab what exists.
Both merge into the Hub's yearly node_* parquets (idempotent on the key).

Run (Hub venv), backgrounded:
  python backfill_mirasole.py
"""
import sys, datetime as dt
from pathlib import Path
import pandas as pd

HUB = Path(__file__).parent / "Ercot_Data_Hub"
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB / "datasets" / "system_gen_by_fuel"))
sys.path.insert(0, str(HUB / "datasets" / "hub_prices"))

from ercot_core import spp_archive, paths
import pull_nodes as pn
import node_generation as ng

NODE = "MIRASOLE_GEN"
UNITS = ["MIRASOLE_MIR11", "MIRASOLE_MIR12", "MIRASOLE_MIR13", "MIRASOLE_MIR21"]

def log(m): print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)

# ── PRICES: year by year, 2020 → 2025-04-19 (we already have 2025-04-20+) ────
PRICE_RANGES = [
    ("2020-01-01", "2020-12-31"), ("2021-01-01", "2021-12-31"),
    ("2022-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-04-19"),
]
log("=== PRICE backfill MIRASOLE_GEN ===")
for s, e in PRICE_RANGES:
    try:
        log(f"prices {s} → {e} …")
        df = spp_archive.fetch_rtm_spp([NODE], s, e, location_type="Resource Node", log=lambda *_: None)
        if df is None or df.empty:
            log(f"  {s[:4]}: no rows"); continue
        pn._merge_save(df, "node_price_{year}.parquet", pn.PRICE_KEY)
        log(f"  {s[:4]}: merged {len(df):,} rows ({df['interval_start'].min()} → {df['interval_start'].max()})")
    except Exception as ex:
        log(f"  {s[:4]}: ERROR {str(ex)[:160]}")

# ── GENERATION: as far back as SCED disclosure allows (≈2024+) ───────────────
# Probe earliest available month, then pull forward to where we already have data.
log("=== GEN backfill (SCED disclosure, retained ~2024+) ===")
GEN_RANGES = [("2024-01-01", "2024-12-31")]  # 2025+ already cached; 2020-23 not retained
for s, e in GEN_RANGES:
    try:
        log(f"gen {s} → {e} …")
        g = ng.fetch_generation([NODE], dt.date.fromisoformat(s), dt.date.fromisoformat(e))
        if g is None or g.empty:
            log(f"  {s[:4]}: no rows (not retained)"); continue
        pn._merge_save(g, "node_generation_{year}.parquet", pn.GEN_KEY)
        log(f"  {s[:4]}: merged {len(g):,} rows ({g['interval_start'].min()} → {g['interval_start'].max()})")
    except Exception as ex:
        log(f"  {s[:4]}: ERROR {str(ex)[:160]}")

# ── Final coverage report ────────────────────────────────────────────────────
def span(tmpl, col):
    lo = hi = None
    for y in range(2020, 2027):
        p = paths.NODE_DATA_DIR / tmpl.format(year=y)
        if not p.exists(): continue
        d = pd.read_parquet(p, columns=["interval_start", col]); d = d[d[col] == NODE]
        if d.empty: continue
        mn, mx = d["interval_start"].min(), d["interval_start"].max()
        lo = mn if lo is None else min(lo, mn); hi = mx if hi is None else max(hi, mx)
    return lo, hi

plo, phi = span("node_price_{year}.parquet", "location")
glo, ghi = span("node_generation_{year}.parquet", "resource_node")
log(f"DONE. PRICE {plo} → {phi} | GEN {glo} → {ghi}")
