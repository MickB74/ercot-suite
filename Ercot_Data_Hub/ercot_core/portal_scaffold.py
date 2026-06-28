"""Generate a standalone single-asset settlement portal from the Markum template.

The Markham and Azure Sky portals are small Streamlit apps that reuse this Hub's
``ercot_core`` engine and cached node data to settle one asset against a VPPA/CfD
contract. They're all the same shape, so a new one is "the Markum portal with a
different asset + contract baked in."

``create_portal`` clones ``ERCOT_Markum`` to a sibling ``ERCOT_<Slug>`` folder and
rewrites the identity: the Python package name, the hardcoded resource node/unit,
the ``ASSET`` facts in ``contract.py``, the contract strike, the branding name, and
the launcher scripts. The result is a runnable portal pointed at the new node —
it reads the data this Hub already pulled, so it works offline immediately.

No new dependency on the portal: this module only reads the template and writes a
new folder; nothing here is imported by the portals themselves.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
from pathlib import Path

from ercot_core import paths

# Suite root holds all the repos (Ercot_Data_Hub, ERCOT_Markum, …).
SUITE_ROOT = paths.ROOT.parent
TEMPLATE_DIR = SUITE_ROOT / "ERCOT_Markum"
TEMPLATE_PKG = "markum"          # the template's package dir / import token
NEW_PKG = "portal"              # every generated portal uses this stable package name

# Markum identity tokens we replace wholesale.
TPL_NODE = "MRKM_SLR_RN"         # template resource node
TPL_UNIT = "MRKM_SLR_PV1"        # template primary SCED unit
TPL_ENV = "MARKUM_HUB_ROOT"      # template hub-root env var

# Files we never copy into the new portal.
_IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", "*.pyc", "data", "node_modules",
    "config.json", "settings.json")

# Text files we rewrite tokens in.
_TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".txt", ".command", ".cfg", ".ini"}

_HUB_CODE = {"North": "HB_NORTH", "Houston": "HB_HOUSTON", "South": "HB_SOUTH",
             "West": "HB_WEST", "Pan": "HB_PAN"}

_QUEUE_OWNERSHIP = SUITE_ROOT / "Ercot_Data_Hub/ercot_core/registry/queue_ownership.json"


def _lookup_parties(project_name: str) -> dict:
    """Search queue_ownership.json for offtaker + developer by project name.

    Returns ``{"offtaker": str, "developer": str}`` — both empty strings when
    nothing matches or the file is absent. The developer is the first named
    entity in the ``owners`` field; the offtaker is extracted from ``vppa``
    (empty when the counterparty is not publicly named).
    """
    if not _QUEUE_OWNERSHIP.exists():
        return {"offtaker": "", "developer": ""}
    try:
        ownership = json.loads(_QUEUE_OWNERSHIP.read_text())
    except Exception:
        return {"offtaker": "", "developer": ""}

    needle = str(project_name).lower()
    best = None
    for entry in ownership.values():
        pname = str(entry.get("project_name", "")).lower()
        # score: full-name contains > any word match
        if pname and needle in pname or pname in needle:
            best = entry
            break
        if any(w in pname for w in needle.split() if len(w) > 3):
            best = entry

    if best is None:
        return {"offtaker": "", "developer": ""}

    # Developer — first entity before ";" or "(" in owners
    owners_raw = str(best.get("owners", "") or "")
    developer = owners_raw.split(";")[0].split("(")[0].strip().rstrip(",")

    # Offtaker — skip if "not publicly named", else first company in vppa
    vppa_raw = str(best.get("vppa", "") or "")
    if not vppa_raw or "not publicly named" in vppa_raw.lower():
        offtaker = ""
    else:
        # collect named companies (capital-letter words before MW/yr markers)
        import re as _re
        companies = _re.findall(
            r"([A-Z][A-Za-z0-9&\-\./ ]+?)(?=\s+\d|\s+VPPA|\s+PPA|\s*;|\s*\(|\s*$)",
            vppa_raw)
        cleaned, seen = [], set()
        for c in companies:
            c = c.strip().rstrip(",. ")
            if c and len(c) > 2 and c not in seen:
                seen.add(c); cleaned.append(c)
        offtaker = "; ".join(cleaned[:6])

    return {"offtaker": offtaker, "developer": developer}


def slugify(name: str) -> str:
    """'Blue Jay Solar' -> 'Blue_Jay_Solar' (folder-safe, Title_Case)."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", str(name)) if p]
    return "_".join(parts) or "Project"


