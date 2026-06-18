"""Shared helpers for the unified Streamlit app (path bootstrap + job runner)."""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root (parent of app/). Put it first so ``ercot_core`` always resolves.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()  # repo root + all dataset dirs on sys.path

import calendar  # noqa: E402
import datetime as _dt  # noqa: E402


def period_picker(st, key: str = "p", min_year: int = 2018, default_mode: str = "Month"):
    """Render a flexible period selector and return (start_date, end_date).

    Modes: Month · Quarter · Year · Custom days (day-level pickers). Defaults to
    the most recent month safely past the ~60-day SCED lag.
    """
    from ercot_core import tz
    today = tz.now_central().date()  # ERCOT "today", not the machine's local date
    max_year = today.year
    years = list(range(max_year, min_year - 1, -1))
    months = list(calendar.month_name)[1:]  # Jan … Dec

    # A reference point ~75 days back lands past the 60-day SCED lag.
    ref = today - _dt.timedelta(days=75)
    dy = ref.year if ref.year in years else max_year
    dm = ref.month

    modes = ["Month", "Quarter", "Year", "Custom days"]
    mode = st.radio("Period type", modes, index=modes.index(default_mode),
                    horizontal=True, key=f"{key}_mode")

    def _eom(y, m):
        return _dt.date(y, m, calendar.monthrange(y, m)[1])

    if mode == "Month":
        c1, c2 = st.columns(2)
        y = c1.selectbox("Year", years, index=years.index(dy), key=f"{key}_my")
        m = c2.selectbox("Month", months, index=dm - 1, key=f"{key}_mm")
        mi = months.index(m) + 1
        return _dt.date(y, mi, 1), _eom(y, mi)

    if mode == "Quarter":
        c1, c2 = st.columns(2)
        y = c1.selectbox("Year", years, index=years.index(dy), key=f"{key}_qy")
        q = c2.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"],
                         index=(dm - 1) // 3, key=f"{key}_qq")
        sm = (int(q[1]) - 1) * 3 + 1
        return _dt.date(y, sm, 1), _eom(y, sm + 2)

    if mode == "Year":
        y = st.selectbox("Year", years, index=years.index(dy), key=f"{key}_yy")
        return _dt.date(y, 1, 1), _dt.date(y, 12, 31)

    # Custom days
    c1, c2 = st.columns(2)
    start = c1.date_input("Start", value=today - _dt.timedelta(days=120), key=f"{key}_cs")
    end = c2.date_input("End", value=today - _dt.timedelta(days=61), key=f"{key}_ce")
    return start, end


def _age_str(ts: float) -> str:
    """Human 'updated …' age from a POSIX mtime."""
    secs = max(0.0, _dt.datetime.now().timestamp() - ts)
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def data_status(st, *, path=None, rows=None, span=None, fresh_within_days=None):
    """Compact freshness line for a cached dataset (one shared look across pages).

    path: a Path/str or iterable of them — the newest mtime renders as "updated Nd ago".
    rows: row count. span: (start, end) shown as "start → end".
    fresh_within_days: if the newest file is older than this, the dot turns ⚠️.
    """
    from pathlib import Path as _P

    bits: list[str] = []
    icon = "🟢"
    if path is not None:
        items = [path] if isinstance(path, (str, _P)) else list(path)
        mtimes = [_P(p).stat().st_mtime for p in items if _P(p).exists()]
        if mtimes:
            newest = max(mtimes)
            bits.append(f"updated {_age_str(newest)}")
            if fresh_within_days is not None:
                age_days = (_dt.datetime.now().timestamp() - newest) / 86400
                if age_days > fresh_within_days:
                    icon = "⚠️"
    if rows is not None:
        bits.append(f"{int(rows):,} rows")
    if span is not None:
        bits.append(f"{span[0]} → {span[1]}")
    if bits:
        st.caption(f"{icon}  " + "  ·  ".join(bits))


def empty_state(st, message: str, *, hint=None, page=None, page_label=None, icon="➡️",
                stop: bool = True):
    """Consistent 'no data here yet' block with an optional recovery link.

    page: a page path registered in the router (e.g. 'views/home.py') to deep-link to.
    Set stop=False to keep rendering after the notice.
    """
    st.info(message)
    if hint:
        st.caption(hint)
    if page:
        st.page_link(page, label=page_label or "Take me there", icon=icon)
    if stop:
        st.stop()


def run_with_logs(st, key: str, extra_args=None) -> int:
    """Run an orchestrator job, streaming its output into a live code block."""
    import orchestrate

    job = orchestrate.JOBS[key]
    st.write(f"Running **{job.label}** …")
    log_area = st.empty()
    lines: list[str] = []
    rc = 0
    gen = orchestrate.stream_job(key, extra_args)
    try:
        while True:
            line = next(gen)
            lines.append(line)
            log_area.code("\n".join(lines[-500:]) or "…")
    except StopIteration as stop:
        rc = stop.value or 0
    if rc == 0:
        st.success(f"✓ {job.label} finished.")
    else:
        st.error(f"✗ {job.label} exited with code {rc}.")
    return rc


# --------------------------------------------------------------------------- #
# Universal plant selector — shared across the analysis pages
# --------------------------------------------------------------------------- #

def universal_plant_assets():
    """All curated-registry plants (solar + wind), sorted by tech then name."""
    from ercot_core import plant_value as pv

    assets = pv.load_solar_assets() + pv.load_wind_assets()
    assets.sort(key=lambda a: (str(a.get("tech", "")).lower(),
                               str(a.get("project_name") or a.get("resource_name", ""))))
    return assets


def universal_plant_label(a: dict) -> str:
    return (f"{a.get('project_name', a['resource_name'])} — {a.get('county', '?')} "
            f"({a['capacity_mw']:,.0f} MW · {str(a.get('tech', '')).title()} · {a['hub']})")


def universal_plant_picker(st, *, sidebar: bool = True, label: str = "🌎 Universal plant"):
    """Render the shared plant selector; return the chosen registry asset (or None).

    Lists every registry plant (both techs) so the shared pick is always valid on
    any page. Reads/writes ``st.session_state['fx_plant']`` (the resource_name), so
    the selection persists across Plant Value, Wind Capture and PPA Settlement;
    each page resolves the returned asset its own way (valuation / nearest cached
    run / ERCOT node). Seeded by index (no widget key) so differing page contexts
    never collide.
    """
    try:
        assets = universal_plant_assets()
    except Exception:  # noqa: BLE001 — registry missing → caller falls back
        return None
    if not assets:
        return None
    names = [a["resource_name"] for a in assets]
    cur = st.session_state.get("fx_plant")
    idx = names.index(cur) if cur in names else 0
    target = st.sidebar if sidebar else st
    # A clear, self-contained block at the top of the sidebar: title, the picker
    # (its own label hidden to avoid a doubled heading), a one-line summary of the
    # current pick, and a divider so it reads as its own card above page controls.
    target.markdown(f"**{label}**")
    i = target.selectbox(label, range(len(assets)), index=idx,
                         format_func=lambda j: universal_plant_label(assets[j]),
                         label_visibility="collapsed",
                         help="Set it once — it follows you across Plant Value, "
                              "Wind Capture and PPA Settlement.")
    asset = assets[i]
    st.session_state["fx_plant"] = asset["resource_name"]
    target.caption(f"{str(asset.get('tech', '')).title()} · {asset['capacity_mw']:,.0f} MW · "
                   f"{asset.get('county', '?')} · {asset['hub']} hub")
    if sidebar:
        target.divider()
    return asset
