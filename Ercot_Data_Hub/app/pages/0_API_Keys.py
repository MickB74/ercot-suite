"""API Keys — one place to view and set every credential the Hub uses.

All keys live in the shared, git-ignored ``config.json`` (chmod 600) at the repo
root, so configuring them here lights up every dataset and page:
  * ERCOT Public API  — hub prices, DAM prices, SCED/node data (via gridstatus)
  * NREL developer API — NSRDB weather for the solar forecast
  * EIA developer API  — optional, for some gridstatus EIA helpers
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app/ (for _common)
from ercot_core.bootstrap import setup_path  # noqa: E402

setup_path()
import _common  # noqa: F401,E402

import streamlit as st  # noqa: E402

from ercot_core import credentials, paths  # noqa: E402

st.title("🔑 API Keys")
st.caption(f"All credentials are stored locally in `{paths.CONFIG_PATH}` "
           "(git-ignored, chmod 600). Set once here; every dataset reads from it.")


def _mask(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "— not set —"
    return f"…{v[-4:]}" if len(v) > 4 else "set"


cfg = credentials.load_config()

# ── ERCOT Public API ────────────────────────────────────────────────────────
ercot_ok = credentials.have_credentials(cfg)
st.subheader(("🟢 " if ercot_ok else "⚠️ ") + "ERCOT Public API")
st.caption("Powers hub prices, DAM prices, and SCED/node data. Free account at "
           "https://apiexplorer.ercot.com (Sign Up → copy your *Primary subscription key*).")
with st.form("ercot"):
    u = st.text_input("Username / email", value=cfg.get("username", ""))
    p = st.text_input("Password", value=cfg.get("password", ""), type="password")
    k = st.text_input("Subscription key", value=cfg.get("subscription_key", ""), type="password")
    bf = st.text_input("Backfill start (first run only)", value=cfg.get("backfill_start", ""),
                       help="Earliest date the very first hub-price pull reaches back to, e.g. 2020-01-01.")
    c1, c2 = st.columns([1, 1])
    if c1.form_submit_button("Save ERCOT credentials", type="primary"):
        cfg = credentials.load_config()
        cfg.update({"username": u.strip(), "password": p.strip(),
                    "subscription_key": k.strip()})
        if bf.strip():
            cfg["backfill_start"] = bf.strip()
        credentials.save_config(cfg)
        credentials.export_to_env(cfg)
        st.success("Saved to config.json (chmod 600).")
        st.rerun()
    if c2.form_submit_button("Test login"):
        try:
            import ercot_api as ea  # datasets/hub_prices on path via setup_path()
            ea.get_access_token(credentials.load_config(), log=lambda m: None)
            st.success("✅ ERCOT login succeeded.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Login failed: {e}")

# ── NREL developer API ───────────────────────────────────────────────────────
nrel_key, nrel_email = credentials.get_nrel_api_key(), credentials.get_nrel_email()
nrel_ok = bool(nrel_key and nrel_email)
st.subheader(("🟢 " if nrel_ok else "⚠️ ") + "NREL developer API")
st.caption("Needed for the **Solar Forecast** (NSRDB weather / PVWatts). Free key at "
           "https://developer.nrel.gov/signup/ — both the key and the registered email are required.")
with st.form("nrel"):
    nk = st.text_input("NREL API key", value=nrel_key, type="password")
    ne = st.text_input("Registered email", value=nrel_email)
    if st.form_submit_button("Save NREL credentials", type="primary"):
        credentials.save_nrel_credentials(nk.strip(), ne.strip())
        st.success("Saved to config.json.")
        st.rerun()

# ── EIA developer API (optional) ─────────────────────────────────────────────
eia_key = credentials.get_eia_api_key()
st.subheader(("🟢 " if eia_key else "◽ ") + "EIA developer API  ·  optional")
st.caption("Only used by some gridstatus EIA helpers. Free key at "
           "https://www.eia.gov/opendata/register.php. The EIA-923/860 bulk pulls do **not** need it.")
with st.form("eia"):
    ek = st.text_input("EIA API key", value=eia_key, type="password")
    if st.form_submit_button("Save EIA key", type="primary"):
        credentials.save_eia_api_key(ek.strip())
        st.success("Saved to config.json (and exported to EIA_API_KEY).")
        st.rerun()

# ── Status summary ───────────────────────────────────────────────────────────
st.divider()
st.markdown("**Stored now**")
st.dataframe(
    {"Service": ["ERCOT username", "ERCOT subscription key", "NREL key", "NREL email", "EIA key"],
     "Status": [cfg.get("username") or "— not set —", _mask(cfg.get("subscription_key", "")),
                _mask(nrel_key), nrel_email or "— not set —", _mask(eia_key)]},
    hide_index=True, use_container_width=True)
