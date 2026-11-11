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

from simulation import road_events

app=FastAPI(title='Maryland MCI - Consolidated Routing')

def _flood_closures(flood_at):
    """Closures of the Aug 12, 2014 flood in effect at `flood_at` ('HH:MM'), or [] when not applied."""
    if flood_at is None:
        return []
    try:
        return road_events.closures_at(flood_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

class RouteRequest(BaseModel):
    provider: str = 'valhalla'
    origin_lat: float = Field(ge=-90,le=90)
    origin_lon: float = Field(ge=-180,le=180)
    destination_lat: float = Field(ge=-90,le=90)
    destination_lon: float = Field(ge=-180,le=180)
    # Optional: avoid the real road closures of Aug 12, 2014 reported by this local time ('HH:MM').
    flood_at: str | None = None

@app.post('/route')
def route(req:RouteRequest):
    providers={'valhalla':valhalla_provider.route,'osmnx':osmnx_provider.route}
    if req.provider not in providers:
        raise HTTPException(400,'Provider must be valhalla or osmnx')
    closures=_flood_closures(req.flood_at)
    if closures and req.provider!='valhalla':
        raise HTTPException(400,'Road closures need the Valhalla provider')
    try:
        origin,destination=(req.origin_lat,req.origin_lon),(req.destination_lat,req.destination_lon)
        if closures:
            result=valhalla_provider.route(origin,destination,road_events.exclusions_for(closures))
            return {**result,'closures_avoided':[c['id'] for c in closures]}
        return providers[req.provider](origin,destination)
    except Exception as exc:
        # Return clear failure, never silently switch providers.
        raise HTTPException(503,f'{req.provider} routing unavailable: {exc}') from exc

@app.get('/api/flood-2014/closures')
def flood_closures():
    """Real road closures of the Aug 12, 2014 flood, placed on the roads they name (for the map)."""
    return {'event_date':road_events.EVENT_DATE,'closures':road_events.all_closures()}

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
    # Reporting only: which Mills et al. (2013) curve to score arrivals with. Does not affect dispatch.
    survival_curve: str = 'moderate'
    # Optional: route around the real Aug 12, 2014 road closures reported by this local time ('HH:MM').
    flood_at: str | None = None
    # Optional what-if road blocks placed on the map: [{lat, lon, at_min, label?}].
    road_blocks: list[dict] = Field(default_factory=list, max_length=20)


def _road_blocks(blocks, horizon):
    out = []
    for i, b in enumerate(blocks, 1):
        try:
            lat, lon, at = float(b['lat']), float(b['lon']), float(b.get('at_min', 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f'Road block {i} needs numeric lat, lon and at_min') from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= at <= horizon):
            raise HTTPException(400, f'Road block {i} is outside the map or the time horizon')
        out.append({'id': f'block_{i}', 'road': str(b.get('label') or f'Road block {i}')[:80],
                    'at_min': at, 'exclude_locations': [{'lat': lat, 'lon': lon}], 'user_block': True})
    return out


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
        'staff_timeline': raw.get('staff_timeline') or [],
        'patients': raw.get('patients') or [],
        'hospital_capacity_final': raw.get('hospital_capacity_final'),
    }


@app.post('/api/simulation/run')
def run_simulation_api(req: SimulationRequest):
    # Closures in effect from minute 0, and closures that start during the run.
    initial = [{**c, 'at_min': 0.0} for c in _flood_closures(req.flood_at)]
    later = road_events.closures_after(req.flood_at) if req.flood_at else []
    later = [c for c in later if c['at_min'] <= req.horizon]
    blocks = _road_blocks(req.road_blocks, req.horizon)
    if req.mode == 'static':
        # The static engine plans once; it can only use closures that exist from the start.
        initial += [{**b, 'at_min': 0.0} for b in blocks]
        later = []
    else:
        initial += [b for b in blocks if b['at_min'] <= 0]
        later = sorted(later + [b for b in blocks if b['at_min'] > 0], key=lambda c: c['at_min'])
    # Every Valhalla call made by this run (static engine, triage, integrated scheduler) avoids them.
    with road_events.use_closures(initial):
        result = _run_simulation(req, later)
    result['flood_at'] = req.flood_at
    result['road_closures_applied'] = [{'id': c['id'], 'road': c['road'], 'time_local': c.get('time_local')}
                                       for c in initial]
    # For the map: when each closure starts, and where it is.
    result['road_closure_timeline'] = [
        {'id': c['id'], 'road': road_events.label(c), 'at_min': round(float(c['at_min']), 2),
         'time_local': c.get('time_local'), 'user_block': bool(c.get('user_block')),
         'points': [[loc['lat'], loc['lon']] for loc in c['exclude_locations']]}
        for c in initial + later]
    return result


