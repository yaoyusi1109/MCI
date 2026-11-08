
"""
Maryland MCI — Gurobi Assignment Optimizer

Initial implementation:
- One MCI
- CSV-driven patient, ambulance, and hospital inputs
- RED > YELLOW > GREEN triage precedence
- BLACK patients excluded from automatic transport
- Non-reusable hospital beds
- 5-minute patient loading
- 60-minute hospital turnover
- Minimize current-batch patient delivery makespan

This module does not modify input CSV files.
"""

import argparse
import json
from pathlib import Path

import gurobipy as gp
import pandas as pd
from gurobipy import GRB


TRIAGE_PRIORITY = {
    "RED": 3,
    "YELLOW": 2,
    "GREEN": 1,
    "BLACK": 0,
}


def load_scenario(scenario_dir):
    path = Path(scenario_dir)

    patients = pd.read_csv(path / "patients.csv")
    ambulances = pd.read_csv(path / "ambulances.csv")
    hospitals = pd.read_csv(path / "hospitals.csv")
    compatibility = pd.read_csv(
        path / "hospital_compatibility.csv"
    )

    with open(path / "config.json", encoding="utf-8") as f:
        config = json.load(f)

    return {
        "patients": patients,
        "ambulances": ambulances,
        "hospitals": hospitals,
        "compatibility": compatibility,
        "config": config,
    }


def optimize_mci(scenario, travel_times, current_time=0.0):
    """
    Optimize the current assignment batch.

    travel_times:
        Dictionary keyed by hospital ID:
        {"H001": 2.15, "H002": 8.0}

    Only ambulances already AVAILABLE_AT_MCI are eligible.
    """

    patients_df = scenario["patients"]
    ambulances_df = scenario["ambulances"]
    hospitals_df = scenario["hospitals"]
    compatibility_df = scenario["compatibility"]
    config = scenario["config"]

    loading_minutes = float(
        config.get("loading_minutes", 5)
    )
    turnover_minutes = float(
        config.get("hospital_turnover_minutes", 60)
    )

    patients = patients_df[
        (patients_df["status"] == "WAITING")
        & (patients_df["triage"].isin(
            ["RED", "YELLOW", "GREEN"]
        ))
        & (patients_df["ready_min"] <= current_time)
    ].copy()

    ambulances = ambulances_df[
        ambulances_df["status"] == "AVAILABLE_AT_MCI"
    ].copy()

    hospitals = hospitals_df[
        hospitals_df["available_beds"] > 0
    ].copy()

    if patients.empty or ambulances.empty:
        return {
            "status": "NO_ASSIGNMENTS",
            "assignments": [],
        }

    P = patients["patient_id"].tolist()
    A = ambulances["ambulance_id"].tolist()
    H = hospitals["hospital_id"].tolist()

    patient_info = patients.set_index("patient_id")
    hospital_info = hospitals.set_index("hospital_id")

    compatible = {}

    for _, row in compatibility_df.iterrows():
        compatible[
            (str(row["triage"]).upper(), row["hospital_id"])
        ] = int(row["compatible"])

    model = gp.Model("maryland_mci_makespan")
    model.Params.OutputFlag = 0

    # x[p,a,h] = 1 if ambulance a transports
    # patient p to hospital h.
    x = model.addVars(
        P, A, H,
        vtype=GRB.BINARY,
        name="assignment"
    )

    # z[p] = 1 if patient p receives an assignment.
    z = model.addVars(
        P,
        vtype=GRB.BINARY,
        name="assigned"
    )

    # Latest hospital arrival for the current batch.
    makespan = model.addVar(
        lb=0,
        vtype=GRB.CONTINUOUS,
        name="makespan"
    )

    # Each patient assigned at most once.
    for p in P:
        model.addConstr(
            gp.quicksum(
                x[p, a, h]
                for a in A for h in H
            ) == z[p]
        )

    # Each ambulance carries at most one patient.
    for a in A:
        model.addConstr(
            gp.quicksum(
                x[p, a, h]
                for p in P for h in H
            ) <= 1
        )

    # Hospital beds are non-reusable.
    for h in H:
        capacity = int(
            hospital_info.loc[h, "available_beds"]
        )

        model.addConstr(
            gp.quicksum(
                x[p, a, h]
                for p in P for a in A
            ) <= capacity
        )

    # Hospital compatibility.
    for p in P:
        triage = str(
            patient_info.loc[p, "triage"]
        ).upper()

        for a in A:
            for h in H:
                if compatible.get((triage, h), 0) != 1:
                    model.addConstr(
                        x[p, a, h] == 0
                    )

    # Patient delivery time = current time
    # + loading + hospital travel.
    for p in P:
        for a in A:
            for h in H:
                if h not in travel_times:
                    model.addConstr(x[p, a, h] == 0)
                    continue

                arrival = (
                    current_time
                    + loading_minutes
                    + float(travel_times[h])
                )

                model.addConstr(
                    makespan >= arrival * x[p, a, h]
                )

    # Multiobjective optimization:
    # First maximize RED assignments,
    # then YELLOW, then GREEN,
    # then minimize makespan.
    #
    # Priorities are lexicographic rather
    # than arbitrary numerical weights.
    for triage, priority in [
        ("RED", 4),
        ("YELLOW", 3),
        ("GREEN", 2),
    ]:
        group = [
            p for p in P
            if str(
                patient_info.loc[p, "triage"]
            ).upper() == triage
        ]

        model.setObjectiveN(
            gp.quicksum(z[p] for p in group),
            index=4-priority,
            priority=priority,
            weight=-1,
            name=f"assign_{triage}"
        )

    model.setObjectiveN(
        makespan,
        index=3,
        priority=1,
        weight=1,
        name="minimize_makespan"
    )

    model.optimize()

    if model.Status != GRB.OPTIMAL:
        return {
            "status": f"GUROBI_STATUS_{model.Status}",
            "assignments": [],
        }

    assignments = []

    for p in P:
        for a in A:
            for h in H:
                if x[p, a, h].X > 0.5:
                    hospital_arrival = (
                        current_time
                        + loading_minutes
                        + float(travel_times[h])
                    )

                    assignments.append({
                        "patient_id": p,
                        "ambulance_id": a,
                        "hospital_id": h,
                        "triage": str(
                            patient_info.loc[p, "triage"]
                        ),
                        "assignment_min": current_time,
                        "loading_complete_min": (
                            current_time + loading_minutes
                        ),
                        "hospital_arrival_min": (
                            hospital_arrival
                        ),
                        "ambulance_available_min": (
                            hospital_arrival
                            + turnover_minutes
                        ),
                    })

    return {
        "status": "OPTIMAL",
        "makespan_min": round(makespan.X, 3),
        "assignments": assignments,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scenario",
        default="data/scenarios/camden_yards"
    )

    args = parser.parse_args()

    scenario = load_scenario(args.scenario)

    # Demonstration travel times.
    # Replace with Valhalla/OSMnx matrix later.
    travel_times = {
        "H001": 2.151,
        "H002": 8.0,
        "H003": 12.0,
    }

    result = optimize_mci(
        scenario,
        travel_times,
        current_time=0
    )

    print(json.dumps(result, indent=2))

    output = Path("outputs/assignment_batch.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"\nSaved: {output}")


if __name__ == "__main__":
    main()
