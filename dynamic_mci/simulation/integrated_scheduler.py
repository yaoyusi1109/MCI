"""Standalone Maryland MCI Stage 2B: shared personnel, care and transport clock.
Synthetic deterioration is NOT a clinical prognosis. Calls a one-dispatch Gurobi MILP.
Run from project root: python -m simulation.integrated_scheduler --scenario data/scenarios/camden_yards_dynamic --triage-results PATH
"""
import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from simulation.engine import get_route, get_hospital_location, location
from simulation.gurobi_dispatch import choose_dispatch

BASE = Path(__file__).resolve().parents[1]
STEP = 0.75
EPS = 1e-7
NEXT = {'GREEN': 'YELLOW', 'YELLOW': 'RED', 'RED': 'BLACK'}
RANK = {'RED': 0, 'YELLOW': 1, 'GREEN': 2, 'BLACK': 3}


def rows(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f'CSV has no header: {path}')
        return list(reader)


def flag(x):
    return str(x).strip().upper() in ('TRUE', 'YES', '1')


class HospitalBedLedger:
    """Initial allocatable beds are conserved across reservations and admissions."""

    def __init__(self, hospitals):
        self.initial = {}
        for h in hospitals:
            hid = h['hospital_id']
            if hid in self.initial:
                raise ValueError(f'Duplicate hospital ID: {hid}')
            n = int(h['available_beds'])
            if n < 0:
                raise ValueError(f'Negative initial beds at {hid}')
            self.initial[hid] = n
        self.reservations = {}  # patient_id -> hospital_id
        self.occupied = {}      # patient_id -> hospital_id
        self.timeline = []
        for hid in sorted(self.initial):
            self._record(0.0, hid, 'INITIAL', None)

    def counts(self, hid):
        if hid not in self.initial:
            raise ValueError(f'Unknown hospital: {hid}')
        reserved = sum(h == hid for h in self.reservations.values())
        occupied = sum(h == hid for h in self.occupied.values())
        return {'initial': self.initial[hid],
                'available': self.initial[hid] - reserved - occupied,
                'reserved': reserved, 'occupied': occupied}

    def available(self, hid):
        return self.counts(hid)['available']

    def snapshot(self):
        return {hid: self.counts(hid) for hid in sorted(self.initial)}

    def _record(self, t, hid, event, pid):
        self.timeline.append({'time_min': round(float(t), 4),
                              'hospital_id': hid, 'event': event,
                              'patient_id': pid, **self.counts(hid)})

    def reserve(self, pid, hid, t):
        if pid in self.reservations or pid in self.occupied:
            raise ValueError(f'Patient already allocated a bed: {pid}')
        if self.available(hid) < 1:
            raise ValueError(f'No allocatable beds at {hid}')
        self.reservations[pid] = hid
        self._record(t, hid, 'BED_RESERVED', pid)

    def occupy(self, pid, hid, t):
        if self.reservations.get(pid) != hid:
            raise ValueError(f'No matching reservation for {pid} at {hid}')
        del self.reservations[pid]
        self.occupied[pid] = hid
        self._record(t, hid, 'BED_OCCUPIED', pid)

    def cancel(self, pid, t):
        if pid not in self.reservations:
            raise ValueError(f'No active reservation for {pid}')
        hid = self.reservations.pop(pid)
        self._record(t, hid, 'BED_RESERVATION_CANCELLED', pid)

    def validate(self):
        issues = []
        if set(self.reservations) & set(self.occupied):
            issues.append('Patient both reserved and occupied')
        for row in self.timeline:
            if (min(row['available'], row['reserved'], row['occupied']) < 0 or
                row['available'] + row['reserved'] + row['occupied'] != row['initial']):
                issues.append(f"Capacity invariant violated: {row['hospital_id']} at {row['time_min']}")
        return issues


