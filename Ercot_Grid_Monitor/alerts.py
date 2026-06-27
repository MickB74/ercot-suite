"""Grid-event alerts — evaluate price rules against live ERCOT data, notify by
email and/or SMS. Standalone (gridstatus-powered); the free counterpart to
GridStatus.io's "SMS and email alerts for grid events".

A rule watches one settlement point's latest price (RT15 or DAM) and fires when
it crosses a threshold. Rules and notifier creds live in alerts_config.json
(git-ignored); per-rule cooldowns are tracked in .alerts_state.json so a
sustained spike doesn't re-alert every run.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

import ercot

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "alerts_config.json"
STATE_PATH = HERE / ".alerts_state.json"

_METRIC_MARKET = {"rt_price": "RT15", "dam_price": "DAM"}
_OPS = {
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


@dataclass
class Rule:
    id: str
    label: str
    location: str
    op: str
    threshold: float
    metric: str = "rt_price"            # rt_price | dam_price
    location_type: str = "Trading Hub"  # Trading Hub | Load Zone
    cooldown_min: int = 60
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def describe(self) -> str:
        m = _METRIC_MARKET.get(self.metric, self.metric)
        return f"{self.location} {m} {self.op} ${self.threshold:,.0f}/MWh"


# --------------------------------------------------------------------------- #
# Config / state
# --------------------------------------------------------------------------- #
def load_config(path=None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        return {"rules": [], "email": {}, "sms": {}}
    return json.loads(p.read_text())


def load_rules(cfg: dict | None = None) -> list[Rule]:
    cfg = cfg if cfg is not None else load_config()
    return [Rule.from_dict(r) for r in cfg.get("rules", [])]


def save_config(cfg: dict) -> None:
    """Persist the whole config (rules + notifier settings) to alerts_config.json."""
    cfg.setdefault("rules", [])
    cfg.setdefault("email", {})
    cfg.setdefault("sms", {})
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate(rules: list[Rule]) -> list[dict]:
    """Check each enabled rule; return {rule, value, interval, triggered, error}."""
    results = []
    for r in rules:
        if not r.enabled:
            continue
        res = {"rule": r, "value": None, "interval": None,
               "triggered": False, "error": None}
        try:
            market = _METRIC_MARKET.get(r.metric)
            if market is None:
                raise ValueError(f"unknown metric {r.metric!r}")
            if r.op not in _OPS:
                raise ValueError(f"unknown op {r.op!r}")
            val, interval = ercot.latest_price(r.location, r.location_type, market)
            res["value"], res["interval"] = val, interval
            if val is not None:
                res["triggered"] = _OPS[r.op](val, r.threshold)
        except Exception as e:  # noqa: BLE001 — one bad rule shouldn't kill the run
            res["error"] = str(e)
        results.append(res)
    return results


# --------------------------------------------------------------------------- #
# Notifiers
# --------------------------------------------------------------------------- #
def send_email(cfg: dict, subject: str, body: str) -> tuple[bool, str]:
    if not cfg.get("enabled"):
        return False, "email disabled"
    to = cfg.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not to:
        return False, "no recipients"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from") or cfg.get("username", "")
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    host, port = cfg.get("host", "smtp.gmail.com"), int(cfg.get("port", 465))
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
                if cfg.get("username"):
                    s.login(cfg["username"], cfg.get("password", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as s:
                s.starttls(context=ssl.create_default_context())
                if cfg.get("username"):
                    s.login(cfg["username"], cfg.get("password", ""))
                s.send_message(msg)
        return True, f"emailed {len(to)} recipient(s)"
    except Exception as e:  # noqa: BLE001
        return False, f"email failed: {e}"


def send_sms(cfg: dict, body: str) -> tuple[bool, str]:
    """Send via Twilio's REST API (stdlib only — no twilio package needed)."""
    if not cfg.get("enabled"):
        return False, "sms disabled"
    sid, token = cfg.get("account_sid"), cfg.get("auth_token")
    frm = cfg.get("from")
    to = cfg.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not (sid and token and frm and to):
        return False, "sms not fully configured"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, sid, token)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))
    sent, errs = 0, []
    for number in to:
        data = urllib.parse.urlencode({"From": frm, "To": number,
                                       "Body": body[:1500]}).encode()
        try:
            opener.open(urllib.request.Request(url, data=data), timeout=20).read()
            sent += 1
        except Exception as e:  # noqa: BLE001
            errs.append(f"{number}: {e}")
    return sent > 0, f"texted {sent}/{len(to)}" + (f"; errors: {errs}" if errs else "")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _format_alert(res: dict) -> str:
    r: Rule = res["rule"]
    return (f"⚡ ERCOT alert: {r.label}\n"
            f"{r.location} ({r.location_type}) {_METRIC_MARKET.get(r.metric, r.metric)} = "
            f"${res['value']:,.2f}/MWh  (rule: {r.op} ${r.threshold:,.0f})\n"
            f"as of {res['interval']}")


def run(config_path=None, *, force: bool = False, verbose: bool = True) -> dict:
    """Evaluate rules, notify on fresh triggers (respecting cooldowns), persist state.

    force=True ignores cooldowns (manual test-fire). Returns a summary dict.
    """
    cfg = load_config(config_path)
    rules = load_rules(cfg)
    state = _load_state()
    now = pd.Timestamp.now(tz="US/Central")

    results = evaluate(rules)
    fired, skipped, notes = [], [], []
    for res in results:
        if res["error"] and verbose:
            print(f"  ! {res['rule'].id}: {res['error']}")
        if not res["triggered"]:
            continue
        r: Rule = res["rule"]
        last = state.get(r.id, {}).get("last_fired")
        if last and not force:
            mins = (now - pd.Timestamp(last)).total_seconds() / 60
            if mins < r.cooldown_min:
                skipped.append(r.id)
                continue
        body = _format_alert(res)
        eok, edetail = send_email(cfg.get("email", {}), f"ERCOT alert: {r.label}", body)
        sok, sdetail = send_sms(cfg.get("sms", {}), body)
        notes.append(f"{r.id}: {edetail}; {sdetail}")
        state[r.id] = {"last_fired": str(now), "last_value": res["value"]}
        fired.append(r.id)
        if verbose:
            print(f"  🔔 {r.label}: ${res['value']:,.2f} → email[{eok}] sms[{sok}]")

    _save_state(state)
    summary = {"checked": len([x for x in results if x["rule"].enabled]),
               "fired": fired, "skipped_cooldown": skipped, "notes": notes,
               "results": results}
    if verbose:
        print(f"alerts: checked {summary['checked']}, fired {len(fired)}, "
              f"on cooldown {len(skipped)}.")
    return summary


if __name__ == "__main__":
    import sys
    run(force="--force" in sys.argv)
