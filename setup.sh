#!/usr/bin/env bash
# ============================================================================
# ERCOT suite — one-shot bootstrap for a fresh clone.
#
# Does EVERYTHING a new user needs after `git clone` to actually run the
# portals:
#   1. Builds the shared Ercot_Data_Hub virtualenv (it owns ercot_core, the
#      ERCOT-pull deps, and the credentials) and scaffolds its config.json.
#   2. Builds each portal's own virtualenv + installs its requirements, and
#      scaffolds each portal's config.json from the committed example.
#   3. Pulls the ERCOT data lake: shared hub prices (via orchestrate) plus each
#      portal's asset-specific node generation + node price (via refresh.py,
#      run with the Hub venv). This needs ERCOT API credentials and can take a
#      long time on a fresh clone (it backfills from scratch).
#
# Re-runnable: existing venvs and config.json files are left as-is; the data
# pull is incremental.
#
# Usage:
#   ./setup.sh                 full bootstrap (env + config + data pull)
#   ./setup.sh --no-data       env + config only; skip the data pull
#   ./setup.sh --hub-full      also pull ALL Data Hub datasets (EIA, SCED, …),
#                              not just the hub prices the portals need
# ============================================================================
set -uo pipefail

SUITE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB="$SUITE/Ercot_Data_Hub"
HUB_PY="$HUB/.venv/bin/python"

PULL_DATA=1
HUB_FULL=0
for arg in "$@"; do
  case "$arg" in
    --no-data)  PULL_DATA=0 ;;
    --hub-full) HUB_FULL=1 ;;
    -h|--help)  sed -n '3,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 2 ;;
  esac
done

# ── pretty output ────────────────────────────────────────────────────────────
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
section() { echo; bold "════════════════════════════════════════════════════════";
            bold "  $*"; bold "════════════════════════════════════════════════════════"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

FAILURES=()

# ── preflight ────────────────────────────────────────────────────────────────
section "Preflight"
PYTHON3=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYV="$("$candidate" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
    MAJOR="${PYV%%.*}"; MINOR="${PYV##*.}"
    if [ "$MAJOR" -gt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; }; then
      PYTHON3="$candidate"; break
    fi
  fi
done
if [ -z "$PYTHON3" ]; then
  err "Python 3.10+ not found — install it and re-run."; exit 1
fi
PYV="$("$PYTHON3" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
ok "python3 $PYV ($PYTHON3)"
[ -d "$HUB" ] || { err "Ercot_Data_Hub not found at $HUB — clone the full monorepo."; exit 1; }
ok "Data Hub present"

# ── venv builder (idempotent; tolerates .venv being a symlink to a sibling) ──
build_venv() {  # $1 = project dir
  local d="$1" name; name="$(basename "$d")"
  if [ -d "$d/.venv" ]; then
    ok "$name: venv exists"
  else
    echo "  $name: creating venv…"
    if ! "$PYTHON3" -m venv "$d/.venv"; then err "$name: venv creation failed"; FAILURES+=("$name venv"); return 1; fi
  fi
  echo "  $name: installing requirements…"
  if "$d/.venv/bin/pip" install --quiet --upgrade pip \
     && "$d/.venv/bin/pip" install --quiet -r "$d/requirements.txt"; then
    ok "$name: deps installed"
  else
    err "$name: pip install failed"; FAILURES+=("$name deps"); return 1
  fi
}

# ── config scaffolder ────────────────────────────────────────────────────────
scaffold_config() {  # $1 = project dir
  local d="$1" name; name="$(basename "$d")"
  if [ -f "$d/config.json" ]; then
    ok "$name: config.json present"
  elif [ -f "$d/config.example.json" ]; then
    cp "$d/config.example.json" "$d/config.json"
    ok "$name: config.json created from example"
  else
    warn "$name: no config.example.json to copy"
  fi
}

# ── 1) Data Hub (foundation) ─────────────────────────────────────────────────
section "1/3  Shared Data Hub"
build_venv "$HUB"
scaffold_config "$HUB"

# Credentials live in the Hub config. The data pull can't run without them.
HAVE_CREDS=0
if [ -f "$HUB/config.json" ]; then
  HAVE_CREDS="$("$HUB_PY" - "$HUB/config.json" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    c = json.load(open(sys.argv[1]))
except Exception:
    print(0); raise SystemExit
need = ("username", "password", "subscription_key")
print(1 if all(str(c.get(k, "")).strip() for k in need) else 0)
PY
)"
fi
if [ "$HAVE_CREDS" = "1" ]; then
  ok "ERCOT API credentials configured"
