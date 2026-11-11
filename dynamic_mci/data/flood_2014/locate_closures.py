"""Place each NWS road-closure report of Aug 12, 2014 on the road it names, for Valhalla exclusion.

NWS Local Storm Report coordinates are rounded to about 1 km, so using them directly would block the
wrong roads. For each closure this script searches the local Valhalla graph (via /locate) around the
report point for edges whose names match the road in the report, picks the one nearest the cross
street the report gives, adds the opposite carriageway unless the report names one direction, and
then checks the result: a route running along the road through that point must change (detour or
fail) when the points are excluded. Output: closures.json (committed) used by the dashboard backend.

Needs Valhalla on VALHALLA_URL (default http://127.0.0.1:8002). Run from dynamic_mci/:
  ../.venv/bin/python data/flood_2014/locate_closures.py
"""

import json
import math
import os
import re
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
VALHALLA = os.getenv("VALHALLA_URL", "http://127.0.0.1:8002").rstrip("/")
CITY_CENTRE = (39.2904, -76.6122)  # for "outer loop" = clockwise around Baltimore

# One entry per NWS report that names a specific closed road (times are local EDT).
# The 2:15 PM Sawmill Creek gauge report ("numerous roads were closed") names no road and is left out.
CLOSURES = [
    {"id": "bowie_rd", "time": "12:45", "road": "Bowie Rd east of US 1, Laurel", "source": "Broadcast Media",
     "near": (39.10, -76.83), "target": r"Bowie", "cross": r"Baltimore Avenue|Washington Boulevard|^US 1$"},
    {"id": "dorsey_rd", "time": "13:15", "road": "Dorsey Rd at Sawmill Creek (between Central Ave and MD 648), Glen Burnie",
     "source": "Dept of Highways", "near": (39.18, -76.62), "target": r"^Dorsey Road$|^MD 176$",
     "between": (r"Central Avenue", r"Baltimore Annapolis Boulevard|^MD 648$")},
    {"id": "aviation_blvd", "time": "13:47", "road": "Aviation Blvd at the BWI rail station road", "source": "Dept of Highways",
     "near": (39.18, -76.69), "target": r"Aviation Boulevard", "cross": r"Amtrak Way"},
    {"id": "md3", "time": "13:52", "road": "MD 3 (Crain Hwy) near Furnace Branch Rd, all lanes", "source": "Dept of Highways",
     "near": (39.18, -76.61), "target": r"Crain Highway|^MD 3", "cross": r"Furnace Branch Road"},
    {"id": "i695_outer", "time": "14:22", "road": "I-695 outer loop at Quarantine Rd", "source": "Dept of Highways",
     "near": (39.20, -76.56), "target": r"I 695|Baltimore Beltway", "cross": r"Quarantine Road",
     "direction": "clockwise"},
    # A ~4 km closed stretch: one block on the main line (both carriageways) stops through traffic.
    {"id": "md295", "time": "14:25", "road": "MD 295 between I-695 and Westport", "source": "Dept of Highways",
     "near": (39.245, -76.65), "target": r"^MD 295$|Baltimore-Washington Parkway",
     "anchors": [(39.232, -76.652)]},
    {"id": "harbor_tunnel", "time": "14:29", "road": "Baltimore Harbor Tunnel (I-895)", "source": "Dept of Highways",
     "near": (39.255, -76.575), "target": r"Baltimore Harbor Tunnel|^I 895", "tunnel": True},
    {"id": "md648_magothy", "time": "17:19", "road": "Baltimore Annapolis Blvd at the Magothy River crossing", "source": "Dept of Highways",
     "near": (39.10, -76.55), "target": r"Baltimore Annapolis Boulevard|^MD 648$", "bridge": True},
]


