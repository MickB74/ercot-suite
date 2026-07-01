"""Turbine power-curve library — normalized output (0–1) vs. hub-height wind speed.

Two ways to get a power curve, in order of fidelity:

  1. **Real published curves (best).** If ``windpowerlib`` is installed, the engine
     can pull a manufacturer power curve from the Open-Energy-Database turbine
     library for any of ~600 real turbine types (see ``wind_power.py``). That is
     the most accurate path and is preferred whenever a real ``turbine_type`` is
     known.

  2. **Parametric curves (always available).** This module — numpy-only, no
     network, no extra deps. A small family of manufacturer-shaped parametric
     curves (cut-in / ramp exponent / rated speed / cut-out) calibrated to match
     the behaviour of the real machines that dominate the ERCOT fleet, plus a
     generic IEC Class II fallback. These are the offline fallback and the curves
     used inside the SCED calibration trainer.

All curves return a value in ``[0, 1]`` that multiplies the segment's nameplate
MW. Operational realism (smooth approach to rated, high-wind taper before
cut-out, hard cut-out) is applied by ``_finalize_power_curve``.
"""

from __future__ import annotations

import numpy as np

# Standard reference air density (kg/m³) that power curves are quoted at (IEC 61400-12).
RHO0 = 1.225


def _finalize_power_curve(v, power, rated_speed, cut_out_speed=25.0,
                          rated_blend_width=0.6, cutout_taper_width=1.5):
    """Apply operational clipping controls to a raw normalized curve.

    - Smooth (logistic) approach to rated to avoid a sharp stair-step.
    - Soft taper just below cut-out to avoid unrealistic high-wind spikes.
    - Hard zero at/above cut-out.
    """
    out = np.clip(np.asarray(power, dtype=float), 0.0, 1.0)

    if rated_blend_width > 0:
        rated_start = rated_speed - rated_blend_width
        rated_end = rated_speed + rated_blend_width
        mask = (v >= rated_start) & (v < rated_end)
        if np.any(mask):
            x = (v[mask] - rated_start) / (rated_end - rated_start)
            blend = 1.0 / (1.0 + np.exp(-8.0 * (x - 0.5)))
            out[mask] = (1.0 - blend) * out[mask] + blend

    taper_start = cut_out_speed - cutout_taper_width
    mask = (v >= taper_start) & (v < cut_out_speed)
    if np.any(mask):
        taper = (cut_out_speed - v[mask]) / cutout_taper_width
        out[mask] = np.minimum(out[mask], np.clip(taper, 0.0, 1.0))

    out[v >= cut_out_speed] = 0.0
    return np.clip(out, 0.0, 1.0)


def _ramp(v, cut_in, rated, exponent, cut_out=25.0):
    """Generic cut-in → rated power ramp with a tunable exponent."""
    power = np.zeros_like(v, dtype=float)
    mask = (v >= cut_in) & (v < rated)
    power[mask] = ((v[mask] - cut_in) / (rated - cut_in)) ** exponent
    power[(v >= rated) & (v < cut_out)] = 1.0
    return _finalize_power_curve(v, power, rated_speed=rated, cut_out_speed=cut_out)


# ---------------------------------------------------------------------------
# Parametric curve family. Each entry: (cut_in, rated, exponent, cut_out).
# Tuned to the real machines that dominate the ERCOT wind fleet.
# ---------------------------------------------------------------------------

PARAMETRIC_CURVES = {
    # Modern low-specific-power / large-rotor machines reach rated early → high CF.
    "VESTAS_V163":  (3.0, 10.5, 2.5, 25.0),   # V163-4.5 low-wind specialist
    "NORDEX_N163":  (3.0, 10.0, 2.5, 25.0),   # N163/5.X very aggressive low wind
    "NORDEX_N149":  (3.0, 11.5, 2.8, 25.0),   # N149/4.X-5.X standard-to-low wind
    "GE_3X":        (3.0, 10.5, 2.6, 25.0),   # GE 3.6-154 modern mainstream
    "SG_3_4_132":   (3.0, 10.8, 2.6, 25.0),   # Siemens Gamesa SG 3.4-132
    "AW3000":       (3.0, 11.5, 2.7, 25.0),   # Acciona AW116/125-3000
    "GE_2X":        (3.0, 11.0, 3.0, 25.0),   # GE 2.5-127 / 2.82-127 workhorse
    "GENERIC_IEC2": (3.0, 12.0, 3.0, 25.0),   # IEC Class II proxy (default)
    "GENERIC_IEC1": (3.5, 13.0, 3.0, 25.0),   # IEC Class I (high-wind sites)
    "GE_1X":        (3.5, 12.5, 3.0, 25.0),   # legacy GE 1.5/1.6 MW
    "MWT_1X":       (4.0, 13.0, 3.0, 25.0),   # legacy Mitsubishi MWT62/1.0
}


