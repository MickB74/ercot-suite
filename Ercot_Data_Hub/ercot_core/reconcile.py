"""Reconcile ERCOT SCED telemetry against EIA-923 metered net generation.

Two different measurements of the same plant's output:
  * SCED  — 5-min dispatch *telemetry* (Telemetered Net Output), ERCOT, ~60-day lag
  * EIA-923 — monthly *revenue-meter* net generation, ~6-month lag

They legitimately differ (telemetry gaps, station service / auxiliary loads,
curtailment, unit-vs-plant boundaries, sampling). This module lines them up by
month so you can see where — and how much — "SCED is off".

The crux is the ERCOT-resource ↔ EIA-plant crosswalk (no official free map). We
match the SCED plant-name crosswalk against EIA plant names by shared
significant tokens, filtered to the same fuel, and let you override per plant.
SCED energy is integrated from plant_sced (per-resource, native interval).
"""

from __future__ import annotations

import re

import pandas as pd

from ercot_core import bootstrap, paths

bootstrap.setup_path()  # eia923 / sced_plants importable

XWALK_CSV = paths.PLANT_SCED_DIR / "eia_sced_crosswalk.csv"

# Words that don't help identify a specific site.
_GENERIC = {
    "SOLAR", "WIND", "STORAGE", "ENERGY", "PROJECT", "LLC", "INC", "LP", "LTD",
    "POWER", "CENTER", "STATION", "PLANT", "FARM", "HYBRID", "GENERATING",
    "GENERATION", "THE", "OF", "AND", "TX", "TEXAS", "COUNTY", "UNIT", "UNITS",
    "BESS", "BATTERY", "HOLDINGS", "COMPANY", "CO", "PARTNERS", "GP", "FUND",
    "PV", "BES", "ESS", "ESR", "FACILITY", "REPOWER", "REPOWERING", "PHASE",
    "I", "II", "III", "IV", "ELECTRIC", "RENEWABLES", "RENEWABLE",
}

# EIA fuel_category -> compatible SCED fuel_group(s).
_FUEL_COMPAT = {
    "Solar": {"Solar"}, "Wind": {"Wind"}, "Nuclear": {"Nuclear"},
    "Coal": {"Coal/Lignite"}, "Gas": {"Gas", "Gas-CC"}, "Hydro": {"Hydro"},
    "Storage": {"Storage"}, "Other Gas": {"Gas", "Gas-CC"}, "Oil": {"Gas", "Diesel"},
    "Biomass": {"Other", "Renewable"}, "Geothermal": {"Other"},
}


def _toks(name: str) -> set[str]:
    return {w for w in re.split(r"[^A-Za-z0-9]+", str(name).upper())
            if len(w) >= 3 and w not in _GENERIC and not w.isdigit()}


# ── EIA side ────────────────────────────────────────────────────────────────
def eia_plant_table(years, region="ercot") -> pd.DataFrame:
    """One row per EIA plant: id, name, state, dominant fuel, total net gen."""
    import eia923
    e = eia923.load(years=list(years), region=region)
    if e.empty:
        return pd.DataFrame(columns=["plant_id", "plant_name", "state", "fuel", "mwh"])
    fuel = (e.groupby(["plant_id", "fuel_category"])["netgen_mwh"].sum()
            .reset_index().sort_values("netgen_mwh", ascending=False)
            .drop_duplicates("plant_id").set_index("plant_id")["fuel_category"])
    g = (e.groupby(["plant_id", "plant_name", "state"])["netgen_mwh"].sum()
         .reset_index().rename(columns={"netgen_mwh": "mwh"}))
    g["fuel"] = g["plant_id"].map(fuel)
    return g.sort_values("mwh", ascending=False).reset_index(drop=True)


def eia_monthly(plant_id, years, region="ercot") -> pd.DataFrame:
    """Monthly net generation (MWh) for one EIA plant."""
    import eia923
    e = eia923.load(years=list(years), region=region)
    e = e[e["plant_id"] == plant_id]
    if e.empty:
        return pd.DataFrame(columns=["month", "eia_mwh"])
    out = (e.groupby("date")["netgen_mwh"].sum().reset_index()
           .rename(columns={"date": "month", "netgen_mwh": "eia_mwh"}))
    out["month"] = pd.to_datetime(out["month"]).dt.to_period("M").dt.to_timestamp()
    return out


