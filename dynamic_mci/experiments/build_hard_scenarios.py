"""
Build the "hard" benchmark scenarios, one folder each under data/scenarios/.

Each folder starts as a copy of camden_yards_dynamic (same incident, station
and file layout), then overwrites patients, dynamics, hospitals,
compatibility, personnel and config. Existing scenario folders are never
modified. Re-running rebuilds only the folders listed in SCENARIOS
(use --force to overwrite them).

    python -m experiments.build_hard_scenarios [--force] [--only NAME ...]

Engine limits these scenarios respect (simulation/integrated_scheduler.py):
- exactly one ambulance (A001) with a two-person crew;
- road_conditions.csv and resource_requests.csv are not read by any engine
  code yet, so road closures and reinforcements are left empty.

Hospital coordinates for H004-H007 are approximate main-campus points;
verify ambulance entrances before using them for real routing studies.
"""

import argparse
import csv
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = BASE / "data" / "scenarios"
TEMPLATE = SCENARIOS_DIR / "camden_yards_dynamic"

HOSPITALS = {
    "H001": ("R Adams Cowley Shock Trauma Center", "PARC", 39.2885, -76.6235),
    "H002": ("Johns Hopkins Hospital", "I", 39.295828, -76.591953),
    "H003": ("Sinai Hospital of Baltimore", "II", 39.351968, -76.661297),
    "H004": ("Mercy Medical Center", "NONE", 39.2948, -76.6136),
    "H005": ("MedStar Harbor Hospital", "NONE", 39.2510, -76.6127),
    "H006": ("Johns Hopkins Bayview Medical Center", "II", 39.2899, -76.5482),
    "H007": ("MedStar Union Memorial Hospital", "NONE", 39.3283, -76.6125),
}
# Same rule as the existing scenarios: RED only to Shock Trauma / Hopkins.
RED_CAPABLE = {"H001", "H002"}

CAPABILITY = {"RED": "HIGH_ACUITY", "YELLOW": "EMERGENCY",
              "GREEN": "MINOR_CARE", "BLACK": "DISPOSITION_PENDING"}

AMBULANCE_CREW = [("EMS001", "A001", "ALS", True), ("EMS002", "A001", "ALS", False)]


def fire_crew(n):
    """F001 crew of n people; FIRE001 is command-eligible and takes command."""
    crew = [("FIRE001", "F001", "ALS", True)]
    crew += [(f"FIRE{i:03d}", "F001", "BLS", False) for i in range(2, n + 1)]
    return crew


