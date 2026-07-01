"""Parity guard for the two wind-engine copies.

The wind engine is vendored in two places that MUST behave identically:
  * standalone:  Ercot_Wind_Forecast/
  * Hub copy:    Ercot_Data_Hub/datasets/wind_forecast/  (what plant_value + the
                 portals actually import)

They diverged once (2026-06: the Hub retuned PARAMETRIC_CURVES and widened the
region-bias clamp; the standalone lagged), which mis-calibrated ws_scale (learned
in the standalone) against the production curves. This test fails on any future
drift in the numbers that matter, so a fix applied to one copy can't silently
skip the other. Docstrings and the per-repo tz import are allowed to differ."""

from __future__ import annotations

import importlib.util
import pathlib

_HUB = pathlib.Path(__file__).resolve().parents[2]                  # Ercot_Data_Hub
_STANDALONE = _HUB.parent / "Ercot_Wind_Forecast"
_HUB_WF = _HUB / "datasets" / "wind_forecast"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parametric_curves_identical():
    a = _load(_STANDALONE / "power_curves.py", "pc_standalone")
    b = _load(_HUB_WF / "power_curves.py", "pc_hub")
    assert a.PARAMETRIC_CURVES == b.PARAMETRIC_CURVES, (
        "power_curves.PARAMETRIC_CURVES diverged between the two wind-engine copies")


def test_curve_selection_identical():
    a = _load(_STANDALONE / "power_curves.py", "pc_standalone2")
    b = _load(_HUB_WF / "power_curves.py", "pc_hub2")
    cases = [("Vestas", "V110-2.0", 110, 2000), ("Nordex", "N149/4.8", 149, 4800),
             ("GE", "1.5-77", 77, 1500), ("Vestas", "V163", 163, 4500),
             ("", "", None, None)]
    for mf, mdl, rot, rat in cases:
        assert a.get_curve_for_specs(mf, mdl, rot, rat) == b.get_curve_for_specs(mf, mdl, rot, rat), (
            f"get_curve_for_specs disagrees for {mf} {mdl}")


def test_region_bias_clamp_identical():
    a = _load(_STANDALONE / "wind_calibration.py", "wc_standalone")
    b = _load(_HUB_WF / "wind_calibration.py", "wc_hub")
    # A learned prior above the old 1.25 clamp exposes a clamp-band divergence.
    tbl = {"hub_multiplier": {"WEST": 1.9}}
    ra, _ = a.region_bias_multiplier(hub_name="WEST", table=tbl)
    rb, _ = b.region_bias_multiplier(hub_name="WEST", table=tbl)
    assert ra == rb, f"region_bias_multiplier clamp diverged: {ra} vs {rb}"


def test_region_split_identical():
    a = _load(_STANDALONE / "wind_calibration.py", "wc_standalone2")
    b = _load(_HUB_WF / "wind_calibration.py", "wc_hub2")
    for lat, lon in [(26.33, -97.59), (26.47, -98.41), (32.4, -100.5), (35.2, -101.8)]:
        assert a.region_for(lat=lat, lon=lon) == b.region_for(lat=lat, lon=lon), (
            f"region_for disagrees at ({lat},{lon})")