# ── SCED side ─────────────────────────────────────────────────────────────
def suggest_resources(eia_plant_name, eia_fuel, registry=None, max_n=12) -> pd.DataFrame:
    """Rank ERCOT SCED resources likely belonging to an EIA plant.

    Match shared significant tokens of the EIA name against each SCED resource's
    plant_name (and the code itself), restricted to a compatible fuel group.
    """
    import sced_plants as sp
    reg = registry if registry is not None else sp.load_registry()
    compat = _FUEL_COMPAT.get(eia_fuel, set())
    cand = reg[reg["fuel_group"].isin(compat)] if compat else reg
    et = _toks(eia_plant_name)
    if not et or cand.empty:
        return pd.DataFrame(columns=["resource_name", "plant_name", "fuel_group", "score"])

    rows = []
    for _, r in cand.iterrows():
        rt = _toks(r.get("plant_name", "")) | _toks(r["resource_name"])
        shared = {t for t in (et & rt) if len(t) >= 4}
        if shared:
            rows.append({"resource_name": r["resource_name"],
                         "plant_name": r.get("plant_name", ""),
                         "fuel_group": r["fuel_group"],
                         "score": len(shared), "matched": ", ".join(sorted(shared))})
    if not rows:
        return pd.DataFrame(columns=["resource_name", "plant_name", "fuel_group", "score", "matched"])
    return (pd.DataFrame(rows).sort_values(["score", "resource_name"], ascending=[False, True])
            .head(max_n).reset_index(drop=True))


def _monthly_from_plant(df, start_ts, end_excl) -> pd.Series | None:
    if df is None or df.empty or "telemetered_net_output" not in df.columns:
        return None
    s = (df.set_index(pd.to_datetime(df["sced_timestamp"]))["telemetered_net_output"]
         .astype(float).sort_index())
    # Integrate in tz-aware US/Central so DST is exact at sub-hourly resolution:
    # the fall-back repeated hour stays two distinct hours, and the spring-forward
    # gap is a true gap (no phantom interval). The window bounds must be tz-aware
    # too, so localize them to Central before filtering.
    if getattr(s.index, "tz", None) is None:
        s.index = s.index.tz_localize("US/Central", ambiguous="infer",
                                      nonexistent="shift_forward")
    else:
        s.index = s.index.tz_convert("US/Central")
    tz = s.index.tz

    def _central(t):
        t = pd.Timestamp(t)
        return t.tz_localize(tz) if t.tzinfo is None else t.tz_convert(tz)

    s = s[(s.index >= _central(start_ts)) & (s.index < _central(end_excl))]
    if s.empty:
        return None
    # ERCOT settles energy on 15-minute intervals. Average MW within each 15-min
    # bin first — this collapses the variable 5–15 min SCED cadence with no
    # density bias — then × 0.25 h = MWh, summed to calendar months. Resampling on
    # the tz-aware index keeps DST exact; matches a true time-weighted integral to
    # < 0.01%. Month index returned tz-naive to align with the EIA monthly index.
    monthly = s.resample("15min").mean().mul(0.25).resample("MS").sum()
    monthly.index = monthly.index.tz_localize(None)
    return monthly


