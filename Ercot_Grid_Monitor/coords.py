"""Geographic coordinates for ERCOT trading hubs and load zones.

A hub/zone price is an index over many buses, not a single physical point, so
these are representative regional centroids — meant for at-a-glance regional
reads, not exact bus siting.
"""

from __future__ import annotations

import pandas as pd

HUB_ZONE_COORDS: dict[str, tuple[float, float]] = {
    # Trading hubs
    "HB_NORTH":   (32.90, -97.00),   # Dallas–Fort Worth
    "HB_SOUTH":   (29.00, -98.20),   # South Texas (San Antonio / Corpus corridor)
    "HB_WEST":    (32.00, -101.50),  # West Texas (Permian / Big Spring)
    "HB_HOUSTON": (29.76, -95.37),   # Houston
    "HB_PAN":     (35.20, -101.80),  # Panhandle (Amarillo)
    "HB_BUSAVG":  (31.00, -99.30),   # grid bus average — geographic center
    "HB_HUBAVG":  (31.40, -98.70),   # hub average — offset from BUSAVG so both read
    # Load zones
    "LZ_NORTH":   (32.50, -97.60),   # North zone (north-central TX)
    "LZ_SOUTH":   (28.50, -98.60),   # South zone (south TX)
    "LZ_WEST":    (32.10, -100.60),  # West zone (west TX)
    "LZ_HOUSTON": (29.70, -95.30),   # Houston zone
    "LZ_AEN":     (30.27, -97.74),   # Austin Energy
    "LZ_CPS":     (29.42, -98.49),   # CPS Energy (San Antonio)
    "LZ_LCRA":    (30.60, -98.30),   # Lower Colorado River Authority (hill country)
    "LZ_RAYBN":   (33.10, -96.20),   # Rayburn Country (northeast TX)
}

HUBS = [k for k in HUB_ZONE_COORDS if k.startswith("HB_")]
ZONES = [k for k in HUB_ZONE_COORDS if k.startswith("LZ_")]

# Individual resource nodes (settlement points) cached in the suite node-price
# lake. Unlike hubs/zones, a node IS a physical plant, so these lat/lons are the
# actual asset sites — harvested from each portal's contract.py ASSET (and the
# vendored ercot_assets.json registry). Only nodes we can place are listed; a few
# ad-hoc-pulled nodes (e.g. BAKE_RN_ALL) have no known siting and are omitted.
# value = (lat, lon, friendly name).
NODE_COORDS: dict[str, tuple[float, float, str]] = {
    "AZURE_RN":     (33.1534,   -99.2847,   "Azure Sky Wind"),        # Throckmorton
    "MRKM_SLR_RN":  (31.694792, -97.374883, "Markum Solar"),          # Bosque
    "BUZI_SLR_RN":  (33.88,    -100.9,      "Stafford Solar"),        # Motley (Roaring Springs)
    "WH_WIND_ALL":  (32.5501,  -100.569,    "Mesquite Star Wind"),    # Fisher
    "RN_RTS1":      (31.2433,   -99.4076,   "Heart of Texas Wind"),   # McCulloch
    "HRNT_SLR_RN":  (34.485162,-101.971401, "Hornet Solar"),          # Swisher
    "MLB_SLR_RN":   (33.221320, -99.586520, "Millers Branch Solar"),  # Haskell
    "MIL_MILG1_2":  (33.231320, -99.596520, "Millers Branch (Miller)"),  # Haskell (offset to de-overlap)
    "MIRASOLE_GEN": (26.465556, -98.411111, "Mirasole (Hidalgo)"),    # Hidalgo
    "SHANNONW_RN":  (33.78,     -98.20,     "Shannon Wind"),          # Clay (approx)
    "7RNCHSLR_ALL": (29.806984, -97.07408,  "7V Solar Ranch"),        # Fayette (EIA 64239 / interconnection.fyi)
    "CITYVICT_ALL": (28.7883,   -97.01,     "Victoria Power Station"),  # Victoria — gas CCGT (gridinfo)
    "BAKE_RN_ALL":  (33.66,     -95.56,     "Baker Branch Solar"),    # Lamar (approx, county centroid)
    # Renewable assets from the ercot_assets.json registry (lat/lon authoritative
    # there; node resolved via resource_node_catalog). RT15 SPP for these is
    # pulled into the node-price lake — see pull_renew_nodes / pull_nodes.py.
    "ANCHOR_ALL":   (32.297859,  -98.86309,  "Anchor Wind III"),      # Eastland
    "ASCK_SLR_RN":  (31.3341,    -94.7291,   "Azalea Springs Solar"), # Angelina
    "BLVN_RN":      (31.232094,  -96.926213, "Blevins Solar"),        # Falls
    "BYNM_SLR_RN":  (31.368933,  -97.818408, "Bynum Solar"),          # Coryell
    "DIVR_SLR_RN":  (31.526185,  -96.580617, "Diver Solar"),          # Limestone
    "DORA_SLR_RN":  (28.887982,  -96.00351,  "Eldora Solar"),         # Matagorda
    "DRCK_SLR_RN":  (32.051377,  -94.790655, "Dry Creek Solar I"),    # Rusk
    "ELZA_SLR_RN":  (32.601641,  -96.337513, "Eliza Solar"),          # Kaufman
    "GAIA_SLR_RN":  (32.027952,  -96.498546, "Gaia Solar"),           # Navarro
    "GRYH_SLR_RN":  (31.894175, -102.579434, "Greyhound Solar"),      # Ector
    "JKLP_SLR_RN":  (30.705329,  -96.06983,  "Blue Jay Solar"),       # Robertson
    "RN_LNP_SLR":   (29.308,     -95.895,    "Long Point Solar"),     # Fort Bend
    "MIDP_SLR_RN":  (31.959508,  -97.088165, "Midpoint Solar"),       # Hill
    "MONTECR1_RN":  (26.353026,  -98.216445, "Monte Cristo 1 Wind"),  # Hidalgo
    "SWTWN4_WND45": (32.271931, -100.398775, "Maryneal Wind"),        # Nolan
    "NRTN_SLR_RN":  (31.7924,   -100.0826,   "Norton Solar"),         # Runnels
    "ROSELAND_ALL": (31.4612,    -96.8249,   "Roseland Solar"),       # Falls
    "TROJ_SLR_RN":  (33.582486,  -97.20553,  "Trojan Solar"),         # Cooke
    "WILDWIND_ALL": (33.54915,   -97.32958,  "Wildwind"),             # Cooke
    "MROW_SLR_RN":  (32.2285,   -100.4346,   "Maryneal Solar"),       # Nolan
    "FRYE_SLR_ALL": (34.4067,   -101.5966,   "Frye Solar"),           # Swisher
    "AJAXWIND_RN":  (33.8525,    -99.0651,   "Ajax Wind"),            # Wilbarger
    "SHAFFER_ALL":  (27.6021,    -97.6471,   "Shaffer Wind"),         # Nueces
    "AZSP_SLR_RN":  (34.5445,   -102.1005,   "Nazareth Solar"),       # Castro
    "PISG_RN_ALL":  (30.6724,    -93.8837,   "Pine Forest Solar"),    # (registry)
    "CABEZON_ALL":  (26.353,     -98.216,    "Rio Bravo Wind"),       # Starr
    "ROUTE66_RN":   (35.222,    -101.831,    "Route 66 Wind"),        # Armstrong
    "HHOLLW2_WND1": (32.32,     -100.22,     "Horse Hollow"),         # Taylor
    "SPLAIN1_RN":   (34.18,     -101.35,     "South Plains Wind"),    # Floyd
    "GPASTURE_ALL": (33.648719,  -99.455534, "Green Pastures"),       # Baylor
    "HRFDWIND_ALL": (34.82,     -102.4,      "Hereford Wind"),        # Deaf Smith
    "CAMWIND_RN":   (26.15,      -97.5,      "Cameron Wind"),         # Cameron
    "RN_SR_WIND1":  (26.05,      -97.4,      "San Roman Wind"),       # Cameron
    "TYLRWIND_RN":  (33.65,      -97.2,      "Tyler Bluff Wind"),     # Cooke
    "FTWIND_UNIT1": (31.63197,   -98.570415, "Flat Top Wind"),        # Mills
    "BCATWD_WD_1":  (33.50355,   -98.578714, "Bobcat Wind"),          # Archer
    "GOA_GOATWIND": (31.83,     -100.87,     "Goat Mountain Wind"),   # Sterling
    "SRWE1_UNIT1":  (26.5,       -98.5,      "South Ranch Wind"),     # Hidalgo
    "VERAWIND_ALL": (33.6,       -99.4,      "Vera Wind"),            # Knox
}

