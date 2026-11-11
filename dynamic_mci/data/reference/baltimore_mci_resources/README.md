# Baltimore City MCI receiving hospitals and transport resources

Reference data for the dynamic MCI model. It records which hospitals receive which casualties and what transport the city and region can bring to a mass-casualty incident. Every designation was checked against an official source where one could be read; anything that could not be checked is marked.

Compiled November 2026 from a user-supplied resource summary, then verified as described below.

## Files

| File | What it holds |
|---|---|
| `hospitals.csv` | 12 hospitals: 11 receiving EDs plus the Baltimore VA as a reserve site. Address, coordinates, MCI tier, trauma level, specialty designations, which triage colours each takes, and the source for each designation. |
| `ems_resources.csv` | Transport resources: BCFD ALS medic units and BLS ambulances, mutual aid, hospital critical-care transport, Maryland State Police helicopters, private BLS, and MTA buses. Counts are ranges with a confidence rating. |
| `make_citywide_scenario.py` | Builds the `camden_yards_citywide` scenario from `hospitals.csv` (see "Use in the simulation"). |

## Hospital designations: what was verified

| ID | Hospital | Trauma level | Specialty | Verified by |
|---|---|---|---|---|
| H001 | R Adams Cowley Shock Trauma Center | PARC | Neurotrauma, hyperbaric | TraumaNet page; 2022 map |
| H002 | The Johns Hopkins Hospital | Adult Level I | Eye trauma (Wilmer); Children's Center: pediatric trauma Level I and pediatric burn | TraumaNet page; 2022 map |
| H003 | Sinai Hospital of Baltimore | Level II | none | TraumaNet page (by its Level II heading); 2022 map |
| H004 | Johns Hopkins Bayview Medical Center | Level II | Adult burn | TraumaNet page; 2022 map |
| H005 | MedStar Union Memorial Hospital | not a trauma center | Hand/upper extremity (Curtis National Hand Center) | 2022 map; MIEMSS annual reports |
| H006–H010 | Mercy, UMMC Midtown, Harbor, Good Samaritan, Saint Agnes | not trauma centers | none (stroke, cardiac or perinatal only) | 2022 map |
| H011 | Grace Medical Center | not a trauma center | none | LifeBridge Health (24-hour ED); not on the 2022 map |
| H012 | Baltimore VA Medical Center | reserve site | none | user-supplied summary only |

The "2022 map" is the Trauma and Specialty Referral Centers map (November 2022) published on Maryland TraumaNet. It is an image, so it was read visually from the full-resolution file. The MIEMSS designation pages returned "not found" when checked, so the TraumaNet page and map were used instead.

## Corrections to the supplied summary

- **PARC is a Maryland designation.** The summary called Shock Trauma "the only PARC in the US". It is Maryland's Primary Adult Resource Center.
- **Union Memorial is not a trauma center.** It is the state's hand/upper-extremity specialty center. In this data it takes YELLOW and GREEN patients. A RED patient goes there only as a hand/upper-extremity specialty referral, which the triage-only model does not represent.
- **Grace Medical Center has no trauma or specialty designation.** It does run a 24-hour emergency department, so it is listed as a general ED.
- **BCFD fleet counts conflict across sources.**
  - WMAR-2 reports 36 medic units: 26 running 24/7 and 10 peak units.
  - Wikipedia lists 21 ALS and 8 BLS full-time units.
  - Reports from 2025 describe 4 ALS units being downgraded to BLS.
  - Neither of the first two sources gives a date, so the data keeps ranges (24–36 total) rated LOW confidence.
- **Several figures are planning estimates from the summary, not verified data:**
  - 12–20 BCFD units committable to a single MCI
  - 5–10 mutual-aid units from Baltimore County
  - 5–8 from Anne Arundel and Howard counties
  - "dozens" of private ambulances

## Assumptions (not data)

- **`mci_accepts`** is the triage routing rule used in the model:
  - RED goes only to designated adult trauma centers (PARC, Level I, Level II).
  - YELLOW goes to trauma centers and general EDs.
  - GREEN goes to general EDs and Union Memorial, keeping trauma centers free for critical patients.
  - This follows the tiering in the supplied summary. It is a modelling choice, not a MIEMSS rule.
- **`available_beds` in the citywide scenario is an assumed MCI intake per hospital** (`TIER_INTAKE` in the script): 5 for PARC and Level I, 3 for every other hospital. Real intake numbers come from the hospitals during an event through MIEMSS hospital resource tracking.

## Use in the simulation

```bash
cd dynamic_mci
../.venv/bin/python data/reference/baltimore_mci_resources/make_citywide_scenario.py
```

The script creates `data/scenarios/camden_yards_citywide`, a copy of `camden_yards_multi_unit` (four fire engines) that uses the 11 receiving hospitals. It overwrites that scenario's hospital tables and fleet files on every run.

It also adds three BCFD medic units, so the scenario runs with four ambulances:

| Unit | Station |
|---|---|
| A001 | 800 Light St (from the base scenario) |
| A002 | Fire Station 23 |
| A003 | Fire Station 34 |
| A004 | Fire Station 26 |

Each unit has two paramedics. Which stations actually house medic units was not verified, so this placement is an assumption (`EXTRA_AMBULANCES` in the script).

The dynamic engine dispatches any number of ambulances. `ems_resources.csv` is not read automatically: it is the reference for how many units a larger scenario could plausibly add.

## Sources

- Maryland TraumaNet, [Trauma Centers in Maryland](https://www.maryland-traumanet.com/resources/trauma-centers/)
- Maryland TraumaNet, [Trauma and Specialty Referral Centers map, November 2022 (PDF)](https://www.maryland-traumanet.com/wp-content/uploads/2023/03/Map-All-Hospitals-Color-Guide-20221117.pdf)
- MIEMSS annual reports, for the hand and eye specialty centers: [2022](https://history.miemss.org/documents/Annual-Report-2022.pdf), [2024](https://history.miemss.org/documents/Annual_Report_2024_Linked_LR.pdf)
- LifeBridge Health, [Grace Medical Center Emergency Department](https://www.lifebridgehealth.org/locations/grace-medical-center-emergency-department)
- WMAR-2 News, [BCFD in need of more paramedics](https://wmar2news.com/infocus/in-focus-baltimore-city-fire-department-in-need-of-more-paramedics)
- Wikipedia, [Baltimore City Fire Department](https://en.wikipedia.org/wiki/Baltimore_City_Fire_Department)
- CBS Baltimore, [Union concerns about downgrading 4 medic units](https://www.cbsnews.com/amp/baltimore/news/maryland-baltimore-emergency-response-downgrade-medic-units)
- BCFD MOP 807-1, MCI Operations, as cited in the supplied summary (NTSB docket attachment; not reviewed here)
- Hospital coordinates: OpenStreetMap (Nominatim). H001–H003 keep the coordinates already used in the existing scenarios.