def hub_code(hub: str) -> str:
    h = str(hub or "").strip()
    if h.upper().startswith("HB_"):
        return h.upper()
    return _HUB_CODE.get(h.title(), "HB_NORTH")


def _pylit(value) -> str:
    """A valid **Python** literal for a scalar (None→'None', not JSON 'null')."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return json.dumps(value)  # double-quoted; valid in both Python and JSON
    return repr(value)  # int / float


def _set_field(text: str, key: str, value) -> str:
    """Replace the value of ``"key": <value>,`` in a Python dict literal (keeps comments).

    Emits a Python literal (so ``None`` stays ``None``, never JSON ``null``). The
    matcher accepts a full quoted string (values with commas, e.g. ``"Bosque, TX"``,
    are replaced whole) or a bare scalar token.
    """
    lit = _pylit(value)
    val = r'"(?:[^"\\]|\\.)*"|[^,\n]+'
    # Use a callable replacement: ``lit`` may contain backslash escapes (e.g.
    # json.dumps emits ``\uXXXX`` for non-ASCII like em-dashes / "Ørsted"),
    # which re.sub would try to interpret in a template string ("bad escape \u").
    return re.sub(rf'("{re.escape(key)}":\s*)(?:{val})',
                  lambda m: m.group(1) + lit, text, count=1)


def _add_asset_fields(text: str, fields: dict) -> str:
    """Insert new ``"key": value,`` lines just before the ASSET dict's closing brace.

    For keys the solar template's ASSET doesn't have (e.g. wind turbine specs). The
    ASSET literal has no nested braces, so the first ``\\n}`` closes it.
    """
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    if not fields:
        return text
    block = "".join(f'    "{k}": {_pylit(v)},\n' for k, v in fields.items())
    # Callable replacement (see _set_field): ``block`` can carry json \uXXXX
    # escapes that a template replacement would choke on ("bad escape \u").
    return re.sub(r"(ASSET\s*=\s*\{.*?\n)(\})",
                  lambda m: m.group(1) + block + m.group(2), text,
                  count=1, flags=re.DOTALL)


def portal_dest(project_name: str) -> Path:
    return SUITE_ROOT / f"ERCOT_{slugify(project_name)}"


def create_portal(asset: dict, *, strike: float, structure: str = "VPPA / CfD",
                  counterparty: str = "Customer", offtaker: str | None = None,
                  developer: str | None = None, overwrite: bool = False) -> dict:
    """Scaffold a new single-asset portal next to the Hub. Returns paths + hints.

    ``asset`` is a curated-registry record (Project Builder shape): ``resource_name``
    is the resource node, ``sced_units`` the underlying units. Raises on missing
    template / existing destination (unless ``overwrite``).
    """
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"Portal template not found at {TEMPLATE_DIR}")

    project_name = str(asset.get("project_name") or asset.get("resource_name") or "Project")

    # Auto-lookup offtaker + developer from queue_ownership.json when not supplied.
    if offtaker is None or developer is None:
        parties = _lookup_parties(project_name)
        if offtaker is None:
            offtaker = parties["offtaker"]
        if developer is None:
            developer = parties["developer"]
    node = str(asset.get("resource_name") or "").strip()
    if not node:
        raise ValueError("Asset has no resource_name (node) to point the portal at.")
    units = [str(u) for u in (asset.get("sced_units") or [node])]
    primary_unit = units[0]
    is_solar = str(asset.get("tech", "")).strip().lower().startswith("sol")

    dest = portal_dest(project_name)
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"{dest} already exists — pick a different name or remove it.")
        shutil.rmtree(dest)

    # 1) Clone the template (minus venv/data/customer config).
    try:
        shutil.copytree(TEMPLATE_DIR, dest, ignore=_IGNORE)

        # 2) Token-rewrite every text file. Order matters: specific names before the
        #    generic lowercase package token. Branding strings ("Markum"/"Markham")
        #    become the project name; the lowercase import token becomes NEW_PKG.
        slug_upper = slugify(project_name).upper()
        replacements = [
            (TPL_NODE, node),
            (TPL_UNIT, primary_unit),
            (TPL_ENV, f"{slug_upper}_HUB_ROOT"),
            ("Markum Solar", project_name),
            ("Markham Solar", project_name),
            ("Markum", project_name),
            ("Markham", project_name),
            (TEMPLATE_PKG, NEW_PKG),  # lowercase 'markum' import token + CSS classes
        ]
        for f in dest.rglob("*"):
            if f.is_file() and f.suffix.lower() in _TEXT_SUFFIXES:
                try:
                    txt = f.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                for old, new in replacements:
                    txt = txt.replace(old, new)
                f.write_text(txt)

        # 2b) Rename the package dir (contents already rewritten) so the paths below resolve.
        (dest / TEMPLATE_PKG).rename(dest / NEW_PKG)

        # 3) Rewrite the ASSET facts + strike in contract.py (token pass already fixed
        #    the names/node; here we set the numeric/identity fields from the registry).
        contract_py = dest / NEW_PKG / "contract.py"
        c = contract_py.read_text()
        c = _set_field(c, "project_name", project_name)
        c = _set_field(c, "resource_node", node)
        c = _set_field(c, "resource_name", primary_unit)
        c = _set_field(c, "capacity_mw", float(asset.get("capacity_mw") or 0.0))
        c = _set_field(c, "tech", "Solar PV" if is_solar else "Wind")
        c = _set_field(c, "hub", hub_code(asset.get("hub")))
        c = _set_field(c, "county", str(asset.get("county") or ""))
        c = _set_field(c, "lat", float(asset.get("lat") or 0.0))
        c = _set_field(c, "lon", float(asset.get("lon") or 0.0))
        # Solar-only attributes: keep for solar, blank for wind so contract.py is honest.
        c = _set_field(c, "dc_ac_ratio", float(asset.get("dc_ac_ratio") or 1.3) if is_solar else None)
        c = _set_field(c, "tracking_type",
                       asset.get("tracking_type", "single_axis") if is_solar else None)
        c = _set_field(c, "eia_plant_id", asset.get("eia_plant_id") if asset.get("eia_plant_id") else None)
        c = _set_field(c, "eia_prime_mover", "PV" if is_solar else None)
        c = _set_field(c, "strike", float(strike))
        c = _set_field(c, "structure", structure)
        c = _set_field(c, "counterparty", counterparty)
        c = _set_field(c, "offtaker", str(offtaker or ""))
        c = _set_field(c, "developer", str(developer or ""))
        # Wind: carry turbine specs into the ASSET so the hero/contract show real facts.
        if not is_solar:
            c = _add_asset_fields(c, {k: asset.get(k) for k in
                                      ("turbine_model", "turbine_manuf",
                                       "hub_height_m", "rotor_diameter_m")})
        contract_py.write_text(c)

        # 4) Auto-select the settlement hub via price correlation (falls back to distance).
        lat = float(asset.get("lat") or 0.0) or None
        lon = float(asset.get("lon") or 0.0) or None
        settle_hub: str | None = None
        hub_method: str = "none"
        try:
            from ercot_core import hub_affinity  # noqa: PLC0415
            if lat and lon:
                try:
                    aff = hub_affinity.best_hub(node, lat=lat, lon=lon)
                except ValueError:
                    # No cached prices yet — fall back to geography
                    aff = hub_affinity.best_hub_by_distance(lat, lon)
            else:
                aff = hub_affinity.best_hub(node)
            settle_hub = aff["hub"]
            hub_method = aff["method"]
        except Exception:  # noqa: BLE001 — affinity is best-effort; never block portal creation
            pass

        # 4b) Write a fresh customer config.json with the contract terms + settlement hub.
        cfg: dict = {
            "structure": structure,
            "strike": float(strike),
            "volume_share_pct": 100.0,
            "counterparty": counterparty,
            "offtaker": str(offtaker or ""),
            "developer": str(developer or ""),
            "eia_plant_id": asset.get("eia_plant_id") or None,
        }
        if settle_hub:
            cfg["settle_at"] = "hub"
            cfg["settle_point"] = settle_hub
        (dest / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")

        # 5) Rename launcher scripts that carry the old name in their filename.
        for cmd in list(dest.glob("*.command")):
            new_name = cmd.name.replace("Markum", project_name).replace("Markham", project_name)
            if new_name != cmd.name:
                cmd.rename(dest / new_name)

        # 6) Give the portal its OWN port. The template launcher is pinned to the
        #    Markum port (8502); without rewriting it, every generated portal collides
        #    on that port and double-clicking just opens whatever app already holds it.
        port = next_portal_port()
        for cmd in dest.glob("*.command"):
            t = cmd.read_text()
            t2 = re.sub(r"PORT=\d+", f"PORT={port}", t)
            if t2 != t:
                cmd.write_text(t2)

        return {
            "path": str(dest),
            "name": project_name,
            "node": node,
            "package": NEW_PKG,
            "port": port,
            "launch": ".venv/bin/streamlit run app/Home.py",
            "settle_hub": settle_hub,
            "settle_hub_method": hub_method,
            # The Control Tower registry is a hand-curated list — paste this in:
            "registry_hint": (f'{{"name": "{project_name}", "dir": "{dest.name}", '
                              f'"port": {port}}}  # add to app/views/home.py _PORTALS'),
        }
    except Exception:
        # Don't leave a half-built portal behind on any failure.
        shutil.rmtree(dest, ignore_errors=True)
        raise


def next_portal_port() -> int:
    """Next free settlement-portal port: one past the highest in the Control Tower
    registry (app/views/home.py _PORTALS). Falls back to 8502."""
    home = Path(__file__).resolve().parents[1] / "app" / "views" / "home.py"
    try:
        ports = [int(m) for m in re.findall(r'"port":\s*(\d+)', home.read_text())]
    except Exception:
        ports = []
    return (max(ports) + 1) if ports else 8502


# --------------------------------------------------------------------------
# Discover + launch portals
# --------------------------------------------------------------------------
def list_portals() -> list[dict]:
    """Sibling single-asset portals (incl. the Markum/Azure Sky originals).

    A portal = an ``ERCOT_*`` folder with ``app/Home.py`` and a package holding
    ``contract.py``. The Data Hub itself has no ``*/contract.py``, so it's excluded.
    """
    out = []
    for d in sorted(SUITE_ROOT.glob("ERCOT_*")):
        if not d.is_dir() or not (d / "app" / "Home.py").exists():
            continue
        pkgs = [p.parent.name for p in d.glob("*/contract.py")]
        if not pkgs:
            continue
        node = None
        try:  # surface the node it settles for (nice in the list)
            m = re.search(r'"resource_node":\s*"([^"]+)"',
                          (d / pkgs[0] / "contract.py").read_text())
            node = m.group(1) if m else None
        except OSError:
            pass
        out.append({"name": d.name, "path": str(d), "package": pkgs[0], "node": node})
    return out


def free_port(start: int = 8600, end: int = 8699) -> int:
    """First TCP port in [start, end] nothing is listening on (for a fresh portal)."""
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No free port in {start}-{end}.")


def launch_portal(path: str | Path, port: int | None = None) -> dict:
    """Start a portal as a detached Streamlit process on a free port.

    Runs with THIS Hub's venv (it has the deps and is the engine the portal
    imports), surviving the parent. Returns ``{port, url, pid, log}``.
    """
    path = Path(path)
    if not (path / "app" / "Home.py").exists():
        raise FileNotFoundError(f"{path} doesn't look like a portal (no app/Home.py).")
    streamlit = paths.ROOT / ".venv" / "bin" / "streamlit"
    if not streamlit.exists():
        raise FileNotFoundError(f"Hub Streamlit not found at {streamlit}.")
    port = port or free_port()
    log = open(path / ".run.log", "ab")  # noqa: SIM115 — handed to the child process
    proc = subprocess.Popen(
        [str(streamlit), "run", "app/Home.py", "--server.port", str(port),
         "--server.headless", "true"],
        cwd=str(path), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"port": port, "url": f"http://localhost:{port}", "pid": proc.pid,
            "log": str(path / ".run.log")}


def pids_on_port(port: int) -> list[int]:
    """PIDs listening on a TCP port (via lsof). Empty if none / lsof missing."""
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def is_running(port: int) -> bool:
    return bool(pids_on_port(port))


def stop_portal(port: int | None = None, pid: int | None = None) -> list[int]:
    """Stop a launched portal. Kills the process group for ``pid`` and/or anything
    listening on ``port``. Returns the PIDs it signalled."""
    killed: list[int] = []
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if port:
        for p in pids_on_port(port):
            try:
                os.kill(p, signal.SIGTERM)
                killed.append(p)
            except OSError:
                pass
    return killed