def run(scenario, triage_file, horizon=240.0, save=True):
    scenario = Path(scenario).resolve()
    triage = json.loads(Path(triage_file).read_text(encoding='utf-8'))
    if triage['scenario'] != scenario.name or triage['validation']['status'] != 'PASS':
        raise ValueError('Triage result scenario or validation mismatch')
    if horizon <= 0:
        raise ValueError('Horizon must be positive')
    patients = rows(scenario / 'patients.csv')
    dynamics = {x['patient_id']: x for x in rows(scenario / 'patient_dynamics.csv')}
    people = rows(scenario / 'ems_personnel.csv')
    units = {x['unit_id']: x for x in rows(scenario / 'response_units.csv')}
    hospitals = rows(scenario / 'hospitals.csv')
    compatible = {(x['triage'].upper(), x['hospital_id']) for x in rows(scenario / 'hospital_compatibility.csv') if flag(x['compatible'])}
    incident = rows(scenario / 'incidents.csv')[0]
    config = json.loads((scenario / 'config.json').read_text(encoding='utf-8-sig'))
    observation_policy = str(
        config.get("optimizer_observation_policy", "NORMAL")
    ).upper()
    release_min = float(
        config.get("optimizer_observation_release_min", 120)
    )
    if observation_policy not in ("NORMAL", "DELAYED"):
        raise ValueError("Invalid optimizer observation policy")

    pickup = location(incident['pickup_latitude'], incident['pickup_longitude'])
    commander = triage.get('incident_commander')
    assessments = {x['patient_id']: x for x in triage['assessments']}
    if len(assessments) != len(patients) or set(assessments) != {x['patient_id'] for x in patients}:
        raise ValueError('Missing or duplicate initial triage assessments')
    selected_units = set(triage['units'])
    ambulances = [u for u in selected_units if units[u]['unit_type'].upper() == 'AMBULANCE']
    if len(ambulances) != 1:
        raise ValueError('Stage 2B requires exactly one selected ambulance')
    aid = ambulances[0]
    crew = sorted(x['personnel_id'] for x in people if x['unit_id'] == aid)
    if len(crew) != 2:
        raise ValueError(f'{aid} requires exactly two assigned EMS crew members')
    staff = {}
    for x in people:
        if x['unit_id'] not in selected_units:
            continue
        wid = x['personnel_id']
        staff[wid] = {'unit': x['unit_id'], 'role': 'COMMAND' if wid == commander else 'AVAILABLE',
                      'patient': None, 'triage': flag(x['triage_capable']), 'stabilize': flag(x['stabilization_capable']),
                      'arrival': float(triage['unit_arrivals_min'][x['unit_id']]), 'until': 0.0}
    if commander not in staff:
        raise ValueError('Commander absent from selected personnel')
    state = {}
    for x in patients:
        pid = x['patient_id']
        d = dynamics[pid]
        threshold = float(d['minutes_per_deterioration'])
        mult = float(d['stabilization_multiplier'])
        if x['triage'].upper() != 'BLACK' and threshold <= 0:
            raise ValueError(f'Invalid threshold: {pid}')
        if not 0 < mult <= 1:
            raise ValueError(f'Invalid multiplier: {pid}')
        assessed = float(assessments[pid]['triage_complete_min'])
        state[pid] = {'id': pid, 'initial': x['triage'].upper(), 'true': x['triage'].upper(),
                      'observed': None, 'optimizer_known': None, 'assessed': assessed, 'due': assessed + 15,
                      'exposure': 0.0, 'threshold': threshold, 'mult': mult,
                      'status': 'UNASSESSED', 'care_min': 0.0, 'reassessments': 0,
                      'transition_count': 0, 'arrival': None, 'hospital': None,
                      'disposition': 'PENDING', 'requires_transport': flag(x.get('requires_transport', 'TRUE'))}
    bed_ledger = HospitalBedLedger(hospitals)
    events, routes, assignments, conflicts = [], [], [], []
    optimizer_decisions = []
    optimizer_patient_registry_history = []

    def build_optimizer_registry(time):
        registry = []

        for patient in sorted(
            state.values(), key=lambda p: p['id']
        ):
            assessed = (
                patient['observed'] is not None
                and patient['assessed'] <= time + EPS
            )

            known_triage = (
                patient['optimizer_known']
                if assessed else 'UNASSESSED'
            )

            status = patient['status']

            if not assessed:
                known_status = 'UNASSESSED'
            elif status in ('LOADING', 'IN_TRANSPORT'):
                known_status = 'COMMITTED'
            elif status == 'DELIVERED':
                known_status = 'DELIVERED'
            elif status == 'EXPECTANT':
                known_status = 'EXPECTANT'
            else:
                known_status = 'WAITING'

            registry.append({
                'patient_id': patient['id'],
                'known_triage': known_triage,
                'known_status': known_status,
                'assessment_complete_min': (
                    round(patient['assessed'], 4)
                    if assessed else None
                ),
                'dispatch_eligible': (
                    known_status == 'WAITING'
                    and known_triage in (
                        'RED', 'YELLOW', 'GREEN'
                    )
                    and patient['requires_transport']
                )
            })

        return registry
    def log(t, kind, **fields):
        events.append({'time_min': round(t, 4), 'event': kind, **fields})
    # Route matrix: no fabricated travel times. Valhalla must be available.
    legs = {}
    for h in hospitals:
        hid = h['hospital_id']

        # Use ambulance entrance coordinates when available.
        # Fall back to hospital coordinates when blank.
        entrance_lat = str(
            h.get("ambulance_entrance_lat") or ""
        ).strip()

        entrance_lon = str(
            h.get("ambulance_entrance_lon") or ""
        ).strip()

        if entrance_lat and entrance_lon:
            dest = location(
                entrance_lat,
                entrance_lon
            )
        else:
            dest = location(
                h["latitude"],
                h["longitude"]
            )

        legs[hid] = (get_route(pickup, dest), get_route(dest, pickup))
    # Finite-state ambulance: ON_SCENE -> LOADING -> TO_HOSPITAL -> TURNOVER -> RETURN -> ON_SCENE.
    ambulance = {'phase': 'BEFORE_ARRIVAL', 'until': float(triage['unit_arrivals_min'][aid]),
                 'patient': None, 'hospital': None, 'trip': 0}
    if commander in crew:
        # No non-transport command transfer in this isolated scenario: block transport rather than silently abandoning command.
        log(0, 'COMMAND_CONTINUITY_WARNING', reason='Ambulance crew holds incident command; transport blocked')
    def advance(p, start, end, stabilizing):
        if p['true'] == 'BLACK' or p['status'] in ('DELIVERED', 'IN_TRANSPORT', 'EXPECTANT', 'LOADING'):
            return
        t = max(start, p['assessed'])
        if t >= end - EPS:
            return
        factor = p['mult'] if stabilizing else 1.0
        if stabilizing:
            p['care_min'] += end - t
        while t < end - EPS and p['true'] != 'BLACK':
            dt = min(end - t, max(0.0, (p['threshold'] - p['exposure']) / factor))
            p['exposure'] += dt * factor
            t += dt
            if p['exposure'] >= p['threshold'] - EPS:
                old = p['true']
                p['true'] = NEXT[old]
                p['transition_count'] += 1
                p['exposure'] = 0.0
                log(t, 'PATIENT_STATE_CHANGED', patient_id=p['id'], from_triage=old, to_triage=p['true'], observed_triage=p['observed'])
                # Do not use hidden true state to remove a patient from the observed queue.
    def release_crew(t):
        for wid in crew:
            staff[wid]['role'] = 'AVAILABLE'
            staff[wid]['patient'] = None
            log(t, 'CREW_RETURNED', personnel_id=wid, ambulance_id=aid)
    t = 0.0
    while t < horizon - EPS:
        end = min(horizon, t + STEP)
        # Process all due task completions and ambulance transitions at the slot boundary.
        for wid, w in staff.items():
            if w['role'] == 'REASSESSING' and w['until'] <= t + EPS:
                p = state[w['patient']]
                old = p['observed']
                p['observed'] = p['true']
                if observation_policy == "NORMAL" or t >= release_min:
                    p['optimizer_known'] = p['observed']
                p['reassessments'] += 1
                p['due'] = t + 15.0
                log(t, 'PATIENT_REASSESSED', patient_id=p['id'], personnel_id=wid, previous_observed_triage=old, observed_triage=p['observed'])
                w.update(role='AVAILABLE', patient=None)
                if p['observed'] == 'BLACK' and p['status'] == 'WAITING':
                    p.update(status='EXPECTANT', disposition='EXPECTANT')
                    log(t, 'EXPECTANT_DISPOSITION', patient_id=p['id'])
        if ambulance['phase'] != 'ON_SCENE' and ambulance['until'] <= t + EPS:
            phase = ambulance['phase']
            pid, hid = ambulance['patient'], ambulance['hospital']
            if phase == 'BEFORE_ARRIVAL':
                ambulance['phase'] = 'ON_SCENE'
                log(t, 'AMBULANCE_ARRIVED', ambulance_id=aid)
            elif phase == 'LOADING':
                p = state[pid]
                p['status'] = 'IN_TRANSPORT'
                ambulance['phase'] = 'TO_HOSPITAL'
                ambulance['until'] = t + legs[hid][0]['travel_minutes']
                log(t, 'AMBULANCE_DEPARTED', ambulance_id=aid, patient_id=pid, hospital_id=hid)
                routes.append({'purpose': 'MCI_TO_HOSPITAL', 'patient_id': pid, 'hospital_id': hid, 'start_min': t, 'end_min': ambulance['until'], **legs[hid][0]})
            elif phase == 'TO_HOSPITAL':
                p = state[pid]
                bed_ledger.occupy(pid, hid, t)
                log(t, 'BED_OCCUPIED', patient_id=pid, hospital_id=hid)
                p.update(status='DELIVERED', disposition='DELIVERED', arrival=t)
                assignments.append({'patient_id': pid, 'triage_at_assignment': p['observed'], 'hospital_id': hid, 'hospital_arrival_min': round(t, 4), 'ambulance_id': aid})
                log(t, 'PATIENT_DELIVERED', patient_id=pid, hospital_id=hid)
                ambulance['phase'] = 'TURNOVER'
                ambulance['until'] = t + float(config.get('hospital_turnover_minutes', 60))
            elif phase == 'TURNOVER':
                ambulance['phase'] = 'RETURN'
                ambulance['until'] = t + legs[hid][1]['travel_minutes']
                routes.append({'purpose': 'HOSPITAL_TO_MCI', 'hospital_id': hid, 'start_min': t, 'end_min': ambulance['until'], **legs[hid][1]})
                log(t, 'TURNOVER_COMPLETE', ambulance_id=aid)
            elif phase == 'RETURN':
                ambulance.update(phase='ON_SCENE', patient=None, hospital=None)
                release_crew(t)
                log(t, 'AMBULANCE_RETURNED', ambulance_id=aid)
        for p in state.values():
            if p['observed'] is None and p['assessed'] <= t + EPS:
                p.update(
                    observed=p['true'],
                    optimizer_known=p['true'],
                    status='WAITING'
                )
                log(t, 'INITIAL_TRIAGE_VISIBLE', patient_id=p['id'], observed_triage=p['observed'])
                if p['observed'] == 'BLACK':
                    p.update(status='EXPECTANT', disposition='EXPECTANT')
        # Release continuous stabilization before task reallocation.
        for w in staff.values():
            if w['role'] == 'STABILIZING':
                w.update(role='AVAILABLE', patient=None)
        # Reassess based on observed state, never hidden state.
        due = sorted((p for p in state.values() if p['status'] == 'WAITING' and p['due'] <= t + EPS), key=lambda p: (RANK[p['observed']], p['due'], p['id']))
        for p in due:
            free = sorted(wid for wid, w in staff.items() if w['role'] == 'AVAILABLE' and w['triage'] and w['arrival'] <= t + EPS)
            if not free:
                continue
            wid = free[0]
            staff[wid].update(role='REASSESSING', patient=p['id'], until=end)
            log(t, 'REASSESSMENT_STARTED', patient_id=p['id'], personnel_id=wid)
            p['due'] = float('inf')
        # Gurobi selects one patient/hospital at each ambulance availability event.
        if observation_policy == "DELAYED" and t >= release_min:
            for patient in state.values():
                if patient['observed'] is not None:
                    patient['optimizer_known'] = patient['observed']

        # Bed capacity comes only from the live ledger, never from hospitals.csv.
        if ambulance['phase'] == 'ON_SCENE' and commander not in crew:
            candidates = [p for p in state.values()
                          if p['status'] == 'WAITING' and p['requires_transport']
                          and p['observed'] in ('RED', 'YELLOW', 'GREEN')]

            if candidates:
                snapshot = {
                    hid: bed_ledger.available(hid)
                    for hid in bed_ledger.initial
                }

                optimizer_inputs = [
                    {
                        'id': patient['id'],
                        'observed': patient['optimizer_known'],
                        'ready_min': patient['assessed']
                    }
                    for patient in candidates
                ]

                patient_registry = build_optimizer_registry(t)

                registry_ids = {
                    p['patient_id']
                    for p in patient_registry
                }

                if registry_ids != set(state):
                    raise RuntimeError(
                        'Patient registry is incomplete'
                    )

                optimizer_patient_registry_history.append({
                    'decision_time_min': round(t, 4),
                    'patients': patient_registry
                })

                decision, decision_log = choose_dispatch(
                    optimizer_inputs, snapshot, compatible,
                    {hid: legs[hid][0]['travel_minutes'] for hid in legs},
                    aid, decision_time_min=t)
                decision_log['available_beds_snapshot'] = snapshot.copy()
                decision_log['patient_registry'] = patient_registry
                decision_log['registry_patient_count'] = len(
                    patient_registry
                )
                decision_log['registry_unassessed_count'] = sum(
                    p['known_status'] == 'UNASSESSED'
                    for p in patient_registry
                )
                decision_log['observation_policy'] = observation_policy
                decision_log['optimizer_input_snapshot'] = {
                    p['id']: p['observed']
                    for p in optimizer_inputs
                }
                decision_log['ems_observed_snapshot'] = {
                    p['id']: p['observed']
                    for p in candidates
                }
                decision_log['true_triage_audit'] = {
                    p['id']: p['true']
                    for p in candidates
                }
                decision_log['reoptimization_reason'] = 'AMBULANCE_AVAILABLE'
                if decision is not None:
                    pid, hid = decision['patient_id'], decision['hospital_id']
                    p = state[pid]
                    # Commit the bed reservation BEFORE crew/ambulance mutation.
                    # A failed reservation cannot create a phantom assignment.
                    bed_ledger.reserve(pid, hid, t)
                    log(t, 'BED_RESERVED', patient_id=pid, hospital_id=hid)
                    for wid in crew:
                        w = staff[wid]
                        if w['role'] == 'REASSESSING':
                            q = state[w['patient']]
                            q['due'] = t
                            log(t, 'REASSESSMENT_INTERRUPTED', patient_id=q['id'], personnel_id=wid)
                        w.update(role='TRANSPORT_RESERVED', patient=pid)
                        log(t, 'CREW_RESERVED', ambulance_id=aid,
                            personnel_id=wid, patient_id=pid)
                    p.update(status='LOADING', hospital=hid)
                    ambulance.update(phase='LOADING', patient=pid, hospital=hid,
                                     until=t + float(config.get('loading_minutes', 5)))
                    log(t, 'PATIENT_ASSIGNED', patient_id=pid, hospital_id=hid,
                        observed_triage=p['observed'],
                        optimizer_known_triage=p['optimizer_known'],
                        optimizer='GUROBI')
                    decision_log.update(selected_patient_id=pid,
                                        selected_hospital_id=hid)
                optimizer_decisions.append(decision_log)
        # Allocate remaining personnel to stabilization.
        covered = set()
        for p in sorted((p for p in state.values() if p['status'] == 'WAITING'), key=lambda p: (RANK[p['observed']], p['id'])):
            free = sorted(wid for wid, w in staff.items() if w['role'] == 'AVAILABLE' and w['stabilize'] and w['arrival'] <= t + EPS)
            if free:
                wid = free[0]
                staff[wid].update(role='STABILIZING', patient=p['id'])
                covered.add(p['id'])
        # Check task exclusivity before advancing the state.
        assignments_by_worker = Counter(wid for wid, w in staff.items() if w['role'] in ('REASSESSING', 'STABILIZING', 'TRANSPORT_RESERVED'))
        if any(n > 1 for n in assignments_by_worker.values()):
            conflicts.append(f'Duplicate worker task at {t}')
        if any(staff[wid]['role'] != 'TRANSPORT_RESERVED' for wid in crew) and ambulance['phase'] in ('LOADING', 'TO_HOSPITAL', 'TURNOVER', 'RETURN'):
            conflicts.append(f'Crew availability violation at {t}')
        for p in state.values():
            advance(p, t, end, p['id'] in covered)
        t = end
    for p in state.values():
        if p['status'] == 'WAITING' and p['observed'] == 'BLACK':
            p.update(status='EXPECTANT', disposition='EXPECTANT')
    conflicts.extend(bed_ledger.validate())
    delivered = [p for p in state.values() if p['status'] == 'DELIVERED']
    remaining = [p for p in state.values() if p['requires_transport'] and p['status'] not in ('DELIVERED', 'EXPECTANT')]
    summary = {'scenario': scenario.name, 'mode': triage['mode'], 'horizon_min': horizon,
               'patients_total': len(state), 'delivered': len(delivered),
               'expectant': sum(p['disposition'] == 'EXPECTANT' for p in state.values()),
               'deceased': 0, 'deceased_note': 'No explicit death process modeled',
               'still_awaiting_or_in_transport': len(remaining),
               'observed_makespan_min': round(max((p['arrival'] for p in delivered), default=0), 4),
               'completed_incident': not remaining and ambulance['phase'] not in ('LOADING', 'TO_HOSPITAL'),
               'transport_policy': 'Gurobi rolling single-dispatch MILP; not full-horizon optimal',
               'validation': {'status': 'PASS' if not conflicts else 'FAIL', 'issues': conflicts}}
    summary['optimizer_observation_policy'] = observation_policy
    summary['optimizer_observation_release_min'] = release_min
    result = {'summary': summary, 'patients': [{k: p[k] for k in ('id', 'initial', 'true', 'observed', 'status', 'disposition', 'arrival', 'hospital', 'care_min', 'reassessments', 'transition_count')} for p in state.values()],
              'assignments': assignments, 'events': sorted(events, key=lambda x: x['time_min']), 'routes': routes,
              'optimizer_decisions': optimizer_decisions,
              'optimizer_patient_registry_history':
                  optimizer_patient_registry_history,
              'hospital_capacity_timeline': bed_ledger.timeline,
              'hospital_capacity_final': bed_ledger.snapshot(),
              'hospital_capacity_for_optimizer': {hid: bed_ledger.available(hid) for hid in bed_ledger.initial}}
    if save:
        run_id = 'integrated_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
        out = BASE / 'outputs' / scenario.name / 'integrated_tests' / run_id
        out.mkdir(parents=True)
        (out / 'integrated_results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        shutil.copytree(scenario, out / 'scenario_snapshot')
        from reporting.integrated_report import generate
        generate(out, result)
        print('Saved:', out)
    print('Validation:', summary['validation']['status'])
    print('Delivered:', summary['delivered'], '| Expectant:', summary['expectant'], '| Remaining:', summary['still_awaiting_or_in_transport'])
    print('Observed delivery makespan:', summary['observed_makespan_min'])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default='data/scenarios/camden_yards_dynamic')
    ap.add_argument('--triage-results', required=True)
    ap.add_argument('--horizon', type=float, default=240)
    args = ap.parse_args()
    run(args.scenario, args.triage_results, args.horizon)


if __name__ == '__main__':
    main()
