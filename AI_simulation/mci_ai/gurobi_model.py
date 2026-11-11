"""Step 5: t0 dispatch plan for ONE MCI site and a fixed ambulance fleet (Gurobi).

Simplified from Repoussis et al. (2016), Section 3.2, with the reward of Chang et al. (2026):
- ambulance r makes ordered trips j = 1..J, one patient per trip   (Repoussis 5-9)
- first site arrival >= max(ambulance travel, scene delay TD)       (Repoussis 10)
- next arrival = previous arrival + load + to hospital + handover + back to site
                                                                    (Repoussis 11-13; one site => linear)
- immediate patients only to trauma hospitals                        (Repoussis 19)
- hospital beds after minor walk-ins                                 (Repoussis 16-17 / Chang minor agents)
- all immediate pickups before any delayed pickup (optional)         (Repoussis 25)
- objective "survival": max sum of SF_class(hospital arrival)        (Chang reward, eq. 2-3)
  objective "flow_time": min sum of hospital arrival times           (Repoussis flow time)

Because there is one site and every patient is present at t0, patients of the same
triage class are interchangeable. So the variables are per trip and class
(x[r, j, h, k]), not per patient, which removes symmetry and keeps the model small enough
for the restricted pip license. Patient ids are attached afterwards.
"""

import math
import time

import gurobipy as gp
from gurobipy import GRB, quicksum

from . import config
from .scenario import travel_min

CLASSES = ("immediate", "delayed")


def survival(t, curve, k):
    b0, b1, b2 = config.SURVIVAL[curve][k]
    return b0 / ((max(t, 0.0) / b1) ** b2 + 1.0)


def walk_ins(scn, minor, share):
    """Minor patients who self-present, split by inverse distance (Chang et al. agent idea)."""
    n = int(round(minor * share))
    w = {h["id"]: 1.0 / (0.5 + _km(scn["site"], h)) for h in scn["hospitals"]}
    tot = sum(w.values())
    raw = {h: n * v / tot for h, v in w.items()}
    out = {h: int(math.floor(v)) for h, v in raw.items()}
    for h in sorted(raw, key=lambda h: raw[h] - out[h], reverse=True)[: n - sum(out.values())]:
        out[h] += 1
    return out


def _km(a, b):
    from .scenario import distance_km
    return distance_km(a, b)


def build_data(scn, p):
    """Turn one validated AI scenario `p` + constants into model data."""
    c = scn["constants"]
    H = [h["id"] for h in scn["hospitals"]]
    trauma = {h["id"]: h["trauma"] for h in scn["hospitals"]}
    d = {h["id"]: travel_min(scn["site"], h, c) * p["hospitals"][h["id"]]["delay"] for h in scn["hospitals"]}
    start = {a["id"]: travel_min(a, scn["site"], c) * p["approach_delay_factor"] for a in scn["ambulances"]}
    wi = walk_ins(scn, p["minor"], float(c["selfpresent_share"]))
    beds = {h: max(0, p["hospitals"][h]["beds"] - wi[h]) for h in H}
    n = {"immediate": p["immediate"], "delayed": p["delayed"]}
    R = [a["id"] for a in scn["ambulances"]]
    total = n["immediate"] + n["delayed"]
    J = max(1, math.ceil(total / len(R)) + 1)        # enough trips for every patient
    return dict(H=H, R=R, J=list(range(1, J + 1)), trauma=trauma, d=d, start=start,
                beds=beds, beds_reported={h: p["hospitals"][h]["beds"] for h in H}, walk_ins=wi,
                n=n, TD=p["scene_delay_min"], load=c["load_min"], handover=c["handover_min"],
                curve=p["survival_curve"], objective=c["objective"],
                triage_order=_truthy(c["triage_order"]), time_limit=float(c["time_limit_s"]))


# Priority for patients left out of the t0 plan (no bed / no trip): a red left out
# costs more than a yellow, and both cost more than any survival probability, so the
# model transports everyone it can and gives scarce trauma beds to red patients first.
UNSERVED_PENALTY = {"immediate": 2.0, "delayed": 1.0}