def sced_monthly(resource_names, start, end, allow_fetch=True) -> pd.DataFrame:
    """Monthly SCED energy (MWh) summed across the given resources.

    Reads each resource's stored per-plant parquet (fast); only fetches from the
    SCED cache when a resource isn't stored and allow_fetch=True. Energy = hourly
    mean MW summed per month.
    """
    import sced_plants as sp
    if not resource_names:
        return pd.DataFrame(columns=["month", "sced_mwh"])
    start_ts = pd.Timestamp(start)
    end_excl = pd.Timestamp(end) + pd.Timedelta(days=1)
    parts = []
    for name in resource_names:
        df = sp.load_plant(name)                          # stored parquet (no daily re-scan)
        if (df is None or df.empty) and allow_fetch:
            df = sp.fetch_plants([name], str(start_ts.date()), str(pd.Timestamp(end).date()),
                                 write=True).get(name)
        m = _monthly_from_plant(df, start_ts, end_excl)
        if m is not None:
            parts.append(m.rename(name))
    if not parts:
        return pd.DataFrame(columns=["month", "sced_mwh"])
    total = pd.concat(parts, axis=1).sum(axis=1)
    out = total.reset_index()
    out.columns = ["month", "sced_mwh"]
    return out


# ── crosswalk overrides ─────────────────────────────────────────────────────
def load_crosswalk() -> pd.DataFrame:
    cols = ["eia_plant_id", "eia_plant_name", "resource_names", "locked"]
    if XWALK_CSV.exists():
        try:
            x = pd.read_csv(XWALK_CSV)
            if "locked" not in x.columns:           # back-compat: old 3-col files
                x["locked"] = 0
            x["locked"] = pd.to_numeric(x["locked"], errors="coerce").fillna(0).astype(int)
            return x
        except Exception:
            pass
    return pd.DataFrame(columns=cols)


def save_crosswalk(eia_plant_id, eia_plant_name, resource_names, locked=None) -> None:
    """Write one plant mapping. ``locked`` left as None preserves the existing
    row's lock flag; pass True to protect a hand-verified mapping from being
    overwritten by a future ``save_auto_matches`` (auto-crosswalk bulk save)."""
    x = load_crosswalk()
    prev = x[x["eia_plant_id"] == eia_plant_id]
    keep_lock = int(prev["locked"].iloc[0]) if (locked is None and not prev.empty) else int(bool(locked))
    x = x[x["eia_plant_id"] != eia_plant_id]
    row = {"eia_plant_id": eia_plant_id, "eia_plant_name": eia_plant_name,
           "resource_names": ";".join(resource_names), "locked": keep_lock}
    x = pd.concat([x, pd.DataFrame([row])], ignore_index=True)
    paths.PLANT_SCED_DIR.mkdir(parents=True, exist_ok=True)
    x.to_csv(XWALK_CSV, index=False)


def mapped_resources(eia_plant_id) -> list[str]:
    x = load_crosswalk()
    hit = x[x["eia_plant_id"] == eia_plant_id]
    if hit.empty:
        return []
    val = str(hit.iloc[0]["resource_names"])
    return [r for r in val.split(";") if r]


# ── reconciliation ───────────────────────────────────────────────────────────
def reconcile(eia_plant_id, eia_plant_name, resource_names, years,
              tolerance=0.10, region="ercot", allow_fetch=True) -> dict:
    """Monthly SCED-vs-EIA reconciliation for one plant.

    Returns {"table": DataFrame(month, eia_mwh, sced_mwh, diff, pct, flag),
             "summary": {...}}.
    """
    eia = eia_monthly(eia_plant_id, years, region=region)
    yspan = (pd.Timestamp(min(years), 1, 1), pd.Timestamp(max(years), 12, 31))
    sced = sced_monthly(resource_names, *yspan, allow_fetch=allow_fetch)

    tbl = eia.merge(sced, on="month", how="outer").sort_values("month").reset_index(drop=True)
    tbl["eia_mwh"] = pd.to_numeric(tbl["eia_mwh"], errors="coerce")
    tbl["sced_mwh"] = pd.to_numeric(tbl["sced_mwh"], errors="coerce")
    tbl["diff_mwh"] = tbl["sced_mwh"] - tbl["eia_mwh"]
    tbl["pct_diff"] = tbl["diff_mwh"] / tbl["eia_mwh"].where(tbl["eia_mwh"].abs() > 1e-9)

    both = tbl.dropna(subset=["eia_mwh", "sced_mwh"])
    off = both[both["pct_diff"].abs() > tolerance]

    def _flag(r):
        if pd.isna(r["eia_mwh"]) or pd.isna(r["sced_mwh"]):
            return "no overlap"
        if abs(r["pct_diff"]) > tolerance:
            return "⚠ off"
        return "ok"
    tbl["flag"] = tbl.apply(_flag, axis=1)

    summary = {
        "eia_plant_id": eia_plant_id, "eia_plant_name": eia_plant_name,
        "resources": resource_names,
        "months_compared": int(len(both)),
        "eia_total_mwh": float(both["eia_mwh"].sum()),
        "sced_total_mwh": float(both["sced_mwh"].sum()),
        "overall_pct_diff": (float(both["sced_mwh"].sum() / both["eia_mwh"].sum() - 1)
                             if both["eia_mwh"].sum() else None),
        "months_off": int(len(off)),
        "tolerance": tolerance,
    }
    return {"table": tbl, "summary": summary}


