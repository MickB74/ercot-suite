"""Small persisted app settings that aren't contract terms.

Kept separate from :mod:`portal.contract` (which owns the deal) so the two
concerns don't tangle. Stored in a git-ignored ``settings.json`` next to the
app. Currently just the optional **linked statement folder** the Invoice Audit
page reads from, so a customer can point at a directory of settlement statements
and pick / batch-audit them without uploading each time.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "settings.json"

DEFAULTS = {
    "invoice_folder": "",   # absolute path to a folder of settlement statements
}

# Statement file types the Invoice Audit page can read.
STATEMENT_SUFFIXES = (".csv", ".xlsx", ".xls", ".xlsb", ".pdf")


def load() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text())
            if isinstance(saved, dict):
                s.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:  # noqa: BLE001 — a broken settings file shouldn't break the app
            pass
    return s


def save(values: dict) -> None:
    clean = {k: values[k] for k in DEFAULTS if k in values}
    SETTINGS_PATH.write_text(json.dumps(clean, indent=2) + "\n")


def get_invoice_folder_str() -> str:
    return str(load().get("invoice_folder", "") or "")


def set_invoice_folder(path: str) -> None:
    s = load()
    s["invoice_folder"] = str(path or "").strip()
    save(s)


def invoice_folder() -> Path | None:
    """The linked folder as a Path if it's set and a real directory, else None."""
    raw = get_invoice_folder_str()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def list_statements(folder: Path | None) -> list[Path]:
    """Statement files (CSV/Excel) in the linked folder, newest first."""
    if folder is None:
        return []
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in STATEMENT_SUFFIXES
             and not p.name.startswith((".", "~$"))]   # skip hidden / Office lock files
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
