"""Unified data export for the Streamlit app — CSV, Excel, Markdown, PDF.

One entry point, :func:`download_block`, renders a compact format picker + a
single download button next to any table. Bytes for the *selected* format only
are built per run (cheap for large frames), and any format whose optional
dependency is missing is dropped gracefully rather than erroring.

Engines: CSV/Markdown via pandas (+tabulate), Excel via openpyxl, PDF via fpdf2.
"""

from __future__ import annotations

import io

import pandas as pd

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Caps so a huge frame can't blow up a PDF (paginated tables stay legible) or an
# over13-wide PDF spill off the page; CSV/Excel/Markdown are uncapped.
_PDF_MAX_ROWS = 500
_PDF_MAX_COLS = 14
_MD_MAX_ROWS = 5000


def _stamp() -> str:
    # Date.now is fine here (display/file-name only; never persisted state).
    return pd.Timestamp.now().strftime("%Y%m%d-%H%M")


def _meta_lines(meta: dict | None) -> list[str]:
    return [f"{k}: {v}" for k, v in (meta or {}).items()]


# --------------------------------------------------------------------------- #
# Per-format encoders
# --------------------------------------------------------------------------- #

def to_csv_bytes(df: pd.DataFrame, **_) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_excel_bytes(df: pd.DataFrame, *, sheet_name: str = "data",
                   meta: dict | None = None, **_) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        if meta:
            (pd.DataFrame(list(meta.items()), columns=["field", "value"])
             .to_excel(xl, sheet_name="about", index=False))
        df.to_excel(xl, sheet_name=str(sheet_name)[:31] or "data", index=False)
    return buf.getvalue()


def to_markdown_bytes(df: pd.DataFrame, *, title: str | None = None,
                     meta: dict | None = None, **_) -> bytes:
    out: list[str] = []
    if title:
        out += [f"# {title}", ""]
    for line in _meta_lines(meta):
        out.append(f"- **{line}**")
    if meta:
        out.append("")
    body, note = df, ""
    if len(df) > _MD_MAX_ROWS:
        body = df.head(_MD_MAX_ROWS)
        note = f"\n\n_Showing first {_MD_MAX_ROWS:,} of {len(df):,} rows._"
    out.append(body.to_markdown(index=False))
    return ("\n".join(out) + note).encode("utf-8")


# fpdf2's built-in Helvetica is latin-1 only; map common unicode to ASCII and
# replace anything else so a stray "×"/"≈"/emoji in the data can't crash the PDF.
_PDF_SUBS = str.maketrans({"—": "-", "–": "-", "…": "...", "×": "x", "≈": "~",
                           "•": "-", "→": "->", "≥": ">=", "≤": "<=", "’": "'",
                           "“": '"', "”": '"', " ": " ", "\xa0": " "})


def _pdf_safe(s) -> str:
    return str(s).translate(_PDF_SUBS).encode("latin-1", "replace").decode("latin-1")


def _pdf_cell(x) -> str:
    if pd.isna(x):
        return ""
    s = f"{x:,.2f}" if isinstance(x, float) else str(x)
    s = s if len(s) <= 30 else s[:29] + "..."
    return _pdf_safe(s)


def to_pdf_bytes(df: pd.DataFrame, *, title: str | None = None,
                meta: dict | None = None, **_) -> bytes:
    from fpdf import FPDF  # optional dep; caller catches ImportError

    clipped_rows = len(df) > _PDF_MAX_ROWS
    clipped_cols = df.shape[1] > _PDF_MAX_COLS
    d = df.head(_PDF_MAX_ROWS).iloc[:, :_PDF_MAX_COLS]

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    if title:
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)
    for line in _meta_lines(meta):
        pdf.cell(0, 5, _pdf_safe(line), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, _pdf_safe(f"{len(df):,} rows x {df.shape[1]} cols - exported {_stamp()}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    headers = [_pdf_safe(c) for c in d.columns]
    rows = [[_pdf_cell(v) for v in r] for r in d.itertuples(index=False, name=None)]
    pdf.set_font("Helvetica", size=7)
    with pdf.table(line_height=4.2, cell_fill_color=235, cell_fill_mode="ROWS",
                   first_row_as_headings=True) as table:
        hr = table.row()
        for h in headers:
            hr.cell(h)
        for r in rows:
            tr = table.row()
            for c in r:
                tr.cell(c)

    if clipped_rows or clipped_cols:
        pdf.ln(1)
        pdf.set_font("Helvetica", "I", 7)
        bits = []
        if clipped_rows:
            bits.append(f"first {_PDF_MAX_ROWS:,} of {len(df):,} rows")
        if clipped_cols:
            bits.append(f"first {_PDF_MAX_COLS} of {df.shape[1]} columns")
        pdf.cell(0, 4, "PDF shows " + " and ".join(bits)
                 + " - use CSV/Excel for the full table.", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# format key -> (label, encoder, extension, mime)
_FORMATS = {
    "CSV": (to_csv_bytes, "csv", "text/csv"),
    "Excel": (to_excel_bytes, "xlsx", EXCEL_MIME),
    "Markdown": (to_markdown_bytes, "md", "text/markdown"),
    "PDF": (to_pdf_bytes, "pdf", "application/pdf"),
}


def _available(formats) -> list[str]:
    out = []
    for f in formats:
        if f == "Excel":
            try:
                import openpyxl  # noqa: F401
            except Exception:  # noqa: BLE001
                continue
        if f == "Markdown":
            try:
                import tabulate  # noqa: F401
            except Exception:  # noqa: BLE001
                continue
        if f == "PDF":
            try:
                import fpdf  # noqa: F401
            except Exception:  # noqa: BLE001
                continue
        out.append(f)
    return out


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

def download_block(st, df: pd.DataFrame, *, name: str, title: str | None = None,
                   meta: dict | None = None,
                   formats=("CSV", "Excel", "Markdown", "PDF"),
                   container=None, key: str | None = None) -> None:
    """Render a format picker + download button for ``df``.

    ``name`` seeds the download file name; ``title``/``meta`` enrich the Markdown
    and PDF headers (``meta`` also becomes an 'about' sheet in Excel). Only the
    chosen format's bytes are built per run. Formats whose optional dependency is
    missing are silently dropped.
    """
    target = container or st
    if df is None or len(df) == 0:
        target.caption("Nothing to export yet.")
        return

    avail = _available(formats)
    if not avail:
        return
    key = key or name
    # No st.columns here — keep it nestable inside callers' column layouts.
    fmt = target.radio("⬇ Export as", avail, horizontal=True, key=f"{key}_fmt")
    encoder, ext, mime = _FORMATS[fmt]
    try:
        data = encoder(df, title=title, meta=meta, sheet_name=name)
    except Exception as exc:  # noqa: BLE001 — never let an export crash the page
        target.warning(f"{fmt} export unavailable: {exc}")
        return
    target.download_button(f"⬇ Download {fmt}", data,
                           file_name=f"{name}_{_stamp()}.{ext}", mime=mime,
                           key=f"{key}_dl")
