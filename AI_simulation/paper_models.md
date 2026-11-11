# Models from the two reference papers, and what this demo keeps

Neither paper publishes code.
The code in `mci_ai/` is our simplified implementation, not the authors'.

## Paper 1: Repoussis et al. (2016), EJOR 255, 531–544 (reference #12)

"Optimizing emergency preparedness and resource utilization in mass-casualty incidents."
A static MILP, written as a flexible job-shop problem: patients are jobs, ambulances and hospitals are machines.

**Sets.**
Sites `S`, hospitals `H`, patients `P`, ambulances `R`, trip positions `J_r` per ambulance, and bed positions `B_h` per hospital.

**Binary variables.**
- `x_rjp` = 1 if patient `p` rides ambulance `r` on its trip `j` (eq. 1).
- `z_phb` = 1 if patient `p` takes bed position `b` at hospital `h` (eq. 2).
- `y_rjh` = 1 if trip `j` of ambulance `r` ends at hospital `h` (eq. 3).

**Continuous variables.**
Site arrival `a_rj`, transport time `at_rj`, deadhead return `āt_rj`, treatment start `st_hb`, and scene waiting time `w_p`.

**Objective.**
Minimize the makespan `C_max` (eq. 4); the total flow time is a second, hierarchical objective.

**Constraints.**
- (5) Every patient rides exactly one trip.
- (6)–(8) One patient and one hospital per trip.
- (9) Trips are used in order.
- (10) The first arrival is at least the disaster time plus the response delay `T_D`.
- (11)–(13) Next arrival = previous arrival + transport + deadhead (big-M).
- (16)–(18) Every patient gets one bed position; positions are filled in order.
- (19) Compatibility: immediate patients only go to trauma level I/II hospitals.
- (20)–(24) Hospital treatment sequence and makespan.
- (25) Triage: at the same site, a higher-priority patient waits no longer than a lower-priority one.
- (31) Symmetry breaking; (39)–(43) lower bounds.

The authors solve large instances with a two-phase heuristic and iterated tabu search (Section 4).

## Paper 2: Chang et al. (2026), Computers & OR 194, 107559 (reference #10)

"Simulation-based dynamic optimization for ambulance dispatch and casualty distribution in mass casualty incident."
A Markov decision process solved by approximate dynamic programming.

**State** `S_t = (τ_t, P_t, Q_t, H_t)`:
- `τ_t`: time.
- `P_t`: per casualty collection point, the counts of immediate / delayed / minor patients.
- `Q_t`: 7 attributes per ambulance.
- `H_t`: per hospital, patients served by class and beds left.

**Decision epochs.** An epoch starts when an ambulance becomes free.

**Action** `x_tjlk ∈ {0, 1, 2}` (eq. 1): send ambulance `l` to collection point `j` for a delayed (1) or immediate (2) patient, then to hospital `k`.

**Reward.** The survival probability when the patient arrives at hospital (eqs. 2–3):
`SF_k(t) = β0 / ((t / β1)^β2 + 1)`.
Parameters (Fig. 12, from Mills et al. 2013):

| Class | Curve | β0 | β1 | β2 |
| --- | --- | --- | --- | --- |
| Immediate | pessimistic | 0.09 | 17 | 1.01 |
| Immediate | moderate | 0.24 | 47 | 1.30 |
| Immediate | optimistic | 0.56 | 91 | 1.58 |
| Delayed | pessimistic | 0.57 | 61 | 2.03 |
| Delayed | moderate | 0.76 | 138 | 2.17 |
| Delayed | optimistic | 0.81 | 160 | 2.41 |

**Objective.** Maximize the expected discounted total reward (eq. 4), via the Bellman equation (eq. 5).

**Solution method.**
Post-decision states (eqs. 6–9) and 10 basis functions feed a deep neural network that approximates the value function (DNN-API).
A hybrid agent-based and discrete-event simulation generates the transitions.
In it, minor patients choose hospitals themselves and use up beds.

**Baselines.** Immediate-first / FIFO crossed with nearest-hospital / cycle-hospital (IF-NHF, IF-CH, FIFO-NHF, FIFO-CH).

## What `mci_ai/gurobi_model.py` keeps

| Demo element | Source |
| --- | --- |
| Ordered trips, one patient per trip | Repoussis (5)–(9) |
| First arrival ≥ max(ambulance travel, scene delay) | Repoussis (10) |
| Next arrival = load + travel + handover + return | Repoussis (11)–(13), linear because there is one site |
| Red only to trauma hospitals | Repoussis (19) |
| All red pickups before any yellow | Repoussis (25), optional |
| Bed cap per hospital, minus green walk-ins | Repoussis (16)–(17) count only; walk-ins from Chang's minor-patient agents |
| Objective `flow_time` (default) | Repoussis flow time (their 2nd objective) |
| Objective `survival`, and the survival reported for every plan | Chang reward, eqs. (2)–(3), Fig. 12 |
| Low / likely / high input scenarios | Stands in for Chang's stochastic simulation, as three AI-estimated cases |

## Dropped (possible next steps)

- Hospital treatment sequencing and bed turnover (Repoussis (20)–(24)); beds are a single cap here.
- Multiple sites, and patients arriving over time.
- Chang's dynamic re-planning at every epoch, and the learned value function.
- Road-level routing: travel times are straight line × circuity × an AI delay factor.
