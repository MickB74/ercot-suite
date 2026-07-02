#!/usr/bin/env python
"""Forecast scorecard — per-asset expected-vs-actual generation over time.

The near-term forecast LEVEL is grounded on each asset's EIA-923 P50 capacity
factor × the anchor's own nameplate (capacity_full_mw). This scores that
grounding against actual SCED metered generation for every month we have both,
per asset, and trends the bias — so a systematic level error (the DC/AC-nameplate
class of bug) shows up as a number instead of needing someone to eyeball a chart.

Also runs pre-flight PLAUSIBILITY GUARDS that would have caught the bugs we hit:
  * grounding-nameplate mismatch (registry capacity_mw vs anchor capacity_full_mw)
  * non-physical solar capacity factor
  * thin anchor (degenerate P10==P90 → no real envelope yet)

Reads only stored data (anchors + node_generation lake), so it can run on a
schedule after each data refresh. Writes data/scorecard/forecast_scorecard.csv.
"""
import warnings; warnings.filterwarnings("ignore")
import json, subprocess, sys, calendar
from pathlib import Path
import pandas as pd

SUITE = Path(__file__).resolve().parent          # …/ercot-suite
HUB = SUITE / "Ercot_Data_Hub"
PY = str(HUB / ".venv/bin/python")
GENDIR = HUB / "data/system_gen/node_data"
ANCHDIR = HUB / "data/eia_anchor"
OUTDIR = HUB / "data/scorecard"; OUTDIR.mkdir(exist_ok=True)

# (portal dir, package) — package is the dir holding contract.py
PORTALS = [
    ("ERCOT_Markum", "markum"), ("ERCOT_Azure_Sky", "azuresky"),
    ("ERCOT_Hidalgo_Mirasole_Wind", "portal"), ("ERCOT_Hornet_Solar", "portal"),
    ("ERCOT_Miller", "portal"), ("ERCOT_Millers_Branch_2", "portal"),
    ("ERCOT_Mesquite_Star", "portal"), ("ERCOT_Stafford_Solar", "portal"),
    ("ERCOT_Heart_of_Texas", "hotwind"), ("ERCOT_Aguayo_Wind", "portal"),
]

# tech-specific non-physical monthly capacity-factor ceilings (AC-nameplate basis)
CF_CEIL = {"solar": 0.42, "wind": 0.65}


def portal_asset(d, pkg):
    """Import the portal's own contract in a clean subprocess → node/units/etc."""
    code = (f"import sys; sys.path.insert(0,'.'); sys.path.insert(0,r'{HUB}');"
            f"from {pkg} import contract as c; import json;"
            "a=c.ASSET; t=c.load_contract();"
            "print(json.dumps({'node':a.get('resource_node'),"
            "'units':a.get('sced_units'),'cap_reg':a.get('capacity_mw'),"
            "'tech':a.get('tech'),'name':a.get('project_name'),"
            "'share':float(t.get('volume_share_pct',100.0))/100.0}))")
    try:
        out = subprocess.run([PY, "-c", code], cwd=str(SUITE / d),
                             capture_output=True, text=True, timeout=120)
        line = [l for l in out.stdout.splitlines() if l.startswith("{")]
        return json.loads(line[-1]) if line else None
    except Exception as e:  # noqa: BLE001
        print(f"  ! {d}: asset load failed ({str(e)[:60]})"); return None


def actual_monthly(node, units, share):
    """Actual SCED metered MWh per month (YYYY-MM) at the contract's units × share."""
    frames = []
    for yr in (2024, 2025, 2026):
        f = GENDIR / f"node_generation_{yr}.parquet"
        if not f.exists():
            continue
        g = pd.read_parquet(f)
        g = g[g["resource_node"] == node]
        if units and "resource_name" in g.columns:
            gu = g[g["resource_name"].isin(units)]
            if not gu.empty:
                g = gu
        if not g.empty:
            frames.append(g)
    if not frames:
        return pd.Series(dtype=float)
    g = pd.concat(frames)
    hrs = (pd.to_datetime(g["interval_end"]) - pd.to_datetime(g["interval_start"])
           ).dt.total_seconds() / 3600.0
    g = g.assign(mwh=g.get("mwh", g["mw"] * hrs),
                 Month=pd.to_datetime(g["interval_start"]).dt.to_period("M").astype(str))
    return g.groupby("Month")["mwh"].sum() * share