def _response_units(scenario, triage):
    """Non-transport units (fire engines) for the map: who they are and how they drove in.

    The ambulance has its own routes in the result; everything else the triage stage dispatched is
    returned here with its station-to-scene route, so the dashboard can show it arriving and parked.
    """
    def rows(name):
        with (scenario / name).open(newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))
    units = {u['unit_id']: u for u in rows('response_units.csv')}
    stations = {s['station_id']: s for s in rows('stations.csv')}
    crew, unit_of = {}, {}
    for p in rows('ems_personnel.csv'):
        crew[p['unit_id']] = crew.get(p['unit_id'], 0) + 1
        unit_of[p['personnel_id']] = p['unit_id']
    commander_unit = unit_of.get(triage.get('incident_commander'))
    info = []
    for uid in triage.get('units', []):
        u = units.get(uid, {})
        if u.get('unit_type', '').upper() == 'AMBULANCE':
            continue
        st = stations.get(u.get('station_id'), {})
        info.append({'unit_id': uid, 'unit_type': u.get('unit_type'), 'station': st.get('station_name'),
                     'crew': crew.get(uid, 0), 'arrival_min': triage.get('unit_arrivals_min', {}).get(uid),
                     'incident_command': uid == commander_unit})
    ids = {u['unit_id'] for u in info}
    return {'response_units': info,
            'unit_routes': [r for r in triage.get('routes', []) if r.get('unit_id') in ids and r.get('coordinates')],
            'incident_commander': triage.get('incident_commander')}


def _run_simulation(req: SimulationRequest, timed_closures=()):
    try:
        scenario = scenario_path(req.scenario_id)
        if req.mode == 'static':
            result = run_simulation(scenario, req.ambulance_id)
            result['mode'] = 'static'
            result['scenario_id'] = req.scenario_id
            return _add_survival(result, req.survival_curve)
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
            raw = run_integrated(scenario, triage_file, horizon=req.horizon, save=True,
                                 timed_closures=list(timed_closures))
            created = [p for p in run_folder.iterdir() if p.is_dir() and p.name not in existing
                       and (p / 'integrated_results.json').is_file()]
            if len(created) != 1:
                raise RuntimeError('Could not uniquely identify the saved integrated run')
            result = _dynamic_dashboard_result(raw, created[0].name, req.scenario_id)
            result.update(_response_units(scenario, triage))
            return _add_survival(result, req.survival_curve)
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

# ---------------------------------------------
# Expected survival (reporting metric only)
# ---------------------------------------------
# SF(t) = b0 / ((t / b1) ** b2 + 1), t = minutes from incident start to hospital arrival.
# Chang et al. (2026) Fig. 12, values from Mills et al. (2013). Same table as AI_simulation/mci_ai/config.py.
# It scores the plan after the fact; the dispatch objective above is unchanged.
SURVIVAL_CURVES = {
    'pessimistic': {'RED': (0.09, 17, 1.01), 'YELLOW': (0.57, 61, 2.03)},
    'moderate':    {'RED': (0.24, 47, 1.30), 'YELLOW': (0.76, 138, 2.17)},
    'optimistic':  {'RED': (0.56, 91, 1.58), 'YELLOW': (0.81, 160, 2.41)},
}