def _build(D, objective, env):
    H, R, J = D["H"], D["R"], D["J"]
    cycle = {h: D["load"] + D["d"][h] + D["handover"] + D["d"][h] for h in H}
    horizon = D["TD"] + max(D["start"].values()) + len(J) * max(cycle.values())
    M = horizon
    keys = [(r, j, h, k) for r in R for j in J for h in H for k in CLASSES
            if k == "delayed" or D["trauma"][h]]          # compatibility by construction (Repoussis 19)

    m = gp.Model(f"mci_t0_{objective}", env=env)
    m.setParam("TimeLimit", D["time_limit"])
    x = m.addVars(keys, vtype=GRB.BINARY, name="x")                   # trip (r,j) -> hospital h, class k
    a = m.addVars(R, J, lb=0, ub=M, name="a")                          # arrival at site / load start
    b = m.addVars(R, J, lb=0, ub=M, name="b")                          # arrival at hospital
    u = m.addVars(CLASSES, lb=0, vtype=GRB.INTEGER, name="unserved")   # patients left for a later plan

    def used(r, j):
        return quicksum(x[r, j, h, k] for h in H for k in CLASSES if (r, j, h, k) in x)

    def carries(r, j, k):
        return quicksum(x[r, j, h, k] for h in H if (r, j, h, k) in x)

    for k in CLASSES:                                                  # every patient on one trip (5)
        m.addConstr(quicksum(x[key] for key in keys if key[3] == k) + u[k] == D["n"][k], f"serve_{k}")
    for r in R:
        for j in J:
            m.addConstr(used(r, j) <= 1, f"one_patient_{r}_{j}")         # (6)-(8)
            m.addConstr(b[r, j] == a[r, j] + D["load"]
                        + quicksum(D["d"][h] * x[r, j, h, k] for h in H for k in CLASSES if (r, j, h, k) in x),
                        f"hosp_arrival_{r}_{j}")
        m.addConstr(a[r, 1] >= max(D["start"][r], D["TD"]), f"first_arrival_{r}")      # (10)
        for j in J[:-1]:
            m.addConstr(used(r, j + 1) <= used(r, j), f"trip_order_{r}_{j}")            # (9)
            m.addConstr(a[r, j + 1] >= a[r, j]
                        + quicksum(cycle[h] * x[r, j, h, k] for h in H for k in CLASSES if (r, j, h, k) in x),
                        f"next_arrival_{r}_{j}")                                          # (11)-(13)
    for h in H:                                                        # beds after walk-ins
        m.addConstr(quicksum(x[key] for key in keys if key[2] == h) <= D["beds"][h], f"beds_{h}")

    if D["triage_order"]:                                              # (25): red before yellow at the site
        last_red = m.addVar(lb=0, ub=M, name="last_red_pickup")
        for r in R:
            for j in J:
                m.addConstr(last_red >= a[r, j] - M * (1 - carries(r, j, "immediate")), f"red_{r}_{j}")
                m.addConstr(a[r, j] >= last_red - M * (1 - carries(r, j, "delayed")), f"yellow_{r}_{j}")

    eps = 1e-4
    if objective == "survival":
        xs = [horizon * i / 30 for i in range(31)]
        s = m.addVars(R, J, lb=0, ub=1, name="s")
        for r in R:
            for j in J:
                for k in CLASSES:
                    f = m.addVar(lb=0, ub=1, name=f"sf_{k}_{r}_{j}")
                    m.addGenConstrPWL(b[r, j], f, xs, [survival(t, D["curve"], k) for t in xs])
                    m.addConstr(s[r, j] <= f + (1 - carries(r, j, k)), f"surv_{k}_{r}_{j}")
                m.addConstr(s[r, j] <= used(r, j), f"surv_used_{r}_{j}")
        m.setObjective(s.sum() - quicksum(UNSERVED_PENALTY[k] * u[k] for k in CLASSES) - eps * b.sum(),
                       GRB.MAXIMIZE)
    else:
        T = m.addVars(R, J, lb=0, ub=M, name="T")
        for r in R:
            for j in J:
                m.addConstr(T[r, j] >= b[r, j] - M * (1 - used(r, j)), f"flow_{r}_{j}")
        m.setObjective(T.sum() + 10 * M * quicksum(UNSERVED_PENALTY[k] * u[k] for k in CLASSES)
                       + eps * a.sum(), GRB.MINIMIZE)
    return m, x, a, b, u, horizon, cycle