# ── attribute-based auto-crosswalk (SCED resource -> EIA-860 plant) ──────────
# Inverse of _FUEL_COMPAT: SCED fuel_group -> compatible EIA fuel_category(s).
_SCED_TO_EIA: dict[str, set] = {}
for _ef, _grps in _FUEL_COMPAT.items():
    for _g in _grps:
        _SCED_TO_EIA.setdefault(_g, set()).add(_ef)


def _norm_county(s) -> str:
    return re.sub(r"\s+(county|co\.?)$", "", str(s).strip().lower()) if s is not None else ""


def _enrich_attributes(reg: pd.DataFrame) -> pd.DataFrame:
    """Fill missing county/capacity for SCED resources from interconnection.fyi
    (+ the ERCOT queue), matched on the resource's human plant name.

    This lets attribute-matching work for resources whose names differ from EIA
    (e.g. Markham↔Markum) by sourcing county+capacity independently.
    """
    refs = []
    try:
        from ercot_core import ifyi
        ie = ifyi.load_ercot_projects()
        if not ie.empty:
            ie = ie.copy()
            # Commercial operation date: actual if known, else the proposed date.
            ie["cod"] = pd.to_datetime(ie.get("actual_completion"), errors="coerce").fillna(
                pd.to_datetime(ie.get("proposed_completion"), errors="coerce"))
            refs.append(ie[["name", "county", "capacity_mw", "cod"]].rename(columns={"name": "pn"}))
    except Exception:
        pass
    try:
        from ercot_core import project_lookup as PL
        q = PL.load_full_queue(allow_fetch=False)
        if not q.empty:
            q = q.rename(columns={"Project Name": "pn", "County": "county",
                                  "Capacity (MW)": "capacity_mw"})[["pn", "county", "capacity_mw"]].copy()
            q["cod"] = pd.NaT  # the GIS queue export carries no COD column
            refs.append(q)
    except Exception:
        pass
    if not refs:
        return reg

    ref = pd.concat(refs, ignore_index=True)
    ref["pnU"] = ref["pn"].astype(str).str.upper()
    ref["capacity_mw"] = pd.to_numeric(ref["capacity_mw"], errors="coerce")

    reg = reg.copy()
    for c in ("county", "capacity_mw", "cod"):
        if c not in reg.columns:
            reg[c] = None
    for idx, row in reg.iterrows():
        if (pd.notna(row.get("county")) and pd.notna(row.get("capacity_mw"))
                and pd.notna(row.get("cod"))):
            continue
        toks = _toks(row.get("plant_name", "")) or _toks(row["resource_name"])
        if not toks:
            continue
        primary = max(toks, key=len)
        if len(primary) < 4:
            continue
        m = ref[ref["pnU"].str.contains(rf"\b{re.escape(primary)}", regex=True, na=False)]
        if m.empty:
            continue
        best = m.sort_values("capacity_mw", ascending=False).iloc[0]
        if pd.isna(row.get("county")):
            reg.at[idx, "county"] = best["county"]
        if pd.isna(row.get("capacity_mw")):
            reg.at[idx, "capacity_mw"] = best["capacity_mw"]
        if pd.isna(row.get("cod")) and "cod" in best.index and pd.notna(best["cod"]):
            reg.at[idx, "cod"] = best["cod"]
    return reg


