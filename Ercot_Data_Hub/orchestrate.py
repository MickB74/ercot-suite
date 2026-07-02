#!/usr/bin/env python3
"""ERCOT Data Hub orchestrator.

One entry point to refresh any (or every) dataset. Each dataset keeps its own
proven updater script; the orchestrator just runs them — as subprocesses of the
hub's single interpreter, with the repo root on PYTHONPATH so ``ercot_core``
resolves and the shared SCED/SPP caches and the one config.json are used. A
crash in one job can't take down the others (or the UI that calls this).

Usage:
    python orchestrate.py status                 # snapshot of all datasets
    python orchestrate.py update                 # update all datasets
    python orchestrate.py update hub_prices       # update one
    python orchestrate.py update system_gen eia923
    python orchestrate.py list                    # show jobs

The unified Streamlit app (app/Home.py) calls run_job()/stream_job() directly
to drive these same jobs with live log output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
HERE = Path(__file__).resolve()
UPDATE_LOG_DIR = ROOT / "logs" / "updates"   # detached-run logs + status files

# Ensure ercot_core importable for the status helpers below.
sys.path.insert(0, str(ROOT))


@dataclass
class Job:
    key: str
    label: str
    dataset_dir: str           # under datasets/
    script: str
    args: list[str] = field(default_factory=list)
    note: str = ""

    def command(self, extra_args: list[str] | None = None) -> list[str]:
        return [sys.executable, self.script, *self.args, *(extra_args or [])]

    def cwd(self) -> Path:
        return DATASETS / self.dataset_dir


def _jobs() -> dict[str, Job]:
    from ercot_core import tz
    yr = tz.now_central().year
    return {
        "system_gen": Job(
            "system_gen", "System generation by fuel (15-min)",
            "system_gen_by_fuel", "update_generation.py", [],
            note="Re-downloads the current-year Fuel Mix Report and merges with provenance.",
        ),
        "node_catalog": Job(
            "node_catalog", "Resource-node catalog (unit ↔ settlement node)",
            "system_gen_by_fuel", "resource_catalog.py", ["--build"],
            note="Rebuilds resource_node_catalog.parquet from ERCOT's resource-node "
                 "mapping. Required by the project portals' node refresh; not produced "
                 "by system_gen, so build it after a fresh checkout.",
        ),
        "hub_prices": Job(
            "hub_prices", "Hub settlement-point prices (RTM 15-min)",
            "hub_prices", "ercot_api.py", ["update"],
            note="Incremental ERCOT API pull — always fetches the latest when run "
                 "(re-pulls the recent overlap + any gap).",
        ),
        "node_prices": Job(
            "node_prices", "Resource-node prices (RTM 15-min)",
            "system_gen_by_fuel", "update_node_prices.py", [],
            note="Incremental archive pull of RT15 SPP at every tracked resource "
                 "node (feeds the portals + scorecard). Re-pulls the recent "
                 "overlap + any gap; --full to rebuild from 2024.",
        ),
        "plant_sced": Job(
            "plant_sced", "Plant-level SCED (registry refresh)",
            "plant_sced", "fetch_plants.py", ["--refresh-registry"],
            note="Rebuilds the available-resource registry from the latest disclosure. "
                 "Per-plant history is pulled on demand in the explorer.",
        ),
        "eia923": Job(
            "eia923", "EIA-923 plant monthly generation & fuel",
            "eia923", "build_cache.py", [str(yr)],
            note=f"Builds/refreshes the current year ({yr}). Pass years to build more.",
        ),
        "eia860": Job(
            "eia860", "EIA-860 plant & generator directory",
            "eia923", "eia860.py", [str(yr - 2)],
            note=f"Full ERCOT plant inventory (identity/siting/sizing). Annual file lags; "
                 f"builds {yr - 2}. Pass a year to build another.",
        ),
        "eia930": Job(
            "eia930", "EIA-930 hourly net generation by balancing authority",
            "eia930", "eia930.py", ["update"],
            note="Near-real-time (~1-day lag) system sanity check: hourly net generation "
                 "per BA from EIA's Hourly Grid Monitor. Incremental; --full to rebuild.",
        ),
        "ifyi": Job(
            "ifyi", "interconnection.fyi project crawl (all ERCOT)",
            "plant_sced", "-m", ["ercot_core.ifyi"],
            note="Crawls every ERCOT project (name, county, capacity, status) for the "
                 "SCED↔EIA auto-crosswalk. Resumable — skips cached, so re-runs are quick.",
        ),
    }


JOBS = _jobs()


# Transient macOS errors: a background daemon (cloud sync / Spotlight /
# fileproviderd) momentarily invalidates a file handle the job holds open,
# surfacing as ESTALE ("Stale NFS file handle", errno 70). The operation always
# succeeds on a fresh attempt, so we retry the whole job rather than crash it.
_ESTALE_MARKERS = ("Stale NFS file handle", "[Errno 70]", "errno 70")
_ESTALE_MAX_ATTEMPTS = 3


def _stream_job_once(key: str, extra_args: list[str] | None = None):
    """Run a job once, yielding output lines. Returns the exit code."""
    job = JOBS[key]
    env = _subprocess_env()
    proc = subprocess.Popen(
        job.command(extra_args), cwd=str(job.cwd()), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip("\n")
    proc.wait()
    return proc.returncode


def stream_job(key: str, extra_args: list[str] | None = None):
    """Run a job, yielding output lines as they arrive; returns the exit code.

    A failure caused solely by a transient ESTALE (see :data:`_ESTALE_MARKERS`)
    is retried from scratch up to :data:`_ESTALE_MAX_ATTEMPTS` times. Any other
    non-zero exit is returned immediately.
    """
    rc = 0
    for attempt in range(1, _ESTALE_MAX_ATTEMPTS + 1):
        saw_estale = False
        gen = _stream_job_once(key, extra_args)
        try:
            while True:
                line = next(gen)
                if any(m in line for m in _ESTALE_MARKERS):
                    saw_estale = True
                yield line
        except StopIteration as stop:
            rc = stop.value or 0
        if rc == 0 or not saw_estale or attempt == _ESTALE_MAX_ATTEMPTS:
            return rc
        yield (f"  ↻ transient filesystem error (ESTALE); retrying "
               f"(attempt {attempt + 1}/{_ESTALE_MAX_ATTEMPTS})…")
    return rc


def run_job(key: str, extra_args: list[str] | None = None, echo: bool = True) -> int:
    """Run a job to completion, optionally echoing output. Returns exit code."""
    gen = stream_job(key, extra_args)
    rc = 0
    try:
        while True:
            line = next(gen)
            if echo:
                print(line, flush=True)
    except StopIteration as stop:
        rc = stop.value or 0
    return rc


# --------------------------------------------------------------------------- #
# Detached runs — survive the caller (e.g. the Streamlit page navigating away).
# A job is launched as its own session-leader process writing to a log file; a
# sidecar status.json tracks state so any later page load can reconcile it.
# --------------------------------------------------------------------------- #
def _job_files(key: str) -> tuple[Path, Path]:
    UPDATE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return (UPDATE_LOG_DIR / f"{key}.log", UPDATE_LOG_DIR / f"{key}.status.json")


def _now_iso() -> str:
    from ercot_core import tz
    return tz.now_central().isoformat(timespec="seconds")


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def launch_detached(key: str, extra_args: list[str] | None = None) -> int:
    """Start a job in its own process/session (survives the caller). Returns pid.

    Output streams to ``logs/updates/<key>.log``; ``<key>.status.json`` tracks
    state. The child re-invokes this module's ``_detached`` entry so it reuses the
    same ESTALE-retry + credential-env path as an interactive run.
    """
    if key not in JOBS:
        raise KeyError(key)
    log_p, status_p = _job_files(key)
    cmd = [sys.executable, str(HERE), "_detached", key, *(extra_args or [])]
    logf = open(log_p, "w")  # noqa: SIM115 — handed to the child; closed on its exit
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=_subprocess_env(),
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,   # detach from the caller's session/controlling tty
        )
    finally:
        logf.close()  # the child inherited the fd; our copy isn't needed
    status_p.write_text(json.dumps(
        {"key": key, "pid": proc.pid, "state": "running", "started": _now_iso()}))
    return proc.pid


def job_state(key: str) -> tuple[str, dict]:
    """('idle'|'running'|'done'|'failed'|'unknown', info). Reconciles a stale
    'running' status (pid gone but no completion written) to 'unknown'."""
    _, status_p = _job_files(key)
    if not status_p.exists():
        return "idle", {}
    try:
        info = json.loads(status_p.read_text())
    except (ValueError, OSError):
        return "idle", {}
    if info.get("state") == "running":
        if _pid_alive(info.get("pid")):
            return "running", info
        return "unknown", info    # process ended without recording a result
    return info.get("state", "idle"), info


def job_log_tail(key: str, n: int = 500) -> str:
    log_p, _ = _job_files(key)
    if not log_p.exists():
        return ""
    try:
        return "\n".join(log_p.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def _finish_detached(key: str, rc: int) -> None:
    _, status_p = _job_files(key)
    try:
        info = json.loads(status_p.read_text())
    except (ValueError, OSError):
        info = {"key": key}
    info.update({"state": "done" if rc == 0 else "failed", "rc": rc,
                 "finished": _now_iso()})
    status_p.write_text(json.dumps(info))


def _subprocess_env() -> dict:
    import os
    env = dict(os.environ)
    # Make ercot_core importable in the child even if a script's bootstrap is
    # bypassed, and ensure the shared config.json credentials reach gridstatus.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
    try:
        from ercot_core import credentials
        credentials.export_to_env()
        for k in ("ERCOT_API_USERNAME", "ERCOT_API_PASSWORD",
                  "ERCOT_PUBLIC_API_SUBSCRIPTION_KEY"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    except Exception:
        pass
    return env


# --------------------------------------------------------------------------
# Status snapshots (read-only; safe to call from the UI)
# --------------------------------------------------------------------------

def status() -> dict:
    """Freshness/size snapshot of every dataset's local store."""
    from ercot_core import paths
    import pandas as pd

    out: dict[str, dict] = {}

    # system_gen — yearly parquets
    sg = {"files": 0, "years": [], "latest_interval": None}
    files = sorted(paths.SYSTEM_GEN_DIR.glob("ercot_gen_by_fuel_*.parquet"))
    sg["files"] = len(files)
    for f in files:
        try:
            sg["years"].append(int(f.stem.rsplit("_", 1)[-1]))
        except ValueError:
            pass
    if files:
        try:
            latest = max(files, key=lambda p: p.stat().st_mtime)
            df = pd.read_parquet(latest, columns=["interval_start"])
            sg["latest_interval"] = str(df["interval_start"].max())
        except Exception as e:
            sg["error"] = str(e)
    out["system_gen"] = sg

    # hub_prices — state file + parquet
    hp = {"rows": 0, "start": None, "end": None, "days_since_update": None}
    if paths.HUB_PRICES_STATE.exists():
        try:
            st = json.loads(paths.HUB_PRICES_STATE.read_text())
            hp.update({"rows": st.get("rows", 0), "start": st.get("start"),
                       "end": st.get("end")})
        except Exception:
            pass
    elif paths.HUB_PRICES_PARQUET.exists():
        try:
            df = pd.read_parquet(paths.HUB_PRICES_PARQUET, columns=["interval_ending_central"])
            hp["rows"] = len(df)
            hp["start"] = str(df["interval_ending_central"].min())
            hp["end"] = str(df["interval_ending_central"].max())
        except Exception as e:
            hp["error"] = str(e)
    out["hub_prices"] = hp

    # node_prices — yearly resource-node SPP parquets. Cheap: row counts from
    # parquet metadata (no data read); range/nodes from just the first & latest
    # year files (the lake is millions of rows — never concat it for a status).
    npx = {"rows": 0, "nodes": 0, "start": None, "end": None, "files": 0}
    npx_files = sorted(paths.NODE_DATA_DIR.glob("node_price_*.parquet"))
    npx["files"] = len(npx_files)
    if npx_files:
        try:
            import pyarrow.parquet as _pq
            npx["rows"] = sum(_pq.ParquetFile(str(f)).metadata.num_rows for f in npx_files)
            _first = pd.read_parquet(npx_files[0], columns=["interval_start"])
            _last = pd.read_parquet(npx_files[-1], columns=["interval_start", "location"])
            npx["start"] = str(pd.to_datetime(_first["interval_start"]).min())
            npx["end"] = str(pd.to_datetime(_last["interval_start"]).max())
            npx["nodes"] = int(_last["location"].nunique())
        except Exception as e:  # noqa: BLE001
            npx["error"] = str(e)
    out["node_prices"] = npx

    # plant_sced — registry + cached disclosure days + per-plant files
    ps = {"resources": 0, "disclosure_days": 0, "plant_files": 0}
    if paths.PLANT_REGISTRY_PARQUET.exists():
        try:
            ps["resources"] = len(pd.read_parquet(paths.PLANT_REGISTRY_PARQUET))
        except Exception:
            pass
    ps["disclosure_days"] = len(list(paths.SCED_CACHE_DIR.glob("disclosure_*.parquet")))
    ps["plant_files"] = len(list(paths.PLANT_DATA_DIR.glob("*.parquet")))
    out["plant_sced"] = ps

    # eia923 — available years
    eia = {"years": []}
    for p in paths.EIA_DIR.glob("eia923_ercot_*.parquet"):
        try:
            eia["years"].append(int(p.stem.rsplit("_", 1)[-1]))
        except ValueError:
            pass
    eia["years"] = sorted(eia["years"])
    out["eia923"] = eia

    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "list":
        for j in JOBS.values():
            print(f"  {j.key:<12} {j.label}\n               {j.note}")
        return 0

    if cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
        return 0

    if cmd == "_detached":   # internal: the child launched by launch_detached()
        if not rest or rest[0] not in JOBS:
            print(f"_detached needs a known job key; got {rest}")
            return 2
        key, extra = rest[0], rest[1:]
        print(f"# {JOBS[key].label} ({key}) — detached run @ {_now_iso()}", flush=True)
        rc = run_job(key, extra, echo=True)
        _finish_detached(key, rc)
        print(f"\n# {'✓ done' if rc == 0 else '✗ failed'} (rc={rc}) @ {_now_iso()}", flush=True)
        return rc

    if cmd == "update":
        keys = rest or list(JOBS)
        unknown = [k for k in keys if k not in JOBS]
        if unknown:
            print(f"Unknown dataset(s): {unknown}. Known: {list(JOBS)}")
            return 2
        failures = []
        for k in keys:
            print("\n" + "=" * 70)
            print(f"# {JOBS[k].label}  ({k})")
            print("=" * 70)
            rc = run_job(k)
            if rc != 0:
                failures.append(k)
                print(f"  !! {k} exited with code {rc}")
        print("\n" + "=" * 70)
        if failures:
            print(f"Completed with failures: {failures}")
            return 1
        print("All requested datasets updated. ✅")
        return 0

    if cmd == "invoice":
        return _invoice_cmd(rest)

    print(f"Unknown command {cmd!r}. Try: status | update | list | invoice")
    return 2


