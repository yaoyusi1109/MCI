"""Static MCI ambulance scheduling: minimize waiting at the scene.
Run: python models/mci_ambulance.py
All example inputs are SYNTHETIC. Times are minutes after triage at time 0.
This adapts reference #12; it does not reproduce its hospital-treatment model.
"""

# 1. Import packages
import gurobipy as gp
from gurobipy import Model, GRB, quicksum

# 2. Sets and input data (replace these synthetic inputs for your experiment)
P = range(4)                    # Patients
K = range(2)                    # Ambulances
H = range(2)                    # Hospitals
S = range(2)                    # MCI sites
Q = range(len(P))               # Potential trips per ambulance; not road edges

patient_site = [0, 0, 1, 1]
priority = [2, 1, 2, 1]         # Larger number = higher triage priority
ready = [0, 0, 0, 0]            # All patients triaged and ready at time 0
available = [0, 0]              # Ambulance availability times
loading = [2, 2, 3, 2]          # Loading time for each patient
handover = [3, 4]               # Unloading/handover time at each hospital

# Ambulance starting location -> site (ambulance x site)
start_time = [[4, 9],
              [8, 3]]
# Site -> hospital travel times (site x hospital)
travel_time = [[8, 14],
               [13, 6]]
# Hospital -> next site travel times (hospital x site); may be asymmetric
return_time = [[8, 13],
               [14, 6]]

# Total available admissions over this experiment, NOT reusable treatment beds
hospital_capacity = [2, 2]
# Patient x hospital: 1 if the hospital can accept/treat this patient
compatible = [[1, 0],
              [1, 1],
              [1, 1],
              [1, 1]]

# Use equal weights to minimize total (and therefore average) scene waiting.
# Different positive weights would define a DIFFERENT, weighted objective.
weight = [1, 1, 1, 1]

# Conservative horizon: enough to serve everyone sequentially if assignments
# are feasible. Includes depot travel, loading, transport, handover and return.
horizon = max(ready) + max(available) + len(P) * (
    max(max(row) for row in start_time)
    + max(loading) + max(max(row) for row in travel_time)
    + max(handover) + max(max(row) for row in return_time)
)
# M must also deactivate an unused return leg, whose RHS can reach horizon+return.
M = horizon + max(max(row) for row in return_time)

# 3. Initialize the model (same structure as the course example)
env = gp.Env(empty=True)
env.setParam("OutputFlag", 0)
env.start()
m = Model("mci_ambulance_waiting", env=env)
m.setParam("MIPGap", 0)

# 4. Decision variables
# x[p,k,q,h] = 1 if ambulance k takes patient p to hospital h on trip q.
x = m.addVars(P, K, Q, H, vtype=GRB.BINARY, name="x")
# u[k,q] = 1 if trip q of ambulance k is used.
u = m.addVars(K, Q, vtype=GRB.BINARY, name="u")
# a[k,q]: pickup/loading START at the site; b[k,q]: hospital arrival;
# f[k,q]: ambulance becomes free after unloading/handover.
a = m.addVars(K, Q, lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="a")
b = m.addVars(K, Q, lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="b")
f = m.addVars(K, Q, lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="f")
# t[p]: pickup/loading start for patient p; w[p]: scene waiting time.
t = m.addVars(P, lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="t")
w = m.addVars(P, lb=0, ub=horizon, vtype=GRB.CONTINUOUS, name="w")

# 5. Objective function: minimize total patient waiting time at the scene
m.setObjective(quicksum(weight[p] * w[p] for p in P), GRB.MINIMIZE)

# 6. Constraints
# C1. Every patient is transported exactly once.
for p in P:
    m.addConstr(quicksum(x[p, k, q, h] for k in K for q in Q for h in H) == 1,
                f"Serve_patient_{p}")

# C2. One patient and one destination hospital per used trip.
for k in K:
    for q in Q:
        m.addConstr(quicksum(x[p, k, q, h] for p in P for h in H) == u[k, q],
                    f"Trip_assignment_{k}_{q}")

# C3. Use trips in order: no empty trip before a later used trip.
for k in K:
    for q in range(len(Q) - 1):
        m.addConstr(u[k, q + 1] <= u[k, q], f"Trip_order_{k}_{q}")

# C4. Only compatible patient-hospital assignments are allowed.
for p in P:
    for k in K:
        for q in Q:
            for h in H:
                m.addConstr(x[p, k, q, h] <= compatible[p][h],
                            f"Compatible_{p}_{k}_{q}_{h}")

