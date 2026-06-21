"""Control Tower — dataset freshness + one-click refresh with live logs.

Rendered as the default page by the router in app/Home.py. The router owns
``st.set_page_config`` and ``paths.ensure_dirs``; this script only renders.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: E402

import streamlit as st  # noqa: E402

import orchestrate  # noqa: E402
from ercot_core import credentials, paths  # noqa: E402

st.title("⚡ ERCOT Data Hub")
st.caption(
    "Unified orchestration for four ERCOT datasets — one credential store, one "
    "shared 60-day SCED cache, one data lake. Refresh anything below, then work "
    "through the sidebar: **Explore** the data, **Resolve & Map** identities, "
    "then **Analyze** (settlements, reconciliation)."
)

# --------------------------------------------------------------------------
# Task router — jump straight to a goal instead of hunting the sidebar
# --------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("**What do you want to do?**")
    q1, q2, q3 = st.columns(3)
    with q1:
        st.page_link("screens/7_PPA_Settlement.py", label="Settle a PPA", icon="🧾")
        st.page_link("screens/2_Hub_Prices.py", label="Explore hub prices", icon="💵")
    with q2:
        st.page_link("screens/8_Reconciliation.py", label="Reconcile a plant", icon="🔁")
        st.page_link("screens/5_Node_Explorer.py", label="Explore a node", icon="📈")
    with q3:
        st.page_link("screens/6_Project_Lookup.py", label="Find a project's node", icon="🔎")
        st.page_link("screens/13_Solar_Forecast.py", label="Forecast solar (lat/long)", icon="☀️")

# --------------------------------------------------------------------------
# Credentials (shared config.json) — only hub_prices + system_gen wind/solar need it
# --------------------------------------------------------------------------
with st.expander("🔑 ERCOT API credentials (shared by all datasets)",
                 expanded=not credentials.have_credentials()):
    cfg = credentials.load_config()
    have = credentials.have_credentials(cfg)
    if have:
        st.success(f"Credentials configured for **{cfg.get('username','?')}**. "
                   "Used by hub prices (direct API) and system-gen wind/solar.")
    else:
        st.warning("No credentials yet. Hub prices and the wind/solar supplement "
                   "need a free ERCOT API account (apiexplorer.ercot.com). The "
                   "Fuel-Mix, SCED, and EIA-923 datasets work without one.")
    with st.form("creds"):
        u = st.text_input("Username / email", value=cfg.get("username", ""))
        p = st.text_input("Password", value=cfg.get("password", ""), type="password")
        k = st.text_input("Subscription key", value=cfg.get("subscription_key", ""),
                          type="password")
        bf = st.text_input("Hub-price backfill start (YYYY-MM-DD)",
                           value=cfg.get("backfill_start", "2024-01-01"))
        if st.form_submit_button("Save credentials"):
            cfg.update({"username": u.strip(), "password": p.strip(),
                        "subscription_key": k.strip(), "backfill_start": bf.strip()})
            credentials.save_config(cfg)
            st.success("Saved to config.json (chmod 600).")
            st.rerun()

# --------------------------------------------------------------------------
# Live-run target (set by the per-dataset buttons below)
# --------------------------------------------------------------------------
run_target = st.session_state.pop("_run_target", None)

# --------------------------------------------------------------------------
# Dataset status cards
# --------------------------------------------------------------------------
st.subheader("Datasets")

snap = orchestrate.status()


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


cards = [
    ("system_gen", "🔥", lambda s: [
        ("Years", ", ".join(map(str, s.get("years", []))) or "—"),
        ("Latest interval", s.get("latest_interval") or "—"),
        ("Parquet files", _fmt(s.get("files", 0))),
    ]),
    ("hub_prices", "💵", lambda s: [
        ("Rows", _fmt(s.get("rows", 0))),
        ("Range", f"{s.get('start','—')} → {s.get('end','—')}"),
    ]),
    ("plant_sced", "🏭", lambda s: [
        ("Resources", _fmt(s.get("resources", 0))),
        ("Cached SCED days", _fmt(s.get("disclosure_days", 0))),
        ("Per-plant files", _fmt(s.get("plant_files", 0))),
    ]),
    ("eia923", "📅", lambda s: [
        ("Years", ", ".join(map(str, s.get("years", []))) or "—"),
    ]),
]

cols = st.columns(len(cards))
for col, (key, icon, rows_fn) in zip(cols, cards):
    job = orchestrate.JOBS[key]
    with col:
        st.markdown(f"### {icon} {job.label}")
        for label, val in rows_fn(snap.get(key, {})):
            st.markdown(f"**{label}:** {val}")
        st.caption(job.note)
        if st.button(f"Update", key=f"btn_{key}", use_container_width=True):
            st.session_state["_run_target"] = key
            st.rerun()

st.divider()
c1, c2 = st.columns([1, 3])
with c1:
    if st.button("⟳ Update ALL datasets", type="primary", use_container_width=True):
        st.session_state["_run_target"] = "__all__"
        st.rerun()
with c2:
    st.caption("Each updater runs as a subprocess with live logs below. "
               "Hub-price first-run backfill and EIA-923 downloads can take a while.")

# --------------------------------------------------------------------------
# Settlement portals directory
# --------------------------------------------------------------------------
_SUITE_ROOT = pathlib.Path(__file__).resolve().parents[3]  # …/ercot-suite

_PORTALS = [
    {"name": "Markum Solar",          "tech": "Solar PV", "mw": 161,  "hub": "HB_NORTH", "icon": "☀️",
     "offtaker": "Colgate-Palmolive",                  "developer": "Scout Clean Energy",  "strike": 35.00,
     "dir": "ERCOT_Markum",           "port": 8502},
    {"name": "Azure Sky Wind",         "tech": "Wind",     "mw": 350,  "hub": "HB_NORTH", "icon": "🌬️",
     "offtaker": "MilliporeSigma · Kellogg · lululemon · Akamai · Synopsys · Uber · HP Hood",
     "developer": "Enel Green Power",  "strike": 17.34,
     "dir": "ERCOT_Azure_Sky",         "port": 8503},
    {"name": "Hidalgo Mirasole Wind",  "tech": "Wind",     "mw": 300,  "hub": "HB_SOUTH", "icon": "🌬️",
     "offtaker": "General Motors · The Home Depot · Bloomberg LP",
     "developer": "EDP Renewables",    "strike": 35.00,
     "dir": "ERCOT_Hidalgo_Mirasole_Wind", "port": 8504},
    {"name": "Hornet Solar",           "tech": "Solar PV", "mw": 600,  "hub": "HB_PAN",   "icon": "☀️",
     "offtaker": "Pfizer · Brunswick Corporation",     "developer": "Vesper Energy",       "strike": 25.00,
     "dir": "ERCOT_Hornet_Solar",      "port": 8505},
    {"name": "Millers Branch Solar",   "tech": "Solar PV", "mw": 318,  "hub": "HB_NORTH", "icon": "☀️",
     "offtaker": "Thermo Fisher Scientific",           "developer": "Southern Power",      "strike": 35.00,
     "dir": "ERCOT_Miller",            "port": 8506},
    {"name": "Mesquite Star",          "tech": "Wind",     "mw": 419,  "hub": "HB_WEST",  "icon": "🌬️",
     "offtaker": "Aggregated · Brown University",       "developer": "Clearway Energy Group", "strike": 29.00,
     "dir": "ERCOT_Mesquite_Star",     "port": 8507},
]


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _launch_portal(portal: dict) -> None:
    portal_dir = _SUITE_ROOT / portal["dir"]
    venv_streamlit = portal_dir / ".venv" / "bin" / "streamlit"
    if not venv_streamlit.exists():
        venv_streamlit = pathlib.Path(".venv/bin/streamlit")  # fallback
    subprocess.Popen(
        [str(venv_streamlit), "run", "app/Home.py",
         "--server.port", str(portal["port"]),
         "--server.headless", "true"],
        cwd=str(portal_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


st.divider()
st.subheader("Settlement Portals")
st.caption("Customer-facing VPPA/CfD settlement apps — one per asset. "
           "Each runs as a separate Streamlit app on its own port.")

_pcols = st.columns(len(_PORTALS))
for _col, _p in zip(_pcols, _PORTALS):
    with _col:
        _running = _port_open(_p["port"])
        st.markdown(
            f"### {_p['icon']} {_p['name']}\n"
            f"**{_p['mw']:,} MW {_p['tech']}** · {_p['hub']}\n\n"
            f"**Strike:** ${_p['strike']:.2f}/MWh\n\n"
            f"**Offtaker:** {_p['offtaker']}\n\n"
            f"**Developer:** {_p['developer']}"
        )
        if _running:
            st.link_button("Open portal ↗", f"http://localhost:{_p['port']}",
                           use_container_width=True, type="primary")
        else:
            if st.button("Launch", key=f"launch_{_p['port']}", use_container_width=True):
                _launch_portal(_p)
                st.toast(f"Launching {_p['name']} on port {_p['port']}…")
                st.rerun()

# --------------------------------------------------------------------------
# Run + stream logs
# --------------------------------------------------------------------------
if run_target:
    st.divider()
    keys = list(orchestrate.JOBS) if run_target == "__all__" else [run_target]
    for k in keys:
        with st.status(f"Updating {orchestrate.JOBS[k].label}…", expanded=True):
            _common.run_with_logs(st, k)
    st.cache_data.clear()
    if st.button("↻ Refresh status"):
        st.rerun()
