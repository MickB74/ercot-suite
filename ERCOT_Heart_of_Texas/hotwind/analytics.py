"""Run the shared settlement engine for Heart of Texas Wind over a date window.

Thin glue: load cached generation + node price, then call
:func:`ercot_core.settlement.compute_settlement` with the portal's contract
terms. Returns the engine's own ``{"intervals", "summary"}`` dict unchanged, so
the figures match the internal Data Hub exactly.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import contract, hub


def settle(start_date: dt.date, end_date: dt.date, terms: dict | None = None) -> dict | None:
    """Settle Heart of Texas Wind over [start_date, end_date] inclusive. None if no data."""
    terms = terms or contract.load_contract()
    a = contract.ASSET
    # Clamp the window to the contract term when set (off by default).
    if terms.get("term_start"):
        try:
            start_date = max(start_date, dt.date.fromisoformat(str(terms["term_start"])))
        except ValueError:
            pass
    if terms.get("term_end"):
        try:
            end_date = min(end_date, dt.date.fromisoformat(str(terms["term_end"])))
        except ValueError:
            pass
    if start_date > end_date:
        return None
    start = pd.Timestamp(start_date)
    end_excl = pd.Timestamp(end_date) + pd.Timedelta(days=1)

    gen_df = hub.generation(a["resource_node"], start, end_excl)
    ref = contract.settle_location(terms)         # node RN_RTS1 or a trading hub
    # Price at the settlement reference — node lake for the node, rich hub store
    # for a hub. Generation is always the plant's node.
    price_df = hub.settlement_prices(ref, start, end_excl)
    if gen_df.empty or price_df.empty:
        return None

    core = hub.core()
    floor, settle_below = contract.floor_args(terms)
    mw_scale = float(terms.get("volume_share_pct", 100.0)) / 100.0

    res = core.settlement.compute_settlement(
        gen_df, price_df, a["resource_node"],
        ppa_price=float(terms.get("strike", 0.0)),
        ref_location=ref,                         # settle at the chosen reference
        market="RT15",
        node_location=a["resource_node"],
        units=a.get("sced_units") or [a["resource_name"]],
        price_floor=floor,
        settle_below_floor=settle_below,
        mw_scale=mw_scale,
        **contract.engine_kwargs(terms),     # ceiling / negatives / REC / escalation
    )
    res["ref_location"] = ref
    excl_pct = float(terms.get("monthly_exclusion_pct", 0.0) or 0.0)
    if excl_pct > 0 and res and not res["intervals"].empty:
        res["intervals"] = _apply_monthly_exclusion(res["intervals"], excl_pct)
    return res


def _apply_monthly_exclusion(intervals: pd.DataFrame, pct: float) -> pd.DataFrame:
    """Apply the VPPA §3.1(d) Excluded Settlement Intervals carve-out.

    Each calendar month the Seller may exclude the Calculation Intervals most
    favorable to the Buyer (the highest offtaker-signed ``cfd`` — where the RT
    index most exceeds the fixed price) from the settlement, up to ``pct``% of
    that month's metered output. Excluded intervals contribute $0 to the CfD
    (the Buyer still receives the RECs / the energy still counts as delivered).
    Adds an ``excluded`` flag and zeroes ``cfd`` on the excluded rows.
    """
    d = intervals.copy()
    d["excluded"] = False
    months = pd.to_datetime(d["interval_start"]).dt.to_period("M")
    for _m, idx in d.groupby(months).groups.items():
        sub = d.loc[idx]
        cap = (pct / 100.0) * float(sub["mwh"].sum())
        order = sub.sort_values("cfd", ascending=False)   # most Buyer-favorable first
        cum = order["mwh"].cumsum()
        d.loc[order.index[cum <= cap], "excluded"] = True
    d.loc[d["excluded"], "cfd"] = 0.0
    return d


def typical_monthly_mwh(share: float = 1.0) -> pd.Series | None:
    """Typical-year MWh by calendar month (1–12) from the cached wind model.

    The "expected" production shape for a representative year at Heart of Texas
    Wind's coordinates and turbine fleet, scaled to the offtaker's ``share`` of
    the plant. Returns a Series indexed 1–12, or None if no profile is cached.
    """
    hourly = hub.wind_typical_hourly()
    if hourly is None or hourly.empty:
        return None
    ac_kw = hourly["ac_kw"]
    idx = ac_kw.index
    months = idx.month if hasattr(idx, "month") else pd.to_datetime(idx).month
    mwh = ac_kw.groupby(months).sum() / 1000.0
    mwh.index.name = "cal_month"
    return (mwh * float(share)).round(3)


def calibrate(actual_by_month: pd.Series, tmy_by_month: pd.Series,
              monthly_intervals: pd.Series | None = None,
              monthly_counts: pd.Series | None = None,
              min_years: int = 2) -> dict:
    """Anchor the TMY typical-year shape to real metered output.

    Returns per-month calibration factors for months with sufficient history.
    A calendar month needs at least *min_years* observations to be calibrated;
    otherwise it falls back to raw TMY (factor 1.0).  Partial months (<80% of
    expected intervals) and ramp months (<60% of TMY) are also excluded.

    Returns ``{factor, per_month, actual_mwh, tmy_mwh, months}``.
    ``per_month`` maps {cal_month: ratio} for qualifying months.
    ``factor`` is the median of those ratios (for UI display / manual override).
    """
    common = actual_by_month.index.intersection(tmy_by_month.index)
    _DAYS = {1: 31, 2: 28.25, 3: 31, 4: 30, 5: 31, 6: 30,
             7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

    full = []
    for cm in common:
        if monthly_counts is not None:
            if int(monthly_counts.get(cm, 0)) < min_years:
                continue
        expected_intervals = _DAYS.get(cm, 30) * 24 * 4
        if monthly_intervals is not None:
            actual_n = float(monthly_intervals.get(cm, 0))
            if actual_n / expected_intervals < 0.80:
                continue
        actual_mwh = float(actual_by_month[cm])
        tmy_val = float(tmy_by_month.get(cm, 0))
        if tmy_val > 0 and actual_mwh / tmy_val < 0.60:
            continue
        full.append(cm)
    common = pd.Index(full)

    per_month: dict[int, float] = {}
    for cm in common:
        a_val = float(actual_by_month[cm])
        t_val = float(tmy_by_month[cm])
        if t_val > 0:
            per_month[cm] = a_val / t_val
    a_sum = float(actual_by_month.reindex(common).sum())
    t_sum = float(tmy_by_month.reindex(common).sum())
    if per_month:
        import statistics
        factor = statistics.median(per_month.values())
    else:
        factor = 1.0
    return {"factor": factor, "per_month": per_month,
            "actual_mwh": a_sum, "tmy_mwh": t_sum,
            "months": int(len(common))}


def sced_monthly_total_mwh(start_date: dt.date, end_date: dt.date) -> pd.Series:
    """Plant-total metered MWh per month from raw SCED ``mw`` (no volume share).

    EIA-923 reports net generation for the whole plant, so the cross-check must
    compare against the full metered output — not the offtaker's contracted
    share. MWh = Σ mw × interval-hours, grouped by calendar month (YYYY-MM).
    """
    a = contract.ASSET
    start = pd.Timestamp(start_date)
    end_excl = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    g = hub.generation(a["resource_node"], start, end_excl)
    if g.empty:
        return pd.Series(dtype=float)
    g = g.copy()
    hrs = (pd.to_datetime(g["interval_end"]) - pd.to_datetime(g["interval_start"])
           ).dt.total_seconds() / 3600.0
    g["mwh"] = g["mw"] * hrs
    g["Month"] = pd.to_datetime(g["interval_start"]).dt.to_period("M").astype(str)
    return g.groupby("Month")["mwh"].sum()


def reconcile_eia(start_date: dt.date, end_date: dt.date,
                  tolerance_pct: float = 8.0) -> dict | None:
    """Cross-check SCED metered generation against EIA-923 net generation.

    An independent second source for the months whose modelled output looked
    off: SCED is ERCOT's ~60-day telemetry; EIA-923 is the plant's own monthly
    filing. Where they diverge beyond ``tolerance_pct`` the metered figure (and
    any settlement built on it) deserves a second look.

    Returns ``None`` if no EIA plant id is mapped (cross-check disabled), else a
    dict: ``table`` (per-month SCED vs EIA, delta, % and a ``flag``),
    ``plant_id``, ``compared`` (# months with both sources), ``flagged`` (# over
    tolerance). Months where EIA hasn't published yet show EIA as NaN and are
    not flagged.
    """
    plant_id = contract.eia_plant_id()
    if plant_id is None:
        return None

    sced = sced_monthly_total_mwh(start_date, end_date)
    if sced.empty:
        return {"table": pd.DataFrame(), "plant_id": plant_id,
                "compared": 0, "flagged": 0}

    eia = hub.eia_monthly_netgen(plant_id, start_date.year, end_date.year)
    df = sced.rename("SCED_MWh").reset_index()  # Month, SCED_MWh
    if not eia.empty:
        eia = eia.copy()
        eia["Month"] = (eia["year"].astype(int).astype(str) + "-"
                        + eia["month"].astype(int).astype(str).str.zfill(2))
        df = df.merge(eia[["Month", "eia_mwh"]].rename(columns={"eia_mwh": "EIA_MWh"}),
                      on="Month", how="left")
    else:
        df["EIA_MWh"] = pd.NA

    df["Delta_MWh"] = df["SCED_MWh"] - df["EIA_MWh"]
    df["Pct"] = (df["Delta_MWh"] / df["EIA_MWh"]) * 100.0
    # flag only months where EIA actually published; leave the rest as <NA>.
    df["flag"] = (df["Pct"].abs() > float(tolerance_pct)).where(df["EIA_MWh"].notna(), pd.NA)
    df = df.sort_values("Month").reset_index(drop=True)

    compared = int(df["EIA_MWh"].notna().sum())
    flagged = int(df["flag"].fillna(False).sum())
    return {"table": df, "plant_id": plant_id,
            "compared": compared, "flagged": flagged,
            "tolerance_pct": float(tolerance_pct)}


def pick_time_basis(raw, mapping, price_df, location):
    """Auto-detect whether a statement labels intervals by 'ending' or 'beginning'.

    Some statements (e.g. some 2024 Millipore months) flip the convention, which
    misaligns the price join by one interval and flags nearly every row. We parse
    the statement both ways and keep whichever makes the bill's own price column
    match ERCOT's published price on the most intervals. Falls back to the
    mapping's basis if there's no price column to test against.
    """
    core = hub.core()
    INV = core.invoice
    if not mapping.get("price_col"):
        return mapping.get("time_basis", "ending")
    exp = INV.expected_prices(price_df, location, "RT15")
    if exp.empty:
        return mapping.get("time_basis", "ending")
    expk = (exp.assign(_k=pd.to_datetime(exp["interval_start"]).dt.tz_localize(None).dt.round("min"))
               .drop_duplicates("_k").set_index("_k")["exp_price"])
    best, best_n = mapping.get("time_basis", "ending"), -1
    for basis in ("ending", "beginning"):
        m = dict(mapping); m["time_basis"] = basis
        try:
            inv = INV.load_invoice(raw, m)
        except Exception:  # noqa: BLE001
            continue
        k = pd.to_datetime(inv["interval_start"]).dt.tz_localize(None).dt.round("min")
        ep = k.map(expk)
        p = pd.to_numeric(inv.get("inv_price"), errors="coerce")
        n = int(((p - ep).abs() < 0.01).sum())
        if n > best_n:
            best, best_n = basis, n
    return best


def audit_net_settlement(inv_df, price_df, terms, *, location, resource_node, units,
                         gen_df=None, volume_basis="invoice", sign="offtaker",
                         neg_treatment="full", neg_floor=0.0, tol=1.0) -> dict:
    """Audit a VPPA **net-settlement** statement, interval by interval.

    Unlike the gross energy-invoice reconciler (which checks ``amount ≈
    price × volume``), this checks the bill's bottom line: the statement's net
    settlement $ against the contract CfD ``(market price − strike) × volume``,
    using ERCOT's published RT15 price at ``location`` and the strike from the
    portal's contract terms. ``volume_basis`` picks whose volume values the
    settlement — the statement's own MWh or ERCOT metered generation.

    Net is offtaker-signed here (positive = offtaker receives). ``sign="generator"``
    flips the statement column first (for statements written generator-signed).
    Returns ``{"intervals", "summary"}`` shaped like the reconciler's output.
    """
    core = hub.core()
    INV = core.invoice
    strike = float(terms.get("strike", 0.0))
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0

    j = inv_df.copy()
    # naive-Central key, rounded to the minute so the statement (tz-aware, and
    # sometimes carrying sub-second Excel artifacts like 23:44:59.999) joins
    # cleanly to the portal's exact 15-minute interval starts.
    def _key(s):
        return pd.to_datetime(s).dt.tz_localize(None).dt.round("min")

    j["_key"] = _key(j["interval_start"])

    # ERCOT price for transparency (and the invoice-basis expected net).
    exp = INV.expected_prices(price_df, location, "RT15")        # interval_start, exp_price
    exp["_key"] = _key(exp["interval_start"])
    j = j.merge(exp[["_key", "exp_price"]], on="_key", how="left")
    if "inv_price" in j.columns:
        j["price_delta"] = pd.to_numeric(j["inv_price"], errors="coerce") - j["exp_price"]

    # Volumes: the bill's own MWh, and ERCOT SCED metered generation at the
    # contracted share — always computed (when generation is available) so the
    # quantity can be checked alongside the money, in either settlement basis.
    j["inv_volume_mwh"] = pd.to_numeric(j.get("inv_volume_mwh"), errors="coerce")
    if gen_df is not None and not gen_df.empty:
        mv = INV.expected_volume(gen_df, resource_node, units=units, mw_scale=share)
        mv["_key"] = _key(mv["interval_start"])
        j = j.merge(mv[["_key", "metered_mwh"]].rename(columns={"metered_mwh": "sced_volume_mwh"}),
                    on="_key", how="left")
    else:
        j["sced_volume_mwh"] = pd.NA
    j["vol_delta"] = j["inv_volume_mwh"] - pd.to_numeric(j["sced_volume_mwh"], errors="coerce")

    # Which volume settles the net: SCED metered (authoritative quantity) or the
    # statement's own asserted volume.
    j["basis_volume_mwh"] = (pd.to_numeric(j["sced_volume_mwh"], errors="coerce")
                             if volume_basis == "metered" else j["inv_volume_mwh"])
    # Negative-price treatment (the standard VPPA lever):
    #   full    — settle the real RT price, however negative.
    #   floor   — clip the floating price at ``neg_floor`` before settling (most
    #             VPPAs: the offtaker doesn't get charged below the floor).
    #   curtail — no settlement at intervals where RT price < floor.
    raw_price = pd.to_numeric(j["exp_price"], errors="coerce")
    eff_price = raw_price.clip(lower=neg_floor) if neg_treatment == "floor" else raw_price
    j["exp_net"] = (eff_price - strike) * j["basis_volume_mwh"]
    if neg_treatment == "curtail":
        j.loc[raw_price < neg_floor, "exp_net"] = 0.0

    # Sign: the statement may be offtaker-signed (positive = offtaker receives) or
    # generator-signed (the negation). "auto" picks whichever fits the expected
    # net better, so the user doesn't have to know the bill's convention.
    raw_inv = pd.to_numeric(j.get("inv_amount"), errors="coerce")
    if sign == "auto":
        d_pos = (raw_inv - j["exp_net"]).abs().sum()
        d_neg = ((-raw_inv) - j["exp_net"]).abs().sum()
        sign = "generator" if d_neg < d_pos else "offtaker"
    j["inv_net"] = -raw_inv if sign == "generator" else raw_inv
    j["net_delta"] = j["inv_net"] - j["exp_net"]

    def _status(row):
        if (pd.isna(row.get("inv_net")) and pd.isna(row.get("inv_volume_mwh"))
                and pd.isna(row.get("inv_price"))):
            return "blank"            # statement has no data here (e.g. pre-delivery-start)
        if pd.isna(row.get("exp_price")):
            return "no_price"         # ERCOT price missing for this interval
        if pd.isna(row.get("exp_net")):
            return "no_volume"        # statement gives no volume to settle
        if pd.isna(row.get("inv_net")):
            return "no_amount"
        return "match" if abs(row["net_delta"]) <= tol else "net_mismatch"

    j["status"] = j.apply(_status, axis=1)
    # Totals over COMPARABLE intervals only (both a statement net and an expected
    # net exist). Otherwise unaudited intervals — no ERCOT price, or no bill
    # amount — would silently inflate the variance against a "ties out" verdict.
    comparable = (pd.to_numeric(j["inv_net"], errors="coerce").notna()
                  & pd.to_numeric(j["exp_net"], errors="coerce").notna())
    inv_tot = float(pd.to_numeric(j.loc[comparable, "inv_net"], errors="coerce").sum())
    exp_tot = float(pd.to_numeric(j.loc[comparable, "exp_net"], errors="coerce").sum())
    inv_vol_series = pd.to_numeric(j["inv_volume_mwh"], errors="coerce")
    sced_series = pd.to_numeric(j["sced_volume_mwh"], errors="coerce")
    has_sced = sced_series.notna()
    # Compare volumes only where SCED exists — telemetry gaps would otherwise
    # make the bill's full volume look inflated against a partial SCED total.
    sced_vol = float(sced_series[has_sced].sum())
    inv_vol_matched = float(inv_vol_series[has_sced].sum())

    # ── EIA-923 monthly volume cross-check (authoritative settlement meter) ──
    # Compare the bill's volume to the plant's EIA-923 net generation × the
    # contracted share, per calendar month. Headline only counts months the bill
    # substantially covers (≥2000 intervals) so a boundary sliver can't skew it.
    eia_table = None
    eia = {}
    plant_id = contract.eia_plant_id()
    if plant_id is not None:
        jm = j.assign(year=pd.to_datetime(j["_key"]).dt.year,
                      month=pd.to_datetime(j["_key"]).dt.month)
        bym = (jm.groupby(["year", "month"])
                 .agg(bill_mwh=("inv_volume_mwh", "sum"),
                      intervals=("inv_volume_mwh", "size")).reset_index())
        yrs = [int(y) for y in bym["year"].unique()]
        edf = hub.eia_monthly_netgen(plant_id, min(yrs), max(yrs),
                                     prime_mover=contract.ASSET.get("eia_prime_mover"))
        if not edf.empty:
            t = bym.merge(edf, on=["year", "month"], how="left")
            t["eia_share_mwh"] = pd.to_numeric(t["eia_mwh"], errors="coerce") * share
            t["delta_mwh"] = t["bill_mwh"] - t["eia_share_mwh"]
            t["pct"] = (t["delta_mwh"] / t["eia_share_mwh"] * 100.0)
            eia_table = t
            cov = t[(t["intervals"] >= 2000) & t["eia_share_mwh"].notna()]
            if not cov.empty:
                e_bill = float(cov["bill_mwh"].sum())
                e_eia = float(cov["eia_share_mwh"].sum())
                eia = {"eia_volume_bill": e_bill, "eia_volume_total": e_eia,
                       "eia_volume_delta": e_bill - e_eia,
                       "eia_volume_pct": ((e_bill - e_eia) / e_eia * 100.0) if e_eia else 0.0,
                       "eia_months": int(len(cov))}

    summary = {
        "eia_plant_id": plant_id, **eia,
        "location": location, "volume_basis": volume_basis, "sign": sign,
        "neg_treatment": neg_treatment, "neg_floor": neg_floor,
        "inv_volume_total": float(inv_vol_series.sum()),
        "inv_volume_matched": inv_vol_matched,
        "sced_volume_total": sced_vol,
        "volume_delta": inv_vol_matched - sced_vol,
        "sced_intervals": int(has_sced.sum()),
        "sced_missing": int((~has_sced).sum()),
        "intervals": int(len(j)),
        "comparable": int(comparable.sum()),
        "unaudited": int((~comparable).sum()),
        "n_match": int((j["status"] == "match").sum()),
        "n_flagged": int((j["status"] == "net_mismatch").sum()),
        "invoiced_total": inv_tot, "expected_total": exp_tot,
        "variance": inv_tot - exp_tot,
        "variance_pct": ((inv_tot - exp_tot) / exp_tot * 100.0) if exp_tot else 0.0,
        "status_counts": j["status"].value_counts().to_dict(),
    }
    return {"intervals": j, "summary": summary, "eia_table": eia_table}


def audit_summary(summary: dict, terms: dict, *, resource_node, units) -> dict:
    """Reconcile a monthly **summary invoice** (from a PDF), not interval data.

    Checks the strike, the internal arithmetic, and — the strong external check —
    the billed volume against EIA-923 net generation × the contracted share for
    the invoice month. Returns a dict of findings; values are None where the PDF
    didn't supply the field or EIA hasn't published the month.
    """
    strike = float(terms.get("strike", 0.0))
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0
    vol = summary.get("volume_mwh")
    fx = summary.get("fixed_rate")
    net = summary.get("net_total")
    fp, flp = summary.get("fixed_payment"), summary.get("floating_payment")

    out = {"volume_mwh": vol, "fixed_rate": fx, "floating_rate": summary.get("floating_rate"),
           "net_total": net, "offtaker_net": (-net if net is not None else None),
           "period_start": summary.get("period_start"), "period_end": summary.get("period_end"),
           "strike_ok": (fx is not None and abs(fx - strike) < 0.01), "strike": strike}
    if None not in (fp, flp, net):
        out["arithmetic_delta"] = net - (fp + flp)

    ps = summary.get("period_start")
    plant_id = contract.eia_plant_id()
    if ps and vol and plant_id is not None:
        try:
            mm, _dd, yy = (int(x) for x in ps.split("."))
        except (ValueError, TypeError):
            mm = yy = None
        if yy:
            edf = hub.eia_monthly_netgen(plant_id, yy, yy,
                                         prime_mover=contract.ASSET.get("eia_prime_mover"))
            row = edf[(edf["year"] == yy) & (edf["month"] == mm)] if not edf.empty else edf
            if len(row):
                eia_share = float(row["eia_mwh"].iloc[0]) * share
                out["eia_volume"] = eia_share
                out["volume_delta"] = vol - eia_share
                out["volume_pct"] = ((vol - eia_share) / eia_share * 100.0) if eia_share else 0.0
                out["eia_plant_id"] = plant_id
    return out


def monthly_breakdown(intervals: pd.DataFrame) -> pd.DataFrame:
    """Per-month MWh, capture price, market value, strike value, CfD."""
    if intervals is None or intervals.empty:
        return pd.DataFrame()
    d = intervals.copy()
    d["Month"] = pd.to_datetime(d["interval_start"]).dt.to_period("M").astype(str)
    g = d.groupby("Month").agg(
        MWh=("mwh", "sum"),
        Market_value=("merchant", "sum"),
        Strike_value=("ppa_revenue", "sum"),
        CfD=("cfd", "sum"),
    ).reset_index()
    g["Capture_$/MWh"] = (g["Market_value"] / g["MWh"]).where(g["MWh"] != 0, 0.0)
    return g[["Month", "MWh", "Capture_$/MWh", "Market_value", "Strike_value", "CfD"]]