def eia860m_plants(month=None, region="ercot") -> pd.DataFrame:
    """ERCO operating generators from EIA-860M (monthly), aggregated to plant level.

    Same schema as the annual plant table plus ``entity_name``. EIA-860M refreshes
    monthly, so it catches plants too new for the cached annual EIA-860 — the gap
    behind several auto-crosswalk misses. Needs network; no API key required
    (gridstatus downloads the public 860M workbook directly). Returns an empty
    frame on any failure so callers can degrade to annual-only.
    """
    import os
    import gridstatus
    from ercot_core import fuels

    eia = gridstatus.EIA(api_key=os.environ.get("EIA_API_KEY") or "unused-by-get_generators")
    if month:
        months = [month]
    else:  # newest 860M lags ~2 months; walk back until one resolves
        base = pd.Timestamp.today().normalize().replace(day=1)
        months = [(base - pd.DateOffset(months=k)).strftime("%Y-%m-01") for k in range(1, 7)]
    op = None
    for m in months:
        try:
            op = eia.get_generators(m)["operating"]
            break
        except Exception:
            continue
    if op is None or op.empty:
        return pd.DataFrame()
    op = op.copy()
    if region == "ercot":
        op = op[op["Balancing Authority Code"].astype(str).str.upper() == "ERCO"]
    if op.empty:
        return pd.DataFrame()
    op["_cap"] = pd.to_numeric(op["Nameplate Capacity"], errors="coerce")
    oy = pd.to_numeric(op.get("Operating Year"), errors="coerce").astype("Int64")
    om = pd.to_numeric(op.get("Operating Month"), errors="coerce").fillna(1).astype("Int64")
    op["_online"] = pd.to_datetime(oy.astype(str) + "-" + om.astype(str).str.zfill(2) + "-01",
                                   errors="coerce")
    op["_fuel"] = op.get("Energy Source Code").map(fuels.eia_fuel_category)
    g = op.groupby("Plant ID").agg(
        plant_name=("Plant Name", "first"), county=("County", "first"),
        state=("Plant State", "first"), entity_name=("Entity Name", "first"),
        mw=("_cap", "sum"), online=("_online", "min")).reset_index()
    fu = (op.dropna(subset=["_fuel"]).groupby(["Plant ID", "_fuel"])["_cap"].sum()
          .reset_index().sort_values("_cap", ascending=False)
          .drop_duplicates("Plant ID").set_index("Plant ID")["_fuel"])
    g["fuel"] = g["Plant ID"].map(fu)
    return g.rename(columns={"Plant ID": "plant_id"})


