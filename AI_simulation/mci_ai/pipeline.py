"""The 5-step t0 pipeline, as plain functions (used by run_demo.py and the dashboard backend).

1. ambulances (count + locations)      -> scenario.make_scenario
2. one MCI site                         -> scenario.make_scenario
3. constants + target hospitals         -> scenario.make_scenario
   deterministic features (distances, flooded roads near routes) -> scenario.route_features
4. AI fills unknowns as low / likely / high scenarios -> ai_estimator.estimate
5. Gurobi plan for each AI scenario     -> gurobi_model.solve
"""

from . import ai_estimator, gurobi_model, scenario


def step_features(scn):
    return scenario.route_features(scn)


def step_ai(scn, feats, backend="mock", model="sonnet", use_cache=True):
    return ai_estimator.estimate(scn, feats, backend=backend, model=model, use_cache=use_cache)


def step_solve(scn, params):
    """Solve one Gurobi model per AI scenario (low / likely / high)."""
    results = {}
    for name, p in params.items():
        data = gurobi_model.build_data(scn, p)
        res = gurobi_model.solve(data)
        res["inputs"] = {"immediate": p["immediate"], "delayed": p["delayed"], "minor": p["minor"],
                         "scene_delay_min": p["scene_delay_min"], "survival_curve": p["survival_curve"],
                         "trips_per_ambulance": len(data["J"])}
        results[name] = res
    return results


def run(backend="mock", model="sonnet", use_cache=True, **scenario_kwargs):
    scn = scenario.make_scenario(**scenario_kwargs)
    feats = step_features(scn)
    ai = step_ai(scn, feats, backend=backend, model=model, use_cache=use_cache)
    plans = step_solve(scn, ai["params"])
    return {"scenario": scn, "features": feats, "ai": ai, "plans": plans}
