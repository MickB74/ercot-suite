"""Generic ERCOT invoice / settlement-statement validator.

Reconciles an *uploaded* invoice (any file with an interval timestamp plus some
of {price $/MWh, volume MWh, amount $}) against this hub's cached ERCOT prices
(× metered generation, optionally), interval by interval.

The whole point is timezone correctness: ERCOT settles in Central Prevailing
Time and the November fall-back hour repeats, so the invoice's wall-clock labels
and our cached data are both lifted to *tz-aware Central* (the validated tz
layer, :mod:`ercot_core.tz`) before they are joined. The match is then on the
absolute instant, which is unambiguous even across the duplicated hour.

Pipeline
--------
    mapping = suggest_mapping(df.columns)            # or hand-built
    inv  = load_invoice(file_or_df, mapping)         # -> tz-aware key + values
    res  = reconcile(inv, price_df=..., gen_df=...,  # cached ERCOT data
                     location="HB_HUBAVG", market="RT15")
    res["intervals"]   # per-interval diff (naive Central for display)
    res["summary"]     # totals, $ variance, status counts, worst offenders

``price_df`` / ``gen_df`` are the tidy node_prices / node_generation frames the
settlement page already builds (see :mod:`ercot_core.prices`,
:func:`ercot_core.settlement.node_generation_mwh`).
"""

from __future__ import annotations

import pandas as pd

from ercot_core import settlement, tz

# ── column-role inference (for the upload UI) ───────────────────────────────
# Each role maps to substrings we look for in a header (lower-cased, stripped).
_ROLE_HINTS = {
    "time_col": ["interval ending", "interval_end", "interval start", "interval",
                 "delivery", "timestamp", "datetime", "date/time", "time", "date",
                 "hour ending", "he", "trade date"],
    "location_col": ["settlement point", "settlement_point", "settlementpoint",
                     "location", "node", "hub", "zone", "resource node", "sp"],
    "dst_flag_col": ["dst", "repeated hour", "repeated_hour", "dstflag"],
    "price_col": ["spp", "settlement point price", "lmp", "price", "$/mwh",
                  "rate", "$ per mwh"],
    "volume_col": ["mwh", "volume", "quantity", "qty", "energy", "metered", "mw"],
    "amount_col": ["amount", "charge", "total", "cost", "$", "settlement amount",
                   "extended"],
}


def suggest_mapping(columns) -> dict:
    """Best-guess column->role mapping for an uploaded invoice's headers.

    Returns a dict with keys time_col / location_col / dst_flag_col / price_col /
    volume_col / amount_col (values are column names or None) plus sensible
    ``time_basis`` ("ending") and ``interval`` ("15min") defaults. Heuristic
    only — the UI lets the user correct it.
    """
    cols = list(columns)
    low = {c: str(c).strip().lower() for c in cols}
    used: set = set()
    out: dict = {}

    def pick(role):
        for hint in _ROLE_HINTS[role]:
            for c in cols:
                if c in used:
                    continue
                if hint in low[c]:
                    used.add(c)
                    return c
        return None

    # Order matters: claim the specific roles before the greedy "$"/"date" ones.
    for role in ("dst_flag_col", "price_col", "volume_col", "amount_col",
                 "location_col", "time_col"):
        out[role] = pick(role)

    basis = "ending"
    if out["time_col"] and ("start" in low[out["time_col"]]
                            or "begin" in low[out["time_col"]]):
        basis = "beginning"
    interval = "hour" if (out["time_col"] and ("hour" in low[out["time_col"]]
                          or low[out["time_col"]] in ("he",))) else "15min"
    out["time_basis"] = basis
    out["interval"] = interval
    out["volume_unit"] = "MWh"
    return out


def _interval_delta(interval: str) -> pd.Timedelta:
    return pd.Timedelta(hours=1) if interval == "hour" else pd.Timedelta(minutes=15)


