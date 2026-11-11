"""Steps 1-3: ambulances, MCI site, target hospitals, constants, and the deterministic
features we can compute without AI.

Travel times are straight-line distance x circuity / speed. The flood data does not
route vehicles; it only counts flooded road pieces near each site -> hospital line.
The AI step turns those counts (plus the incident text) into delay factors.
"""

import copy
import json
import math
from functools import lru_cache

from . import config


def distance_km(a, b):
    """Haversine distance between two {"lat", "lon"} points."""
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def travel_min(a, b, c):
    """Clear-road travel time in minutes, before any AI delay factor."""
    return distance_km(a, b) * c["circuity"] / c["speed_kmh"] * 60.0


def make_scenario(site=None, ambulances=None, hospital_ids=None, constants=None):
    """Steps 1-3. Anything not given falls back to the fixed defaults in config.py.

    `constants` may override both DEFAULT_CONSTANTS and ASSUMPTIONS; they are merged into
    one dict, scn["constants"], which every later step reads.
    """
    c = {**config.ASSUMPTIONS, **config.DEFAULT_CONSTANTS}
    c.update({k: v for k, v in (constants or {}).items() if v is not None and k in c})
    ambulances = copy.deepcopy(ambulances or config.DEFAULT_AMBULANCES)[: config.MAX_AMBULANCES]
    if not ambulances:
        raise ValueError("need at least one ambulance")
    ids = hospital_ids or config.DEFAULT_HOSPITAL_IDS
    hospitals = [h for h in config.HOSPITALS if h["id"] in set(ids)]
    if not hospitals:
        raise ValueError(f"no known hospital ids in {ids}")
    return {"site": dict(site or config.DEFAULT_SITE), "ambulances": ambulances,
            "hospitals": hospitals, "constants": c}


# ---------- road-closure features (data copied from the repo's dashboard) ----------

def _xy(lat, lon, lat0=39.29):
    return lon * math.cos(math.radians(lat0)) * 111_320.0, lat * 110_540.0


@lru_cache(maxsize=1)
def _road_chunks():
    """[(x, y, L0), ...]: midpoint and first-flood level (x2) of each 50 m road piece that ever floods."""
    text = config.ROAD_DATA_PATH.read_text()
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    never = data["meta"]["never"]
    out = []
    for _cls, _name, d, ch in data["roads"]:
        pts, x, y = [], 0, 0
        for i in range(0, len(d), 2):
            x += d[i]; y += d[i + 1]
            pts.append((y / 1e5, x / 1e5))
        start = 0
        for k in range(0, len(ch), 4):
            end, l0 = ch[k], ch[k + 1]
            if l0 != never and l0 > 0:
                seg = pts[start:end + 1] or pts[start:start + 1]
                out.append((*_xy(sum(p[0] for p in seg) / len(seg), sum(p[1] for p in seg) / len(seg)), l0))
            start = end
    return out


def _dist_to_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def route_features(scn):
    """Per hospital: distance, clear travel time, and flooded road pieces near the route.

    A piece counts as 'closed' when water is >= 1 ft above its first-flood level and
    'slowed' when 0-1 ft above it, same rule as road_closures.html.
    """
    c = scn["constants"]
    lx2 = round(2 * c["flood_level_ft"])
    sx, sy = _xy(scn["site"]["lat"], scn["site"]["lon"])
    feats = []
    for h in scn["hospitals"]:
        hx, hy = _xy(h["lat"], h["lon"])
        closed = slowed = 0
        for x, y, l0 in _road_chunks():
            if lx2 >= l0 and _dist_to_segment(x, y, sx, sy, hx, hy) <= c["corridor_m"]:
                if lx2 - l0 >= 2:
                    closed += 1
                else:
                    slowed += 1
        feats.append({"hospital": h["id"], "name": h["name"], "trauma": h["trauma"],
                      "km": round(distance_km(scn["site"], h), 2),
                      "clear_min": round(travel_min(scn["site"], h, c), 1),
                      "closed_pieces": closed, "slowed_pieces": slowed})
    amb = [{"ambulance": a["id"], "to_site_clear_min": round(travel_min(a, scn["site"], c), 1)}
           for a in scn["ambulances"]]
    return {"routes": feats, "ambulances": amb}
