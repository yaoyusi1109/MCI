# AI_simulation: AI-estimated inputs → Gurobi dispatch plan (t0 demo)

This folder shows one way to put an LLM into a Gurobi pipeline for a mass casualty incident (MCI).
It covers one site, a fixed set of ambulances, and one plan made at t0.
The AI never makes the dispatch decision.
It only estimates the inputs we do not have, and every value is checked before Gurobi sees it.

All inputs are synthetic placeholders.
Hospital coordinates are approximate, and the ambulance positions are made up.

## Pipeline

1. **Ambulances**: count and positions (fixed defaults in `mci_ai/config.py`, all editable).
2. **MCI site**: one location.
3. **Constants**: target hospitals, speed, and the flood level from the hurricane scenario.
   The code computes clear-road travel times and counts flooded road pieces near each site → hospital route.
   The road and flood data are read from `../dashboard/road_closures_data.js` (shared with the road-closure dashboard).
4. **AI estimate**: the LLM returns low / likely / high scenarios.
   Each has red / yellow / green counts, bed capacity, route delay factors, scene delay and the survival curve.
   `ai_estimator.validate()` checks the schema and clamps the values.
5. **Gurobi**: one model per scenario, giving trips, hospitals, arrival times and expected survival.

The model is a one-site simplification of Repoussis et al. (2016), with the survival reward of Chang et al. (2026).
See [paper_models.md](paper_models.md) for both papers' formulations and what was kept or dropped.

## Files

| Path | Role |
| --- | --- |
| `mci_ai/config.py` | Fixed defaults: site, ambulances, hospitals, constants, fixed assumptions, survival curves |
| `mci_ai/scenario.py` | Steps 1–3: scenario, travel times, flooded-road counts |
| `mci_ai/ai_estimator.py` | Step 4: prompt, `claude -p` call or mock, validation, cache |
| `mci_ai/gurobi_model.py` | Step 5: the t0 MILP |
| `mci_ai/pipeline.py` | The steps as plain functions (use these from a notebook) |
| `run_demo.py` | Command-line demo for exploration |
| `dashboard/demo.html`, `dashboard/server.py` | Standalone demo page plus a thin local backend that only calls `mci_ai` |
| `../dynamic_mci/app.py`, `../dynamic_mci/frontend/ai_sim.js` | The same pipeline as the **AI simulation** tab of the main dashboard (`/api/ai-sim/*`) |
| `paper_models.md` | Models extracted from references #10 and #12 |

## Run

Needs a Python with `gurobipy` (the restricted pip license is enough for up to 8 ambulances × 40 patients).

```bash
cd AI_simulation
python run_demo.py                                  # defaults, mock AI (no LLM)
python run_demo.py --backend claude                 # real AI call through your own Claude Code CLI
python run_demo.py --set flood_level_ft=6 --hospitals H1,H2,H6 --site 39.285,-76.61
python run_demo.py --json out.json                  # everything as JSON, for a notebook

python dashboard/server.py                          # then open http://127.0.0.1:8765
```

The main dashboard (`../dynamic_mci`, `uvicorn app:app`) has the same pipeline as its **AI simulation** tab.
That tab is separate from the scenario simulation: it has its own site, ambulances and hospitals, and never reads or writes the scenario CSVs.
Flood inputs are left out there for now (water level 0, so no road counts as flooded).

Dashboard tabs:
- **Hurricane**: flood scenario and time step, which set the water level.
- **Casualty condition**: site, incident text, the **Run AI estimate** button, and the editable estimates table.
- **Simulation constants**: ambulances, target hospitals, speed, and a collapsed section of fixed assumptions.
- **Run & results**: a live step log and the plan on the map.

You can type values into the estimates table and solve without calling the AI.

## AI access and sharing

- No credential is stored in this folder.
  The `claude` backend runs the official Claude Code CLI (`claude -p`) as whoever starts the script or server.
  It uses that person's own login, or their own `ANTHROPIC_API_KEY` environment variable.
- Each teammate runs their own copy.
  `server.py` listens on 127.0.0.1 only; do not host it for others, or they would be using the runner's account.
- Calls are light: one per button click, with no tools and no saved session.
  Replies are cached in `.cache/` (git-ignored), and "fresh call" / `--no-cache` asks again.
- Without Claude Code, use the mock backend, or type the values in by hand.
- Course note: generative-AI use in this project was approved by the instructor (CaSE 650 syllabus requires approval).

## Known limits

- Beds are one cap per hospital with no turnover, so a pessimistic bed estimate leaves patients unserved in the t0 plan.
- The survival objective uses piecewise-linear curves and is slow to prove optimal, so it is warm-started from the flow-time plan.
  Flow time is the default.
- At high water levels, flooding around a waterfront site dominates every route's flooded-road count.
