"""Robust reader for real-world settlement statements / invoices.

Customer bills are messy: title rows, a date-range banner, a ``(D) (E) (D-E)``
formula-annotation row, the *real* column header buried several rows down, then
the data, sometimes followed by totals/footer rows — and occasionally the data
sits on the second sheet of a workbook. A naive ``pd.read_csv``/``read_excel``
reads row 0 as the header (so columns come through as ``Unnamed: 1`` …) and the
strict timestamp parse later trips over a label cell like ``Seller:``.

:func:`load_clean` finds the data without any of that guesswork from the user:

  1. read the whole grid with no header assumption (every sheet, for Excel);
  2. score each column by how many cells parse as a real timestamp, and pick the
     column + the **longest contiguous run** of timestamp rows as the data block
     (this rejects an isolated date-range banner above the table);
  3. take the nearest text-ish row above the block as the column header;
  4. return just the data rows, properly named, plus the detected time column.

It always degrades gracefully: if it can't confidently find a timestamp block
it falls back to a plain header-row-0 read, so it never does worse than before.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

import pandas as pd

# A cell is "timestamp-like" only if, as text, it has a digit AND a date/time
# separator. This deliberately rejects plain numbers ("17.98") so a price column
# is never mistaken for a timestamp column.
_SEP = re.compile(r"[-/:]")
_DIGIT = re.compile(r"\d")
_ALPHA = re.compile(r"[A-Za-z]")


def _parsed_dt(col: pd.Series) -> pd.Series:
    """Vectorised: the column parsed to datetimes, NaT where not timestamp-like.

    A bare ``datetime.time`` (time-of-day with no date) is NOT a real interval
    timestamp — Excel stores trailing/footer "00:00" padding rows that way — so
    those are forced to NaT (otherwise ``str(time)`` like "00:00:00" would parse
    to *today's* date and pollute the data block / crash the strict parse).
    """
    col = col.map(lambda v: None if isinstance(v, dt.time) else v)
    s = col.astype("string").str.strip()
    dtlike = s.str.contains(_DIGIT, na=False) & s.str.contains(_SEP, na=False)
    return pd.to_datetime(s.where(dtlike), errors="coerce", format="mixed")


def _longest_true_run(mask: list[bool]) -> tuple[int, int] | None:
    """(start, end_inclusive) of the longest run of True in ``mask``, or None."""
    best = cur = None
    for i, v in enumerate(mask):
        if v:
            cur = (cur[0], i) if cur else (i, i)
            if best is None or (cur[1] - cur[0]) > (best[1] - best[0]):
                best = cur
        else:
            cur = None
    return best


def _is_blank(v) -> bool:
    return (v is None or (isinstance(v, float) and pd.isna(v))
            or (isinstance(v, str) and v.strip() == ""))


def _drop_blank_columns(body: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that are entirely empty (ragged trailing cols like 'Column 14').

    Such all-NaN/blank object columns also break Streamlit's Arrow rendering, so
    removing them keeps both the audit and the on-screen preview clean.
    """
    keep = [c for c in body.columns if not body[c].map(_is_blank).all()]
    return body[keep] if keep else body


def _header_name(value, i: int) -> str:
    if isinstance(value, str) and _ALPHA.search(value):
        return value.strip()
    if value is not None and not (isinstance(value, float) and pd.isna(value)):
        txt = str(value).strip()
        if txt and txt.lower() != "nan" and _ALPHA.search(txt):
            return txt
    return f"Column {i + 1}"


def _dedupe(names: list[str]) -> list[str]:
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _csv_grid(src) -> pd.DataFrame:
    """Read a CSV into a header-less grid, tolerant of ragged rows / preamble.

    pandas' C parser fixes the column count from the first line and errors on a
    bill whose rows vary in width; the ``csv`` module just yields lists, which we
    pad to the widest row. Accepts a path or an uploaded file-like buffer.
    """
    if hasattr(src, "read"):
        data = src.read()
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig", errors="replace")
        text = data
    else:
        with open(src, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            text = f.read()
    rows = list(csv.reader(io.StringIO(text)))
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows)


def _grids(src, name: str) -> list[pd.DataFrame]:
    """Every sheet (Excel) / the file (CSV) as a header-less object grid."""
    if str(name).lower().endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(src, header=None, dtype=object, sheet_name=None)
        return list(sheets.values())
    return [_csv_grid(src)]


def _detect_one(grid: pd.DataFrame):
    """Best (df, info) for one grid, or None if no timestamp block is found."""
    if grid is None or grid.empty:
        return None
    grid = grid.reset_index(drop=True)

    # Score columns by parseable-timestamp count; pick the best.
    best_col, best_parsed, best_score = None, None, 0
    for c in grid.columns:
        parsed = _parsed_dt(grid[c])
        score = int(parsed.notna().sum())
        if score > best_score:
            best_col, best_parsed, best_score = c, parsed, score
    if best_col is None or best_score < 3:
        return None

    # Data = the longest contiguous run of timestamp rows in that column
    # (rejects a lone date-range banner sitting above the real table).
    run = _longest_true_run(list(best_parsed.notna()))
    if run is None or (run[1] - run[0] + 1) < 3:
        return None
    first, last = run
    rows = list(range(first, last + 1))

    # Header = nearest row above the block with ≥2 alphabetic cells.
    header_row = None
    for r in range(first - 1, -1, -1):
        text_cells = sum(1 for v in grid.loc[r]
                         if isinstance(v, str) and _ALPHA.search(v))
        if text_cells >= 2:
            header_row = r
            break

    body = grid.loc[rows].reset_index(drop=True)
    if header_row is not None:
        names = _dedupe([_header_name(grid.loc[header_row, c], i)
                         for i, c in enumerate(grid.columns)])
    else:
        names = [f"Column {i + 1}" for i in range(grid.shape[1])]
    body.columns = names
    time_col_name = names[list(grid.columns).index(best_col)]
    body = _drop_blank_columns(body)

    info = {
        "method": "detected",
        "header_row": header_row,
        "time_col": time_col_name,
        "n_rows": len(body),
        "n_skipped": int(len(grid) - len(body)),
    }
    return body, info


def load_clean(src, name: str):
    """Return ``(DataFrame, info)`` — a tidy statement and what was detected.

    ``info`` keys: ``method`` ("detected" | "raw"), ``time_col`` (best-guess
    timestamp column or None), ``header_row``, ``n_rows``, ``n_skipped``, and
    ``sheets`` (how many sheets were scanned).
    """
    grids = _grids(src, name)
    candidates = [d for d in (_detect_one(g) for g in grids) if d is not None]
    if candidates:
        # Prefer the sheet/grid with the most detected data rows.
        body, info = max(candidates, key=lambda d: d[1]["n_rows"])
        info["sheets"] = len(grids)
        return body, info

    # Fallback: no timestamp block confidently found. Promote the first text-ish
    # row of the first grid to a header (never re-reads a consumed buffer).
    grid = grids[0].reset_index(drop=True) if grids else pd.DataFrame()
    if grid.empty:
        return grid, {"method": "raw", "time_col": None, "header_row": None,
                      "n_rows": 0, "n_skipped": 0, "sheets": len(grids)}
    hr = 0
    for r in range(min(len(grid), 15)):
        if sum(1 for v in grid.loc[r] if isinstance(v, str) and _ALPHA.search(v)) >= 2:
            hr = r
            break
    body = grid.loc[hr + 1:].reset_index(drop=True)
    body.columns = _dedupe([_header_name(grid.loc[hr, c], i)
                            for i, c in enumerate(grid.columns)])
    body = _drop_blank_columns(body)
    return body, {"method": "raw", "time_col": None, "header_row": hr,
                  "n_rows": len(body), "n_skipped": hr, "sheets": len(grids)}


# --------------------------------------------------------------------------- #
# Column-mapping helpers — keep a $/MWh rate column from being read as "volume"
# --------------------------------------------------------------------------- #

# Tokens that mark a column as money / per-unit-rate rather than an energy qty.
_MONEYISH = ("$/mwh", "/mwh", "price", "rate", "settlement", "payment", "charge",
             "cost", "amount", "revenue")


def _is_moneyish(name: str) -> bool:
    n = str(name).lower()
    return ("$" in n) or any(t in n for t in _MONEYISH)


def _is_volumeish(name: str) -> bool:
    n = str(name).lower()
    if _is_moneyish(n):
        return False
    return any(t in n for t in ("mwh", "volume", "quantity", "qty", "energy",
                                "generation", "output", "metered", "produced",
                                "production"))


def refine_mapping(columns, guess: dict) -> dict:
    """Fix the common auto-map blunder: a ``$/MWh`` column chosen as ``volume``.

    The shared heuristic keys on the substring ``mwh``, so a "Net Settlement
    $/MWh" rate column gets picked as the energy volume — which makes the
    expected ``price × volume`` amount nonsense. Here we reject money/rate
    columns for the volume role and prefer a real generation/energy column.
    """
    g = dict(guess)
    cols = list(columns)
    vol = g.get("volume_col")
    if vol is None or _is_moneyish(vol):
        gen_first = [c for c in cols if _is_volumeish(c)
                     and any(t in str(c).lower() for t in
                             ("generation", "output", "metered", "produced", "production"))]
        cand = gen_first[0] if gen_first else next((c for c in cols if _is_volumeish(c)), None)
        if cand is not None:
            g["volume_col"] = cand
    return g


def vppa_mapping(columns) -> dict:
    """Best-guess mapping for a VPPA **net-settlement** statement.

    Picks the market (floating) price, the generation volume, and the net
    settlement **$** column (not the per-MWh rate, not the cumulative running
    total). The UI still lets the user correct any of it.
    """
    cols = list(columns)
    low = {c: str(c).strip().lower() for c in cols}

    def find(pred):
        return next((c for c in cols if pred(low[c])), None)

    price = (find(lambda n: "floating" in n and "pay" not in n)
             or find(lambda n: any(t in n for t in ("rt_", "real", "market", "spp", "lmp"))
                     and "pay" not in n)
             or find(lambda n: "price" in n and "fixed" not in n and "pay" not in n))
    # Prefer the OFFTAKER'S share volume (e.g. "<Buyer> Net Output (MWh)") over the
    # full "Plant Generation" column — the net settlement is computed on the share.
    volume = (find(lambda n: _is_volumeish(n) and "net" in n
                   and any(t in n for t in ("output", "volume", "mwh")) and "plant" not in n)
              or find(lambda n: _is_volumeish(n) and "output" in n and "plant" not in n)
              or find(lambda n: _is_volumeish(n)
                      and any(t in n for t in ("generation", "output", "metered", "produced", "production")))
              or find(lambda n: _is_volumeish(n)))
    amount = (find(lambda n: "net" in n and "settlement" in n and "$" in n
                   and "/mwh" not in n and "cumul" not in n)
              or find(lambda n: "net" in n and ("$" in n or "amount" in n)
                      and "/mwh" not in n and "cumul" not in n)
              or find(lambda n: "settlement" in n and "$" in n and "/mwh" not in n and "cumul" not in n))
    return {"time_col": None, "price_col": price, "volume_col": volume,
            "amount_col": amount, "time_basis": "ending", "interval": "15min",
            "volume_unit": "MWh"}


def read_pdf_summary(src, name: str) -> dict:
    """Extract the monthly totals from a Millipore PDF **summary invoice**.

    The PDF invoices are not interval statements — they carry one line each for
    the fixed and floating legs plus the invoice total. Returns a dict with
    ``period_start/period_end`` (MM.DD.YYYY), ``volume_mwh``, ``fixed_rate``,
    ``floating_rate``, ``fixed_payment``, ``floating_payment``, ``net_total``
    (a trailing ``-`` in the PDF means a credit, parsed as negative). Missing
    keys mean that field/format wasn't recognised — the caller should check.
    """
    import io
    import pdfplumber  # noqa: PLC0415

    if hasattr(src, "read"):
        data = src.read()
        buf = io.BytesIO(data if isinstance(data, (bytes, bytearray)) else data.encode())
    else:
        buf = src
    with pdfplumber.open(buf) as pdf:
        txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages)

    def _num(s):
        s = s.strip().replace(",", "")
        neg = s.endswith("-")
        try:
            v = float(s.rstrip("-"))
        except ValueError:
            return None
        return -v if neg else v

    out: dict = {}
    m = re.search(r"Invoice Period:\s*([\d.]+)\s*-\s*([\d.]+)", txt)
    if m:
        out["period_start"], out["period_end"] = m.group(1), m.group(2)
    mf = re.search(r"FIXED RATE\D*([\d,]+\.\d+)\s*MWh\s*([\d,]+\.\d+)\s*USD\s*([\d,]+\.\d+-?)\s*USD", txt)
    if mf:
        out["volume_mwh"] = _num(mf.group(1)); out["fixed_rate"] = _num(mf.group(2))
        out["fixed_payment"] = _num(mf.group(3))
    mfl = re.search(r"FLOATING RATE\D*([\d,]+\.\d+)\s*MWh\s*([\d,]+\.\d+)\s*USD\s*([\d,]+\.\d+-?)\s*USD", txt)
    if mfl:
        out["floating_rate"] = _num(mfl.group(2)); out["floating_payment"] = _num(mfl.group(3))
    mt = re.search(r"INVOICE TOTAL\s*([\d,]+\.\d+-?)\s*USD", txt)
    if mt:
        out["net_total"] = _num(mt.group(1))
    return out


def drop_unparseable_times(df: pd.DataFrame, time_col: str) -> tuple[pd.DataFrame, int]:
    """Drop rows whose ``time_col`` isn't a real timestamp; return (df, n_dropped).

    A safety net for the reconciler's strict parse: even after header detection,
    a stray totals/label row inside the block (e.g. ``Seller:``) would crash
    ``load_invoice``. Filtering here keeps a single bad cell from sinking the
    whole audit.
    """
    if not time_col or time_col not in df.columns:
        return df, 0
    keep = _parsed_dt(df[time_col]).notna()
    return df[keep].reset_index(drop=True), int((~keep).sum())
