"""
Write the real Baltimore data in data/reference/ into every scenario folder.

    python -m experiments.build_real_scenarios [--only NAME ...] [--dry-run]

For each folder in data/scenarios/ this rewrites only the data files the
folder already has, out of:
    patients.csv, patient_dynamics.csv, hospitals.csv,
    hospital_compatibility.csv, stations.csv, ambulances.csv,
    response_units.csv, ems_personnel.csv
It never touches config.json, incidents.csv, road_conditions.csv or
resource_requests.csv, so each folder keeps its own policy settings.

What makes each folder different is kept from its original design
(data/reference/scenario_profiles.json): for every triage class, the new
patients repeat the folder's original pattern of ready time, deterioration
and stabilization in order. A class missing from a folder falls back to
camden_yards_dynamic's pattern.

Sources and assumptions: data/reference/SOURCES.md.
"""

import argparse
import csv
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
REF = BASE / "data" / "reference"
SCENARIOS_DIR = BASE / "data" / "scenarios"
FALLBACK_PROFILE = "camden_yards_dynamic"

TRIAGE_ORDER = ("RED", "YELLOW", "GREEN", "BLACK")
CAPABILITY = {"RED": "HIGH_ACUITY", "YELLOW": "EMERGENCY",
              "GREEN": "MINOR_CARE", "BLACK": "DISPOSITION_PENDING"}


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def tf(x):
    return "TRUE" if x else "FALSE"


def num(x):
    """1.0 -> '1', 0.5 -> '0.5' (keeps the CSVs in the style they had)."""
    return str(int(x)) if float(x).is_integer() else str(x)


def load_reference():
    inputs = json.loads((REF / "scenario_inputs.json").read_text(encoding="utf-8"))
    hospitals = read_csv(REF / "hospitals_baltimore.csv")
    stations = read_csv(REF / "stations_baltimore.csv")
    profiles = json.loads((REF / "scenario_profiles.json").read_text(encoding="utf-8"))
    counts = inputs["triage_counts"]
    if sum(counts.values()) != inputs["population"]["total"]:
        raise ValueError("triage_counts must add up to population.total")
    return inputs, hospitals, stations, profiles


def patient_rows(counts, profile, fallback):
    patients, dynamics = [], []
    pid = 0
    for tri in TRIAGE_ORDER:
        pattern = profile.get(tri) or fallback[tri]
        for i in range(counts[tri]):
            ready, mpd, mult = pattern[i % len(pattern)]
            if mpd is None:                       # base folders without dynamics
                _, mpd, mult = fallback[tri][i % len(fallback[tri])]
            pid += 1
            p = f"P{pid:05d}"
            black = tri == "BLACK"
            patients.append([p, "MCI01", tri, num(ready),
                             "AWAITING_DISPOSITION" if black else "WAITING",
                             tf(not black), CAPABILITY[tri], "" if black else "TRANSPORT"])
            dynamics.append([p, num(mpd), num(mult)])
    return patients, dynamics


def build(folder, inputs, hospitals, stations, profiles, dry_run=False):
    name = folder.name
    if name not in profiles:
        print(f"[skip] {name}: no entry in scenario_profiles.json")
        return
    has = {p.name for p in folder.glob("*.csv")}
    lvl3 = inputs["mci_level_3_response"]
    share = float(inputs["free_bed_share"])
    red_ok = set(inputs["red_compatible_trauma_levels"])
    medics = int(lvl3["medic_units"])
    companies = int(lvl3["suppression_companies"])
    crew = int(inputs["assumptions"]["suppression_company_crew"])
    if len(stations) < max(medics, companies):
        raise ValueError("not enough stations for the response units")

    out = {}
    patients, dynamics = patient_rows(inputs["triage_counts"], profiles[name],
                                      profiles[FALLBACK_PROFILE])
    out["patients.csv"] = (["patient_id", "incident_id", "triage", "ready_min", "status",
                            "requires_transport", "required_capability", "disposition"], patients)
    out["patient_dynamics.csv"] = (["patient_id", "minutes_per_deterioration",
                                    "stabilization_multiplier"], dynamics)

    out["hospitals.csv"] = (
        ["hospital_id", "name", "trauma_level", "available_beds", "latitude", "longitude",
         "ambulance_entrance_lat", "ambulance_entrance_lon", "licensed_beds"],
        [[h["hospital_id"], h["name"], h["trauma_level"],
          math.floor(int(h["licensed_beds_fy2026"]) * share),
          h["latitude"], h["longitude"], "", "", h["licensed_beds_fy2026"]] for h in hospitals])
    out["hospital_compatibility.csv"] = (
        ["triage", "hospital_id", "compatible"],
        [[tri, h["hospital_id"],
          int(tri in ("YELLOW", "GREEN") or (tri == "RED" and h["trauma_level"] in red_ok))]
         for tri in TRIAGE_ORDER for h in hospitals])

    out["stations.csv"] = (
        ["station_id", "station_name", "street_address", "city", "jurisdiction", "latitude",
         "longitude", "vehicle_exit_lat", "vehicle_exit_lon", "staffing"],
        [[s["station_id"], s["station_name"], s["street_address"], s["city"],
          s["jurisdiction"], s["latitude"], s["longitude"], s["latitude"], s["longitude"],
          s["staffing"]] for s in stations])

    # Nearest stations first (stations_baltimore.csv is sorted, S001 first).
    out["ambulances.csv"] = (
        ["ambulance_id", "station_id", "status", "available_min", "capacity_patients"],
        [[f"A{i:03d}", stations[i - 1]["station_id"],
          "AVAILABLE_AT_MCI" if i == 1 else "AT_STATION", 0, 1] for i in range(1, medics + 1)])

    # integrated_scheduler needs exactly one AMBULANCE unit: only A001 is listed here.
    fire_units = [(f"F{i:03d}", stations[i - 1]["station_id"]) for i in range(1, companies + 1)]
    out["response_units.csv"] = (
        ["unit_id", "unit_type", "station_id", "dispatch_min", "status"],
        [["A001", "AMBULANCE", stations[0]["station_id"], 0, "AT_STATION"]]
        + [[u, "FIRE_EMS", s, 0, "AT_STATION"] for u, s in fire_units])

    people = [["EMS001", "A001", "ALS", "TRUE", "TRUE", "TRUE"],
              ["EMS002", "A001", "ALS", "TRUE", "TRUE", "FALSE"]]
    if int(lvl3.get("deputy_chief_ems", 0)):
        people.append(["DC001", fire_units[0][0], "ALS", "TRUE", "TRUE", "TRUE"])
    n = 0
    for u, _ in fire_units:
        for k in range(crew):
            n += 1
            people.append([f"FIRE{n:03d}", u, "ALS" if k == 0 else "BLS", "TRUE", "TRUE",
                           tf(k == 0)])
    out["ems_personnel.csv"] = (["personnel_id", "unit_id", "qualification", "triage_capable",
                                 "stabilization_capable", "command_eligible"], people)

    written = []
    for fname, (header, rows) in out.items():
        if fname not in has:
            continue
        if not dry_run:
            write_csv(folder / fname, header, rows)
        written.append(f"{fname}={len(rows)}")
    print(f"[{'dry' if dry_run else 'built'}] {name}: " + ", ".join(written))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="scenario folder names")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    inputs, hospitals, stations, profiles = load_reference()
    folders = sorted(p for p in SCENARIOS_DIR.iterdir() if p.is_dir())
    if a.only:
        folders = [p for p in folders if p.name in set(a.only)]
    for folder in folders:
        build(folder, inputs, hospitals, stations, profiles, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
