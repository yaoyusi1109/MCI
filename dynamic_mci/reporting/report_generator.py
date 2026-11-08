
"""
Maryland MCI reporting.
Creates one Excel workbook and one patient CSV per run.
Does not modify the original scenario.
"""

import csv
import json
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def number(value, default=0):
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def generate_reports(run_dir):
    run_dir = Path(run_dir)
    snapshot = run_dir / "scenario_snapshot"

    result = json.loads(
        (run_dir / "simulation_results.json").read_text(
            encoding="utf-8"
        )
    )

    patients = read_csv(snapshot / "patients.csv")
    hospitals = read_csv(snapshot / "hospitals.csv")
    compatibility = read_csv(
        snapshot / "hospital_compatibility.csv"
    )

    config = json.loads(
        (snapshot / "config.json").read_text(
            encoding="utf-8"
        )
    )

    assignments = result.get("assignments", [])
    events = result.get("events", [])
    routes = result.get("routes", [])

    hospital_names = {
        h["hospital_id"]: h["name"]
        for h in hospitals
    }

    allowed = {
        (r["triage"].upper(), r["hospital_id"])
        for r in compatibility
        if str(r["compatible"]).strip().lower()
        in ("1", "true", "yes")
    }

    assignment_by_patient = {
        a["patient_id"]: a
        for a in assignments
    }

    assignment_events = {
        e["patient_id"]: number(e["time_min"])
        for e in events
        if e["event"] == "PATIENT_ASSIGNED"
    }

    patient_rows = []

    for patient in patients:
        pid = patient["patient_id"]
        triage = patient["triage"].upper()
        ready = number(patient.get("ready_min"))
        a = assignment_by_patient.get(pid)

        if a:
            assigned = assignment_events.get(pid)
            loading_end = number(
                a["loading_complete_min"]
            )
            arrival = number(
                a["hospital_arrival_min"]
            )

            wait = (
                round(assigned - ready, 3)
                if assigned is not None else None
            )

            patient_rows.append({
                "patient_id": pid,
                "triage": triage,
                "status": "DELIVERED",
                "ambulance_id": a["ambulance_id"],
                "hospital_id": a["hospital_id"],
                "hospital_name": hospital_names.get(
                    a["hospital_id"], ""
                ),
                "ready_min": ready,
                "assignment_min": assigned,
                "waiting_min": wait,
                "loading_complete_min": loading_end,
                "hospital_arrival_min": arrival,
                "time_to_hospital_min": round(
                    arrival - ready, 3
                ),
            })
        else:
            patient_rows.append({
                "patient_id": pid,
                "triage": triage,
                "status": (
                    "SEPARATE_DISPOSITION"
                    if triage == "BLACK"
                    else "NOT_DELIVERED"
                ),
                "ambulance_id": "",
                "hospital_id": "",
                "hospital_name": "",
                "ready_min": ready,
                "assignment_min": None,
                "waiting_min": None,
                "loading_complete_min": None,
                "hospital_arrival_min": None,
                "time_to_hospital_min": None,
            })

    # Save patient-level CSV.
    csv_path = run_dir / "patient_results.csv"
    fields = list(patient_rows[0].keys()) if patient_rows else []

    if fields:
        with open(
            csv_path, "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(patient_rows)

    # Independent validation checks.
    issues = []
    tolerance = 0.005

    delivered = [
        p for p in patient_rows
        if p["status"] == "DELIVERED"
    ]

    if len(delivered) != len(assignments):
        issues.append(
            "Assignment count does not match delivered records"
        )

    if len(assignment_by_patient) != len(assignments):
        issues.append("Duplicate patient assignments")

    for p in delivered:
        if (
            p["triage"], p["hospital_id"]
        ) not in allowed:
            issues.append(
                f"{p['patient_id']}: incompatible hospital"
            )

        if p["assignment_min"] is None:
            issues.append(
                f"{p['patient_id']}: assignment event missing"
            )
        else:
            loading = number(
                config.get("loading_minutes", 5)
            )
            actual = (
                p["loading_complete_min"]
                - p["assignment_min"]
            )
            if abs(actual - loading) > tolerance:
                issues.append(
                    f"{p['patient_id']}: loading duration mismatch"
                )

            if (
                p["assignment_min"]
                < p["ready_min"] - tolerance
            ):
                issues.append(
                    f"{p['patient_id']}: assigned before ready"
                )

    admissions = Counter(
        a["hospital_id"] for a in assignments
    )

    hospital_rows = []

    for h in hospitals:
        hid = h["hospital_id"]
        beds = int(number(h["available_beds"]))
        used = admissions[hid]
        remaining = beds - used

        hospital_rows.append({
            "hospital_id": hid,
            "hospital_name": h["name"],
            "trauma_level": h["trauma_level"],
            "starting_beds": beds,
            "patients_received": used,
            "remaining_beds": remaining,
        })

        if remaining < 0:
            issues.append(
                f"{hid}: hospital bed capacity exceeded"
            )

    observed_makespan = max(
        (p["hospital_arrival_min"] for p in delivered),
        default=0
    )

    if abs(
        observed_makespan - number(
            result.get("makespan_min")
        )
    ) > tolerance:
        issues.append("Reported makespan mismatch")

    # Check strict assignment order for this
    # one-ambulance, all-patients-ready demonstration.
    priority = {"RED": 0, "YELLOW": 1, "GREEN": 2}
    ordered = sorted(
        delivered,
        key=lambda p: p["assignment_min"]
        if p["assignment_min"] is not None
        else float("inf")
    )

    if (
        len({a["ambulance_id"] for a in assignments}) == 1
        and all(p["ready_min"] == 0 for p in delivered)
    ):
        ranks = [priority[p["triage"]] for p in ordered]
        if ranks != sorted(ranks):
            issues.append("Triage assignment order violated")

    # Verify 60-minute turnover between deliveries
    # and subsequent hospital-to-MCI return routes.
    turnover = number(
        config.get("hospital_turnover_minutes", 60)
    )
    delivered_events = sorted(
        (
            number(e["time_min"])
            for e in events
            if e["event"] == "PATIENT_DELIVERED"
        )
    )
    turnover_events = sorted(
        (
            number(e["time_min"])
            for e in events
            if e["event"] == "TURNOVER_COMPLETE"
        )
    )

    if len(turnover_events) != max(
        0, len(delivered_events) - 1
    ):
        issues.append("Unexpected turnover event count")
    else:
        for arrival, completed in zip(
            delivered_events, turnover_events
        ):
            if abs(
                completed - arrival - turnover
            ) > tolerance:
                issues.append("Turnover duration mismatch")

    total_distance = sum(
        number(r.get("distance_miles"))
        for r in routes
    )

    total_driving = sum(
        number(r["end_min"]) - number(r["start_min"])
        for r in routes
    )

    summary = {
        "run_id": result.get("run_id", run_dir.name),
        "scenario": result.get("scenario", ""),
        "simulation_status": result.get("status", ""),
        "observed_makespan_min": round(
            observed_makespan, 3
        ),
        "patients_total": len(patients),
        "patients_delivered": len(delivered),
        "separate_dispositions": sum(
            p["status"] == "SEPARATE_DISPOSITION"
            for p in patient_rows
        ),
        "hospital_beds_consumed": len(delivered),
        "total_driving_miles": round(
            total_distance, 3
        ),
        "total_driving_minutes": round(
            total_driving, 3
        ),
        "validation_status": (
            "PASS" if not issues else "FAIL"
        ),
        "validation_issues": issues,
        "optimization_scope": (
            "Rolling batch assignment; global "
            "makespan optimality not established"
        ),
    }

    (run_dir / "simulation_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8"
    )

    (run_dir / "validation_report.json").write_text(
        json.dumps({
            "status": summary["validation_status"],
            "issues": issues,
            "checks": [
                "Patient assignments",
                "Hospital compatibility",
                "Hospital capacity",
                "Loading duration",
                "Patient readiness",
                "Makespan consistency",
                "Triage order (limited scenario)",
                "Turnover duration",
            ],
            "note": (
                "PASS means these implemented checks "
                "passed; it is not proof of global "
                "optimality or clinical validity."
            )
        }, indent=2),
        encoding="utf-8"
    )

    # Create Excel workbook.
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary & Patients"

    ws.append(["MARYLAND MCI - SIMULATION RESULTS"])
    ws.append(["Run ID", summary["run_id"]])
    ws.append(["Scenario", summary["scenario"]])
    ws.append([
        "Observed makespan (min)",
        summary["observed_makespan_min"]
    ])
    ws.append([
        "Patients delivered",
        summary["patients_delivered"]
    ])
    ws.append([
        "Total patients",
        summary["patients_total"]
    ])
    ws.append([
        "Driving distance (miles)",
        summary["total_driving_miles"]
    ])
    ws.append([
        "Validation", summary["validation_status"]
    ])
    ws.append([
        "Optimization scope",
        summary["optimization_scope"]
    ])
    ws.append([])

    ws.append(list(fields))
    for p in patient_rows:
        ws.append([p[k] for k in fields])

    timeline = wb.create_sheet("Ambulance Timeline")
    event_columns = [
        "time_min", "event", "ambulance_id",
        "patient_id", "hospital_id", "triage",
        "station_id"
    ]
    timeline.append(event_columns)
    for e in sorted(
        events, key=lambda e: number(e["time_min"])
    ):
        timeline.append([
            e.get(k, "") for k in event_columns
        ])

    hospitals_ws = wb.create_sheet("Hospital Utilization")
    hospital_columns = list(hospital_rows[0].keys())
    hospitals_ws.append(hospital_columns)
    for h in hospital_rows:
        hospitals_ws.append([
            h[k] for k in hospital_columns
        ])

    validation_ws = wb.create_sheet("Validation")
    validation_ws.append([
        "Overall status", summary["validation_status"]
    ])
    validation_ws.append([
        "Global optimality", "NOT ESTABLISHED"
    ])
    validation_ws.append([])
    validation_ws.append(["Issue"])
    if issues:
        for issue in issues:
            validation_ws.append([issue])
    else:
        validation_ws.append([
            "No issues found in implemented checks"
        ])

    # Consistent, readable Excel formatting.
    for sheet in wb.worksheets:
        sheet.freeze_panes = (
            "A12" if sheet == ws else "A2"
        )
        sheet.sheet_view.showGridLines = False

        for cell in sheet[1]:
            cell.fill = PatternFill(
                "solid", fgColor="17365D"
            )
            cell.font = Font(
                bold=True, color="FFFFFF"
            )

        if sheet == ws:
            for cell in sheet[11]:
                cell.fill = PatternFill(
                    "solid", fgColor="DCEAF7"
                )
                cell.font = Font(
                    bold=True, color="17365D"
                )

        for col in sheet.columns:
            letter = get_column_letter(
                col[0].column
            )
            width = max(
                len(str(c.value or ""))
                for c in col
            )
            sheet.column_dimensions[letter].width = min(
                max(width + 3, 14), 48
            )

        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(
                    vertical="center"
                )

    workbook_path = run_dir / "MCI_Full_Results.xlsx"
    wb.save(workbook_path)

    return {
        "excel": str(workbook_path),
        "patients_csv": str(csv_path),
        "summary": summary
    }
