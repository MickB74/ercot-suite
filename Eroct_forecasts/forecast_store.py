"""Persist and reload forecast artifacts under data/forecasts/."""

from __future__ import annotations

import json

import pandas as pd

import pf_paths


def _stub(meta: dict) -> str:
    return f"{meta['hub']}_{meta['asof']}"


def save(curve: pd.DataFrame, meta: dict, hourly: pd.DataFrame | None = None) -> dict:
    pf_paths.ensure_dirs()
    stub = _stub(meta)
    paths = {}
    p = pf_paths.FORECASTS_DIR / f"forecast_{stub}.parquet"
    curve.to_parquet(p, index=False)
    paths["monthly"] = str(p)

    pm = pf_paths.FORECASTS_DIR / f"forecast_{stub}.meta.json"
    pm.write_text(json.dumps(meta, indent=2, default=str))
    paths["meta"] = str(pm)

    pc = pf_paths.FORECASTS_DIR / f"forecast_{stub}.csv"
    curve.assign(month=pd.to_datetime(curve["month"]).dt.strftime("%Y-%m")).to_csv(pc, index=False)
    paths["csv"] = str(pc)

    if hourly is not None:
        ph = pf_paths.FORECASTS_DIR / f"forecast_{stub}_8760.parquet"
        hourly.to_parquet(ph, index=False)
        paths["hourly"] = str(ph)
    return paths


def load(hub: str, asof: str) -> tuple[pd.DataFrame, dict]:
    stub = f"{hub}_{asof}"
    curve = pd.read_parquet(pf_paths.FORECASTS_DIR / f"forecast_{stub}.parquet")
    meta = json.loads((pf_paths.FORECASTS_DIR / f"forecast_{stub}.meta.json").read_text())
    return curve, meta


def list_forecasts() -> list[str]:
    return sorted(p.stem.replace("forecast_", "")
                  for p in pf_paths.FORECASTS_DIR.glob("forecast_*.parquet")
                  if not p.stem.endswith("_8760"))
