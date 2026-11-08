from pathlib import Path
import os
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from routing_providers import osmnx_provider, valhalla_provider

import re
from fastapi.responses import FileResponse

from fastapi import Body
from scenario_manager import (
    list_scenarios,
    load_scenario,
    save_scenario
)

app=FastAPI(title='Maryland MCI - Consolidated Routing')

class RouteRequest(BaseModel):
    provider: str = 'valhalla'
    origin_lat: float = Field(ge=-90,le=90)
    origin_lon: float = Field(ge=-180,le=180)
    destination_lat: float = Field(ge=-90,le=90)
    destination_lon: float = Field(ge=-180,le=180)

@app.post('/route')
def route(req:RouteRequest):
    providers={'valhalla':valhalla_provider.route,'osmnx':osmnx_provider.route}
    if req.provider not in providers:
        raise HTTPException(400,'Provider must be valhalla or osmnx')
    try:
        return providers[req.provider]((req.origin_lat,req.origin_lon),(req.destination_lat,req.destination_lon))
    except Exception as exc:
        # Return clear failure, never silently switch providers.
        raise HTTPException(503,f'{req.provider} routing unavailable: {exc}') from exc

@app.get('/geocode')
def geocode(q:str=Query(min_length=4,max_length=200)):
    # Nominatim is a public, rate-limited service. Search only on explicit user action.
    try:
        r=requests.get('https://nominatim.openstreetmap.org/search',params={'q':q,'format':'json','limit':1,'countrycodes':'us'},
            headers={'User-Agent':os.getenv('GEOCODER_USER_AGENT','MarylandMCIResearchPrototype/0.1 (local academic testing)')},timeout=15)
        r.raise_for_status(); items=r.json()
    except requests.RequestException as exc:
        raise HTTPException(502,f'Address lookup unavailable: {exc}') from exc
    if not items: raise HTTPException(404,'Address not found')
    return {'lat':float(items[0]['lat']),'lon':float(items[0]['lon']),'display_name':items[0]['display_name']}

@app.get('/health')
def health():
    return {'status':'ok','osmnx_graph_present':osmnx_provider.GRAPH_PATH.exists(),
            'valhalla_url':valhalla_provider.VALHALLA_URL}




# ---------------------------------------------
# Maryland MCI Scenario Management API
# ---------------------------------------------

@app.get("/api/scenarios")
def get_scenarios():
    return {
        "scenarios": list_scenarios()
    }


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str):
    try:
        return load_scenario(scenario_id)

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )


@app.put("/api/scenarios/{scenario_id}")
def update_scenario(
    scenario_id: str,
    data: dict = Body(...)
):
    try:
        return save_scenario(
            scenario_id,
            data
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )




# Simulation endpoints (localhost-only prototype; run synchronously in worker thread)
from simulation.engine import run_simulation
from scenario_manager import scenario_path

class SimulationRequest(BaseModel):
    scenario_id: str = 'camden_yards'
    ambulance_id: str = 'A001'

@app.post('/api/simulation/run')
def run_simulation_api(req: SimulationRequest):
    try:
        return run_simulation(scenario_path(req.scenario_id), req.ambulance_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f'Simulation failed: {exc}') from exc

# ---------------------------------------------
# Maryland MCI report downloads
# ---------------------------------------------

REPORT_ROOT = (
    Path(__file__).resolve().parent / "outputs"
)

REPORT_FILES = {
    "excel": (
        "MCI_Full_Results.xlsx",
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    "patients": (
        "patient_results.csv",
        "text/csv"
    )
}


@app.get(
    "/api/reports/{scenario_id}/{run_id}/{report_type}"
)
def download_report(
    scenario_id: str,
    run_id: str,
    report_type: str
):
    # Validate scenario folder name.
    if not re.fullmatch(
        r"[A-Za-z0-9_-]+",
        scenario_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid scenario ID"
        )

    # Validate unique run ID.
    if not re.fullmatch(
        r"run_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}",
        run_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid run ID"
        )

    if report_type not in REPORT_FILES:
        raise HTTPException(
            status_code=400,
            detail="Unknown report type"
        )

    filename, media_type = REPORT_FILES[report_type]

    path = (
        REPORT_ROOT
        / scenario_id
        / run_id
        / filename
    )

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Report not found"
        )

    return FileResponse(
        path,
        media_type=media_type,
        filename=f"{scenario_id}_{run_id}_{filename}"
    )

app.mount('/',StaticFiles(directory=Path(__file__).resolve().parent/'frontend',html=True),name='frontend')
