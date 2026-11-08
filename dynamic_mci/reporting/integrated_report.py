"""Report standalone integrated scheduler outcomes; separate from baseline report."""
import csv
import json
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def generate(folder, result):
    folder = Path(folder)
    summary = result['summary']
    patients = result['patients']
    with (folder / 'patient_results.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(patients[0]))
        writer.writeheader()
        writer.writerows(patients)
    with (folder / 'simulation_summary.json').open('w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Summary & Patients'
    ws.append(['MARYLAND MCI - DYNAMIC INTEGRATION TEST'])
    for k, v in summary.items():
        ws.append([k, json.dumps(v) if isinstance(v, dict) else v])
    ws.append([])
    header_row = ws.max_row + 1
    ws.append(list(patients[0]))
    for p in patients:
        ws.append(list(p.values()))
    for name, values in [('Assignments', result['assignments']), ('Event Timeline', result['events'])]:
        sheet = wb.create_sheet(name)
        if values:
            keys = sorted({key for v in values for key in v})
            sheet.append(keys)
            for v in values:
                sheet.append([v.get(k, '') for k in keys])
    capacity = wb.create_sheet('Hospital Capacity Timeline')
    capacity_rows = result.get('hospital_capacity_timeline', [])
    if capacity_rows:
        keys = ['time_min', 'hospital_id', 'event', 'patient_id', 'initial', 'available', 'reserved', 'occupied']
        capacity.append(keys)
        for entry in capacity_rows:
            capacity.append([entry.get(k, '') for k in keys])
    final_capacity = wb.create_sheet('Final Hospital Capacity')
    final_capacity.append(['hospital_id', 'initial', 'available', 'reserved', 'occupied'])
    for hid, values in sorted(result.get('hospital_capacity_final', {}).items()):
        final_capacity.append([hid, values['initial'], values['available'], values['reserved'], values['occupied']])
    opt = wb.create_sheet('Gurobi Decisions')
    decision_rows = result.get('optimizer_decisions', [])
    if decision_rows:
        keys = ['decision_time_min', 'ambulance_id', 'optimizer', 'model',
                'optimizer_status', 'objective_value', 'solve_time_ms',
                'candidate_patients', 'feasible_pairs', 'selected_patient_id',
                'selected_hospital_id', 'reoptimization_reason',
                'available_beds_snapshot']
        opt.append(keys)
        for entry in decision_rows:
            opt.append([json.dumps(entry.get(k)) if isinstance(entry.get(k), dict)
                        else entry.get(k, '') for k in keys])
    else:
        opt.append(['No Gurobi decisions recorded'])
    val = wb.create_sheet('Validation')
    val.append(['Status', summary['validation']['status']])
    val.append(['Transport policy', summary['transport_policy']])
    val.append(['Clinical interpretation', 'Synthetic deterioration; BLACK does not equal death'])
    for issue in summary['validation']['issues']:
        val.append(['Issue', issue])
    for sheet in wb:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = 'A2'
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='17365D')
        if sheet == ws:
            for cell in sheet[header_row]:
                cell.font = Font(bold=True, color='17365D')
                cell.fill = PatternFill('solid', fgColor='DCEAF7')
        for col in sheet.columns:
            letter = get_column_letter(col[0].column)
            sheet.column_dimensions[letter].width = min(45, max(14, max(len(str(c.value or '')) for c in col) + 2))
    wb.save(folder / 'MCI_Dynamic_Results.xlsx')
