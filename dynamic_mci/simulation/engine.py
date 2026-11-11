
"""
Maryland MCI - Event-driven ambulance simulation

Version 1:
- One active ambulance
- Known patient inventory
- Gurobi patient and hospital assignment
- Valhalla road routing
- Non-reusable hospital beds
- Five-minute loading and configurable turnover
- Unique results folder for every simulation run

Original scenario CSV files are never modified.
"""

from reporting.report_generator import generate_reports

import argparse
import copy
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import requests

from optimization.optimizer.mci_ambulance_gurobi import (
    load_scenario,
    optimize_mci,
)
from simulation.road_events import current_closures, current_exclusions


BASE = Path(__file__).resolve().parents[1]

VALHALLA_URL = os.getenv(
    "VALHALLA_URL",
    "http://127.0.0.1:8002"
).rstrip("/")

OUTPUT_DIR = BASE / "outputs"


def location(lat, lon):
    """Validate latitude and longitude."""
    if pd.isna(lat) or pd.isna(lon):
        raise ValueError("Missing routing coordinates")

    lat = float(lat)
    lon = float(lon)

    if not (-90 <= lat <= 90):
        raise ValueError("Invalid latitude")

    if not (-180 <= lon <= 180):
        raise ValueError("Invalid longitude")

    return lat, lon



def get_hospital_location(hospital):
    """
    Prefer ambulance entrance coordinates.
    Fall back to hospital coordinates if
    either entrance coordinate is missing.
    """
    def present(value):
        # Blank CSV cells arrive as NaN from pandas (or "" when read as text).
        return pd.notna(value) and str(value).strip() != ""

    entrance_lat = hospital.get("ambulance_entrance_lat")
    entrance_lon = hospital.get("ambulance_entrance_lon")

    if present(entrance_lat) and present(entrance_lon):
        return location(entrance_lat, entrance_lon)

    return location(
        hospital["latitude"],
        hospital["longitude"]
    )



def get_station_location(row):
    """Prefer the station's vehicle exit coordinates."""

    if (
        pd.notna(row["vehicle_exit_lat"])
        and pd.notna(row["vehicle_exit_lon"])
    ):
        return location(
            row["vehicle_exit_lat"],
            row["vehicle_exit_lon"]
        )

    return location(
        row["latitude"],
        row["longitude"]
    )


def decode_polyline(encoded, precision=6):
    """Decode Valhalla's encoded polyline6 geometry."""

    coordinates = []
    index = 0
    lat = 0
    lon = 0
    factor = 10 ** precision

    while index < len(encoded):
        values = []

        for _ in range(2):
            result = 0
            shift = 0

            while True:
                if index >= len(encoded):
                    raise ValueError(
                        "Invalid encoded route geometry"
                    )

                b = ord(encoded[index]) - 63
                index += 1

                result |= (b & 0x1F) << shift
                shift += 5

                if b < 0x20:
                    break

            values.append(
                ~(result >> 1)
                if result & 1
                else result >> 1
            )

        lat += values[0]
        lon += values[1]

        coordinates.append([
            lat / factor,
            lon / factor
        ])

    return coordinates


def _trip_coordinates(trip):
    coordinates = []
    for leg in trip["legs"]:
        segment = decode_polyline(leg["shape"])
        if coordinates and segment and coordinates[-1] == segment[0]:
            segment = segment[1:]
        coordinates.extend(segment)
    return coordinates


def _seg_m(a, b):
    dy = (b[0] - a[0]) * 111000
    dx = (b[1] - a[1]) * 111000 * math.cos(math.radians(b[0]))
    return math.hypot(dx, dy)


def _trip_vertex_minutes(trip):
    """Cumulative driving minutes at each shape vertex.

    Each Valhalla maneuver carries its own time (slow streets, fast highways); it is spread over that
    maneuver's segments by length. Lets playback and re-routing place the ambulance by elapsed time
    rather than assuming one constant speed for the whole trip.
    """
    out = []
    for leg in trip["legs"]:
        shape = decode_polyline(leg["shape"])
        times = [0.0] * len(shape)
        for m in leg.get("maneuvers", []):
            b, e = int(m.get("begin_shape_index", 0)), int(m.get("end_shape_index", 0))
            seg = [_seg_m(shape[i], shape[i + 1]) for i in range(b, min(e, len(shape) - 1))]
            total = sum(seg)
            for k, length in enumerate(seg):
                share = length / total if total else 1.0 / len(seg)
                times[b + k + 1] = times[b + k] + float(m.get("time", 0)) * share
        base = out[-1] if out else 0.0
        cumulative = [base + x / 60.0 for x in times]
        if out and cumulative:
            cumulative = cumulative[1:]  # shared vertex between legs
        out.extend(cumulative)
    return out