def _add_survival(result, curve):
    if curve not in SURVIVAL_CURVES:
        raise ValueError('survival_curve must be one of ' + ', '.join(SURVIVAL_CURVES))
    total, scored = 0.0, 0
    for a in result.get('assignments') or []:
        params = SURVIVAL_CURVES[curve].get(str(a.get('triage') or '').upper())
        t = _as_float(a.get('hospital_arrival_min'))
        if params is None or t is None:
            a['survival'] = None  # GREEN / BLACK / undelivered: no curve
            continue
        b0, b1, b2 = params
        a['survival'] = round(b0 / ((max(t, 0.0) / b1) ** b2 + 1.0), 3)
        total += a['survival']
        scored += 1
    result['survival_curve'] = curve
    result['expected_survivors'] = round(total, 2)
    result['survival_scored_patients'] = scored
    return result


# ---------------------------------------------
# AI simulation tab (separate model; does not read or write scenario CSVs)
# ---------------------------------------------
# Wraps ../AI_simulation/mci_ai: AI-estimated inputs (low / likely / high) -> one t0 Gurobi plan each.
# The "claude" backend runs the Claude Code CLI of whoever runs this server, so keep the server
# on 127.0.0.1; teammates run their own copy.
import sys
AI_SIM_DIR = Path(__file__).resolve().parent.parent / 'AI_simulation'
if str(AI_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(AI_SIM_DIR))


def _ai_modules():
    try:
        from mci_ai import ai_estimator, config as ai_config, gurobi_model, scenario as ai_scenario
    except ImportError as exc:
        raise HTTPException(503, f'AI simulation unavailable: {exc}') from exc
    return ai_estimator, ai_config, gurobi_model, ai_scenario


def _ai_call(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f'{type(exc).__name__}: {exc}') from exc


def _ai_scn(body):
    _, _, _, ai_scenario = _ai_modules()
    return ai_scenario.make_scenario(site=body.get('site'), ambulances=body.get('ambulances'),
                                     hospital_ids=body.get('hospital_ids'), constants=body.get('constants'))


@app.get('/api/ai-sim/defaults')
def ai_sim_defaults():
    est, cfg, _, _ = _ai_modules()
    return {'site': cfg.DEFAULT_SITE, 'ambulances': cfg.DEFAULT_AMBULANCES,
            'hospitals': cfg.HOSPITALS, 'hospital_ids': cfg.DEFAULT_HOSPITAL_IDS,
            'constants': cfg.DEFAULT_CONSTANTS, 'assumptions': cfg.ASSUMPTIONS,
            'survival_curves': list(cfg.SURVIVAL), 'backends': est.available_backends()}


@app.post('/api/ai-sim/features')
def ai_sim_features(body: dict = Body(...)):
    def go():
        _, _, _, ai_scenario = _ai_modules()
        scn = _ai_scn(body)
        return {'scenario': scn, 'features': ai_scenario.route_features(scn)}
    return _ai_call(go)


@app.post('/api/ai-sim/estimate')
def ai_sim_estimate(body: dict = Body(...)):
    def go():
        est, _, _, ai_scenario = _ai_modules()
        scn = _ai_scn(body)
        feats = ai_scenario.route_features(scn)
        return est.estimate(scn, feats, backend=body.get('backend', 'mock'),
                            model=body.get('model') or 'sonnet', use_cache=not body.get('fresh'))
    return _ai_call(go)


@app.post('/api/ai-sim/solve')
def ai_sim_solve(body: dict = Body(...)):
    def go():
        est, _, gm, _ = _ai_modules()
        scn = _ai_scn(body)
        params, log = est.validate(body['raw'], scn)  # same checks as AI output
        name = body.get('name', 'likely')
        res = gm.solve(gm.build_data(scn, params[name]))
        res['validation_log'] = log
        res['inputs'] = params[name]
        return res
    return _ai_call(go)



app.mount('/',StaticFiles(directory=Path(__file__).resolve().parent/'frontend',html=True),name='frontend')