# C5. Hospital total admission capacity over the experiment.
for h in H:
    m.addConstr(quicksum(x[p, k, q, h] for p in P for k in K for q in Q)
                <= hospital_capacity[h], f"Hospital_capacity_{h}")

# C6. Unused trips have zero timestamps.
for k in K:
    for q in Q:
        m.addConstr(a[k, q] <= horizon * u[k, q], f"Active_pickup_{k}_{q}")
        m.addConstr(b[k, q] <= horizon * u[k, q], f"Active_arrival_{k}_{q}")
        m.addConstr(f[k, q] <= horizon * u[k, q], f"Active_release_{k}_{q}")

# C7. Initial trip: ambulance availability + travel from its starting location.
for k in K:
    m.addConstr(a[k, 0] >= available[k] * u[k, 0]
                + quicksum(start_time[k][patient_site[p]] * x[p, k, 0, h]
                           for p in P for h in H), f"First_trip_{k}")

# C8. Patient pickup -> hospital arrival -> ambulance release.
for k in K:
    for q in Q:
        m.addConstr(b[k, q] == a[k, q]
                    + quicksum((loading[p] + travel_time[patient_site[p]][h])
                               * x[p, k, q, h] for p in P for h in H),
                    f"Hospital_arrival_{k}_{q}")
        m.addConstr(f[k, q] == b[k, q]
                    + quicksum(handover[h] * x[p, k, q, h] for p in P for h in H),
                    f"Ambulance_release_{k}_{q}")

# C9. A later pickup must allow handover AND travel from the previous hospital.
# Both binary expressions must equal 1 to activate this timing constraint.
for k in K:
    for q in range(len(Q) - 1):
        for h in H:
            for p in P:
                previous_hospital = quicksum(x[i, k, q, h] for i in P)
                next_patient = quicksum(x[p, k, q + 1, j] for j in H)
                m.addConstr(a[k, q + 1] >= f[k, q]
                            + return_time[h][patient_site[p]]
                            - M * (2 - previous_hospital - next_patient),
                            f"Next_pickup_{k}_{q}_{h}_{p}")

# C10. Link each patient's pickup time to the assigned ambulance trip.
for p in P:
    for k in K:
        for q in Q:
            assigned = quicksum(x[p, k, q, h] for h in H)
            m.addConstr(t[p] >= a[k, q] - M * (1 - assigned),
                        f"Pickup_link_lower_{p}_{k}_{q}")
            m.addConstr(t[p] <= a[k, q] + M * (1 - assigned),
                        f"Pickup_link_upper_{p}_{k}_{q}")

# C11. No pickup before patient readiness; waiting stops when loading starts.
for p in P:
    m.addConstr(t[p] >= ready[p], f"Patient_ready_{p}")
    m.addConstr(w[p] == t[p] - ready[p], f"Waiting_time_{p}")

# C12. Triage priority within the SAME site, as in reference #12.
# All ready[p] = 0 in this static example. Simultaneous pickup is allowed.
# With staggered/dynamic arrivals, redesign this rule before using the model.
for p in P:
    for i in P:
        if patient_site[p] == patient_site[i] and priority[p] > priority[i]:
            m.addConstr(t[p] <= t[i], f"Triage_priority_{p}_{i}")

# 7. Optimize the model
m.optimize()

# 8. Print results
if m.status == GRB.OPTIMAL:
    print(f"Optimal total waiting time: {m.objVal:.2f} patient-minutes")
    print(f"Average waiting time: {sum(w[p].X for p in P) / len(P):.2f} minutes")
    print("\nPatient waiting times:")
    for p in P:
        print(f"  Patient {p+1}: site {patient_site[p]+1}, priority {priority[p]}, "
              f"pickup {t[p].X:.2f}, waiting {w[p].X:.2f} minutes")
    print("\nAmbulance schedules (labels are 1-based):")
    for k in K:
        for q in Q:
            for p in P:
                for h in H:
                    if x[p, k, q, h].X > 0.5:
                        print(f"  Ambulance {k+1}, trip {q+1}: patient {p+1} "
                              f"at site {patient_site[p]+1} -> hospital {h+1}; "
                              f"pickup {a[k,q].X:.2f}, hospital arrival {b[k,q].X:.2f}, "
                              f"free {f[k,q].X:.2f}")
else:
    print(f"Gurobi status: {m.status}")
