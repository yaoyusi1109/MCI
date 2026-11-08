
"""
Maryland MCI - Shared Patient Care Scheduler
Stage 2A: Resource-constrained care and reassessment.

Research simulation only.
Synthetic deterioration parameters, not clinical predictions.

Does not modify existing scenario files, Gurobi,
or the main ambulance simulation.
"""

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE = Path(__file__).resolve().parents[1]

NEXT_STATE = {
    "GREEN": "YELLOW",
    "YELLOW": "RED",
    "RED": "BLACK",
}

PRIORITY = {
    "RED": 0,
    "YELLOW": 1,
    "GREEN": 2,
    "BLACK": 3,
}


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def truth(value):
    return str(value).strip().upper() in (
        "TRUE", "YES", "1"
    )


def run_care_scheduler(
    scenario_dir,
    triage_results_path,
    horizon=120.0,
    reassessment_interval=15.0,
    save=True,
):
    scenario_dir = Path(scenario_dir).resolve()

    triage = json.loads(
        Path(triage_results_path).read_text(
            encoding="utf-8"
        )
    )

    if triage["scenario"] != scenario_dir.name:
        raise ValueError("Scenario mismatch")

    if triage["validation"]["status"] != "PASS":
        raise ValueError("Initial triage validation failed")

    if horizon <= 0 or reassessment_interval <= 0:
        raise ValueError("Invalid simulation duration")

    patients_csv = read_csv(
        scenario_dir / "patients.csv"
    )
    dynamics_csv = read_csv(
        scenario_dir / "patient_dynamics.csv"
    )
    personnel_csv = read_csv(
        scenario_dir / "ems_personnel.csv"
    )
    units_csv = read_csv(
        scenario_dir / "response_units.csv"
    )

    params = {
        p["patient_id"]: p for p in dynamics_csv
    }

    units = {
        u["unit_id"]: u for u in units_csv
    }

    people = {
        p["personnel_id"]: p for p in personnel_csv
    }

    commander = triage["incident_commander"]
    selected_units = set(triage["units"])

    events = []

    def log(t, kind, **details):
        events.append({
            "time_min": round(t, 4),
            "event": kind,
            **details
        })

    # ----------------------------------------
    # Initialize patient states
    # ----------------------------------------

    assessments = {
        a["patient_id"]: a
        for a in triage["assessments"]
    }

    if len(assessments) != len(patients_csv):
        raise ValueError(
            "All patients require initial triage"
        )

    state = {}

    for patient in patients_csv:
        pid = patient["patient_id"]

        if pid not in params or pid not in assessments:
            raise ValueError(
                f"Missing patient data for {pid}"
            )

        initial = patient["triage"].upper()

        if initial not in PRIORITY:
            raise ValueError(f"Invalid triage: {pid}")

        threshold = float(
            params[pid]["minutes_per_deterioration"]
        )
        multiplier = float(
            params[pid]["stabilization_multiplier"]
        )

        if initial != "BLACK" and threshold <= 0:
            raise ValueError(
                f"Invalid deterioration threshold: {pid}"
            )

        if not 0 < multiplier <= 1:
            raise ValueError(
                f"Invalid stabilization multiplier: {pid}"
            )

        assessed_at = float(
            assessments[pid]["triage_complete_min"]
        )

        state[pid] = {
            "patient_id": pid,
            "initial_triage": initial,
            "true_triage": initial,
            "observed_triage": None,
            "assessment_min": assessed_at,
            "next_reassessment": (
                assessed_at + reassessment_interval
            ),
            "threshold": threshold,
            "multiplier": multiplier,
            "exposure": 0.0,
            "status": "AWAITING_INITIAL_TRIAGE",
            "deteriorations": 0,
            "stabilization_minutes": 0.0,
            "reassessments": 0,
        }

    # ----------------------------------------
    # Initialize personnel
    # ----------------------------------------

    workers = {}

    for person in personnel_csv:
        pid = person["personnel_id"]
        uid = person["unit_id"]

        if uid not in selected_units:
            continue

        if uid not in units:
            raise ValueError(f"Unknown unit: {uid}")

        if pid == commander:
            role = "INCIDENT_COMMAND"
        else:
            role = "AVAILABLE"

        workers[pid] = {
            "unit_id": uid,
            "role": role,
            "triage_capable": truth(
                person["triage_capable"]
            ),
            "stabilization_capable": truth(
                person["stabilization_capable"]
            ),
            "arrival_min": float(
                triage["unit_arrivals_min"][uid]
            ),
            "assigned_patient": None,
            "busy_until": 0.0,
        }

    if commander not in workers:
        raise ValueError("Commander not found")

    # Initial triage is already completed
    # by the preceding standalone module.
    # No ambulance departure is modeled yet.

    # ----------------------------------------
    # Discrete-event approximation
    # ----------------------------------------

    STEP = 45.0 / 60.0
    time = 0.0
    EPS = 1e-7

    while time < horizon - EPS:
        end = min(time + STEP, horizon)

        # Make newly assessed patients visible.
        for p in state.values():
            if (
                p["observed_triage"] is None
                and p["assessment_min"] <= time + EPS
            ):
                p["observed_triage"] = p["true_triage"]
                p["status"] = "WAITING"

                log(
                    time,
                    "PATIENT_AVAILABLE_FOR_CARE",
                    patient_id=p["patient_id"],
                    triage=p["observed_triage"]
                )

        # Release completed reassessment tasks.
        for wid, w in workers.items():
            if (
                w["role"] == "REASSESSING"
                and w["busy_until"] <= time + EPS
            ):
                pid = w["assigned_patient"]
                p = state[pid]

                old = p["observed_triage"]
                p["observed_triage"] = p["true_triage"]
                p["reassessments"] += 1

                log(
                    time,
                    "PATIENT_REASSESSED",
                    patient_id=pid,
                    personnel_id=wid,
                    previous_observed_triage=old,
                    observed_triage=p["observed_triage"]
                )

                w["role"] = "AVAILABLE"
                w["assigned_patient"] = None

                p["status"] = "WAITING"

        # Clear stabilization assignments at
        # each scheduling decision. This allows
        # resources to be reallocated dynamically.
        for w in workers.values():
            if w["role"] == "STABILIZING":
                w["role"] = "AVAILABLE"
                w["assigned_patient"] = None

        # Only arrived, non-command workers
        # can receive new assignments.
        free = sorted(
            wid for wid, w in workers.items()
            if w["role"] == "AVAILABLE"
            and w["arrival_min"] <= time + EPS
        )

        # Reassessment is prioritized over
        # stabilization when due.
        due = sorted(
            (
                p for p in state.values()
                if p["observed_triage"] is not None
                and p["true_triage"] != "BLACK"
                and p["next_reassessment"] <= time + EPS
                and p["status"] != "REASSESSING"
            ),
            key=lambda p: (
                PRIORITY[p["observed_triage"]],
                p["next_reassessment"],
                p["patient_id"]
            )
        )

        for p in due:
            eligible = [
                wid for wid in free
                if workers[wid]["triage_capable"]
            ]

            if not eligible:
                break

            wid = eligible[0]
            free.remove(wid)

            w = workers[wid]
            w["role"] = "REASSESSING"
            w["assigned_patient"] = p["patient_id"]
            w["busy_until"] = end

            p["status"] = "REASSESSING"
            p["next_reassessment"] = (
                end + reassessment_interval
            )

            log(
                time,
                "REASSESSMENT_STARTED",
                patient_id=p["patient_id"],
                personnel_id=wid
            )

        # Allocate remaining workers to
        # stabilization, highest observed
        # triage priority first.
        waiting = sorted(
            (
                p for p in state.values()
                if p["observed_triage"] is not None
                and p["true_triage"] != "BLACK"
                and p["status"] != "REASSESSING"
            ),
            key=lambda p: (
                PRIORITY[p["observed_triage"]],
                p["patient_id"]
            )
        )

        stabilized = set()

        for p in waiting:
            eligible = [
                wid for wid in free
                if workers[wid]["stabilization_capable"]
            ]

            if not eligible:
                break

            wid = eligible[0]
            free.remove(wid)

            workers[wid]["role"] = "STABILIZING"
            workers[wid]["assigned_patient"] = (
                p["patient_id"]
            )

            stabilized.add(p["patient_id"])

            p["status"] = "STABILIZING"

            log(
                time,
                "STABILIZATION_ASSIGNED",
                patient_id=p["patient_id"],
                personnel_id=wid
            )

        # Advance underlying patient conditions.
        for p in state.values():
            if p["true_triage"] == "BLACK":
                continue

            active_start = max(
                time, p["assessment_min"]
            )

            duration = max(0.0, end - active_start)

            if duration <= EPS:
                continue

            receiving_care = (
                p["patient_id"] in stabilized
            )

            factor = (
                p["multiplier"]
                if receiving_care else 1.0
            )

            if receiving_care:
                p["stabilization_minutes"] += duration

            exposure_left = duration * factor
            elapsed = 0.0

            while (
                exposure_left > EPS
                and p["true_triage"] != "BLACK"
            ):
                needed = (
                    p["threshold"] - p["exposure"]
                )

                used = min(needed, exposure_left)

                p["exposure"] += used
                exposure_left -= used
                elapsed += used / factor

                if (
                    p["exposure"]
                    >= p["threshold"] - EPS
                ):
                    old = p["true_triage"]
                    new = NEXT_STATE[old]

                    p["true_triage"] = new
                    p["deteriorations"] += 1
                    p["exposure"] = 0.0

                    log(
                        active_start + elapsed,
                        "PATIENT_STATE_CHANGED",
                        patient_id=p["patient_id"],
                        from_triage=old,
                        to_triage=new
                    )

                    if new == "BLACK":
                        p["status"] = (
                            "SEPARATE_DISPOSITION"
                        )

        time = end

    # ----------------------------------------
    # Finalize and validate
    # ----------------------------------------

    issues = []

    for wid, w in workers.items():
        if (
            w["role"] == "INCIDENT_COMMAND"
            and w["assigned_patient"] is not None
        ):
            issues.append(
                f"Commander assigned to care: {wid}"
            )

    for p in state.values():
        if p["true_triage"] not in PRIORITY:
            issues.append(
                f"Invalid patient state: {p['patient_id']}"
            )

    result = {
        "scenario": scenario_dir.name,
        "mode": triage["mode"],
        "horizon_min": horizon,
        "incident_commander": commander,
        "patients": [
            {
                "patient_id": p["patient_id"],
                "initial_triage": p["initial_triage"],
                "true_triage": p["true_triage"],
                "observed_triage": p["observed_triage"],
                "deteriorations": p["deteriorations"],
                "reassessments": p["reassessments"],
                "stabilization_minutes": round(
                    p["stabilization_minutes"], 3
                )
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
            "45-second scheduling resolution",
            "No deterioration before initial triage",
            "No ambulance departures or transport",
            "Reassessment and stabilization simplified",
            "No treatment qualification matrix",
            "BLACK is not automatically deceased"
        ]
    }

    if save:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%d_%H%M%S")

        run_id = (
            f"care_{timestamp}_{uuid4().hex[:8]}"
        )

        run_dir = (
            BASE / "outputs"
            / scenario_dir.name
            / "care_tests"
            / run_id
        )

        run_dir.mkdir(
            parents=True,
            exist_ok=False
        )

        result["run_id"] = run_id

        output_path = run_dir / "care_results.json"

        output_path.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8"
        )

        shutil.copytree(
            scenario_dir,
            run_dir / "scenario_snapshot"
        )

        print("Saved:", output_path)

    print("\nCare scheduling completed")
    print("Mode:", result["mode"])
    print("Commander:", commander)
    print("Validation:", result["validation"]["status"])

    for p in result["patients"]:
        print(
            p["patient_id"],
            p["initial_triage"],
            "->", p["true_triage"],
            "| observed:", p["observed_triage"],
            "| reassessments:", p["reassessments"],
            "| stabilization:",
            p["stabilization_minutes"], "min"
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

    args = parser.parse_args()

    run_care_scheduler(
        args.scenario,
        args.triage_results,
        horizon=args.horizon
    )


if __name__ == "__main__":
    main()