def auto_crosswalk(years, region="ercot", cap_tol=0.15, enrich=True,
                   use_860m=False, eia860m_month=None) -> pd.DataFrame:
    """Best EIA-860 plant for each SCED resource, scored on fuel+name+county+capacity+COD.

    Returns one row per SCED resource with the top candidate, a score, a
    confidence band, and the evidence used. County/capacity for the SCED side
    come from the plant-name crosswalk (queue/ifyi-derived, where known).

    ``use_860m=True`` augments the candidate pool with EIA-860M (monthly): it adds
    plants too new for the cached annual 860 and folds each plant's EIA *entity
    name* into the name-match tokens (an extra signal when the resource's plant
    name is unresolved). Requires network at build time; falls back silently to
    annual-only if the 860M fetch fails.
    """
    import eia860
    import sced_plants as sp
    from ercot_core import plant_names

    eia = eia860.load(list(years), region=region)
    if eia.empty:
        return pd.DataFrame()
    ep = (eia.groupby(["plant_id", "plant_name"]).agg(
        county=("county", "first"), state=("state", "first"),
        fuel=("fuel_category", lambda s: s.mode().iat[0] if not s.mode().empty else None),
        mw=("nameplate_mw", "sum"), online=("online_date", "min")).reset_index())
    ep["ncounty"] = ep["county"].map(_norm_county)
    ep["toks"] = ep["plant_name"].map(_toks)

    if use_860m:
        try:
            m = eia860m_plants(month=eia860m_month, region=region)
        except Exception:
            m = pd.DataFrame()
        if not m.empty:
            # attach entity name to existing (annual) plants; append fresh plants
            ent = m.dropna(subset=["entity_name"]).set_index("plant_id")["entity_name"].to_dict()
            ep["entity_name"] = ep["plant_id"].map(ent)
            fresh = m[~m["plant_id"].isin(set(ep["plant_id"]))]
            ep = pd.concat([ep, fresh], ignore_index=True)
            ep["ncounty"] = ep["county"].map(_norm_county)
            # fold entity-name tokens into the name-match set for every plant
            ent_col = ep.get("entity_name", pd.Series(index=ep.index, dtype=object)).fillna("")
            ep["toks"] = [_toks(pn) | _toks(en) for pn, en in zip(ep["plant_name"], ent_col)]

    reg = sp.load_registry()
    xw = plant_names.load_crosswalk()
    if not xw.empty:
        reg = reg.merge(xw[["resource_name", "county", "capacity_mw"]], on="resource_name", how="left")
    for c in ("county", "capacity_mw", "cod"):
        if c not in reg.columns:
            reg[c] = None
    if enrich:
        reg = _enrich_attributes(reg)

    rows = []
    for _, r in reg.iterrows():
        eia_fuels = _SCED_TO_EIA.get(r["fuel_group"], set())
        cands = ep[ep["fuel"].isin(eia_fuels)] if eia_fuels else ep
        if cands.empty:
            continue
        rtoks = _toks(r.get("plant_name", "")) | _toks(r["resource_name"])
        rcounty = _norm_county(r.get("county"))
        rcap = pd.to_numeric(pd.Series([r.get("capacity_mw")]), errors="coerce").iloc[0]
        rcod = pd.to_datetime(r.get("cod"), errors="coerce")
        rcod_yr = rcod.year if pd.notna(rcod) else None

        best, best_score, best_ev, best_sig = None, 0.0, "", 0
        second_score = 0.0
        for c in cands.itertuples():
            shared = {t for t in (rtoks & c.toks) if len(t) >= 4}
            name_s = len(shared)
            county_s = 1 if (rcounty and c.ncounty and rcounty == c.ncounty) else 0
            cap_s = 0
            if pd.notna(rcap) and pd.notna(c.mw) and c.mw > 0 and abs(rcap - c.mw) / c.mw <= cap_tol:
                cap_s = 1
            cyr = c.online.year if pd.notna(c.online) else None
            cod_s = 1 if (rcod_yr is not None and cyr is not None and abs(rcod_yr - cyr) <= 1) else 0
            # A shared specific (>=4-char, non-generic) name token and the hard
            # facts (capacity, COD) are the strong signals; county is coarse
            # (many plants share one) so it's weighted below a real name match.
            score = 3 * name_s + 2 * county_s + 3 * cap_s + 3 * cod_s
            if score > best_score:
                second_score = best_score   # the prior best becomes the runner-up
                ev = []
                if shared:
                    ev.append("name:" + "+".join(sorted(shared)))
                if county_s:
                    ev.append(f"county:{c.county}")
                if cap_s:
                    ev.append(f"cap≈{c.mw:.0f}MW")
                if cod_s:
                    ev.append(f"COD≈{cyr}")
                best, best_score, best_ev = c, score, ", ".join(ev)
                best_sig = (1 if name_s else 0) + county_s + cap_s + cod_s
            elif score > second_score:
                second_score = score

        if best is None or best_score == 0:
            continue
        has_cap = "cap≈" in best_ev
        has_cod = "COD≈" in best_ev
        hard = has_cap or has_cod              # a physical/temporal fact agrees
        margin = best_score - second_score      # how clearly it beat the runner-up
        # High needs a hard attribute AND >=3 agreeing signals; medium needs >=2.
        if hard and best_sig >= 3:
            conf = "high"
        elif best_sig >= 2:
            conf = "medium"
        else:
            conf = "low"
        # A thin win over the next candidate is exactly how the Markum/Tokio-type
        # errors slipped through — demote one band when the margin is small.
        if margin < 3 and conf != "low":
            conf = "medium" if conf == "high" else "low"
            best_ev += f", thin(+{margin:.0f})"
        rows.append({
            "resource_name": r["resource_name"], "sced_plant_name": r.get("plant_name", ""),
            "fuel_group": r["fuel_group"], "eia_plant_id": int(best.plant_id),
            "eia_plant_name": best.plant_name, "eia_county": best.county,
            "eia_mw": round(float(best.mw), 1) if pd.notna(best.mw) else None,
            "eia_online": (best.online.date().isoformat() if pd.notna(best.online) else None),
            "score": best_score, "margin": round(float(margin), 1),
            "confidence": conf, "evidence": best_ev,
        })
    if not rows:
        return pd.DataFrame()
    order = {"high": 0, "medium": 1, "low": 2}
    return (pd.DataFrame(rows).sort_values(["confidence", "score", "eia_plant_name"],
            key=lambda s: s.map(order) if s.name == "confidence" else s,
            ascending=[True, False, True]).reset_index(drop=True))