def solve(data):
    """Solve the t0 model. For the survival objective (piecewise-linear, slow to prove optimal),
    first solve the linear flow-time model and use its plan as a warm start (MIP start)."""
    D = data
    H, R, J = D["H"], D["R"], D["J"]
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.start()
    t0 = time.time()
    warm = None
    if D["objective"] == "survival":
        m0, x0, *_ = _build(D, "flow_time", env)
        m0.optimize()
        if m0.SolCount:
            warm = {key: var.X for key, var in x0.items()}
            warm_info = {"status": _STATUS.get(m0.Status, str(m0.Status)), "seconds": round(m0.Runtime, 2)}
    m, x, a, b, u, horizon, cycle = _build(D, D["objective"], env)
    if warm:
        for key, v in warm.items():
            x[key].Start = v
    m.optimize()
    out = {"status": m.Status, "status_name": _STATUS.get(m.Status, str(m.Status)),
           "seconds": round(time.time() - t0, 2),
           "size": {"vars": m.NumVars, "bin_int_vars": m.NumIntVars,
                    "linear_constrs": m.NumConstrs, "pwl_constrs": m.NumGenConstrs},
           "horizon_min": round(horizon, 1)}
    if warm:
        out["warm_start"] = warm_info
    if m.SolCount == 0:
        return out
    out["mip_gap"] = round(m.MIPGap, 4) if m.IsMIP else 0.0

    trips = []
    for r in R:
        for j in J:
            for h in H:
                for k in CLASSES:
                    if (r, j, h, k) in x and x[r, j, h, k].X > 0.5:
                        trips.append({"ambulance": r, "trip": j, "hospital": h, "class": k,
                                      "pickup": round(a[r, j].X, 1), "hospital_arrival": round(b[r, j].X, 1),
                                      "back_at_site": round(a[r, j].X + cycle[h], 1),
                                      "survival": round(survival(b[r, j].X, D["curve"], k), 3)})
    # Attach patient ids: within a class, earlier pickup gets the lower id.
    for k, tag in (("immediate", "R"), ("delayed", "Y")):
        for i, t in enumerate(sorted((t for t in trips if t["class"] == k), key=lambda t: t["pickup"])):
            t["patient"] = f"{tag}{i + 1}"
    trips.sort(key=lambda t: (t["ambulance"], t["trip"]))

    exp = sum(t["survival"] for t in trips)
    out.update({
        "objective": D["objective"], "trips": trips,
        "unserved": {k: int(round(u[k].X)) for k in CLASSES},
        "expected_survivors": round(exp, 2),
        "transported": len(trips),
        "mean_arrival": {k: _mean([t["hospital_arrival"] for t in trips if t["class"] == k]) for k in CLASSES},
        "makespan": max((t["hospital_arrival"] for t in trips), default=0.0),
        "beds": {h: {"reported": D["beds_reported"][h], "walk_ins": D["walk_ins"][h],
                     "usable": D["beds"][h], "used": sum(t["hospital"] == h for t in trips)} for h in H},
        "travel_min": {h: round(D["d"][h], 1) for h in H},
        "ambulance_to_site_min": {r: round(D["start"][r], 1) for r in R},
    })
    return out


def _truthy(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "y")


def _mean(v):
    return round(sum(v) / len(v), 1) if v else None


_STATUS = {GRB.OPTIMAL: "optimal", GRB.TIME_LIMIT: "time limit (best found)",
           GRB.INFEASIBLE: "infeasible", GRB.INF_OR_UNBD: "infeasible or unbounded",
           GRB.SUBOPTIMAL: "suboptimal"}
