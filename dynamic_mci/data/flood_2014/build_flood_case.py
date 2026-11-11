"""Build frontend/flood_2014.json: the August 12, 2014 Baltimore flash flood case for the dashboard.

Inputs (this folder, small and committed):
  lsr_lwx_20140812.geojson  NWS Local Storm Reports, office LWX, 2014-08-12 08Z to 08-13 12Z, from the
                            Iowa Environmental Mesonet: mesonet.agron.iastate.edu/geojson/lsr.php
  bwi_asos_20140812.csv     BWI hourly precipitation (p01i, inches, local time), Iowa Environmental Mesonet
                            ASOS archive: mesonet.agron.iastate.edu/cgi-bin/request/asos.py
Input (large, not committed; pass its path):
  natural_disaster_human_mobility.csv  Wang & Taylor geotagged-Twitter mobility data (Kaggle / Dryad).
      Event "33_Baltimore" covers 2014-07-28 to 08-28. NOTE the columns are mislabelled: "latitude"
      holds the real longitude, and "longitude.anon" holds a latitude the authors shifted on purpose to
      anonymise users by a constant offset. The data are clipped to Baltimore City's bounding box,
      so the offset is recovered from the city's southern edge (both edges agree to 0.0002 deg).
      Only aggregates leave this script: users per hour, radius of gyration, and a ~450 m grid of
      distinct-user counts with sparse cells suppressed. No user id, timestamp or point is exported.

Run from dynamic_mci/:
  ../.venv/bin/python data/flood_2014/build_flood_case.py ../data/natural_disaster_human_mobility.csv
"""

import argparse
import csv
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "frontend" / "flood_2014.json"
EVENT_DAY = "2014-08-12"
# Same weekday as the event, outside the event day, inside the data window.
COMPARE_DAYS = ["2014-07-29", "2014-08-05", "2014-08-19", "2014-08-26"]
EDT = timedelta(hours=-4)                      # NWS reports are UTC; Baltimore in August is UTC-4
REPORT_BBOX = (-76.95, 39.05, -76.35, 39.55)   # Baltimore metro (lon_min, lat_min, lon_max, lat_max)
BALTIMORE_CITY_LAT_MIN = 39.1972               # southern edge of Baltimore City (Census TIGER place extent)
GRID_DEG = (0.004, 0.005)                      # cell size in (lat, lon) degrees, about 445 m x 430 m
MIN_USERS_PERIOD = 10                          # suppress cells with fewer distinct users over the 31 days
MIN_USERS_DAY = 5                              # ... and on the single event day
KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON = 111.32 * math.cos(math.radians(39.29))  # Baltimore's true latitude, for east-west scale

SOURCES = [
    {"label": "NWS Baltimore/Washington, Storm Data, August 2014",
     "url": "https://www.weather.gov/media/lwx/stormdata/storm0814.pdf"},
    {"label": "NWS monthly hazards summary (PRESTO), August 2014",
     "url": "https://www.weather.gov/media/publications/presto/2014aug.pdf"},
    {"label": "NWS Local Storm Reports via Iowa Environmental Mesonet",
     "url": "https://mesonet.agron.iastate.edu/lsr/"},
    {"label": "BWI hourly precipitation (ASOS) via Iowa Environmental Mesonet",
     "url": "https://mesonet.agron.iastate.edu/request/download.phtml"},
    {"label": "Wang & Taylor, human mobility during natural disasters (Kaggle / Dryad)",
     "url": "https://www.kaggle.com/datasets/dryad/human-mobility-during-natural-disasters"},
]


def rainfall():
    hours = []
    with (HERE / "bwi_asos_20140812.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            stamp = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M")
            if stamp.strftime("%Y-%m-%d") != EVENT_DAY:
                continue
            raw = row["p01i"].strip()
            trace = raw == "T"
            inches = 0.0 if trace or raw == "M" else float(raw)
            start = stamp - timedelta(hours=1)
            hours.append({"hour": stamp.hour, "inches": inches, "trace": trace,
                          "window": f"{start:%H:%M}–{stamp:%H:%M}"})
    return hours


UPPER_TOKENS = {"BWI", "USGS", "MARC", "MD", "US", "I", "N", "S", "E", "W", "NE", "NW", "SE", "SW",
                "NNE", "ENE", "ESE", "SSE", "SSW", "WSW", "WNW", "NNW"}


def tidy(text, sentence):
    """NWS reports are all caps; make them readable but keep acronyms and compass points."""
    words = []
    for i, word in enumerate(text.split(" ")):
        core = word.strip(".,;:()").upper()
        if core in UPPER_TOKENS:
            words.append(word.upper())
        elif sentence and i > 0:
            words.append(word.lower())
        else:
            words.append(word.capitalize())
    out = " ".join(words)
    if sentence:  # capitalise each sentence start and the one proper noun NWS uses here
        out = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
        out = out.replace("amtrak", "Amtrak")
    return out


