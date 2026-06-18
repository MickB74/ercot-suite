"""One ERCOT Public API credential store for the whole monorepo.

Before the merge there were two:
  * hub_prices read ``config.json`` (username / password / subscription_key)
  * system_gen's api_source read three environment variables, via gridstatus

Both used the *same* free ERCOT account. Here they share one ``config.json`` at
the repo root, and :func:`export_to_env` mirrors it into the env vars gridstatus
expects — so configuring credentials once lights up every dataset.

config.json schema (all optional; git-ignored, chmod 600):
    {
      "username": "...", "password": "...", "subscription_key": "...",
      "backfill_start": "2020-01-01"
    }
"""

from __future__ import annotations

import getpass
import json
import os

from ercot_core import paths

# gridstatus / ErcotAPI read these (system_gen's api_source, wind/solar feeds).
ENV_USERNAME = "ERCOT_API_USERNAME"
ENV_PASSWORD = "ERCOT_API_PASSWORD"
ENV_SUBKEY = "ERCOT_PUBLIC_API_SUBSCRIPTION_KEY"

_REQUIRED = ("username", "password", "subscription_key")


def load_config() -> dict:
    if paths.CONFIG_PATH.exists():
        try:
            return json.loads(paths.CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    paths.CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(paths.CONFIG_PATH, 0o600)  # password lives here
    except OSError:
        pass


def have_credentials(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    return all(cfg.get(k) for k in _REQUIRED)


# --- NREL developer API (PVWatts / NSRDB weather for the solar forecast) ----
# Independent of the ERCOT API account; a separate free key from
# https://developer.nrel.gov/signup/. Stored in the same config.json.

def get_nrel_api_key() -> str:
    return load_config().get("nrel_api_key", "") or os.environ.get("NREL_API_KEY", "")


def get_nrel_email() -> str:
    return load_config().get("nrel_email", "") or os.environ.get("NREL_EMAIL", "")


def save_nrel_credentials(api_key: str, email: str) -> None:
    cfg = load_config()
    cfg.update({"nrel_api_key": api_key, "nrel_email": email})
    save_config(cfg)


# --- EIA developer API (optional; used by some gridstatus EIA helpers) -------
# A separate free key from https://www.eia.gov/opendata/register.php. Stored in
# config.json and mirrored to the EIA_API_KEY env var that gridstatus reads.
ENV_EIA = "EIA_API_KEY"


def get_eia_api_key() -> str:
    return load_config().get("eia_api_key", "") or os.environ.get(ENV_EIA, "")


def save_eia_api_key(api_key: str) -> None:
    cfg = load_config()
    cfg["eia_api_key"] = api_key.strip()
    save_config(cfg)
    if api_key.strip():
        os.environ[ENV_EIA] = api_key.strip()


def export_to_env(cfg: dict | None = None) -> bool:
    """Mirror config.json credentials into the env vars gridstatus reads.

    Returns True if all three were exported. Existing env vars win (so a shell
    that already exports them isn't clobbered).
    """
    cfg = cfg if cfg is not None else load_config()
    mapping = {
        ENV_USERNAME: cfg.get("username"),
        ENV_PASSWORD: cfg.get("password"),
        ENV_SUBKEY: cfg.get("subscription_key"),
        ENV_EIA: cfg.get("eia_api_key"),       # optional
    }
    for env_key, val in mapping.items():
        if val and not os.environ.get(env_key):
            os.environ[env_key] = str(val)
    return all(os.environ.get(k) for k in (ENV_USERNAME, ENV_PASSWORD, ENV_SUBKEY))


def credentials_present_in_env() -> bool:
    return all(os.environ.get(k) for k in (ENV_USERNAME, ENV_PASSWORD, ENV_SUBKEY))


def set_credentials_interactive() -> None:
    print("\nERCOT Public API credentials (shared by all datasets)")
    print("Get a free account at https://apiexplorer.ercot.com (Sign Up),")
    print("then copy your 'Primary subscription key' from your profile.\n")
    cfg = load_config()
    username = input(f"ERCOT API username/email [{cfg.get('username','')}]: ").strip() or cfg.get("username", "")
    password = getpass.getpass("ERCOT API password: ").strip() or cfg.get("password", "")
    key = input(f"Subscription key [{'set' if cfg.get('subscription_key') else ''}]: ").strip() or cfg.get("subscription_key", "")
    cfg.update({"username": username, "password": password, "subscription_key": key})
    save_config(cfg)
    print(f"\nSaved to {paths.CONFIG_PATH}")
