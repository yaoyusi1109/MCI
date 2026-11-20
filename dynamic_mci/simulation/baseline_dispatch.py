"""
Baseline dispatch policies for the rolling single-dispatch simulator.

Every policy has the same interface as
simulation.gurobi_dispatch.choose_dispatch, so the integrated scheduler
can swap them in without other changes:

    decision, info = dispatcher(
        patients, capacity, compatible, travel_minutes,
        ambulance_id, decision_time_min=t)

Inputs (identical to the Gurobi dispatcher, so the comparison is fair):
    patients        [{'id', 'observed', 'ready_min'}]  optimizer-known triage
    capacity        {hospital_id: available beds}      live bed ledger
    compatible      {(triage, hospital_id)}            allowed pairs
    travel_minutes  {hospital_id: MCI -> hospital minutes}

None of the baselines read hidden true triage or deterioration rates.

Policies
--------
gurobi                 Existing lexicographic MILP (simulation.gurobi_dispatch).
severe_first           RED > YELLOW > GREEN, then longest wait, then nearest
                       compatible hospital. A rule-based copy of the MILP's
                       objective order; useful as a sanity check.
severe_first_balanced  RED > YELLOW > GREEN, then longest wait, then the
                       compatible hospital with the MOST remaining beds
                       (load spreading), nearest as tie-break.
closest_first          Shortest MCI -> hospital trip, triage ignored.
                       All patients share one pickup point, so "closest"
                       means the shortest delivery trip. Ties: longest wait.
fcfs                   First come, first served: earliest triage completion,
                       then nearest compatible hospital. Triage ignored.
random                 Uniformly random patient, then uniformly random
                       feasible hospital for that patient. Seeded per run.
"""

import random as _random
from functools import partial
from time import perf_counter


PRIORITY = {"RED": 3, "YELLOW": 2, "GREEN": 1}

POLICY_DESCRIPTIONS = {
    "gurobi": "Gurobi MILP: triage > wait > travel (lexicographic)",
    "severe_first": "Severe first, then longest wait, then nearest hospital",
    "severe_first_balanced": "Severe first, then hospital with most free beds",
    "closest_first": "Shortest hospital trip first (triage ignored)",
    "fcfs": "First come, first served (triage ignored)",
    "random": "Random patient and random feasible hospital (seeded)",
}

POLICIES = tuple(POLICY_DESCRIPTIONS)


def _wait(p, t):
    return max(0.0, t - float(p.get("ready_min", 0)))


def _feasible_pairs(patients, capacity, compatible, travel_minutes):
    eligible = [p for p in patients if p["observed"] in PRIORITY]
    pairs = [
        (p, h)
        for p in eligible
        for h, count in sorted(capacity.items())
        if count > 0
        and (p["observed"], h) in compatible
        and h in travel_minutes
    ]
    return eligible, pairs


def _finish(policy, pairs, eligible, chosen, ambulance_id, t, start):
    info = {
        "decision_time_min": round(t, 4),
        "ambulance_id": ambulance_id,
        "optimizer": policy.upper(),
        "model": f"baseline_{policy}",
        "candidate_patients": len(eligible),
        "feasible_pairs": len(pairs),
        "objective_value": None,
        "solve_time_ms": round((perf_counter() - start) * 1000, 3),
    }
    if chosen is None:
        info["optimizer_status"] = "NO_FEASIBLE_PAIR"
        return None, info
    patient, hospital = chosen
    info.update(
        optimizer_status="HEURISTIC",
        selected_priority=patient["observed"],
        selected_waiting_min=round(_wait(patient, t), 4),
    )
    return {
        "patient_id": patient["id"],
        "hospital_id": hospital,
        "ambulance_id": ambulance_id,
    }, info


def _rule_dispatch(policy, key, patients, capacity, compatible,
                   travel_minutes, ambulance_id, decision_time_min=0.0):
    start = perf_counter()
    eligible, pairs = _feasible_pairs(
        patients, capacity, compatible, travel_minutes)
    t = float(decision_time_min)
    chosen = min(
        pairs,
        key=lambda ph: key(ph[0], ph[1], t, capacity, travel_minutes),
        default=None,
    )
    return _finish(policy, pairs, eligible, chosen, ambulance_id, t, start)


# Sort keys: smaller is better. Patient ID and hospital ID are the final
# tie-breaks so every rule is deterministic.

def _key_severe_first(p, h, t, cap, tt):
    return (-PRIORITY[p["observed"]], -_wait(p, t), float(tt[h]), p["id"], h)


def _key_severe_balanced(p, h, t, cap, tt):
    return (-PRIORITY[p["observed"]], -_wait(p, t), -cap[h], float(tt[h]),
            p["id"], h)


def _key_closest_first(p, h, t, cap, tt):
    return (float(tt[h]), -_wait(p, t), p["id"], h)


def _key_fcfs(p, h, t, cap, tt):
    return (float(p.get("ready_min", 0)), p["id"], float(tt[h]), h)


def _random_dispatch(rng, patients, capacity, compatible, travel_minutes,
                     ambulance_id, decision_time_min=0.0):
    start = perf_counter()
    eligible, pairs = _feasible_pairs(
        patients, capacity, compatible, travel_minutes)
    chosen = None
    if pairs:
        ids = sorted({p["id"] for p, _ in pairs})
        pid = rng.choice(ids)
        options = sorted(
            ((p, h) for p, h in pairs if p["id"] == pid),
            key=lambda ph: ph[1])
        chosen = rng.choice(options)
    return _finish("random", pairs, eligible, chosen, ambulance_id,
                   float(decision_time_min), start)


_RULE_KEYS = {
    "severe_first": _key_severe_first,
    "severe_first_balanced": _key_severe_balanced,
    "closest_first": _key_closest_first,
    "fcfs": _key_fcfs,
}


def get_dispatcher(policy="gurobi", seed=0):
    """Return a dispatcher callable for the named policy.

    Call once per simulation run: the random policy keeps its own seeded
    generator, so one run is reproducible for a given seed.
    """
    policy = str(policy or "gurobi").lower()
    if policy == "gurobi":
        from simulation.gurobi_dispatch import choose_dispatch
        return choose_dispatch
    if policy == "random":
        return partial(_random_dispatch, _random.Random(seed))
    if policy in _RULE_KEYS:
        return partial(_rule_dispatch, policy, _RULE_KEYS[policy])
    raise ValueError(
        f"Unknown dispatch policy '{policy}'. Choose one of: "
        + ", ".join(POLICIES))