rows, summaries = [], []
for d, pkg in PORTALS:
    a = portal_asset(d, pkg)
    if not a or not a.get("node"):
        continue
    node, name, tech = a["node"], a.get("name") or a["node"], (a.get("tech") or "").lower()
    tech = "wind" if "wind" in tech else "solar"
    share = a["share"]; cap_reg = float(a.get("cap_reg") or 0)
    apath = ANCHDIR / f"{node}.json"
    if not apath.exists():
        summaries.append({"asset": name, "node": node, "flags": "no EIA anchor"})
        continue
    anc = json.loads(apath.read_text())
    cap_full = float(anc.get("capacity_full_mw") or 0)
    cf50 = {int(k): float(v) for k, v in (anc.get("monthly_cf_p50") or {}).items()}
    p10, p90 = anc.get("monthly_cf_p10") or {}, anc.get("monthly_cf_p90") or {}
    if not cap_full or not cf50:
        summaries.append({"asset": name, "node": node, "flags": "anchor missing cap/cf"})
        continue
    actual = actual_monthly(node, a.get("units"), share)

    # ---- score expected (fixed grounding) vs actual, per month ----
    biases = []
    for mo, act in actual.items():
        if act <= 0:
            continue
        m = int(mo[5:7]); days = calendar.monthrange(int(mo[:4]), m)[1]
        if m not in cf50:
            continue
        exp = cf50[m] * cap_full * share * days * 24.0
        bias = (exp - act) / act * 100.0
        rows.append({"asset": name, "node": node, "tech": tech, "month": mo,
                     "expected_mwh": round(exp), "actual_mwh": round(act),
                     "bias_pct": round(bias, 1),
                     "cf_actual": round(act / (cap_full * share * days * 24.0), 3)})
        biases.append(bias)
    med = pd.Series(biases).median() if biases else float("nan")
    mape = pd.Series(biases).abs().mean() if biases else float("nan")

    # ---- guards ----
    # The scorecard's PRIMARY accuracy signal is median bias vs actuals. Guards
    # add data-hygiene checks that matter most where we can't yet validate.
    flags = []
    ratio = cap_reg / cap_full if cap_full else float("nan")
    if cap_reg and (ratio > 1.10 or ratio < 0.90):
        # DC-vs-AC nameplate labeling gap. Grounding now uses the anchor (AC) cap,
        # so this is only a risk where we have too little history to confirm it.
        note = (f"validated by {len(biases)}-mo bias {med:+.0f}%"
                if len(biases) >= 6 else "UNVALIDATED — verify grounding")
        flags.append(f"registry/anchor nameplate ratio {ratio:.2f} (DC vs AC; {note})")
    if p10 and p90 and p10 == p90:
        flags.append("thin anchor (P10==P90, <2yr — rebuild when more data lands)")
    # physical CF check against the DC nameplate (larger of reg/anchor)
    dc_cap = max(cap_reg, cap_full) if cap_reg else cap_full
    peak_cf_dc = max(cf50.values()) * cap_full / dc_cap if dc_cap else float("nan")
    if peak_cf_dc > CF_CEIL[tech]:
        flags.append(f"peak CF {peak_cf_dc:.0%} vs DC nameplate > {CF_CEIL[tech]:.0%} ({tech} non-physical?)")

    summaries.append({"asset": name, "node": node, "tech": tech, "n_months": len(biases),
                      "median_bias_%": round(med, 1) if biases else None,
                      "mape_%": round(mape, 1) if biases else None,
                      "cap_reg": cap_reg, "cap_anchor": round(cap_full, 1),
                      "flags": "; ".join(flags) or "ok"})

sc = pd.DataFrame(rows)
sm = pd.DataFrame(summaries)
sc.to_csv(OUTDIR / "forecast_scorecard.csv", index=False)
sm.to_csv(OUTDIR / "forecast_scorecard_summary.csv", index=False)

pd.set_option("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 60)
print("\n================= FORECAST SCORECARD (grounding accuracy) =================")
print(sm.to_string(index=False))
print(f"\nrows written: {len(sc)}  ->  {OUTDIR}/forecast_scorecard.csv")
flagged = sm[(sm["flags"] != "ok") & (sm["flags"].notna())]
print(f"\n⚠️  {len(flagged)} asset(s) flagged by guards:")
for _, r in flagged.iterrows():
    print(f"   • {r['asset']}: {r['flags']}")
bad = sm[(sm["median_bias_%"].abs() > 10) if "median_bias_%" in sm else []]
if len(bad):
    print(f"\n📉 {len(bad)} asset(s) with systematic level bias >10% (next to investigate):")
    for _, r in bad.iterrows():
        print(f"   • {r['asset']}: median bias {r['median_bias_%']:+.0f}% over {r['n_months']} months")