# Patient tuple: (triage, ready_min, minutes_per_deterioration, stabilization_multiplier)
# minutes_per_deterioration = exposure minutes per step GREEN->YELLOW->RED->BLACK;
# a stabilized patient accumulates exposure at `multiplier` speed.
SCENARIOS = {
    "camden_yards_hard_trauma_bed_trap": {
        "summary": "Only 2 RED-capable beds. Nearest-hospital rules spend them on "
                   "YELLOWs, and a YELLOW that later upgrades to RED has nowhere to go.",
        "tests": "Reserving scarce trauma beds for patients who are likely to become RED.",
        "hospitals": {"H001": 1, "H002": 1, "H003": 8},
        "patients": [("YELLOW", 0, 40, 0.5), ("RED", 0, 60, 0.5), ("YELLOW", 0, 25, 0.5),
                     ("GREEN", 0, 120, 0.5), ("YELLOW", 0, 35, 0.5), ("GREEN", 0, 150, 0.5),
                     ("BLACK", 0, 0, 1.0)],
        "fire_crew": 4,
        "config": {},
        "expected": "severe_first / gurobi send the first YELLOW to Hopkins (nearest "
                    "free trauma bed); when P003 upgrades to RED no RED-capable bed is "
                    "left. Sending YELLOWs to Sinai keeps Hopkins free.",
    },
    "camden_yards_hard_fast_yellow_slow_red": {
        "summary": "Two stable REDs and three fast-deteriorating YELLOWs with only one "
                   "free stabilizer. Taking REDs first lets YELLOWs slide to RED, then BLACK.",
        "tests": "Value of knowing deterioration speed, not just the current triage colour. "
                 "All current policies see only triage colour, so all are expected to "
                 "do poorly here; the gap shows what a deterioration-aware model could gain.",
        "hospitals": {"H001": 3, "H002": 3, "H004": 6},
        "patients": [("RED", 0, 150, 0.8), ("RED", 0, 140, 0.8), ("YELLOW", 0, 12, 0.6),
                     ("YELLOW", 0, 14, 0.6), ("GREEN", 0, 200, 0.5), ("YELLOW", 0, 18, 0.6)],
        "fire_crew": 2,
        "config": {},
        "expected": "Every triage-only policy serves P001/P002 first; P003/P004/P006 "
                    "upgrade and some reach BLACK before the ambulance returns.",
    },
    "camden_yards_hard_staggered_arrivals": {
        "summary": "Walking wounded are ready at t=0, but two REDs are extricated at "
                   "t=45 and t=75. Greedy rules use the trauma beds before the REDs appear.",
        "tests": "Holding capacity for patients who are not yet visible (look-ahead).",
        "hospitals": {"H001": 1, "H002": 2, "H003": 6},
        "patients": [("YELLOW", 0, 60, 0.5), ("YELLOW", 0, 50, 0.5), ("GREEN", 0, 180, 0.5),
                     ("YELLOW", 0, 45, 0.5), ("GREEN", 0, 200, 0.5), ("RED", 45, 30, 0.5),
                     ("RED", 75, 40, 0.5), ("YELLOW", 100, 30, 0.5)],
        "fire_crew": 4,
        "config": {},
        "expected": "The first YELLOWs take Shock Trauma and Hopkins (nearest free "
                    "beds); the late REDs then find too few RED-capable beds. Sending "
                    "early YELLOWs to Sinai (far, 6 beds) would keep them free.",
    },
    "camden_yards_hard_round_trip": {
        "summary": "Near hospitals have 2 beds each; far hospitals (Sinai, Bayview) have "
                   "many. With one ambulance and a 240-min horizon, every far trip costs "
                   "a whole extra cycle.",
        "tests": "Trading cycle time against bed scarcity: who should use the near beds "
                 "and when to accept a long round trip.",
        "hospitals": {"H001": 2, "H002": 2, "H004": 2, "H003": 10, "H006": 8},
        "patients": [("YELLOW", 0, 55, 0.5), ("RED", 0, 60, 0.5), ("GREEN", 0, 150, 0.5),
                     ("YELLOW", 0, 45, 0.5), ("RED", 0, 50, 0.5), ("GREEN", 0, 160, 0.5),
                     ("YELLOW", 0, 60, 0.5), ("RED", 0, 70, 0.5), ("GREEN", 0, 170, 0.5),
                     ("YELLOW", 0, 50, 0.5), ("GREEN", 0, 180, 0.5), ("YELLOW", 0, 55, 0.5)],
        "fire_crew": 4,
        "config": {},
        "expected": "Not every patient can be delivered within 240 min. Load-balancing "
                    "sends patients far and loses trips; nearest-first fills near beds "
                    "with whoever is first.",
    },
    "camden_yards_hard_large_scarce_beds": {
        "summary": "18 patients (4 RED, 7 YELLOW, 5 GREEN, 2 BLACK), 7 hospitals with "
                   "19 beds in total, one ambulance. The main stress test.",
        "tests": "Everything at once: bed reservation, deterioration, round-trip cost, "
                 "and a large backlog the horizon cannot clear.",
        "hospitals": {"H001": 2, "H002": 2, "H003": 4, "H004": 3, "H005": 3,
                      "H006": 3, "H007": 2},
        "patients": [("YELLOW", 0, 45, 0.5), ("GREEN", 0, 150, 0.5), ("RED", 0, 40, 0.5),
                     ("YELLOW", 0, 30, 0.5), ("BLACK", 0, 0, 1.0), ("GREEN", 0, 170, 0.5),
                     ("RED", 0, 55, 0.5), ("YELLOW", 0, 60, 0.5), ("YELLOW", 0, 25, 0.5),
                     ("GREEN", 0, 140, 0.5), ("RED", 0, 35, 0.5), ("YELLOW", 0, 50, 0.5),
                     ("GREEN", 0, 200, 0.5), ("YELLOW", 0, 40, 0.5), ("RED", 30, 45, 0.5),
                     ("YELLOW", 30, 35, 0.5), ("GREEN", 30, 160, 0.5), ("BLACK", 30, 0, 1.0)],
        "fire_crew": 6,
        "config": {},
        "expected": "Large differences in deliveries, BLACK transitions and expected "
                    "survivors between policies.",
    },
    "camden_yards_hard_delayed_info_cascade": {
        "summary": "The optimizer's triage view is frozen until t=60 while YELLOWs "
                   "deteriorate quickly and RED-capable beds are scarce.",
        "tests": "Robustness to stale information: decisions made on outdated triage.",
        "hospitals": {"H001": 1, "H002": 2, "H004": 4},
        "patients": [("GREEN", 0, 30, 0.6), ("RED", 0, 70, 0.5), ("YELLOW", 0, 20, 0.6),
                     ("YELLOW", 0, 25, 0.6), ("GREEN", 0, 40, 0.6), ("YELLOW", 0, 30, 0.6),
                     ("BLACK", 0, 0, 1.0)],
        "fire_crew": 3,
        "config": {"optimizer_observation_policy": "DELAYED",
                   "optimizer_observation_release_min": 60},
        "expected": "Before t=60 dispatch uses initial triage only; patients who have "
                    "already upgraded are under-prioritized and lose RED-capable beds.",
    },
}


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def tf(x):
    return "TRUE" if x else "FALSE"