def _trip_via(trip, limit=3):
    """Main named roads of a trip, longest first (for explanations)."""
    length = {}
    for leg in trip["legs"]:
        for m in leg.get("maneuvers", []):
            for name in m.get("street_names") or []:
                length[name] = length.get(name, 0.0) + float(m.get("length", 0))
    return [n for n, _ in sorted(length.items(), key=lambda kv: -kv[1])[:limit]]


def passes_near(coordinates, point, tol_m=30.0):
    """True if the polyline comes within tol_m of point (segment distance, local projection)."""
    k =math.cos(math.radians(point[0])) * 111320.0
    px, py = point[1] * k, point[0] * 110570.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(coordinates, coordinates[1:]):
        ax, ay, bx, by = a_lon * k, a_lat * 110570.0, b_lon * k, b_lat * 110570.0
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        if math.hypot(px - ax - t * dx, py - ay - t * dy) <= tol_m:
            return True
    return False


def _valhalla(origin, destination, exclusions=None, alternates=0):
    payload = {
        "locations": [
            {"lat": origin[0], "lon": origin[1]},
            {"lat": destination[0], "lon": destination[1]}
        ],
        "costing": "auto",
        "units": "miles"
    }
    if exclusions:
        payload["exclude_locations"] = exclusions
    if alternates:
        payload["alternates"] = alternates
    response = requests.post(f"{VALHALLA_URL}/route", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def get_route(origin, destination, explain=False):
    """Request a driving route from Valhalla.

    Avoids the road closures in effect for this run (simulation/road_events.py). With explain=True
    the result also carries `why`: the main roads used, the next-best alternative, and which
    closures the unobstructed fastest route would have used (with the extra minutes they cost).
    """

    exclusions = current_exclusions()
    result = _valhalla(origin, destination, exclusions, alternates=1 if explain else 0)
    trip = result["trip"]
    summary = trip["summary"]
    minutes = float(summary["time"]) / 60.0

    route = {
        "travel_minutes": minutes,
        "distance_miles": float(summary["length"]),
        "coordinates": _trip_coordinates(trip)
    }
    if not explain:
        return route
    vertex_minutes = _trip_vertex_minutes(trip)
    if len(vertex_minutes) == len(route["coordinates"]):
        route["vertex_minutes"] = [round(v, 4) for v in vertex_minutes]

    why = {"rule": "lowest-cost route on the road network (Valhalla car costing: driving time plus "
                   "penalties for turns, signals and minor roads)",
           "via": _trip_via(trip), "alternative": None, "avoided_closures": [], "unobstructed_minutes": None}
    alternates = result.get("alternates") or []
    if alternates:
        alt = alternates[0]["trip"]
        why["alternative"] = {"minutes": round(float(alt["summary"]["time"]) / 60.0, 2),
                              "extra_min": round(float(alt["summary"]["time"]) / 60.0 - minutes, 2),
                              "via": _trip_via(alt)}
    if exclusions:
        # Which closures pushed us off the normal fastest route, and what that cost.
        free = _valhalla(origin, destination)["trip"]
        free_coords = _trip_coordinates(free)
        why["unobstructed_minutes"] = round(float(free["summary"]["time"]) / 60.0, 2)
        for closure in current_closures():
            if any(passes_near(free_coords, (loc["lat"], loc["lon"])) for loc in closure["exclude_locations"]):
                why["avoided_closures"].append(closure.get("road") or closure.get("label") or closure.get("id"))
    route["why"] = why
    return route


def run_simulation(
    scenario_dir,
    ambulance_id="A001"
):
    """Run one ambulance through all transport patients."""

    scenario_dir = Path(scenario_dir).resolve()

    scenario = load_scenario(scenario_dir)

    # Independent in-memory state.
    state = copy.deepcopy(scenario)

    patients = state["patients"]
    ambulances = state["ambulances"]
    hospitals = state["hospitals"]

    incidents = pd.read_csv(
        scenario_dir / "incidents.csv"
    )

    stations = pd.read_csv(
        scenario_dir / "stations.csv"
    )

    if incidents.empty:
        raise ValueError(
            "incidents.csv contains no incidents"
        )

    incident = incidents.iloc[0]

    pickup = location(
        incident["pickup_latitude"],
        incident["pickup_longitude"]
    )

    ambulance_rows = ambulances[
        ambulances["ambulance_id"] == ambulance_id
    ]

    if len(ambulance_rows) != 1:
        raise ValueError(
            f"Ambulance {ambulance_id} "
            "not found uniquely"
        )

    ambulance = ambulance_rows.iloc[0]

    station_id = ambulance["station_id"]

    station_rows = stations[
        stations["station_id"] == station_id
    ]

    if len(station_rows) != 1:
        raise ValueError(
            f"Station {station_id} "
            "not found uniquely"
        )

    station = get_station_location(
        station_rows.iloc[0]
    )

    # Keep only the selected ambulance active.
    ambulances.loc[
        ambulances["ambulance_id"] != ambulance_id,
        "status"
    ] = "AT_STATION"

    events = []
    routes = []
    assignments = []

    current_time = float(
        ambulance["available_min"]
    )

    loading_minutes = float(
        state["config"].get(
            "loading_minutes", 5
        )
    )

    turnover_minutes = float(
        state["config"].get(
            "hospital_turnover_minutes", 60
        )
    )

    horizon = float(
        state["config"].get(
            "simulation_horizon_min", 1440
        )
    )

    def add_event(time, event_type, **details):
        events.append({
            "time_min": round(float(time), 3),
            "event": event_type,
            "ambulance_id": ambulance_id,
            **details
        })

    def record_drive(start, route, purpose):
        finish = start + route["travel_minutes"]

        routes.append({
            "ambulance_id": ambulance_id,
            "purpose": purpose,
            "start_min": round(start, 3),
            "end_min": round(finish, 3),
            "distance_miles": route["distance_miles"],
            "coordinates": route["coordinates"]
        })

        return finish

    # -----------------------------------------
    # Initial station dispatch
    # -----------------------------------------

    add_event(
        current_time,
        "DISPATCHED",
        station_id=station_id
    )

    initial_route = get_route(
        station,
        pickup
    )

    current_time = record_drive(
        current_time,
        initial_route,
        "STATION_TO_MCI"
    )

    add_event(
        current_time,
        "ARRIVED_AT_MCI"
    )

    # -----------------------------------------
    # Identify transport-required patients
    # -----------------------------------------

    transport_mask = (
        patients["triage"].str.upper().isin(
            ["RED", "YELLOW", "GREEN"]
        )
        &
        patients["requires_transport"]
        .astype(str)
        .str.upper()
        .eq("TRUE")
    )

    transport_patients = set(
        patients.loc[
            transport_mask,
            "patient_id"
        ]
    )

    if not transport_patients:
        raise ValueError(
            "No transport-required patients found"
        )

    delivered = set()

    # -----------------------------------------
    # Repeated assignment loop
    # -----------------------------------------

    while delivered != transport_patients:

        if current_time > horizon:
            raise RuntimeError(
                "Simulation exceeded its time horizon"
            )

        ambulances.loc[
            ambulances["ambulance_id"] == ambulance_id,
            "status"
        ] = "AVAILABLE_AT_MCI"

        hospital_routes = {}
        travel_times = {}

        # Calculate routes to hospitals
        # with available, non-reusable beds.
        for _, hospital in hospitals.iterrows():

            hospital_id = hospital["hospital_id"]

            if int(
                hospital["available_beds"]
            ) <= 0:
                continue

            try:
                destination = get_hospital_location(
                    hospital
                )

                route = get_route(
                    pickup,
                    destination
                )

                hospital_routes[
                    hospital_id
                ] = route

                travel_times[
                    hospital_id
                ] = route["travel_minutes"]

            except (
                ValueError,
                requests.RequestException
            ) as exc:

                print(
                    f"Skipping hospital "
                    f"{hospital_id}: {exc}",
                    flush=True
                )

        if not travel_times:
            raise RuntimeError(
                "No reachable hospitals "
                "with available beds"
            )

        # -------------------------------------
        # Gurobi optimization
        # -------------------------------------

        optimization_result = optimize_mci(
            state,
            travel_times,
            current_time=current_time
        )

        if (
            optimization_result["status"]
            != "OPTIMAL"
            or not optimization_result["assignments"]
        ):

            remaining = patients[
                patients["patient_id"].isin(
                    transport_patients - delivered
                )
            ]

            future = remaining[
                remaining["ready_min"] > current_time
            ]

            if not future.empty:
                current_time = float(
                    future["ready_min"].min()
                )
                continue

            raise RuntimeError(
                "Gurobi could not assign "
                "a remaining patient"
            )

        assignment = (
            optimization_result["assignments"][0]
        )

        patient_id = assignment["patient_id"]
        hospital_id = assignment["hospital_id"]

        hospital_mask = (
            hospitals["hospital_id"]
            == hospital_id
        )

        # Reserve one non-reusable bed.
        hospitals.loc[
            hospital_mask,
            "available_beds"
        ] -= 1

        patients.loc[
            patients["patient_id"] == patient_id,
            "status"
        ] = "ASSIGNED"

        add_event(
            current_time,
            "PATIENT_ASSIGNED",
            patient_id=patient_id,
            hospital_id=hospital_id,
            triage=assignment["triage"]
        )

        # -------------------------------------
        # Patient loading
        # -------------------------------------

        current_time += loading_minutes

        add_event(
            current_time,
            "LOADING_COMPLETE",
            patient_id=patient_id
        )

        # -------------------------------------
        # Hospital transport
        # -------------------------------------

        route = hospital_routes[
            hospital_id
        ]

        departure_time = current_time

        current_time = record_drive(
            current_time,
            route,
            "MCI_TO_HOSPITAL"
        )

        patients.loc[
            patients["patient_id"] == patient_id,
            "status"
        ] = "DELIVERED"

        delivered.add(patient_id)

        assignments.append({
            "patient_id": patient_id,
            "ambulance_id": ambulance_id,
            "hospital_id": hospital_id,
            "triage": assignment["triage"],
            "loading_complete_min": round(
                departure_time, 3
            ),
            "hospital_arrival_min": round(
                current_time, 3
            )
        })

        add_event(
            current_time,
            "PATIENT_DELIVERED",
            patient_id=patient_id,
            hospital_id=hospital_id
        )

        # Makespan ends when the final
        # transport-required patient arrives.
        if delivered == transport_patients:
            break

        # -------------------------------------
        # Hospital turnover
        # -------------------------------------

        current_time += turnover_minutes

        add_event(
            current_time,
            "TURNOVER_COMPLETE",
            hospital_id=hospital_id
        )

        # -------------------------------------
        # Return to MCI
        # -------------------------------------

        hospital_location = get_hospital_location(
            hospitals.loc[
                hospital_mask
            ].iloc[0]
        )

        return_route = get_route(
            hospital_location,
            pickup
        )

        current_time = record_drive(
            current_time,
            return_route,
            "HOSPITAL_TO_MCI"
        )

        add_event(
            current_time,
            "RETURNED_TO_MCI"
        )

    # -----------------------------------------
    # Simulation results
    # -----------------------------------------

    makespan = max(
        item["hospital_arrival_min"]
        for item in assignments
    )

    result = {
        "status": "COMPLETED",
        "scenario": scenario_dir.name,
        "ambulance_id": ambulance_id,
        "makespan_min": round(makespan, 3),
        "patients_delivered": len(delivered),
        "transport_patients": len(
            transport_patients
        ),
        "assignments": assignments,
        "events": events,
        "routes": routes
    }

    # -----------------------------------------
    # Unique run history
    # -----------------------------------------

    # -----------------------------------------
    # Scenario-specific run history
    # -----------------------------------------

    scenario_name = scenario_dir.name

    scenario_output_dir = OUTPUT_DIR / scenario_name

    scenario_output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    run_id = (
        f"run_{timestamp}_"
        f"{uuid4().hex[:8]}"
    )

    run_dir = scenario_output_dir / run_id

    run_dir.mkdir(
        parents=True,
        exist_ok=False
    )

    result["run_id"] = run_id

    output_path = run_dir / "simulation_results.json"

    output_path.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8"
    )

    # Preserve the original scenario inputs.
    shutil.copytree(
        scenario_dir,
        run_dir / "scenario_snapshot"
    )

    # Generate reports.
    reports = generate_reports(run_dir)

    print("Excel report:", reports["excel"])
    print("Patient CSV:", reports["patients_csv"])
    print(
        "Validation:",
        reports["summary"]["validation_status"]
    )

    print("\nSimulation completed!")
    print(f"Patients delivered: {len(delivered)}")
    print(f"MCI makespan: {makespan:.2f} minutes")
    print(f"Run ID: {run_id}")
    print(f"Saved: {output_path}")

    return result



def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scenario",
        default="data/scenarios/camden_yards"
    )

    parser.add_argument(
        "--ambulance",
        default="A001"
    )

    args = parser.parse_args()

    run_simulation(
        args.scenario,
        args.ambulance
    )


if __name__ == "__main__":
    main()