def get_normalized_power(wind_speed_series, turbine_type="GENERIC"):
    """Normalized output (0–1) for a wind-speed series (m/s) and turbine type.

    ``turbine_type`` may be one of the ``PARAMETRIC_CURVES`` keys, or a few
    common aliases; anything unknown falls back to the IEC Class II generic.
    """
    v = np.asarray(wind_speed_series, dtype=float)
    t = str(turbine_type or "GENERIC").upper().replace("-", "_").replace(" ", "_")

    # Direct catalogue hit.
    if t in PARAMETRIC_CURVES:
        return _ramp(v, *PARAMETRIC_CURVES[t])

    # Aliases / fuzzy mapping for free-text turbine labels.
    if "V163" in t or "V150" in t or "V162" in t:
        key = "VESTAS_V163"
    elif "V136" in t:
        key = "VESTAS_V163"
    elif "N163" in t or "N175" in t:
        key = "NORDEX_N163"
    elif "N149" in t or "N155" in t:
        key = "NORDEX_N149"
    elif "GE" in t and ("3.6" in t or "154" in t or "3.4" in t or "140" in t):
        key = "GE_3X"
    elif "GE" in t and ("2.8" in t or "2.5" in t or "127" in t or "2.3" in t):
        key = "GE_2X"
    elif "GE" in t and ("1.5" in t or "1.6" in t or "1.7" in t or "1.8" in t):
        key = "GE_1X"
    elif t.startswith("SG") or "GAMESA" in t or "SIEMENS" in t:
        key = "SG_3_4_132"
    elif "MWT" in t or "MITSUBISHI" in t:
        key = "MWT_1X"
    elif "AW" in t or "ACCIONA" in t:
        key = "AW3000"
    elif "V110" in t or "V100" in t or "V120" in t or "V117" in t or "V126" in t:
        key = "GENERIC_IEC2"
    elif "GENERIC" in t or t == "":
        key = "GENERIC_IEC2"
    else:
        key = "GENERIC_IEC2"

    return _ramp(v, *PARAMETRIC_CURVES[key])


def get_curve_for_specs(manuf, model, rotor_m=None, rated_kw=None):
    """Pick the best parametric curve key from turbine metadata.

    Mirrors how the USWTDB / EIA records describe turbines (manufacturer + model
    string, optional rotor diameter and rated power). Specific-power
    (rated_kw / swept-area) is the strongest single predictor of curve shape:
    low specific power → large rotor → reaches rated early → high-CF curve.
    """
    model_u = str(model or "").upper()
    manuf_u = str(manuf or "").upper()
    combo = f"{manuf_u} {model_u}"

    # Strong, model-specific matches for machines whose exact curve shape we know.
    for token, key in (
        ("V163", "VESTAS_V163"), ("V162", "VESTAS_V163"), ("V150", "VESTAS_V163"),
        ("N163", "NORDEX_N163"), ("N149", "NORDEX_N149"),
        ("GE3.", "GE_3X"), ("GE3", "GE_3X"), ("3.4-140", "GE_3X"), ("3.6-154", "GE_3X"),
        ("GE2.", "GE_2X"), ("2.82-127", "GE_2X"), ("2.5-127", "GE_2X"), ("2.3-116", "GE_2X"),
        ("GE1.", "GE_1X"), ("1.5-77", "GE_1X"), ("1.5-87", "GE_1X"), ("1.79-100", "GE_1X"),
        ("SG", "SG_3_4_132"), ("SWT-2.3", "GE_2X"), ("SWT-2.7", "GE_2X"),
        ("MWT", "MWT_1X"), ("AW1", "AW3000"),
    ):
        if token in combo:
            return key

    # Specific-power heuristic — the strongest single predictor of curve shape,
    # so it runs BEFORE any coarse model-name fallback. Low specific power (large
    # rotor per MW) reaches rated early → high-CF curve. This correctly routes
    # modern low-wind Vestas/GE machines (e.g. V110-2.0 ≈ 210 W/m², V136-3.45)
    # that were previously mis-mapped to the conservative GENERIC_IEC2.
    if rotor_m and rated_kw and rotor_m > 0:
        swept = np.pi * (float(rotor_m) / 2.0) ** 2
        specific_power = float(rated_kw) * 1000.0 / swept  # W/m²
        if specific_power < 230:
            return "NORDEX_N163"      # very low specific power, high CF
        if specific_power < 300:
            return "GE_3X"
        if specific_power < 380:
            return "GE_2X"
        return "GENERIC_IEC1"          # high specific power → high-wind machine

    # Model-name fallback for older Vestas when rotor/rated are unknown.
    for token, key in (("V110", "GE_3X"), ("V100", "GE_2X"), ("V120", "GE_3X"),
                       ("V117", "GE_3X"), ("V126", "GE_3X")):
        if token in combo:
            return key

    # Rotor-diameter-only heuristic.
    if rotor_m and rotor_m > 145:
        return "VESTAS_V163"
    if rotor_m and rotor_m > 120:
        return "GE_3X"

    return "GENERIC_IEC2"


def density_correct_speed(wind_speed, air_density, turbine_type="GENERIC"):
    """IEC 61400-12 air-density wind-speed correction.

    Power curves are quoted at ρ₀ = 1.225 kg/m³. For active stall / pitch
    turbines the standard correction maps the measured speed to an
    equivalent-density speed: v* = v · (ρ/ρ₀)^(1/3). Reading the standard curve
    at v* reproduces the density effect on power without a second curve.
    """
    rho = np.asarray(air_density, dtype=float)
    rho = np.where(np.isfinite(rho) & (rho > 0), rho, RHO0)
    return np.asarray(wind_speed, dtype=float) * (rho / RHO0) ** (1.0 / 3.0)