def _invoice_cmd(rest) -> int:
    """Headless invoice validation:

        python orchestrate.py invoice <file> [--location HB_HUBAVG] [--market RT15|DAM]
            [--volume-source invoice|metered] [--node NAME] [--abs-tol 0.01]
            [--rel-tol 0.005] [--out reconciliation.csv]
    """
    import argparse
    import pandas as pd

    from ercot_core import invoice as INV, prices as PX, dam_prices as DAMX, paths, tz

    ap = argparse.ArgumentParser(prog="orchestrate.py invoice")
    ap.add_argument("file")
    ap.add_argument("--location", default="HB_HUBAVG")
    ap.add_argument("--market", default="RT15", choices=["RT15", "DAM"])
    ap.add_argument("--volume-source", default="invoice", choices=["invoice", "metered"])
    ap.add_argument("--node", default=None)
    ap.add_argument("--abs-tol", type=float, default=0.01)
    ap.add_argument("--rel-tol", type=float, default=0.005)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(rest)

    inv = INV.load_invoice(a.file, INV.suggest_mapping(pd.read_csv(a.file, nrows=0).columns
                                                       if a.file.lower().endswith(".csv")
                                                       else pd.read_excel(a.file, nrows=0).columns))
    lo = inv["interval_start"].min().tz_convert(tz.CENTRAL).tz_localize(None)
    hi = inv["interval_start"].max().tz_convert(tz.CENTRAL).tz_localize(None)
    start, end_excl = pd.Timestamp(lo) - pd.Timedelta(hours=1), pd.Timestamp(hi) + pd.Timedelta(hours=2)
    price_df = (DAMX.dam_store_prices([a.location], start, end_excl) if a.market == "DAM"
                else PX.hub_store_prices([a.location], start, end_excl))
    if price_df.empty:
        print(f"No cached {a.market} price for {a.location} over {lo.date()} → {hi.date()}.")
        return 1
    gen_df = None
    if a.volume_source == "metered" and paths.NODE_DATA_DIR.exists():
        files = sorted(paths.NODE_DATA_DIR.glob("node_generation_*.parquet"))
        if files:
            gen_df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    res = INV.reconcile(inv, price_df=price_df, gen_df=gen_df, location=a.location,
                        market=a.market, resource_node=a.node,
                        volume_source=a.volume_source, abs_tol=a.abs_tol, rel_tol=a.rel_tol)
    s = res["summary"]
    print(json.dumps({k: v for k, v in s.items() if k != "worst"}, indent=2, default=str))
    out = a.out or f"invoice_reconciliation_{a.location}_{a.market}.csv"
    res["intervals"].to_csv(out, index=False)
    print(f"\nPer-interval reconciliation written to {out}")
    return 0 if s["n_flagged"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