else
  warn "ERCOT API credentials NOT set in $HUB/config.json"
  warn "  (need: username, password, subscription_key)"
fi

# ── 2) Portals ───────────────────────────────────────────────────────────────
section "2/3  Portals"
PORTALS=()
for d in "$SUITE"/*/; do
  d="${d%/}"; base="$(basename "$d")"
  [ "$base" = "Ercot_Data_Hub" ] && continue
  [ -f "$d/app/Home.py" ] || continue
  [ -f "$d/requirements.txt" ] || continue
  ls "$d"/Open*.command >/dev/null 2>&1 || continue
  PORTALS+=("$d")
done
bold "  Found ${#PORTALS[@]} portal(s)."
for d in "${PORTALS[@]}"; do
  echo; bold "  • $(basename "$d")"
  build_venv "$d"
  scaffold_config "$d"
done

# ── 3) Data pull ─────────────────────────────────────────────────────────────
section "3/3  ERCOT data lake"
if [ "$PULL_DATA" = "0" ]; then
  warn "Skipping data pull (--no-data). Run ./setup.sh without it to fetch data."
elif [ "$HAVE_CREDS" != "1" ]; then
  err "Cannot pull data — ERCOT API credentials are not configured."
  err "Fill in username / password / subscription_key in:"
  err "  $HUB/config.json"
  err "…then re-run ./setup.sh (env setup above is already done)."
  FAILURES+=("data pull (no credentials)")
else
  # Shared hub prices (all portals settle/compare against these).
  bold "  Hub prices (shared)…"
  if [ "$HUB_FULL" = "1" ]; then
    bold "  --hub-full: pulling ALL Data Hub datasets (this is the slow path)…"
    ( cd "$HUB" && "$HUB_PY" orchestrate.py update ) || FAILURES+=("hub: full update")
  else
    ( cd "$HUB" && "$HUB_PY" orchestrate.py update hub_prices ) || FAILURES+=("hub_prices")
  fi
  # Per-portal asset data (node generation + node price). refresh.py runs in the
  # Hub venv — it has the pull deps + credentials — with the portal as cwd.
  for d in "${PORTALS[@]}"; do
    base="$(basename "$d")"
    if [ -f "$d/refresh.py" ]; then
      echo; bold "  $base: pulling asset data…"
      ( cd "$d" && "$HUB_PY" refresh.py ) || { err "$base: refresh failed"; FAILURES+=("$base data"); }
    else
      warn "$base: no refresh.py — skipping data pull"
    fi
  done
fi

# ── summary ──────────────────────────────────────────────────────────────────
section "Done"
if [ "${#FAILURES[@]}" -eq 0 ]; then
  ok "Setup complete."
  echo
  echo "  Launch a portal:   ./launch_portal.sh            (interactive menu)"
  echo "                     ./launch_portal.sh azure      (by name)"
  echo "                     ./launch_portal.sh all        (start every portal)"
else
  warn "Setup finished with ${#FAILURES[@]} issue(s):"
  for f in "${FAILURES[@]}"; do echo "      - $f"; done
  echo
  echo "  Environments are set up; fix the items above (usually credentials)"
  echo "  and re-run ./setup.sh — it skips work that's already done."
  exit 1
fi
