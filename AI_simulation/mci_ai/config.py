"""Fixed default inputs for the t0 AI + Gurobi demo. Edit freely; every value can also be
overridden from run_demo.py flags or the dashboard form.

Everything here is a placeholder for exploration, not validated data.
Hospital coordinates are approximate and trauma flags are simplified; ambulance positions
are made up. Replace them with HIFLD / OSM data (references #9, #15) before drawing conclusions.
"""

from pathlib import Path

AI_SIM_DIR = Path(__file__).resolve().parent.parent
# Shared with the road-closure dashboard at the repo root (one copy in git).
ROAD_DATA_PATH = AI_SIM_DIR.parent / "dashboard" / "road_closures_data.js"
CACHE_DIR = AI_SIM_DIR / ".cache"

# ---------------- default scenario (steps 1-3) ----------------

# Step 2: one MCI site (Fells Point waterfront, flood-prone).
DEFAULT_SITE = {"lat": 39.2820, "lon": -76.5930}

# Step 1: ambulances available at t0 and where they are.
DEFAULT_AMBULANCES = [
    {"id": "A1", "lat": 39.3100, "lon": -76.6400},
    {"id": "A2", "lat": 39.2700, "lon": -76.6600},
    {"id": "A3", "lat": 39.3300, "lon": -76.5800},
    {"id": "A4", "lat": 39.2550, "lon": -76.5600},
]

# Target hospitals patients can be transferred to. trauma=True may receive immediate (red)
# patients, following Repoussis et al. (2016): immediate patients only go to trauma level I/II.
HOSPITALS = [
    {"id": "H1", "name": "Johns Hopkins Hospital",             "lat": 39.2963, "lon": -76.5925, "trauma": True},
    {"id": "H2", "name": "UMMC / R Adams Cowley Shock Trauma", "lat": 39.2885, "lon": -76.6245, "trauma": True},
    {"id": "H3", "name": "Johns Hopkins Bayview",              "lat": 39.2900, "lon": -76.5490, "trauma": True},
    {"id": "H4", "name": "Sinai Hospital",                     "lat": 39.3530, "lon": -76.6610, "trauma": True},
    {"id": "H5", "name": "MedStar Union Memorial",             "lat": 39.3285, "lon": -76.6125, "trauma": False},
    {"id": "H6", "name": "Mercy Medical Center",               "lat": 39.2955, "lon": -76.6115, "trauma": False},
    {"id": "H7", "name": "MedStar Harbor Hospital",            "lat": 39.2515, "lon": -76.6140, "trauma": False},
]
DEFAULT_HOSPITAL_IDS = ["H1", "H2", "H3", "H6"]

# Step 3a: the few constants you are expected to change.
DEFAULT_CONSTANTS = {
    "speed_kmh": 40.0,       # average ambulance speed on a clear road
    "flood_level_ft": 4.0,   # water level used to count closed roads (road-closure dashboard data)
    "incident": "Explosion at a crowded waterfront event during a coastal storm; injured people at one site.",
}

# Step 3b: fixed assumptions (collapsed on the dashboard; still editable).
ASSUMPTIONS = {
    "load_min": 3.0,             # ambulance time at the scene to load a patient (Repoussis l_p,1)
    "handover_min": 5.0,         # ambulance time at the hospital to unload / hand over
    "max_patients": 40,          # cap on red + yellow patients (restricted Gurobi license size)
    "circuity": 1.3,             # road distance / straight-line distance
    "corridor_m": 400.0,         # flooded road pieces within this distance of a route count against it
    "selfpresent_share": 0.3,    # share of minor (green) patients who walk in to hospitals (Chang et al.)
    "objective": "flow_time",    # "flow_time" (Repoussis; fast) or "survival" (Chang reward; slow PWL model)
    "triage_order": True,        # all red pickups before any yellow pickup (Repoussis constraint 25)
    "time_limit_s": 5,           # Gurobi time limit per model (survival model is warm-started)
}

# Survival probability SF(t) = b0 / ((t / b1) ** b2 + 1), t in minutes from t0 to hospital arrival.
# Chang et al. (2026) Fig. 12, values from Mills et al. (2013).
SURVIVAL = {
    "pessimistic": {"immediate": (0.09, 17, 1.01), "delayed": (0.57, 61, 2.03)},
    "moderate":    {"immediate": (0.24, 47, 1.30), "delayed": (0.76, 138, 2.17)},
    "optimistic":  {"immediate": (0.56, 91, 1.58), "delayed": (0.81, 160, 2.41)},
}

# Restricted Gurobi license (pip install) allows 2000 variables / 2000 linear constraints;
# 8 ambulances x 40 patients stays well inside it.
MAX_AMBULANCES = 8