def reports():
    data = json.loads((HERE / "lsr_lwx_20140812.geojson").read_text())
    # A report counts as a road closure only if it names a road (closures.json, from locate_closures.py);
    # e.g. "numerous roads were closed" at a stream gauge does not.
    closure_times = {c["time_local"] for c in json.loads((HERE / "closures.json").read_text())["closures"]}
    out = []
    for feat in data["features"]:
        lon, lat = feat["geometry"]["coordinates"]
        if not (REPORT_BBOX[0] <= lon <= REPORT_BBOX[2] and REPORT_BBOX[1] <= lat <= REPORT_BBOX[3]):
            continue
        p = feat["properties"]
        utc = datetime.strptime(p["valid"], "%Y-%m-%dT%H:%M:%SZ")
        remark = (p.get("remark") or "").strip()
        out.append({
            "time_local": (utc + EDT).strftime("%Y-%m-%d %H:%M"),
            "type": p["typetext"].title(),
            "place": tidy(p.get("city") or "", sentence=False),
            "county": tidy(p.get("county") or "", sentence=False),
            "lat": lat, "lon": lon,
            "remark": tidy(remark, sentence=True),
            "road_closed": (utc + EDT).strftime("%Y-%m-%d %H:%M") in closure_times,
            "source": (p.get("source") or "").title(),
        })
    return sorted(out, key=lambda r: r["time_local"])


def mobility(csv_path):
    df = pd.read_csv(csv_path, usecols=["disaster.event", "user.anon", "latitude", "longitude.anon", "time"])
    b = df[df["disaster.event"] == "33_Baltimore"].copy()
    b["time"] = pd.to_datetime(b["time"])
    b["day"] = b["time"].dt.strftime("%Y-%m-%d")
    b["hour"] = b["time"].dt.hour
    # Planar km; a constant shift of either coordinate leaves these distances unchanged.
    b["x"] = b["latitude"] * KM_PER_DEG_LON          # column holds longitude
    b["y"] = b["longitude.anon"] * KM_PER_DEG_LAT    # column holds the shifted latitude

    full_days = sorted(d for d, n in b.groupby("day")["hour"].nunique().items() if n >= 20)
    b = b[b["day"].isin(full_days)]

    per_hour = b.groupby(["day", "hour"])["user.anon"].nunique().unstack(fill_value=0).reindex(columns=range(24), fill_value=0)
    hourly = []
    for h in range(24):
        ref = per_hour.loc[COMPARE_DAYS, h]
        hourly.append({"hour": h, "event_users": int(per_hour.loc[EVENT_DAY, h]),
                       "typical_mean": round(float(ref.mean()), 1),
                       "typical_min": int(ref.min()), "typical_max": int(ref.max())})

    def gyration(g):
        cx, cy = g["x"].mean(), g["y"].mean()
        return math.sqrt(((g["x"] - cx) ** 2 + (g["y"] - cy) ** 2).mean())

    multi = b.groupby(["day", "user.anon"]).filter(lambda g: len(g) >= 2)
    rg = multi.groupby(["day", "user.anon"])[["x", "y"]].apply(gyration)
    daily = []
    for day in full_days:
        daily.append({"date": day, "active_users": int(b.loc[b["day"] == day, "user.anon"].nunique()),
                      "mean_rg_km": round(float(rg.loc[day].mean()), 3)})
    return hourly, daily, grid(b, full_days), {"users": int(b["user.anon"].nunique()), "points": int(len(b)),
                                               "first_day": full_days[0], "last_day": full_days[-1]}


def grid(b, full_days):
    """Distinct users per ~450 m cell, over all days and on the event day; sparse cells dropped."""
    shift = BALTIMORE_CITY_LAT_MIN - b["longitude.anon"].min()
    lat = b["longitude.anon"] + shift
    lon = b["latitude"]
    g = pd.DataFrame({"user": b["user.anon"], "day": b["day"],
                      "i": (lat // GRID_DEG[0]).astype(int), "j": (lon // GRID_DEG[1]).astype(int)})
    period = g.groupby(["i", "j"])["user"].nunique()
    event = g[g["day"] == EVENT_DAY].groupby(["i", "j"])["user"].nunique()
    cells = []
    for (i, j), n in period.items():
        if n < MIN_USERS_PERIOD:
            continue
        e = int(event.get((i, j), 0))
        cells.append({"lat": round((i + 0.5) * GRID_DEG[0], 4), "lon": round((j + 0.5) * GRID_DEG[1], 4),
                      "users": int(n), "event_users": e if e >= MIN_USERS_DAY else None})
    return {"cell_deg": list(GRID_DEG), "min_users_period": MIN_USERS_PERIOD, "min_users_day": MIN_USERS_DAY,
            "days": len(full_days), "cells": cells,
            "suppressed_cells": int((period < MIN_USERS_PERIOD).sum())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mobility_csv", help="path to natural_disaster_human_mobility.csv")
    args = ap.parse_args()

    rain = rainfall()
    hourly, daily, activity_grid, mob_meta = mobility(args.mobility_csv)
    rep = reports()
    peak = max(rain, key=lambda r: r["inches"])
    storm = [r for r in hourly if 11 <= r["hour"] <= 15]
    storm_ratio = sum(r["event_users"] for r in storm) / sum(r["typical_mean"] for r in storm)

    result = {
        "meta": {
            "title": "Baltimore flash flood, August 12, 2014",
            "built": datetime.now().strftime("%Y-%m-%d"),
            "bwi_total_in": round(sum(r["inches"] for r in rain), 2),
            "peak_hour": peak,
            "road_closures": sum(r["road_closed"] for r in rep),
            "storm_window": "11:00–15:59",
            "storm_users_vs_typical": round(storm_ratio, 3),
            "compare_days": COMPARE_DAYS,
            "mobility": mob_meta,
            "sources": SOURCES,
        },
        "rain_hourly": rain,
        "reports": rep,
        "activity_hourly": hourly,
        "mobility_daily": daily,
        "activity_grid": activity_grid,
    }
    OUT.write_text(json.dumps(result, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes): {len(rep)} reports, BWI {result['meta']['bwi_total_in']} in, "
          f"storm-window users x{storm_ratio:.2f} vs typical Tuesday")


if __name__ == "__main__":
    main()
