"""Build the camden_yards_citywide scenario from the reference hospital list.

Copies camden_yards_multi_unit (the Camden Yards MCI with four fire engines) to camden_yards_citywide,
then replaces its hospitals.csv and hospital_compatibility.csv with every Baltimore City hospital in
hospitals.csv here except RESERVE sites, and adds the medic units in EXTRA_AMBULANCES (assumed).

- trauma_level: PARC / I / II from the designation, "None" for general EDs (the map colours by it).
- compatibility: a triage colour may go to a hospital when it is in that hospital's mci_accepts
  (RED only to designated adult trauma centres; YELLOW/GREEN as listed).
- available_beds: ASSUMED MCI intake per hospital by tier (TIER_INTAKE below). Real numbers come
  from the hospitals during an event (MIEMSS hospital resource tracking); edit and re-run.

Run from dynamic_mci/:  ../.venv/bin/python data/reference/baltimore_mci_resources/make_citywide_scenario.py
"""

import csv
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE.parents[1] / "scenarios"
BASE = SCENARIOS / "camden_yards_multi_unit"
OUT = SCENARIOS / "camden_yards_citywide"

# Assumption, not data: how many MCI patients each tier can take before diverting.
TIER_INTAKE = {"A_TOP_TRAUMA": 5, "B_TRAUMA_SPECIALTY": 3, "C_GENERAL_ED": 3}
TRIAGE = ("RED", "YELLOW", "GREEN")

# Assumption, not data: extra BCFD medic units for the MCI, one at each fire station already in the
# scenario (which stations house medic units is not verified). A001 at S001 comes from the base
# scenario. Each medic unit has two paramedics; none is command-eligible, so command stays with fire.
EXTRA_AMBULANCES = [("A002", "S023"), ("A003", "S034"), ("A004", "S026")]


def read(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def add_fleet():
    """Rebuild ambulances, response units and crews from the base scenario plus EXTRA_AMBULANCES."""
    fields, amb = read(BASE / "ambulances.csv")
    amb = [r for r in amb if r["ambulance_id"] == "A001"]
    amb += [{"ambulance_id": a, "station_id": s, "status": "AT_STATION", "available_min": "0",
             "capacity_patients": "1"} for a, s in EXTRA_AMBULANCES]
    write(OUT / "ambulances.csv", fields, amb)

    fields, units = read(BASE / "response_units.csv")
    units += [{"unit_id": a, "unit_type": "AMBULANCE", "station_id": s, "dispatch_min": "0",
               "status": "AT_STATION"} for a, s in EXTRA_AMBULANCES]
    write(OUT / "response_units.csv", fields, units)

    fields, people = read(BASE / "ems_personnel.csv")
    for k, (a, _) in enumerate(EXTRA_AMBULANCES):
        for j in (1, 2):
            people.append({"personnel_id": f"EMS{2 * k + 2 + j:03d}", "unit_id": a, "qualification": "ALS",
                           "triage_capable": "TRUE", "stabilization_capable": "TRUE", "command_eligible": "FALSE"})
    write(OUT / "ems_personnel.csv", fields, people)


def main():
    with (HERE / "hospitals.csv").open(newline="", encoding="utf-8") as f:
        ref = [r for r in csv.DictReader(f) if r["mci_tier"] in TIER_INTAKE]

    if not OUT.exists():
        shutil.copytree(BASE, OUT)

    with (OUT / "hospitals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["hospital_id", "name", "trauma_level", "available_beds", "latitude", "longitude",
                    "ambulance_entrance_lat", "ambulance_entrance_lon"])
        for r in ref:
            w.writerow([r["hospital_id"], r["name"], r["trauma_level"] or "None", TIER_INTAKE[r["mci_tier"]],
                        r["latitude"], r["longitude"], "", ""])

    with (OUT / "hospital_compatibility.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["triage", "hospital_id", "compatible"])
        for t in TRIAGE:
            for r in ref:
                w.writerow([t, r["hospital_id"], int(t in r["mci_accepts"].split(";"))])

    add_fleet()
    print(f"wrote {OUT.name}: {len(ref)} hospitals; RED-capable: "
          + ", ".join(r["hospital_id"] for r in ref if "RED" in r["mci_accepts"].split(";"))
          + "; ambulances: A001, " + ", ".join(a for a, _ in EXTRA_AMBULANCES))


if __name__ == "__main__":
    main()
