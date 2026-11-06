# MCI ambulance scheduling model

`mci_ambulance.ipynb` contains the mathematical formulation and course-style Gurobi code. `mci_ambulance.py` is the same code as a directly runnable script.

This is an adaptation of reference #12 to **minimize patient waiting at the scene**, not a reproduction of the paper's makespan / treatment-completion objective. It uses synthetic inputs: 4 patients, 2 sites, 2 ambulances, and 2 hospitals. It is not fitted to the Baltimore dashboard.

```bash
python -m pip install -r models/requirements.txt
python models/mci_ambulance.py
```

A Gurobi license that supports the instance size is required. This small example was solved with gurobipy 13.0.3. Larger instances may require an academic/full license.

## Assumptions and scope

- All patients are known and ready after triage at time 0. Priority is fixed and higher numbers mean higher priority.
- A vehicle carries one patient per trip and may perform several trips. Its initial location and availability are known.
- Waiting ends when the ambulance arrives and loading starts. It excludes loading, travel to hospital, and hospital waiting.
- Within the same site, a higher-priority patient's pickup must be no later than a lower-priority patient's pickup. Equal-priority order remains a decision; simultaneous pickups are allowed. There is no global precedence across sites.
- Travel times are fixed and may be asymmetric. Return travel is from the previous destination hospital to the next patient's site, not necessarily the original site.
- A vehicle is released after unloading/handover; it need not remain until medical treatment finishes.
- Hospital capacity means total available admissions during the experiment, not beds that become reusable after treatment. Hospital treatment queues and sequencing are not modeled.
- Hospital compatibility is enforced patient by patient.
- Road-edge routing is not modeled. For a fixed road-closure scenario, externally calculate directed shortest-path travel times and supply `start_time`, `travel_time`, and `return_time`. An unreachable pair must be forbidden, rather than assigned travel time zero. Changing closures during a trip requires a dynamic extension.
- The objective is only scene waiting. The destination of an ambulance's final trip may therefore have several equally optimal choices; do not interpret a particular final destination as a unique recommendation.
- This model can intentionally idle a vehicle to respect priority or optimize other patients' pickup times. It does not impose a non-idling dispatch rule.

## Validation

Gurobi solved the baseline to optimality: total waiting **54 patient-minutes**, average **13.5 minutes**. Pickup times for patients 1–4 are **4, 25, 3, 22 minutes**. Every patient is transported exactly once, compatibility and hospital capacities hold, and return/handover times are respected.

An independent enumeration of patient sequences, vehicle splits, and hospital assignments matched the Gurobi optimum. It propagated both vehicle and triage precedence constraints, allowing necessary idling. Increasing site 1 → hospital 1 travel time from 8 to 20 minutes gave an independently verified optimum of **66 patient-minutes**. Reducing total hospital admissions below the number of patients correctly gave infeasibility.

## Sources

- Reference #12: [Repoussis et al. published article](../reference/12_Repoussis_Emergency_Preparedness_Published.pdf), especially Sections 3.1–3.2 and constraint (25). Our hospital admission cap and waiting-only objective are explicit simplifications.
- Coding structure follows the user's supplied supply-chain example: imports, data, environment/model, `addVars`, `setObjective`, looped `addConstr`, `optimize`, and output.
- [Gurobi Python Model reference](https://docs.gurobi.com/projects/optimizer/en/current/reference/python/model.html).