def build(name, spec, force=False):
    out = SCENARIOS_DIR / name
    if out.exists():
        if not force:
            print(f"[skip] {name} exists (use --force to rebuild)")
            return
        shutil.rmtree(out)
    shutil.copytree(TEMPLATE, out)

    patients, dynamics = [], []
    for i, (tri, ready, mpd, mult) in enumerate(spec["patients"], start=1):
        pid = f"P{i:03d}"
        black = tri == "BLACK"
        patients.append([pid, "MCI01", tri, ready,
                         "AWAITING_DISPOSITION" if black else "WAITING",
                         tf(not black), CAPABILITY[tri], "" if black else "TRANSPORT"])
        dynamics.append([pid, mpd, mult])
    write_csv(out / "patients.csv",
              ["patient_id", "incident_id", "triage", "ready_min", "status",
               "requires_transport", "required_capability", "disposition"], patients)
    write_csv(out / "patient_dynamics.csv",
              ["patient_id", "minutes_per_deterioration", "stabilization_multiplier"],
              dynamics)

    hids = list(spec["hospitals"])
    write_csv(out / "hospitals.csv",
              ["hospital_id", "name", "trauma_level", "available_beds", "latitude",
               "longitude", "ambulance_entrance_lat", "ambulance_entrance_lon"],
              [[h, HOSPITALS[h][0], HOSPITALS[h][1], spec["hospitals"][h],
                HOSPITALS[h][2], HOSPITALS[h][3], "", ""] for h in hids])
    compat = []
    for tri in ("RED", "YELLOW", "GREEN", "BLACK"):
        for h in hids:
            ok = tri in ("YELLOW", "GREEN") or (tri == "RED" and h in RED_CAPABLE)
            compat.append([tri, h, int(ok)])
    write_csv(out / "hospital_compatibility.csv",
              ["triage", "hospital_id", "compatible"], compat)

    people = AMBULANCE_CREW + fire_crew(spec["fire_crew"])
    write_csv(out / "ems_personnel.csv",
              ["personnel_id", "unit_id", "qualification", "triage_capable",
               "stabilization_capable", "command_eligible"],
              [[pid, unit, q, "TRUE", "TRUE", tf(cmd)] for pid, unit, q, cmd in people])

    config = json.loads((TEMPLATE / "config.json").read_text(encoding="utf-8-sig"))
    config["hospital_turnover_minutes"] = 20
    config.update(spec["config"])
    (out / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    counts = {t: sum(p[0] == t for p in spec["patients"])
              for t in ("RED", "YELLOW", "GREEN", "BLACK")}
    beds = ", ".join(f"{h} {HOSPITALS[h][0]} ({n})" for h, n in spec["hospitals"].items())
    (out / "SCENARIO.md").write_text(
        f"# {name}\n\n{spec['summary']}\n\n"
        f"**What it tests:** {spec['tests']}\n\n"
        f"**Expected failure of myopic rules:** {spec['expected']}\n\n"
        f"- Patients: {len(spec['patients'])} "
        f"({', '.join(f'{v} {k}' for k, v in counts.items() if v)})\n"
        f"- Beds: {beds}; RED only to H001/H002\n"
        f"- One ambulance (A001), fire crew of {spec['fire_crew']} (one holds command)\n"
        f"- Hospital turnover 20 min (base scenarios use 60)\n"
        + "".join(f"- `{k}` = {v}\n" for k, v in spec["config"].items())
        + "\nGenerated by `python -m experiments.build_hard_scenarios`; edit the spec "
          "there rather than these CSVs.\n",
        encoding="utf-8")
    print(f"[built] {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="overwrite existing hard scenarios")
    ap.add_argument("--only", nargs="*", choices=list(SCENARIOS))
    args = ap.parse_args()
    for name in args.only or SCENARIOS:
        build(name, SCENARIOS[name], force=args.force)


if __name__ == "__main__":
    main()
