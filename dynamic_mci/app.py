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




# Simulation endpoints (localhost-only prototype; synchronous worker-thread calls)
import csv
import io
import threading
from fastapi.responses import StreamingResponse
from simulation.engine import run_simulation
from scenario_manager import scenario_path

_dynamic_run_lock = threading.Lock()


class SimulationRequest(BaseModel):
    scenario_id: str = 'camden_yards'
    ambulance_id: str = 'A001'
    mode: str = 'static'
    horizon: float = Field(default=240.0, gt=0, le=1440)


def _as_float(value, default=None):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _dynamic_dashboard_result(raw, run_id, scenario_id):
    # This adapter never fabricates route coordinates or clinical events.
    summary = raw.get('summary') or {}
    patients = {p.get('id'): p for p in raw.get('patients', [])}
    assignments = []
    for item in raw.get('assignments', []):
        pid = item.get('patient_id') or item.get('id')
        patient = patients.get(pid) or {}
        arrival = _as_float(item.get('hospital_arrival_min'))
        if arrival is None:
            arrival = _as_float(item.get('arrival'))
        if arrival is None:
            arrival = _as_float(patient.get('arrival'))
        assignments.append({
            **item,
            'patient_id': pid,
            'hospital_id': item.get('hospital_id') or patient.get('hospital'),
            'triage': item.get('triage') or patient.get('observed') or patient.get('initial') or 'UNKNOWN',
            'hospital_arrival_min': arrival,
        })
    # Only completed deliveries should count in playback.
    delivered = sum(p.get('disposition') == 'DELIVERED' for p in patients.values())
    makespan = _as_float(summary.get('observed_makespan_min'), 0.0)
    if makespan is None:
        makespan = 0.0
    events = raw.get('events') or []
    # Display all clinical events, even if they occur after last delivery.
    final_event = max((_as_float(e.get('time_min'), 0.0) for e in events), default=0.0)
    playback_end = max(makespan, final_event)
    routes = []
    for route in raw.get('routes') or []:
        coords = route.get('coordinates')
        if isinstance(coords, list) and len(coords) > 1 and all(
            isinstance(c, (list, tuple)) and len(c) >= 2 for c in coords
        ):
            routes.append(route)
    return {
        'mode': 'dynamic', 'scenario_id': scenario_id, 'run_id': run_id,
        'makespan_min': makespan, 'playback_end_min': playback_end,
        'transport_patients': delivered,
        'delivered': summary.get('delivered', delivered),
        'expectant': summary.get('expectant'),
        'validation': summary.get('validation'),
        'assignments': assignments, 'events': events, 'routes': routes,
        'optimizer_decisions': raw.get('optimizer_decisions') or [],
        'patients': raw.get('patients') or [],
        'hospital_capacity_final': raw.get('hospital_capacity_final'),
    }


@app.post('/api/simulation/run')
def run_simulation_api(req: SimulationRequest):
    try:
        scenario = scenario_path(req.scenario_id)
        if req.mode == 'static':
            result = run_simulation(scenario, req.ambulance_id)
            result['mode'] = 'static'
            result['scenario_id'] = req.scenario_id
            return result
        if req.mode != 'dynamic':
            raise ValueError('Mode must be static or dynamic')

        # Only one dynamic run at a time; the two-stage pipeline must remain paired.
        if not _dynamic_run_lock.acquire(blocking=False):
            raise HTTPException(409, 'Another dynamic simulation is running')
        try:
            from simulation.triage_engine import run_triage
            from simulation.integrated_scheduler import run as run_integrated
            triage = run_triage(scenario, include_fire=True, save=True)
            if triage.get('validation', {}).get('status') != 'PASS':
                raise ValueError('Rolling triage failed validation')
            triage_id = triage.get('run_id')
            if not triage_id:
                raise RuntimeError('Rolling triage did not return a run ID')
            triage_file = (REPORT_ROOT / req.scenario_id / 'triage_tests'
                           / triage_id / 'triage_results.json')
            if not triage_file.is_file():
                raise FileNotFoundError(f'Triage result not saved: {triage_file}')
            run_folder = REPORT_ROOT / req.scenario_id / 'integrated_tests'
            existing = {p.name for p in run_folder.iterdir() if p.is_dir()} if run_folder.exists() else set()
            raw = run_integrated(scenario, triage_file, horizon=req.horizon, save=True)
            created = [p for p in run_folder.iterdir() if p.is_dir() and p.name not in existing
                       and (p / 'integrated_results.json').is_file()]
            if len(created) != 1:
                raise RuntimeError('Could not uniquely identify the saved integrated run')
            return _dynamic_dashboard_result(raw, created[0].name, req.scenario_id)
        finally:
            _dynamic_run_lock.release()
    except HTTPException:
        raise
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f'Simulation failed: {exc}') from exc


# Maryland MCI report downloads
REPORT_ROOT = Path(__file__).resolve().parent / 'outputs'
REPORT_FILES = {
    'excel': ('MCI_Full_Results.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    'patients': ('patient_results.csv', 'text/csv'),
}


@app.get('/api/reports/{scenario_id}/{run_id}/{report_type}')
def download_report(scenario_id: str, run_id: str, report_type: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', scenario_id):
        raise HTTPException(400, 'Invalid scenario ID')
    if not re.fullmatch(r'(?:run|integrated)_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}', run_id):
        raise HTTPException(400, 'Invalid run ID')
    if report_type not in REPORT_FILES:
        raise HTTPException(400, 'Unknown report type')
    dynamic = run_id.startswith('integrated_')
    folder = REPORT_ROOT / scenario_id / 'integrated_tests' / run_id if dynamic else REPORT_ROOT / scenario_id / run_id
    if dynamic:
        if report_type == 'excel':
            matches = sorted(folder.glob('*.xlsx')) if folder.is_dir() else []
            if len(matches) != 1:
                raise HTTPException(404, 'Expected one integrated Excel report; found ' + str(len(matches)))
            path = matches[0]
            return FileResponse(path, media_type=REPORT_FILES['excel'][1],
                                filename=f'{scenario_id}_{run_id}_{path.name}')
        json_path = folder / 'integrated_results.json'
        if not json_path.is_file():
            raise HTTPException(404, 'Integrated results not found')
        import json
        data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        patients = data.get('patients') or []
        stream = io.StringIO()
        fields = ['id', 'initial', 'true', 'observed', 'status', 'disposition',
                  'arrival', 'hospital', 'care_min', 'reassessments', 'transition_count']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(patients)
        return StreamingResponse(iter([stream.getvalue()]), media_type='text/csv',
                                 headers={'Content-Disposition': f'attachment; filename="{scenario_id}_{run_id}_patients.csv"'})
    filename, media_type = REPORT_FILES[report_type]
    path = folder / filename
    if not path.is_file():
        raise HTTPException(404, 'Report not found')
    return FileResponse(path, media_type=media_type,
                        filename=f'{scenario_id}_{run_id}_{filename}')

app.mount('/',StaticFiles(directory=Path(__file__).resolve().parent/'frontend',html=True),name='frontend')