def dist_m(a, b):
    dlat = math.radians(b[0] - a[0]); dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def bearing(a, b):
    la1, la2 = math.radians(a[0]), math.radians(b[0]); dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(la2); x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def offset(p, heading_deg, metres):
    r = metres / 6371000; h = math.radians(heading_deg); la = math.radians(p[0]); lo = math.radians(p[1])
    la2 = math.asin(math.sin(la) * math.cos(r) + math.cos(la) * math.sin(r) * math.cos(h))
    lo2 = lo + math.atan2(math.sin(h) * math.sin(r) * math.cos(la), math.cos(r) - math.sin(la) * math.sin(la2))
    return (math.degrees(la2), math.degrees(lo2))


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def scan(near, radius_m=3200, step_m=120):
    """All named, drivable edges Valhalla finds on a grid around `near`."""
    pts = []
    n = int(radius_m // step_m)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            p = offset(offset(near, 0, i * step_m), 90, j * step_m)
            if dist_m(near, p) <= radius_m:
                pts.append(p)
    found = []
    for k in range(0, len(pts), 250):
        body = {"verbose": True, "costing": "auto",
                "locations": [{"lat": p[0], "lon": p[1], "radius": int(step_m * 0.75)} for p in pts[k:k + 250]]}
        r = requests.post(f"{VALHALLA}/locate", json=body, timeout=120)
        r.raise_for_status()
        for loc in r.json():
            for e in loc.get("edges") or []:
                names = (e.get("edge_info") or {}).get("names") or []
                if names:
                    edge = e.get("edge") or {}
                    found.append({"lat": e["correlated_lat"], "lon": e["correlated_lon"], "heading": e.get("heading"),
                                  "names": names, "tunnel": edge.get("tunnel", False), "bridge": edge.get("bridge", False)})
    return found


def matches(edge, pattern):
    return pattern and any(re.search(pattern, n, re.I) for n in edge["names"])


def decode(shape, precision=6):
    coords, idx, lat, lon, f = [], 0, 0, 0, 10 ** precision
    while idx < len(shape):
        for axis in (0, 1):
            res = sh = 0
            while True:
                b = ord(shape[idx]) - 63; idx += 1
                res |= (b & 31) << sh; sh += 5
                if b < 32:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if axis == 0:
                lat += d
            else:
                lon += d
        coords.append((lat / f, lon / f))
    return coords


def route(a, b, exclude=None):
    body = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}], "costing": "auto", "units": "miles"}
    if exclude:
        body["exclude_locations"] = exclude
    r = requests.post(f"{VALHALLA}/route", json=body, timeout=60)
    if r.status_code != 200:
        return None
    trip = r.json()["trip"]
    shape = [pt for leg in trip["legs"] for pt in decode(leg["shape"])]
    return {"min": round(trip["summary"]["time"] / 60, 2), "shape": shape}


def seg_dist_m(p, a, b):
    """Distance from p to segment ab (local flat projection; fine at these scales)."""
    k = math.cos(math.radians(p[0])) * 111320
    ax, ay, bx, by, px, py = a[1] * k, a[0] * 110570, b[1] * k, b[0] * 110570, p[1] * k, p[0] * 110570
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def passes(rt, p, tol_m=25):
    # Long straight edges (tunnels, freeways) have few vertices, so measure to segments, not vertices.
    s = rt["shape"] if rt else []
    return len(s) > 1 and min(seg_dist_m(p, s[i], s[i + 1]) for i in range(len(s) - 1)) <= tol_m


def check_point(p, exclude):
    """Find a stretch of road through p that the open route uses; excluding must push the route off p."""
    here = (p["lat"], p["lon"])
    for d in (350, 1200, 2500, 4000):
        a = offset(here, p["heading"] + 180, d)
        b = offset(here, p["heading"], d)
        open_rt = route(a, b)
        if not passes(open_rt, here):
            continue
        closed_rt = route(a, b, exclude)
        if closed_rt is None:
            continue  # endpoints snapped onto the excluded edge itself; try a longer stretch
        # A point at a junction can still be passed by the detour along the cross street, so a clear
        # increase in travel time also counts as blocked.
        blocked = not passes(closed_rt, here) or closed_rt["min"] > open_rt["min"] * 1.1
        return {"heading": round(p["heading"]), "span_m": 2 * d, "open_min": open_rt["min"],
                "closed_min": closed_rt["min"], "blocked": blocked}
    return {"heading": round(p["heading"]), "blocked": None}


