
from pathlib import Path
import pandas as pd
import json

def load_scenario(scenario_dir):
    path = Path(scenario_dir)

    patients = pd.read_csv(path / "patients.csv")
    ambulances = pd.read_csv(path / "ambulances.csv")
    hospitals = pd.read_csv(path / "hospitals.csv")
    stations = pd.read_csv(path / "stations.csv")
    compatibility = pd.read_csv(
        path / "hospital_compatibility.csv"
    )

    with open(path / "config.json", encoding="utf-8") as f:
        config = json.load(f)

    return {
        "patients": patients,
        "ambulances": ambulances,
        "hospitals": hospitals,
        "stations": stations,
        "compatibility": compatibility,
        "config": config
    }