def load_invoice(src, mapping: dict) -> pd.DataFrame:
    """Read an invoice (path/file-like/DataFrame) into a normalized long frame.

    Output columns (only those the mapping supplies are filled):
      * ``interval_start`` — tz-aware Central, interval-BEGINNING (the join key)
      * ``location``       — str (or "" if no location column)
      * ``inv_price``      — $/MWh, ``inv_volume_mwh`` — MWh, ``inv_amount`` — $

    The timestamp is parsed, lifted to tz-aware Central (DST-correct, using the
    DST flag column if mapped), and — when the invoice labels the interval END
    (the ERCOT convention) — shifted back one interval to interval-beginning so
    it lines up with our interval-start data.
    """
    df = src if isinstance(src, pd.DataFrame) else _read_any(src)
    df = df.copy()

    tcol = mapping.get("time_col")
    if not tcol or tcol not in df.columns:
        raise ValueError("invoice mapping needs a valid 'time_col'")

    flags = None
    fcol = mapping.get("dst_flag_col")
    if fcol and fcol in df.columns:
        flags = df[fcol]

    aware = tz.localize_central(df[tcol], flags=flags)
    if mapping.get("time_basis", "ending") == "ending":
        aware = aware - _interval_delta(mapping.get("interval", "15min"))

    out = pd.DataFrame({"interval_start": aware})
    lcol = mapping.get("location_col")
    out["location"] = (df[lcol].astype(str) if lcol and lcol in df.columns else "")

    pcol = mapping.get("price_col")
    if pcol and pcol in df.columns:
        out["inv_price"] = pd.to_numeric(df[pcol], errors="coerce")
    vcol = mapping.get("volume_col")
    if vcol and vcol in df.columns:
        vol = pd.to_numeric(df[vcol], errors="coerce")
        if str(mapping.get("volume_unit", "MWh")).upper() == "MW":
            vol = vol * (_interval_delta(mapping.get("interval", "15min"))
                         / pd.Timedelta(hours=1))  # MW -> MWh for the interval
        out["inv_volume_mwh"] = vol
    acol = mapping.get("amount_col")
    if acol and acol in df.columns:
        out["inv_amount"] = pd.to_numeric(df[acol], errors="coerce")

    if not any(c in out.columns for c in ("inv_price", "inv_volume_mwh", "inv_amount")):
        raise ValueError("invoice mapping must supply at least one of "
                         "price_col / volume_col / amount_col")
    return out.dropna(subset=["interval_start"]).reset_index(drop=True)


def _read_any(src) -> pd.DataFrame:
    name = str(getattr(src, "name", src)).lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(src)
    return pd.read_csv(src)


def expected_prices(price_df: pd.DataFrame, location: str, market: str) -> pd.DataFrame:
    """Market SPP per interval at `location`/`market`, tz-aware Central key.

    Thin wrapper over the settlement price accessor so the invoice path and the
    PPA path resolve prices identically (DST-correct, flag-aware).
    """
    p = settlement._price_series(price_df, location, market)
    return p.rename(columns={"spp": "exp_price"})


def expected_volume(gen_df: pd.DataFrame, resource_node: str,
                    units=None, mw_scale: float = 1.0) -> pd.DataFrame:
    """Metered generation MWh per interval at a node, tz-aware Central key."""
    g = settlement.node_generation_mwh(gen_df, resource_node, units=units,
                                       mw_scale=mw_scale)
    return g.rename(columns={"mwh": "metered_mwh"})[["interval_start", "metered_mwh"]]


