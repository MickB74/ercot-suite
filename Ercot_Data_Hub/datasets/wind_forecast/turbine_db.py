"""Resolve the *actual turbines* installed at a coordinate, from the USWTDB.

The U.S. Wind Turbine Database (USWTDB, USGS/LBNL/AWEA) records every utility
wind turbine in the country at turbine-level resolution: manufacturer, model,
hub height, rotor diameter, rated capacity, commissioning year, project name,
and exact lat/long. This module turns a coordinate into the real turbine
**fleet** at that site so the forecast is built from the machines that are
actually there — not a guessed generic turbine.

A Texas extract (``reference/uswtdb_tx.json``) is bundled for offline use. The
full national database can be refreshed from the public USGS API with
``refresh_national()`` (writes ``reference/uswtdb_us.json``).

Record fields used (USWTDB schema):
  p_name  project name          t_manu  manufacturer      t_hh   hub height (m)
  p_cap   project capacity (MW) t_model model string       t_rd   rotor dia (m)
  xlong   longitude             t_cap   turbine rated (kW) p_year project year
  ylat    latitude
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import power_curves

HERE = Path(__file__).resolve().parent
TX_DB = HERE / "reference" / "uswtdb_tx.json"
US_DB = HERE / "reference" / "uswtdb_us.json"

USWTDB_API = "https://eersc.usgs.gov/api/uswtdb/v1/turbines"
_EARTH_KM = 6371.0


@dataclass
class TurbineSegment:
    """One homogeneous group of turbines within a project."""

    manufacturer: str
    model: str
    count: int
    rated_kw: float
    hub_height_m: float
    rotor_m: float
    curve_key: str = "GENERIC_IEC2"

    @property
    def capacity_mw(self) -> float:
        return self.count * self.rated_kw / 1000.0


@dataclass
class ProjectFleet:
    """A wind project and its turbine composition, resolved from USWTDB."""

    name: str
    lat: float
    lon: float
    segments: list[TurbineSegment] = field(default_factory=list)
    project_year: int | None = None
    distance_km: float = 0.0

    @property
    def capacity_mw(self) -> float:
        return sum(s.capacity_mw for s in self.segments)

    @property
    def n_turbines(self) -> int:
        return sum(s.count for s in self.segments)

    @property
    def mean_hub_height_m(self) -> float:
        tot = self.n_turbines
        if not tot:
            return 0.0
        return sum(s.hub_height_m * s.count for s in self.segments) / tot

    def describe(self) -> str:
        parts = [f"{s.count}× {s.manufacturer} {s.model}".strip() for s in self.segments]
        return "; ".join(parts)


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def _load_records(path: Path | None = None) -> list[dict]:
    """Load USWTDB records, preferring the national file if present."""
    for p in ([path] if path else [US_DB, TX_DB]):
        if p and p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
    return []


def _segment_key(rec: dict) -> tuple:
    return (str(rec.get("t_manu") or "Unknown").strip(),
            str(rec.get("t_model") or "Unknown").strip(),
            float(rec.get("t_hh") or 0) or 0.0,
            float(rec.get("t_rd") or 0) or 0.0,
            float(rec.get("t_cap") or 0) or 0.0)


def _build_segments(records: list[dict], fallback_hub: float) -> list[TurbineSegment]:
    groups: dict[tuple, int] = defaultdict(int)
    for r in records:
        groups[_segment_key(r)] += 1
    segs = []
    for (manuf, model, hh, rd, cap_kw), n in sorted(groups.items(), key=lambda kv: -kv[1]):
        hub = hh if hh > 0 else fallback_hub
        rated = cap_kw if cap_kw > 0 else 2500.0
        segs.append(TurbineSegment(
            manufacturer=manuf, model=model, count=n, rated_kw=rated,
            hub_height_m=hub, rotor_m=rd,
            curve_key=power_curves.get_curve_for_specs(manuf, model, rd or None, rated),
        ))
    return segs


def find_project_near(lat: float, lon: float, radius_km: float = 8.0,
                      records: list[dict] | None = None,
                      fallback_hub: float = 90.0) -> ProjectFleet | None:
    """Resolve the wind project nearest to ``(lat, lon)`` and its turbine fleet.

    All turbines whose project (``p_name``) has any turbine within ``radius_km``
    of the point are grouped into homogeneous segments. Returns ``None`` if no
    turbine is found within the radius (e.g. a greenfield coordinate).
    """
    records = records if records is not None else _load_records()
    if not records:
        return None

    # Nearest turbine → its project name.
    best = None
    best_d = float("inf")
    for r in records:
        ylat, xlong = r.get("ylat"), r.get("xlong")
        if ylat is None or xlong is None:
            continue
        d = _haversine_km(lat, lon, float(ylat), float(xlong))
        if d < best_d:
            best_d, best = d, r
    if best is None or best_d > radius_km:
        return None

    pname = str(best.get("p_name") or "Unknown project")
    members = [r for r in records if str(r.get("p_name")) == pname]

    lats = [float(r["ylat"]) for r in members if r.get("ylat") is not None]
    lons = [float(r["xlong"]) for r in members if r.get("xlong") is not None]
    clat = sum(lats) / len(lats) if lats else lat
    clon = sum(lons) / len(lons) if lons else lon
    years = [int(r["p_year"]) for r in members if r.get("p_year")]

    return ProjectFleet(
        name=pname, lat=clat, lon=clon,
        segments=_build_segments(members, fallback_hub),
        project_year=max(years) if years else None,
        distance_km=round(best_d, 2),
    )


def list_projects(records: list[dict] | None = None) -> list[dict]:
    """All distinct projects with capacity + centroid, for a pick list."""
    records = records if records is not None else _load_records()
    by_proj: dict[str, dict] = {}
    for r in records:
        name = str(r.get("p_name") or "Unknown")
        e = by_proj.setdefault(name, {"name": name, "lats": [], "lons": [],
                                      "cap": r.get("p_cap"), "n": 0, "year": r.get("p_year")})
        if r.get("ylat") is not None:
            e["lats"].append(float(r["ylat"]))
            e["lons"].append(float(r["xlong"]))
        e["n"] += 1
    out = []
    for e in by_proj.values():
        if not e["lats"]:
            continue
        out.append({
            "name": e["name"],
            "lat": round(sum(e["lats"]) / len(e["lats"]), 5),
            "lon": round(sum(e["lons"]) / len(e["lons"]), 5),
            "capacity_mw": float(e["cap"]) if e["cap"] else None,
            "n_turbines": e["n"],
            "year": e["year"],
        })
    return sorted(out, key=lambda d: d["name"])


def refresh_national(out_path: Path | None = None, state: str | None = None) -> Path:
    """Download the latest USWTDB from the USGS API → JSON on disk.

    ``state`` (e.g. ``"TX"``) restricts the download; ``None`` pulls the whole
    country (~75k turbines). Paginated to respect the API row cap.
    """
    import requests

    out_path = out_path or (US_DB if not state else (HERE / "reference" / f"uswtdb_{state.lower()}.json"))
    cols = ("case_id,p_name,p_year,p_cap,t_manu,t_model,t_cap,t_hh,t_rd,t_rsa,"
            "xlong,ylat,t_state,t_county")
    rows: list[dict] = []
    limit = 5000
    offset = 0
    while True:
        params = {"select": cols, "limit": limit, "offset": offset}
        if state:
            params["t_state"] = f"eq.{state.upper()}"
        r = requests.get(USWTDB_API, params=params, timeout=120,
                         headers={"Accept": "application/json"})
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows))
    return out_path


if __name__ == "__main__":
    # Smoke test against the bundled TX extract (Azure Sky Wind ≈ 33.15, -99.28).
    fleet = find_project_near(33.1534, -99.2847, radius_km=10)
    if fleet:
        print(f"{fleet.name}  ({fleet.distance_km} km, {fleet.project_year})")
        print(f"  {fleet.n_turbines} turbines, {fleet.capacity_mw:.1f} MW, "
              f"mean hub {fleet.mean_hub_height_m:.0f} m")
        for s in fleet.segments:
            print(f"  {s.count:>3}× {s.manufacturer} {s.model:<14} "
                  f"{s.rated_kw:.0f}kW  hh={s.hub_height_m:.0f}m rd={s.rotor_m:.0f}m  → {s.curve_key}")
    else:
        print("no project found")