def save_auto_matches(match_df: pd.DataFrame, min_confidence="high") -> int:
    """Persist auto-crosswalk rows (>= min_confidence) into the reconcile crosswalk,
    grouping SCED resources by EIA plant. Returns the number of plants written.

    Locked rows (hand-verified overrides) are never touched: an auto match is
    skipped if its EIA plant id is locked, or if any of its SCED resources already
    belong to a locked row (so a corrected resource→plant mapping can't be
    re-broken by a fuzzy auto match)."""
    order = {"high": 0, "medium": 1, "low": 2}
    keep = match_df[match_df["confidence"].map(order) <= order[min_confidence]]
    existing = load_crosswalk()
    locked = existing[existing["locked"] == 1]
    locked_ids = set(locked["eia_plant_id"].astype(int))
    locked_res = {r for v in locked["resource_names"] for r in str(v).split(";") if r}
    n = 0
    for pid, g in keep.groupby("eia_plant_id"):
        res = sorted(g["resource_name"].tolist())
        if int(pid) in locked_ids or (set(res) & locked_res):
            continue
        save_crosswalk(int(pid), g["eia_plant_name"].iloc[0], res)
        n += 1
    return n


# ── code → interconnection.fyi name resolver ────────────────────────────────
def _is_subseq(code: str, token: str) -> bool:
    it = iter(token)
    return all(ch in it for ch in code)


def _resource_code(resource_name: str) -> str:
    return re.sub(r"[^A-Z]", "", str(resource_name).split("_")[0].upper())


def resolve_names(min_code_len: int = 4) -> pd.DataFrame:
    """Propose authoritative interconnection.fyi names for ERCOT resources by
    recognizing the code as an abbreviation (subsequence) of a project name
    token — e.g. MRKM → Markum. Returns a review table with confidence.
    """
    import sced_plants as sp
    from ercot_core import ifyi

    ie = ifyi.load_ercot_projects()
    if ie.empty:
        return pd.DataFrame()
    ie = ie.dropna(subset=["name"]).copy()
    ie["toks"] = ie["name"].map(_toks)
    ie["cap"] = pd.to_numeric(ie["capacity_mw"], errors="coerce")

    reg = sp.load_registry()
    rows = []
    for r in reg.itertuples():
        code = _resource_code(r.resource_name)
        if len(code) < min_code_len:
            continue
        cands = []
        for p in ie.itertuples():
            hit = next((t for t in p.toks if len(t) >= min_code_len
                        and t[0] == code[0] and _is_subseq(code, t)), None)
            if hit:
                cands.append((p, hit))
        if not cands:
            continue
        qids = {p.queue_id for p, _ in cands}
        best, tok = max(cands, key=lambda pt: (pt[0].cap if pd.notna(pt[0].cap) else 0))
        cur = str(getattr(r, "plant_name", "") or "")
        rows.append({
            "resource_name": r.resource_name, "current_name": cur,
            "current_source": getattr(r, "name_source", ""),
            "proposed_name": best.name, "queue_id": best.queue_id,
            "county": best.county, "capacity_mw": round(float(best.cap), 1) if pd.notna(best.cap) else None,
            "status": best.status, "candidates": len(qids),
            "differs": cur.strip().lower() != str(best.name).strip().lower(),
            "confidence": "high" if len(qids) == 1 else "medium",
            "matched_token": tok,
        })
    if not rows:
        return pd.DataFrame()
    order = {"high": 0, "medium": 1}
    return (pd.DataFrame(rows)
            .sort_values(["differs", "confidence", "resource_name"],
                         key=lambda s: s.map(order) if s.name == "confidence" else s,
                         ascending=[False, True, True]).reset_index(drop=True))