def nearest(edges, p):
    return min(edges, key=lambda e: dist_m((e["lat"], e["lon"]), p))


def anchors(spec, edges, road):
    """Where on the named road the closure is: near a cross street, midway between two, or given points."""
    if spec.get("anchors"):
        return [tuple(a) for a in spec["anchors"]], True
    if spec.get("between"):
        ends = []
        for pattern in spec["between"]:
            cross = [e for e in edges if matches(e, pattern)]
            if not cross:
                return [spec["near"]], False
            # The road point closest to any point of this cross street = the junction.
            j = min(((r, min(dist_m((r["lat"], r["lon"]), (c["lat"], c["lon"])) for c in cross)) for r in road),
                    key=lambda t: t[1])[0]
            ends.append((j["lat"], j["lon"]))
        return [((ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2)], True
    cross = [e for e in edges if matches(e, spec.get("cross"))]
    if cross:
        j = min(road, key=lambda r: min(dist_m((r["lat"], r["lon"]), (c["lat"], c["lon"])) for c in cross))
        return [(j["lat"], j["lon"])], True
    return [spec["near"]], False


def place(spec):
    edges = scan(spec["near"])
    road = [e for e in edges if matches(e, spec["target"]) and (not spec.get("tunnel") or e["tunnel"])]
    if spec.get("bridge"):
        road = [e for e in road if e["bridge"]] or road
    if not road:
        raise RuntimeError(f"{spec['id']}: no edge named /{spec['target']}/ near {spec['near']}")
    if spec.get("direction") == "clockwise":
        def clockwise_ok(e):
            want = (bearing(CITY_CENTRE, (e["lat"], e["lon"])) + 90) % 360
            return angle_diff(e["heading"], want) < 60
        road = [e for e in road if clockwise_ok(e)] or road
    where, anchored = anchors(spec, edges, road)
    points = []
    for a in where:
        best = nearest(road, a)
        points.append(best)
        if spec.get("direction") is None:
            # Other carriageway / opposite direction: same road, opposite heading, close by.
            opp = [e for e in road if angle_diff(e["heading"], best["heading"]) > 135
                   and dist_m((e["lat"], e["lon"]), (best["lat"], best["lon"])) < 150]
            if opp:
                points.append(nearest(opp, (best["lat"], best["lon"])))
    best = points[0]
    cross = anchored
    exclude = [{"lat": round(p["lat"], 6), "lon": round(p["lon"], 6), "heading": round(p["heading"]),
                "heading_tolerance": 45} for p in points]
    checks = [check_point(p, exclude) for p in points]
    matched = next(n for n in best["names"] if re.search(spec["target"], n, re.I))
    return {"id": spec["id"], "time_local": f"2014-08-12 {spec['time']}", "road": spec["road"], "source": spec["source"],
            "matched_name": matched, "all_names": best["names"], "exclude_locations": exclude, "check": checks,
            "cross_street_found": bool(cross)}


def main():
    out = []
    for spec in CLOSURES:
        res = place(spec)
        ok = all(c["blocked"] for c in res["check"])
        res["verified"] = ok
        out.append(res)
        print(f"{res['id']:15s} {res['matched_name'][:24]:24s} names={res['all_names']} pts={len(res['exclude_locations'])} "
              f"anchored={'y' if res['cross_street_found'] else 'n'} {'OK' if ok else 'NOT VERIFIED'}")
        for c in res["check"]:
            print("      ", c)
    (HERE / "closures.json").write_text(json.dumps({"event": "Baltimore flash flood, 2014-08-12",
                                                    "method": __doc__.strip().splitlines()[0], "closures": out}, indent=1))
    print("wrote", HERE / "closures.json")


if __name__ == "__main__":
    main()