NODES = list(NODE_COORDS)

# Plain-English names for the pickers.
LABELS: dict[str, str] = {
    "HB_HUBAVG": "Hub average (grid-wide)",
    "HB_BUSAVG": "Bus average (grid-wide)",
    "HB_NORTH": "North hub (Dallas–Fort Worth)",
    "HB_SOUTH": "South hub",
    "HB_WEST": "West hub (Permian)",
    "HB_HOUSTON": "Houston hub",
    "HB_PAN": "Panhandle hub",
    "LZ_NORTH": "North zone",
    "LZ_SOUTH": "South zone",
    "LZ_WEST": "West zone",
    "LZ_HOUSTON": "Houston zone",
    "LZ_AEN": "Austin Energy zone",
    "LZ_CPS": "San Antonio (CPS) zone",
    "LZ_LCRA": "LCRA zone (hill country)",
    "LZ_RAYBN": "Rayburn zone (northeast TX)",
}


def label(loc: str) -> str:
    """Friendly name + code, e.g. 'Hub average (grid-wide) — HB_HUBAVG'."""
    if loc in NODE_COORDS:
        return f"{NODE_COORDS[loc][2]} — {loc}"
    return f"{LABELS.get(loc, loc)} — {loc}"


def locations(location_type: str) -> list[str]:
    if location_type == "Resource Node":
        return NODES
    return HUBS if location_type == "Trading Hub" else ZONES


def coords_frame(location_type: str | None = None) -> pd.DataFrame:
    """location, latitude, longitude — optionally filtered to one type."""
    if location_type == "Resource Node":
        return pd.DataFrame([(loc, lat, lon) for loc, (lat, lon, _n) in NODE_COORDS.items()],
                            columns=["location", "latitude", "longitude"])
    items = HUB_ZONE_COORDS.items()
    if location_type is not None:
        want = set(locations(location_type))
        items = [(k, v) for k, v in items if k in want]
    return pd.DataFrame([(loc, lat, lon) for loc, (lat, lon) in items],
                        columns=["location", "latitude", "longitude"])
