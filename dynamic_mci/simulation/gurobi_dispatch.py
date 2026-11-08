
"""
One-dispatch Gurobi MILP for Maryland MCI.

The simulator owns patient states, ambulances,
and hospital bed reservations.

Gurobi receives optimizer-known triage and
currently available hospital capacity.

Hierarchical objectives:
1. Highest observed triage priority
2. Longest waiting time
3. Shortest hospital travel time
4. Deterministic tie-breaking

This is a rolling single-dispatch optimizer,
not a full multi-trip scheduling model.
"""

from time import perf_counter

import gurobipy as gp
from gurobipy import GRB


PRIORITY = {
    "RED": 3,
    "YELLOW": 2,
    "GREEN": 1,
}


def choose_dispatch(
    patients,
    capacity,
    compatible,
    travel_minutes,
    ambulance_id,
    decision_time_min=0.0,
):
    """
    Select one patient-hospital assignment.

    Does not modify patient states or bed capacity.

    Patient dictionaries must contain:
        id
        observed
        ready_min

    Returns:
        (decision, decision_log)
    """

    start = perf_counter()

    eligible = [
        p for p in patients
        if p["observed"] in PRIORITY
    ]

    choices = [
        (p, h)
        for p in eligible
        for h, count in capacity.items()
        if count > 0
        and (p["observed"], h) in compatible
        and h in travel_minutes
    ]

    info = {
        "decision_time_min": round(
            decision_time_min, 4
        ),
        "ambulance_id": ambulance_id,
        "optimizer": "GUROBI",
        "model": "single_dispatch_multiobjective_v2",
        "candidate_patients": len(eligible),
        "feasible_pairs": len(choices),
    }

    if not choices:
        return None, {
            **info,
            "optimizer_status": "NO_FEASIBLE_PAIR",
            "objective_value": None,
            "solve_time_ms": 0.0,
        }

    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag", 0)
        env.start()

        with gp.Model(
            "mci_next_dispatch", env=env
        ) as model:

            x = model.addVars(
                len(choices),
                vtype=GRB.BINARY,
                name="assign",
            )

            model.addConstr(
                gp.quicksum(
                    x[i]
                    for i in range(len(choices))
                ) == 1,
                name="exactly_one_dispatch",
            )

            # Objective 1:
            # Highest optimizer-known triage priority.
            priority_expr = gp.quicksum(
                PRIORITY[p["observed"]] * x[i]
                for i, (p, h) in enumerate(choices)
            )

            # Objective 2:
            # Longest waiting time since readiness.
            waiting_expr = gp.quicksum(
                max(
                    0.0,
                    decision_time_min
                    - float(p.get("ready_min", 0))
                ) * x[i]
                for i, (p, h) in enumerate(choices)
            )

            # Objective 3:
            # Shortest compatible hospital travel.
            travel_expr = gp.quicksum(
                float(travel_minutes[h]) * x[i]
                for i, (p, h) in enumerate(choices)
            )

            # Objective 4:
            # Deterministic tie-breaking.
            tie_expr = gp.quicksum(
                i * x[i]
                for i in range(len(choices))
            )

            model.ModelSense = GRB.MAXIMIZE

            model.setObjectiveN(
                priority_expr,
                index=0,
                priority=3,
                name="triage_priority",
                abstol=0.0,
                reltol=0.0,
            )

            model.setObjectiveN(
                waiting_expr,
                index=1,
                priority=2,
                name="waiting_time",
                abstol=0.0,
                reltol=0.0,
            )

            model.setObjectiveN(
                -travel_expr,
                index=2,
                priority=1,
                name="travel_time",
                abstol=0.0,
                reltol=0.0,
            )

            model.setObjectiveN(
                -tie_expr,
                index=3,
                priority=0,
                name="deterministic_tiebreak",
                abstol=0.0,
                reltol=0.0,
            )

            model.optimize()

            status = (
                "OPTIMAL"
                if model.Status == GRB.OPTIMAL
                else str(model.Status)
            )

            info.update(
                optimizer_status=status,
                solve_time_ms=round(
                    (perf_counter() - start) * 1000,
                    3,
                ),
                objective_value=(
                    round(model.ObjVal, 6)
                    if model.SolCount
                    else None
                ),
            )

            if model.Status != GRB.OPTIMAL:
                raise RuntimeError(
                    f"Gurobi dispatch status: "
                    f"{model.Status}"
                )

            selected = next(
                i
                for i in range(len(choices))
                if x[i].X > 0.5
            )

            patient, hospital = choices[selected]

            info["selected_priority"] = (
                patient["observed"]
            )

            info["selected_waiting_min"] = round(
                max(
                    0.0,
                    decision_time_min
                    - float(
                        patient.get("ready_min", 0)
                    )
                ),
                4,
            )

            return {
                "patient_id": patient["id"],
                "hospital_id": hospital,
                "ambulance_id": ambulance_id,
            }, info
