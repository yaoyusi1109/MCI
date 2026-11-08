
"""
Maryland MCI - Patient Dynamics
Stage 2: Deterministic deterioration and stabilization.

Standalone research prototype:
- Reads completed rolling-triage results
- Tracks true and observed triage separately
- Allocates non-command fire/EMS responders
- Models stabilization as reduced deterioration exposure
- Reassesses patients periodically
- Preserves scenario files and saves unique results

Does not modify the main simulation or Gurobi.
Clinical parameters are synthetic, not validated.
"""

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tracemalloc import start
from uuid import uuid4


BASE = Path(__file__).resolve().parents[1]
EPS = 1e-8
NEXT_STATE = {
    "GREEN": "YELLOW",
    "YELLOW": "RED",
    "RED": "BLACK",
}
VALID_STATES = set(NEXT_STATE) | {"BLACK"}


def read_csv(path):
    with Path(path).open(
        newline="", encoding="utf-8-sig"
    ) as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Missing CSV header: {path}")
        return list(reader)


def is_true(value):
    return str(value).strip().upper() in (
        "TRUE", "YES", "1"
    )


def run_dynamics(
    scenario_dir,
    triage_results_path,
    horizon_min=120.0,
    reassessment_interval_min=15.0,
    save=True,
):
    scenario_dir = Path(scenario_dir).resolve()
    triage_results_path = Path(
        triage_results_path
    ).resolve()

    patients = read_csv(
        scenario_dir / "patients.csv"
    )
    parameters = read_csv(
        scenario_dir / "patient_dynamics.csv"
    )
    personnel = read_csv(
        scenario_dir / "ems_personnel.csv"
    )
    units = read_csv(
        scenario_dir / "response_units.csv"
    )

    triage = json.loads(
        triage_results_path.read_text(
            encoding="utf-8"
        )
    )

    if triage["scenario"] != scenario_dir.name:
        raise ValueError("Triage scenario mismatch")

    if triage.get("validation", {}).get("status") != "PASS":
        raise ValueError(
            "Triage results must pass validation"
        )

    if horizon_min <= 0:
        raise ValueError("Horizon must be positive")

    if reassessment_interval_min <= 0:
        raise ValueError(
            "Reassessment interval must be positive"
        )

    def unique_index(rows, key):
        result = {}
        for row in rows:
            identifier = row[key]
            if identifier in result:
                raise ValueError(
                    f"Duplicate {key}: {identifier}"
                )
            result[identifier] = row
        return result

    patient_by_id = unique_index(
        patients, "patient_id"
    )
    params_by_id = unique_index(
        parameters, "patient_id"
    )
    people_by_id = unique_index(
        personnel, "personnel_id"
    )
    units_by_id = unique_index(
        units, "unit_id"
    )

    if set(patient_by_id) != set(params_by_id):
        raise ValueError(
            "Patient dynamics parameters must "
            "match patients.csv exactly"
        )

    assessments = unique_index(
        triage["assessments"], "patient_id"
    )

    if set(assessments) != set(patient_by_id):
        raise ValueError(
            "Every patient needs an initial assessment"
        )

    commander = triage.get("incident_commander")

    # Only non-transporting FIRE_EMS personnel
    # can provide continuous scene stabilization
    # in this isolated test.
    fire_workers = []

    for person in personnel:
        pid = person["personnel_id"]
        uid = person["unit_id"]

        if uid not in units_by_id:
            raise ValueError(f"Unknown unit: {uid}")

        if (
            units_by_id[uid]["unit_type"].upper()
            == "FIRE_EMS"
            and is_true(
                person["stabilization_capable"]
            )
            and pid != commander
            and uid in triage["units"]
        ):
            fire_workers.append(pid)

    fire_workers.sort()

    # Triage workers become available after
    # their last assessment. A fire responder
    # cannot stabilize while still triaging.
    last_triage_end = {}

    for assessment in assessments.values():
        worker = assessment["personnel_id"]
        end = float(
            assessment["triage_complete_min"]
        )
        last_triage_end[worker] = max(
            last_triage_end.get(worker, 0),
            end
        )

    arrivals = triage["unit_arrivals_min"]

    worker_ready = {}

    for worker in fire_workers:
        uid = people_by_id[worker]["unit_id"]

        worker_ready[worker] = max(
            float(arrivals[uid]),
            last_triage_end.get(worker, 0)
        )

    # Patient ground truth and observed state
    # are deliberately separate.
    state = {}

    for pid, patient in patient_by_id.items():
        initial = patient["triage"].upper()

        if initial not in VALID_STATES:
            raise ValueError(
                f"Invalid triage for {pid}"
            )

        threshold = float(
            params_by_id[pid][
                "minutes_per_deterioration"
            ]
        )

        multiplier = float(
            params_by_id[pid][
                "stabilization_multiplier"
            ]
        )

        if initial != "BLACK" and threshold <= 0:
            raise ValueError(
                f"{pid}: threshold must be positive"
            )

        if not (0 < multiplier <= 1):
            raise ValueError(
                f"{pid}: multiplier must be in (0,1]"
            )

        state[pid] = {
            "patient_id": pid,
            "initial_triage": initial,
            "true_triage": initial,
            "observed_triage": None,
            "assessment_min": float(
                assessments[pid]["triage_complete_min"]
            ),
            "threshold_min": threshold,
            "multiplier": multiplier,
            "exposure_min": 0.0,
            "care_status": "UNTREATED",
            "assigned_worker": None,
            "last_reassessment_min": None,
            "transitions": 0,
        }

    events = []

    def log(time, event, **details):
        events.append({
            "time_min": round(float(time), 4),
            "event": event,
            **details
        })

    # Initial assessments reveal the actual
    # condition at the time of assessment.
    # For this test, no deterioration occurs
    # before initial assessment.
    for pid, p in state.items():
        if p["assessment_min"] > horizon_min:
            raise ValueError(
                f"{pid}: assessment after horizon"
            )

        log(
            p["assessment_min"],
            "INITIAL_TRIAGE_COMPLETED",
            patient_id=pid,
            observed_triage=p["initial_triage"]
        )

    # Generate personnel availability events.
    # All eligible fire responders are assumed
    # to stay on scene after their final triage.
    timeline = {0.0, float(horizon_min)}

    for p in state.values():
        timeline.add(p["assessment_min"])

    for ready in worker_ready.values():
        if ready <= horizon_min:
            timeline.add(ready)

    # Reassess at fixed intervals from each
    # patient's initial assessment.
    reassessments = {}

    for pid, p in state.items():
        times = []
        t = (
            p["assessment_min"]
            + reassessment_interval_min
        )

        while t <= horizon_min + EPS:
            times.append(round(t, 8))
            timeline.add(round(t, 8))
            t += reassessment_interval_min

        reassessments[pid] = set(times)

    # Stabilization allocation is intentionally
    # simple and deterministic. Assign one
    # qualified fire worker per eligible patient.
    # RED and YELLOW are prioritized, followed
    # by GREEN.
    available_workers = set()
    allocated_workers = set()

    def assign_stabilization(time):
        waiting = sorted(
            (
                p for p in state.values()
                if p["assessment_min"] <= time + EPS
                and p["true_triage"] != "BLACK"
                and p["assigned_worker"] is None
            ),
            key=lambda p: (
                {
                    "RED": 0,
                    "YELLOW": 1,
                    "GREEN": 2
                }[p["observed_triage"] or
                  p["initial_triage"]],
                p["assessment_min"],
                p["patient_id"]
            )
        )

        workers = sorted(
            available_workers - allocated_workers
        )

        for worker, p in zip(workers, waiting):
            allocated_workers.add(worker)
            p["assigned_worker"] = worker
            p["care_status"] = "STABILIZING"

            log(
                time,
                "STABILIZATION_STARTED",
                patient_id=p["patient_id"],
                personnel_id=worker
            )

    def release_worker(time, p):
        worker = p["assigned_worker"]

        if worker is not None:
            allocated_workers.remove(worker)
            p["assigned_worker"] = None

            log(
                time,
                "STABILIZATION_ENDED",
                patient_id=p["patient_id"],
                personnel_id=worker
            )

        p["care_status"] = "UNTREATED"


    # Event-driven deterioration:
    # accumulate untreated-equivalent exposure
    # and process threshold crossings exactly.
    def advance_patient(p, start, end):
        if p["true_triage"] == "BLACK":
            return

        current = max(start, p["assessment_min"])

        if current >= end - EPS:
            return

        while current < end - EPS:
            factor = (
                p["multiplier"]
                if p["care_status"] == "STABILIZING"
                else 1.0
            )

            remaining_exposure = (
                p["threshold_min"] - p["exposure_min"]
            )

            time_to_transition = (
                remaining_exposure / factor
            )

            step = min(
                end - current,
                max(0.0, time_to_transition)
            )

            p["exposure_min"] += step * factor
            current += step

            if (
                p["exposure_min"]
                >= p["threshold_min"] - EPS
            ):
                old = p["true_triage"]
                new = NEXT_STATE.get(old)

                if new is None:
                    break

                p["true_triage"] = new
                p["transitions"] += 1
                p["exposure_min"] = 0.0

                log(
                    current,
                    "PATIENT_STATE_CHANGED",
                    patient_id=p["patient_id"],
                    from_triage=old,
                    to_triage=new,
                    observed_triage=p["observed_triage"]
                )

                if new == "BLACK":
                    release_worker(current, p)
                    break


            remaining_exposure = (
                p["threshold_min"] - p["exposure_min"]
            )

            time_to_transition = (
                remaining_exposure / factor
            )

            step = min(
                end - current,
                max(0.0, time_to_transition)
            )

            p["exposure_min"] += step * factor
            current += step

            if (
                p["exposure_min"]
                >= p["threshold_min"] - EPS
            ):
                old = p["true_triage"]
                new = NEXT_STATE.get(old)

                if new is None:
                    break

                p["true_triage"] = new
                p["transitions"] += 1
                p["exposure_min"] = 0.0

                log(
                    current,
                    "PATIENT_STATE_CHANGED",
                    patient_id=p["patient_id"],
                    from_triage=old,
                    to_triage=new,
                    observed_triage=p["observed_triage"]
                )

                if new == "BLACK":
                    release_worker(current, p)
                    break

    # Process initial assessments, worker
    # availability, and reassessments at
    # each scheduled timestamp.
    ordered_times = sorted(timeline)
    previous = 0.0

    for time in ordered_times:
        # Advance underlying conditions between
        # scheduled events.
        for p in state.values():
            advance_patient(p, previous, time)

        # Release workers whose patients became
        # BLACK during the preceding interval.
        for worker, ready in worker_ready.items():
            if abs(ready - time) <= EPS:
                available_workers.add(worker)
                log(
                    time,
                    "PERSONNEL_AVAILABLE_FOR_CARE",
                    personnel_id=worker
                )

        for pid, p in state.items():
            if abs(
                p["assessment_min"] - time
            ) <= EPS:
                p["observed_triage"] = (
                    p["true_triage"]
                )
                p["last_reassessment_min"] = time

            if time in reassessments[pid]:
                old = p["observed_triage"]
                p["observed_triage"] = (
                    p["true_triage"]
                )
                p["last_reassessment_min"] = time

                log(
                    time,
                    "PATIENT_REASSESSED",
                    patient_id=pid,
                    previous_observed_triage=old,
                    observed_triage=p["observed_triage"]
                )

        assign_stabilization(time)
        previous = time

    # Validate personnel assignments and
    # patient state consistency.
    issues = []

    if commander in allocated_workers:
        issues.append(
            "Incident commander assigned to stabilization"
        )

    active_assignments = [
        p["assigned_worker"]
        for p in state.values()
        if p["assigned_worker"] is not None
    ]

    if len(active_assignments) != len(
        set(active_assignments)
    ):
        issues.append(
            "Personnel assigned to multiple patients"
        )

    for p in state.values():
        if p["true_triage"] not in VALID_STATES:
            issues.append(
                f"Invalid patient state: {p['patient_id']}"
            )

    result = {
        "scenario": scenario_dir.name,
        "source_triage_run": triage.get("run_id"),
        "mode": triage["mode"],
        "horizon_min": horizon_min,
        "reassessment_interval_min":
            reassessment_interval_min,
        "incident_commander": commander,
        "patients": [
            {
                "patient_id": p["patient_id"],
                "initial_triage": p["initial_triage"],
                "true_triage": p["true_triage"],
                "observed_triage":
                    p["observed_triage"],
                "care_status": p["care_status"],
                "assigned_worker":
                    p["assigned_worker"],
                "deterioration_count":
                    p["transitions"],
            }
            for p in state.values()
        ],
        "events": sorted(
            events,
            key=lambda e: e["time_min"]
        ),
        "validation": {
            "status": (
                "PASS" if not issues else "FAIL"
            ),
            "issues": issues
        },
        "limitations": [
            "Synthetic deterioration parameters",
            "No deterioration before first triage",
            "Fixed reassessment schedule",
            "No ambulance departure or transport",
            "No clinical treatment qualification matrix",
            "No personnel task switching after assignment",
            "BLACK is a separate model disposition, not proof of death"
        ]
    }

    if save:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%d_%H%M%S")

        run_id = (
            f"dynamics_{timestamp}_"
            f"{uuid4().hex[:8]}"
        )

        run_dir = (
            BASE / "outputs"
            / scenario_dir.name
            / "dynamics_tests"
            / run_id
        )

        run_dir.mkdir(
            parents=True,
            exist_ok=False
        )

        result["run_id"] = run_id

        output_path = (
            run_dir / "patient_dynamics_results.json"
        )

        output_path.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8"
        )

        shutil.copytree(
            scenario_dir,
            run_dir / "scenario_snapshot"
        )

        print("Saved:", output_path)

    print("\nPatient dynamics completed")
    print("Mode:", result["mode"])
    print("Commander:", commander)
    print(
        "Validation:",
        result["validation"]["status"]
    )

    for p in result["patients"]:
        print(
            p["patient_id"],
            p["initial_triage"],
            "->",
            p["true_triage"],
            "| observed:",
            p["observed_triage"],
            "| care:",
            p["care_status"]
        )

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scenario",
        default="data/scenarios/camden_yards_dynamic"
    )

    parser.add_argument(
        "--triage-results",
        required=True
    )

    parser.add_argument(
        "--horizon",
        type=float,
        default=120.0
    )

    parser.add_argument(
        "--reassessment",
        type=float,
        default=15.0
    )

    args = parser.parse_args()

    run_dynamics(
        scenario_dir=args.scenario,
        triage_results_path=args.triage_results,
        horizon_min=args.horizon,
        reassessment_interval_min=args.reassessment
    )


if __name__ == "__main__":
    main()