def reconcile(
    invoice_df: pd.DataFrame,
    *,
    price_df: pd.DataFrame | None = None,
    gen_df: pd.DataFrame | None = None,
    location: str,
    market: str = "RT15",
    resource_node: str | None = None,
    units=None,
    volume_source: str = "invoice",
    abs_tol: float = 0.01,
    rel_tol: float = 0.005,
) -> dict:
    """Reconcile an invoice against cached ERCOT prices (× metered generation).

    ``volume_source``:
      * ``"invoice"``  — validate the rate/extension arithmetic: expected $ =
        invoiced MWh × market SPP. (Needs the invoice to carry a volume.)
      * ``"metered"``  — also validate the *quantity*: pull our metered
        generation at ``resource_node`` and use it as the volume. (Needs gen_df.)

    Tolerances: a compared field passes when ``|delta| <= max(abs_tol,
    rel_tol*|expected|)``. ``abs_tol`` is dollars for amounts, and is reused as a
    small floor for price ($/MWh) and volume (MWh) comparisons.

    Returns ``{"intervals": <per-interval diff, naive Central>, "summary": {...}}``.
    """
    inv = invoice_df.copy()
    if "interval_start" not in inv.columns:
        raise ValueError("invoice_df must come from load_invoice (needs interval_start)")

    # Expected market price for every invoiced interval.
    exp = (expected_prices(price_df, location, market)
           if price_df is not None else
           pd.DataFrame(columns=["interval_start", "exp_price"]))

    # Join key: the interval-start instant, except DAM is hourly — a 15-min
    # invoice row is matched to the DAM price for the hour that contains it.
    def _key(s):
        return s.dt.floor("h") if market == "DAM" else s
    inv = inv.assign(_k=_key(inv["interval_start"]))
    exp = exp.assign(_k=_key(exp["interval_start"])).drop(columns=["interval_start"])
    merged = inv.merge(exp, on="_k", how="outer", indicator=True)
    # right-only rows have no invoice interval_start — fall back to the key.
    merged["interval_start"] = merged["interval_start"].fillna(merged["_k"])

    # Only flag billing gaps *inside* the invoice's own window (callers may pad
    # the price fetch); expected-only rows outside that span are not "missing".
    if len(invoice_df):
        lo, hi = invoice_df["interval_start"].min(), invoice_df["interval_start"].max()
        drop = (merged["_merge"] == "right_only") & (
            (merged["interval_start"] < lo) | (merged["interval_start"] > hi))
        merged = merged[~drop].copy()
    merged = merged.drop(columns="_k")

    # Volume to value: invoice's own, or our metered generation.
    if volume_source == "metered":
        node = resource_node or location
        vol = (expected_volume(gen_df, node, units=units)
               if gen_df is not None else
               pd.DataFrame(columns=["interval_start", "metered_mwh"]))
        merged = merged.merge(vol, on="interval_start", how="left")
        merged["volume_mwh"] = merged.get("metered_mwh")
    else:
        merged["volume_mwh"] = merged.get("inv_volume_mwh")

    if "volume_mwh" not in merged.columns:
        merged["volume_mwh"] = pd.NA
    # Expected amount = volume × market price (when we have both).
    merged["exp_amount"] = pd.to_numeric(merged["volume_mwh"], errors="coerce") * merged["exp_price"]

    # Per-field deltas (only where the invoice supplies that field).
    if "inv_price" in merged.columns:
        merged["price_delta"] = merged["inv_price"] - merged["exp_price"]
    if "metered_mwh" in merged.columns and "inv_volume_mwh" in merged.columns:
        merged["volume_delta"] = merged["inv_volume_mwh"] - merged["metered_mwh"]
    if "inv_amount" in merged.columns:
        merged["amount_delta"] = merged["inv_amount"] - merged["exp_amount"]

    merged["status"] = [
        _classify(row, abs_tol, rel_tol) for _, row in merged.iterrows()
    ]

    # Display: naive Central, sorted, tidy column order.
    merged["interval_start"] = tz.to_naive_central(merged["interval_start"])
    merged = merged.sort_values(["location", "interval_start"]).reset_index(drop=True)
    cols = [c for c in [
        "interval_start", "location", "inv_price", "exp_price", "price_delta",
        "inv_volume_mwh", "metered_mwh", "volume_delta",
        "inv_amount", "exp_amount", "amount_delta", "status",
    ] if c in merged.columns]
    intervals = merged[cols]

    summary = _summarize(intervals, location, market, volume_source)
    return {"intervals": intervals, "summary": summary}


def _within(delta, expected, abs_tol, rel_tol) -> bool:
    if pd.isna(delta):
        return True  # not compared -> not a failure
    tol = max(abs_tol, rel_tol * abs(expected) if pd.notna(expected) else 0.0)
    return abs(delta) <= tol


def _classify(row, abs_tol, rel_tol) -> str:
    ind = row.get("_merge")
    if ind == "left_only":
        return "extra_in_invoice"      # invoice has it, we have no market price
    if ind == "right_only":
        return "missing_in_invoice"    # we expected it, invoice doesn't bill it
    # both sides present — check each compared field
    if "volume_delta" in row and pd.notna(row.get("volume_delta")):
        if not _within(row["volume_delta"], row.get("metered_mwh"), abs_tol, rel_tol):
            return "volume_mismatch"
    if pd.notna(row.get("price_delta")):
        if not _within(row["price_delta"], row.get("exp_price"), abs_tol, rel_tol):
            return "price_mismatch"
    if pd.notna(row.get("amount_delta")):
        if not _within(row["amount_delta"], row.get("exp_amount"), abs_tol, rel_tol):
            return "amount_mismatch"
    return "match"


def _summarize(intervals: pd.DataFrame, location, market, volume_source) -> dict:
    counts = intervals["status"].value_counts().to_dict()
    inv_total = float(intervals.get("inv_amount", pd.Series(dtype=float)).sum())
    exp_total = float(intervals.get("exp_amount", pd.Series(dtype=float)).sum())
    variance = inv_total - exp_total
    flagged = intervals[~intervals["status"].isin(["match"])]
    worst = (flagged.reindex(
        flagged.get("amount_delta", pd.Series(dtype=float)).abs()
        .sort_values(ascending=False).index)
        .head(10)) if "amount_delta" in intervals.columns else flagged.head(10)
    span = (None if intervals.empty else
            (intervals["interval_start"].min(), intervals["interval_start"].max()))
    return {
        "location": location,
        "market": market,
        "volume_source": volume_source,
        "intervals": int(len(intervals)),
        "status_counts": counts,
        "n_match": int(counts.get("match", 0)),
        "n_flagged": int(len(flagged)),
        "invoiced_total": inv_total,
        "expected_total": exp_total,
        "variance": variance,                       # invoiced - expected ($)
        "variance_pct": (variance / exp_total * 100.0) if exp_total else 0.0,
        "span": span,
        "worst": worst,
    }
