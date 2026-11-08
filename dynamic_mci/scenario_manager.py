
"""
Maryland MCI Scenario Manager

Loads and saves scenario CSV files and config.json.
Original scenario inputs are not changed during simulation.
"""

import csv
import io
import json
import os
import re
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = BASE_DIR / "data" / "scenarios"


CSV_FILES = [
    "patients.csv",
    "ambulances.csv",
    "stations.csv",
    "hospitals.csv",
    "hospital_compatibility.csv",
    "incidents.csv",
    "road_conditions.csv",
    "resource_requests.csv",
    "response_units.csv",
    "ems_personnel.csv",
]


REQUIRED_COLUMNS = {
    "patients.csv": [
        "patient_id", "incident_id", "triage",
        "ready_min", "status"
    ],
    "ambulances.csv": [
        "ambulance_id", "station_id", "status",
        "available_min"
    ],
    "stations.csv": [
        "station_id", "station_name"
    ],
    "hospitals.csv": [
        "hospital_id", "name", "trauma_level",
        "available_beds"
    ],
    "hospital_compatibility.csv": [
        "triage", "hospital_id", "compatible"
    ],
    "incidents.csv": [
        "incident_id", "incident_name",
        "latitude", "longitude",
        "pickup_latitude", "pickup_longitude"
    ],
    "road_conditions.csv": [
        "condition_id"
    ],
    "resource_requests.csv": [
        "request_id"
    ],

    "response_units.csv": [
        "unit_id",
        "unit_type",
        "station_id",
        "dispatch_min",
        "status",
    ],
    "ems_personnel.csv": [
    "personnel_id",
    "unit_id",
    "qualification",
    "triage_capable",
    "stabilization_capable",
    "command_eligible",
],


}


def scenario_path(scenario_id):
    if not re.fullmatch(
        r"[A-Za-z0-9_-]+", scenario_id
    ):
        raise ValueError("Invalid scenario ID")

    path = SCENARIOS_DIR / scenario_id

    if not path.is_dir():
        raise FileNotFoundError(
            f"Scenario not found: {scenario_id}"
        )

    return path


def list_scenarios():
    if not SCENARIOS_DIR.exists():
        return []

    return sorted(
        p.name for p in SCENARIOS_DIR.iterdir()
        if p.is_dir()
    )


def load_scenario(scenario_id):
    path = scenario_path(scenario_id)

    result = {
        "scenario_id": scenario_id,
        "tables": {},
        "config": {}
    }

    for filename in CSV_FILES:
        file_path = path / filename

        if not file_path.exists():
            if filename in (
                "response_units.csv",
                "ems_personnel.csv"
            ):
                result["tables"][filename] = {
                    "columns": REQUIRED_COLUMNS[filename],
                    "rows": []
                }
                continue

            raise FileNotFoundError(
                f"Missing scenario file: {filename}"
            )

        with open(
            file_path,
            newline="",
            encoding="utf-8-sig"
        ) as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                raise ValueError(
                    f"{filename} has no header"
                )

            missing = (
                set(REQUIRED_COLUMNS[filename])
                - set(reader.fieldnames)
            )

            if missing:
                raise ValueError(
                    f"{filename} missing columns: "
                    + ", ".join(sorted(missing))
                )

            result["tables"][filename] = {
                "columns": reader.fieldnames,
                "rows": list(reader)
            }

    config_path = path / "config.json"

    with open(
        config_path,
        encoding="utf-8"
    ) as f:
        result["config"] = json.load(f)

    return result










def save_scenario(scenario_id, data):
    path = scenario_path(scenario_id)

    tables = data.get("tables", {})
    config = data.get("config", {})

    if not isinstance(tables, dict):
        raise ValueError("Tables must be an object")

    if not isinstance(config, dict):
        raise ValueError("Config must be an object")

    # Validate everything before writing any files.
    prepared = {}

    for filename in CSV_FILES:

        # Preserve older scenarios without resource files.
        if (
            filename in (
                "response_units.csv",
                "ems_personnel.csv"
            )
            and not (path / filename).exists()
            and not tables.get(filename, {}).get("rows")
        ):
            continue

        if filename not in tables:
            raise ValueError(
                f"Missing table: {filename}"
            )

        table = tables[filename]
        columns = table.get("columns", [])
        rows = table.get("rows", [])

        if not isinstance(columns, list):
            raise ValueError(
                f"Invalid columns in {filename}"
            )

        if not isinstance(rows, list):
            raise ValueError(
                f"Invalid rows in {filename}"
            )

        missing = set(
            REQUIRED_COLUMNS[filename]
        ) - set(columns)

        if missing:
            raise ValueError(
                f"{filename} missing columns: "
                + ", ".join(sorted(missing))
            )

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=columns,
            extrasaction="ignore"
        )

        writer.writeheader()

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    f"Invalid row in {filename}"
                )

            writer.writerow({
                col: row.get(col, "")
                for col in columns
            })

        prepared[filename] = buffer.getvalue()

    prepared["config.json"] = json.dumps(
        config, indent=2
    )

    # Write temporary files first, then replace.
    # This reduces the risk of partially written CSVs.
    staged = []

    try:
        for filename, content in prepared.items():
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path,
                prefix=".scenario_",
                suffix=".tmp",
                delete=False
            ) as f:
                f.write(content)
                staged.append(
                    (Path(f.name), path / filename)
                )

        for temporary, destination in staged:
            os.replace(temporary, destination)

    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)

    return {
        "status": "saved",
        "scenario_id": scenario_id
    }