def apply_resolved_names(df: pd.DataFrame, min_confidence="high", only_differs=True) -> int:
    """Write resolver proposals (>= min_confidence) into the resolved-names tier."""
    from ercot_core import plant_names
    order = {"high": 0, "medium": 1}
    keep = df[df["confidence"].map(order) <= order[min_confidence]]
    if only_differs:
        keep = keep[keep["differs"]]
    rows = keep[["resource_name", "proposed_name", "queue_id", "county", "capacity_mw"]].rename(
        columns={"proposed_name": "plant_name"}).to_dict("records")
    return plant_names.record_resolved_names(rows)


def batch_reconcile(years, tolerance=0.10, region="ercot", allow_fetch=True,
                    progress=None) -> pd.DataFrame:
    """Reconcile every saved crosswalk mapping; one summary row per plant.

    `progress(i, n, name)` is an optional callback for UI status. Returns a
    DataFrame sorted worst-divergence first, with a `status` column.
    """
    x = load_crosswalk()
    rows = []
    n = len(x)

    # ONE fleet-wide SCED scan: fetch every mapped resource in a single pass
    # (the daily disclosure is read once and shared across all resources),
    # populating per-plant parquets. Then each plant reconciles from storage.
    if allow_fetch and not x.empty:
        import sced_plants as sp
        all_res = sorted({s for v in x["resource_names"] for s in str(v).split(";") if s})
        if all_res:
            if progress:
                progress(0, n, f"fetching SCED for {len(all_res)} resources (one pass)…")
            sp.fetch_plants(all_res, f"{min(years)}-01-01", f"{max(years)}-12-31", write=True)

    for i, r in x.reset_index(drop=True).iterrows():
        pid = int(r["eia_plant_id"])
        pname = str(r["eia_plant_name"])
        resources = [s for s in str(r["resource_names"]).split(";") if s]
        if progress:
            progress(i, n, pname)
        try:
            s = reconcile(pid, pname, resources, years, tolerance=tolerance,
                          region=region, allow_fetch=False)["summary"]
            if s["months_compared"] == 0:
                status = "no overlap"
            elif s["months_off"] > 0:
                status = "⚠ off"
            else:
                status = "ok"
            rows.append({
                "plant_id": pid, "plant": pname, "resources": len(resources),
                "months": s["months_compared"], "eia_mwh": s["eia_total_mwh"],
                "sced_mwh": s["sced_total_mwh"], "overall_pct": s["overall_pct_diff"],
                "months_off": s["months_off"], "status": status,
            })
        except Exception as e:
            rows.append({"plant_id": pid, "plant": pname, "resources": len(resources),
                         "months": 0, "eia_mwh": None, "sced_mwh": None,
                         "overall_pct": None, "months_off": None, "status": f"error: {e}"})
    if not rows:
        return pd.DataFrame(columns=["plant_id", "plant", "resources", "months",
                                     "eia_mwh", "sced_mwh", "overall_pct", "months_off", "status"])
    df = pd.DataFrame(rows)
    df["_sortkey"] = df["overall_pct"].abs().fillna(-1)
    return df.sort_values("_sortkey", ascending=False).drop(columns="_sortkey").reset_index(drop=True)
