# Reference data for the Camden Yards scenarios

The files here are the source of truth for the scenario folders in `../scenarios/`.
`python -m experiments.build_real_scenarios` (run from `dynamic_mci/`) writes them into every scenario folder.

| File | Contents |
| --- | --- |
| `scenario_inputs.json` | Crowd size, triage split, free-bed share, MCI Level 3 response, assumptions |
| `hospitals_baltimore.csv` | 14 acute-care hospitals in Baltimore City and County: FY2026 licensed beds, trauma level, location |
| `stations_baltimore.csv` | 99 fire/EMS stations in Baltimore City and County, career or volunteer, distance to the MCI |
| `scenario_profiles.json` | Each scenario's original per-triage pattern (ready time, deterioration, stabilization), kept so each folder still tests what it was built to test |
| `raw/` | Unedited downloads from Maryland iMAP |

## Patients

- **Count: 45,000.** A sold-out game: ticketed fans (42,455+; venue record 49,828), event and operations staff (~1,000–1,500), concessions and hospitality (~500–800), teams, media and league officials (~200–300).
  Source: <https://stadiumcapacities.com/stadiums/oriole-park-at-camden-yards/>
- **Every person is treated as a casualty.** This is a worst case, chosen on purpose.
- **Triage split 1 : 7 : 25 : 68 % (BLACK : RED : YELLOW : GREEN).** Based on previous blast events and a recommendation by Claude Sonnet 5.5. The split varies heavily with indoor/outdoor setting and building structure.
  - Frykberg and Tepas, *Terrorist bombings*, Annals of Surgery (1988)
  - Frykberg, *Terrorist bombings in Madrid*, Critical Care (2004)
  - Madrid 2004 bombings hospital series, World Journal of Surgery
  - Arnold, *Mass-casualty terrorist bombings*, Part I (PubMed)
  - *The Initial Response to the Boston Marathon Bombing*
- **Counts used:** BLACK 450, RED 3,150, YELLOW 11,250, GREEN 30,150.
  The percentages add up to 101% (45,450 people), so GREEN was reduced by 450 to keep the total at 45,000.
- **Deterioration:** there is no single, fixed clinical deterioration rate for MCI patients. It depends on the injury mechanism, age, existing conditions, available resources and transport times.
  The values in `patient_dynamics.csv` are model placeholders carried over from each scenario's original design, not clinical data.

## Hospitals

- Locations: Maryland iMAP, *Maryland Hospitals*: <https://data.imap.maryland.gov/datasets/cc9954d3db7c4da18322f533a8c25ea3_0>
  (`raw/imap_md_hospitals.json`; its `License_Capacity` field is older, so it is kept only for comparison).
- Licensed beds: FY2026 acute-care licensed beds (Baltimore Hospital Capacity Comparison spreadsheet; HSCRC):
  <https://hscrc.maryland.gov/Documents/Work%20Group%20Uploads/ED%20WTR%20Commission/ED%20WTR%20Access%20and%20Capacity%20Subgroup%20March%2026.pdf>
- Trauma levels: MIEMSS, *Trauma & Specialty Referral Centers*: <https://www.miemss.org/home/hospitals/specialty-referral-centers>
  PARC = R Adams Cowley Shock Trauma Center; Level I = Johns Hopkins Hospital; Level II = Johns Hopkins Bayview, Sinai.
  RED patients may go to PARC, Level I or Level II only.
- **Available beds = floor(licensed beds × 0.29).** Maryland sets licensed acute-care capacity at 140% of average daily census, so a hospital at that rule runs at about 71% occupancy (1 ÷ 1.4), leaving about 29% free.
  This is an upper bound: staffed beds are fewer than licensed beds, so real occupancy is likely higher.
  Beds also depend on which unit admits the patient; that is not modelled.
- Left out:
  - UM Rehabilitation & Orthopaedic Institute (4 licensed acute beds, no emergency department)
  - Bon Secours, Kennedy Krieger, Mount Washington Pediatric, Levindale, Sheppard Pratt and Spring Grove (not in the FY2026 acute-care table: specialty, pediatric, geriatric or psychiatric)
- UMMC's licensed beds include Shock Trauma; the two are one row (H001).

## Stations and units

- Locations: Maryland iMAP, *MD Fire* (county and municipal fire stations), filtered to Baltimore City and Baltimore County; offices, academies, depots and communications centres removed.
  `raw/imap_*_fire_stations_baltimore.json`. iMAP uses older station names and numbers, so check them against the departments' own lists:
  - Baltimore City Fire Department, 38 stations, all career: <https://www.baltimorecity.gov/fire/our-work/fire-stations>
  - Baltimore County Fire Department, 58 stations (25 career, 33 volunteer): <https://www.baltimorecountymd.gov/departments/fire/stations>
  - Unit lists: <https://www.marylandfirefighters.com/bmore.htm>, <https://www.baltimorecountymd.gov/departments/fire/ems>
- The iMAP extract has 40 city, 25 county career and 34 county volunteer records.
- Career and volunteer personnel are dispatched by proximity and equipment type and follow the same command and procedures on scene, so they are treated alike.
- Hospitals don't own 911 ambulances; BCFD (city) and the county fire department plus volunteer companies (county) do.
- Which unit type arrives first (ambulance, medic unit, engine, truck, hazmat, critical response) can't be known in advance.
- **MCI Level 3 response** (51+ patients): Level 2 response plus Deputy Chief EMS, 4 more suppression companies (8 total), 5 more medic units (16 total), and an apparatus coordinator.
  The incident commander may also request: MD State Police Medevac, MTA buses, commercial ambulances (via MIEMSS), Baltimore City Health Department (OST3C), mutual aid, Go Team, Medical Examiner, CISM Team, Chaplain Corps.
  These on-request resources have no published counts, so `resource_requests.csv` is left empty.
- The number of firefighters on scene matters more than the number of trucks: more first responders means faster triage, so the model can start sooner.
- Assumed crews: 4 per suppression company, 2 per medic unit. Personnel counts are hard to find because published numbers include administrative staff.

## Engine limits (as of this data)

- `simulation/integrated_scheduler.py` needs exactly one AMBULANCE unit in `response_units.csv`, so only A001 is listed there.
  The other 15 medic units are in `ambulances.csv` only.
- `simulation/gurobi_dispatch.py` builds one binary variable per patient–hospital pair.
  With 45,000 patients this is far past the restricted Gurobi license (2,000 variables).
- `road_conditions.csv` and `resource_requests.csv` are not read by the engine yet.
