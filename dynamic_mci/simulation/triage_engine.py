
"""
Maryland MCI - Rolling Triage Engine
Stage 1B: Dynamic Incident Command

Standalone test module. Does not modify Gurobi,
the main ambulance simulation, or scenario CSVs.

Features:
- Valhalla response-unit routing
- Dynamic incident commander selection
- Preference for non-transporting command personnel
- At most one command transfer
- 45-second rolling triage
- Personnel resource exclusivity
- Independent validation and saved results
"""

import argparse
import csv
import heapq
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from simulation.engine import (
    get_route,
    get_station_location,
    location,
)

BASE = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = (
    BASE / "data" / "scenarios" / "camden_yards_dynamic"
)

TRIAGE_SECONDS = 45
EPS = 1e-8


def load_csv(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")

    with path.open(
        newline="",
        encoding="utf-8-sig"
    ) as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header: {path.name}")
        return list(reader)


def yes(value):
    return str(value).strip().upper() in {
        "TRUE", "YES", "1"
    }


def minutes(value):
    return float(value or 0)


def run_triage(
    scenario_dir=DEFAULT_SCENARIO,
    include_fire=True,
    save=True
):
    scenario_dir = Path(scenario_dir).resolve()

    patients = load_csv(scenario_dir / "patients.csv")
    units = load_csv(scenario_dir / "response_units.csv")
    people = load_csv(scenario_dir / "ems_personnel.csv")
    stations = load_csv(scenario_dir / "stations.csv")
    incidents = load_csv(scenario_dir / "incidents.csv")

    if not patients or len(incidents) != 1:
        raise ValueError(
            "Stage 1B requires patients and one incident"
        )

    for records, field in [
        (patients, "patient_id"),
        (units, "unit_id"),
        (people, "personnel_id"),
    ]:
        ids = [r[field] for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {field}")

    if any("command_eligible" not in p for p in people):
        raise ValueError(
            "ems_personnel.csv needs command_eligible"
        )

    stations_by_id = {
        s["station_id"]: s for s in stations
    }
    units_by_id = {
        u["unit_id"]: u for u in units
    }
    people_by_id = {
        p["personnel_id"]: p for p in people
    }

    for p in people:
        if p["unit_id"] not in units_by_id:
            raise ValueError(
                f"Unknown unit for {p['personnel_id']}"
            )

    pickup = location(
        incidents[0]["pickup_latitude"],
        incidents[0]["pickup_longitude"]
    )

    selected_units = [
        u for u in units
        if u["unit_type"].upper() == "AMBULANCE"
        or (
            include_fire
            and u["unit_type"].upper() == "FIRE_EMS"
        )
    ]

    if not selected_units:
        raise ValueError("No response units selected")

    selected_ids = {
        u["unit_id"] for u in selected_units
    }

    selected_people = [
        p for p in people
        if p["unit_id"] in selected_ids
    ]

    queue = []
    sequence = 0
    events = []
    routes = []
    arrivals = {}

    available = set()
    busy = set()
    pending = {
        p["patient_id"]: p for p in patients
    }
    assessed = {}

    commander = None
    command_transfers = 0
    command_history = []

    def schedule(time, priority, kind, payload):
        nonlocal sequence
        sequence += 1
        heapq.heappush(
            queue,
            (
                float(time),
                priority,
                sequence,
                kind,
                payload
            )
        )

    def log(time, kind, **details):
        events.append({
            "time_min": round(float(time), 4),
            "event": kind,
            **details
        })

    def non_transport(person):
        unit = units_by_id[person["unit_id"]]
        return unit["unit_type"].upper() != "AMBULANCE"

    def commander_rank(person):
        return (
            0 if non_transport(person) else 1,
            person["unit_id"],
            person["personnel_id"]
        )

    def choose_commander(time):
        nonlocal commander, command_transfers

        eligible = sorted(
            (
                people_by_id[pid]
                for pid in available
                if yes(people_by_id[pid]["command_eligible"])
                and pid not in busy
            ),
            key=commander_rank
        )

        if not eligible:
            return

        candidate = eligible[0]["personnel_id"]

        if commander is None:
            commander = candidate
            available.remove(candidate)

            record = {
                "time_min": round(time, 4),
                "personnel_id": candidate,
                "unit_id": people_by_id[candidate]["unit_id"],
                "action": "ESTABLISHED"
            }
            command_history.append(record)

            log(
                time,
                "INCIDENT_COMMAND_ESTABLISHED",
                personnel_id=candidate,
                unit_id=record["unit_id"]
            )
            return

        if command_transfers >= 1:
            return

        old = people_by_id[commander]
        new = people_by_id[candidate]

        # Transfer only from a transporting unit
        # to an eligible non-transporting unit.
        if non_transport(old) or not non_transport(new):
            return

        previous = commander
        commander = candidate
        command_transfers += 1

        available.remove(candidate)
        available.add(previous)

        command_history.append({
            "time_min": round(time, 4),
            "personnel_id": candidate,
            "unit_id": new["unit_id"],
            "action": "TRANSFERRED"
        })

        log(
            time,
            "INCIDENT_COMMAND_TRANSFERRED",
            from_personnel_id=previous,
            to_personnel_id=candidate
        )

        log(
            time,
            "PERSONNEL_RELEASED_FROM_COMMAND",
            personnel_id=previous
        )

    def assign_triage(time):
        ready = sorted(
            (
                p for p in pending.values()
                if minutes(p.get("ready_min")) <= time + EPS
            ),
            key=lambda p: (
                minutes(p.get("ready_min")),
                p["patient_id"]
            )
        )

        workers = sorted(
            pid for pid in available
            if pid not in busy
            and yes(people_by_id[pid]["triage_capable"])
        )

        for worker, patient in zip(workers, ready):
            pid = patient["patient_id"]

            del pending[pid]
            busy.add(worker)

            log(
                time,
                "TRIAGE_STARTED",
                patient_id=pid,
                personnel_id=worker
            )

            schedule(
                time + TRIAGE_SECONDS / 60,
                0,
                "TRIAGE_COMPLETED",
                {
                    "patient_id": pid,
                    "personnel_id": worker,
                    "triage": patient["triage"]
                }
            )

    # ----------------------------------------
    # Response-unit travel and dispatch
    # ----------------------------------------

    for unit in selected_units:
        uid = unit["unit_id"]
        sid = unit["station_id"]

        if sid not in stations_by_id:
            raise ValueError(
                f"Unknown station {sid} for {uid}"
            )

        origin = get_station_location(
            stations_by_id[sid]
        )

        # explain=True also returns per-vertex driving times, so the dashboard can animate the unit.
        route = get_route(origin, pickup, explain=True)

        dispatch = minutes(unit.get("dispatch_min"))
        arrival = dispatch + route["travel_minutes"]

        arrivals[uid] = arrival

        routes.append({
            "unit_id": uid,
            "purpose": "STATION_TO_MCI",
            "start_min": round(dispatch, 4),
            "end_min": round(arrival, 4),
            "distance_miles": route["distance_miles"],
            "coordinates": route["coordinates"],
            "vertex_minutes": route.get("vertex_minutes")
        })

        log(dispatch, "UNIT_DISPATCHED", unit_id=uid)

        schedule(
            arrival,
            0,
            "UNIT_ARRIVED",
            {"unit_id": uid}
        )

    for p in patients:
        schedule(
            minutes(p.get("ready_min")),
            1,
            "PATIENT_READY",
            {"patient_id": p["patient_id"]}
        )

    # ----------------------------------------
    # Event-driven scheduler
    # ----------------------------------------

    while queue:
        time = queue[0][0]
        batch = []

        # Process every event at this timestamp
        # before assigning command or triage.
        while queue and abs(queue[0][0] - time) < EPS:
            batch.append(heapq.heappop(queue))

        for _, _, _, kind, payload in sorted(batch):
            if kind == "UNIT_ARRIVED":
                uid = payload["unit_id"]

                log(time, "UNIT_ARRIVED", unit_id=uid)

                for p in selected_people:
                    if p["unit_id"] == uid:
                        pid = p["personnel_id"]
                        available.add(pid)

                        log(
                            time,
                            "PERSONNEL_AVAILABLE",
                            unit_id=uid,
                            personnel_id=pid
                        )

            elif kind == "TRIAGE_COMPLETED":
                pid = payload["patient_id"]
                worker = payload["personnel_id"]

                busy.remove(worker)

                assessed[pid] = {
                    "patient_id": pid,
                    "triage": payload["triage"],
                    "personnel_id": worker,
                    "triage_complete_min": round(time, 4)
                }

                log(
                    time,
                    "TRIAGE_COMPLETED",
                    patient_id=pid,
                    personnel_id=worker,
                    observed_triage=payload["triage"]
                )

            elif kind == "PATIENT_READY":
                log(
                    time,
                    "PATIENT_READY",
                    patient_id=payload["patient_id"]
                )

        choose_commander(time)
        assign_triage(time)

    # ----------------------------------------
    # Validation
    # ----------------------------------------

    issues = []

    if len(assessed) != len(patients):
        issues.append(
            f"Only {len(assessed)} of "
            f"{len(patients)} patients assessed"
        )

    if commander is None:
        issues.append("No eligible incident commander")

    if command_transfers > 1:
        issues.append("More than one command transfer")

    starts = {
        (e["patient_id"], e["personnel_id"]):
        e["time_min"]
        for e in events
        if e["event"] == "TRIAGE_STARTED"
    }

    intervals = {}

    for record in assessed.values():
        pid = record["patient_id"]
        worker = record["personnel_id"]
        key = (pid, worker)

        start = starts.get(key)

        if start is None:
            issues.append(f"Missing triage start for {pid}")
            continue

        finish = record["triage_complete_min"]

        if abs(
            finish - start - TRIAGE_SECONDS / 60
        ) > 0.001:
            issues.append(
                f"Incorrect triage duration: {pid}"
            )

        uid = people_by_id[worker]["unit_id"]

        if start < arrivals[uid] - 0.001:
            issues.append(
                f"{worker} triaged before arrival"
            )

        intervals.setdefault(worker, []).append(
            (start, finish)
        )

    for worker, periods in intervals.items():
        periods.sort()

        for a, b in zip(periods, periods[1:]):
            if a[1] > b[0] + 0.001:
                issues.append(
                    f"Overlapping triage: {worker}"
                )

    # Command personnel must never perform
    # triage during their command assignment.
    for i, record in enumerate(command_history):
        start = record["time_min"]
        worker = record["personnel_id"]

        end = (
            command_history[i + 1]["time_min"]
            if i + 1 < len(command_history)
            else float("inf")
        )

        for triage_start, triage_end in intervals.get(
            worker, []
        ):
            if (
                triage_start < end - EPS
                and triage_end > start + EPS
            ):
                issues.append(
                    f"Commander {worker} performed triage"
                )

    triage_end = max(
        (
            r["triage_complete_min"]
            for r in assessed.values()
        ),
        default=0
    )

    result = {
        "scenario": scenario_dir.name,
        "mode": (
            "AMBULANCE_PLUS_FIRE"
            if include_fire else "AMBULANCE_ONLY"
        ),
        "status": (
            "COMPLETED"
            if len(assessed) == len(patients)
            else "INCOMPLETE"
        ),
        "triage_duration_seconds": TRIAGE_SECONDS,
        "units": [u["unit_id"] for u in selected_units],
        "unit_arrivals_min": {
            k: round(v, 4)
            for k, v in arrivals.items()
        },
        "incident_commander": commander,
        "command_transfers": command_transfers,
        "command_history": command_history,
        "patients_total": len(patients),
        "patients_assessed": len(assessed),
        "triage_completion_min": round(triage_end, 4),
        "assessments": sorted(
            assessed.values(),
            key=lambda r: (
                r["triage_complete_min"],
                r["patient_id"]
            )
        ),
        "events": sorted(
            events,
            key=lambda e: e["time_min"]
        ),
        "routes": routes,
        "validation": {
            "status": "PASS" if not issues else "FAIL",
            "issues": issues
        }
    }

    # ----------------------------------------
    # Save isolated test results
    # ----------------------------------------

    if save:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%d_%H%M%S")

        run_id = (
            f"triage_{timestamp}_{uuid4().hex[:8]}"
        )

        run_dir = (
            BASE / "outputs"
            / scenario_dir.name
            / "triage_tests"
            / run_id
        )

        run_dir.mkdir(parents=True, exist_ok=False)
        result["run_id"] = run_id

        (run_dir / "triage_results.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8"
        )

        shutil.copytree(
            scenario_dir,
            run_dir / "scenario_snapshot"
        )

        print("Saved:", run_dir / "triage_results.json")

    print("\nRolling triage completed")
    print("Mode:", result["mode"])
    print("Units:", ", ".join(result["units"]))
    print("Commander:", commander)
    print("Command transfers:", command_transfers)
    print("Patients assessed:", len(assessed))
    print(
        "Triage completion:",
        f"{triage_end:.4f} minutes"
    )
    print("Validation:", result["validation"]["status"])

    for issue in issues:
        print("Validation issue:", issue)

    for a in result["assessments"]:
        print(
            a["patient_id"],
            a["triage"],
            "at", a["triage_complete_min"],
            "by", a["personnel_id"]
        )

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scenario",
        default=str(DEFAULT_SCENARIO)
    )

    parser.add_argument(
        "--no-fire",
        action="store_true"
    )

    args = parser.parse_args()

    run_triage(
        scenario_dir=args.scenario,
        include_fire=not args.no_fire
    )


if __name__ == "__main__":
    main()
