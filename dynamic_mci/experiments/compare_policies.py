"""
Run every dispatch policy on every scenario and tabulate outcomes.

For each scenario, rolling triage runs once and every policy reuses that
triage result, so policies differ only in their dispatch choices.

Usage (from dynamic_mci/):
    python -m experiments.compare_policies
    python -m experiments.compare_policies --scenarios stress_trauma_bed_trap \
        --policies gurobi severe_first closest_first --seeds 10
    python -m experiments.compare_policies --routing approx   # no Valhalla

--routing valhalla (default) uses the same road travel times as the dashboard.
--routing approx replaces them with straight-line distance x 1.4 detour at
25 mph. Use approx only for quick offline checks; its numbers are not
comparable with Valhalla runs, and the output CSV records which was used.

Output: outputs/policy_comparison/<timestamp>/runs.csv and summary.csv
"""

import argparse
import contextlib
import csv
import io
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from reporting.survival import SURVIVAL_CURVES, survival_probability
from simulation import integrated_scheduler, triage_engine
from simulation.baseline_dispatch import POLICIES

BASE = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = BASE / "data" / "scenarios"
DYNAMIC_FILES = ("patient_dynamics.csv", "response_units.csv", "ems_personnel.csv")


def approx_route(origin, destination):
    """Straight-line route: haversine distance x 1.4 detour at 25 mph."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    miles = 2 * 3958.8 * math.asin(math.sqrt(a)) * 1.4
    return {
        "travel_minutes": miles / 25.0 * 60.0,
        "distance_miles": miles,
        "coordinates": [list(origin), list(destination)],
        "provider": "approx",
    }


def dynamic_scenarios():
    return sorted(
        p.name for p in SCENARIOS_DIR.iterdir()
        if p.is_dir() and all((p / f).is_file() for f in DYNAMIC_FILES)
    )


def metrics(raw, curve):
    s = raw["summary"]
    assignments = raw["assignments"]
    patients = raw["patients"]
    by_triage = {k: 0 for k in ("RED", "YELLOW", "GREEN")}
    survival = 0.0
    red_arrivals = []
    for a in assignments:
        tri = str(a.get("triage_at_assignment") or "").upper()
        if tri in by_triage:
            by_triage[tri] += 1
        p = survival_probability(tri, a.get("hospital_arrival_min"), curve)
        if p is not None:
            survival += p
        if tri == "RED":
            red_arrivals.append(float(a["hospital_arrival_min"]))
    initially_urgent = sum(p["initial"] in ("RED", "YELLOW") for p in patients)
    return {
        "delivered": s["delivered"],
        "delivered_red": by_triage["RED"],
        "delivered_yellow": by_triage["YELLOW"],
        "delivered_green": by_triage["GREEN"],
        "deteriorated_to_black": s.get("deteriorated_to_black"),
        "deterioration_transitions": s.get("deterioration_transitions"),
        "expectant": s["expectant"],
        "still_waiting": s["still_awaiting_or_in_transport"],
        "makespan_min": s["observed_makespan_min"],
        "mean_arrival_min": round(statistics.mean(
            float(a["hospital_arrival_min"]) for a in assignments), 2)
        if assignments else None,
        "last_red_arrival_min": max(red_arrivals) if red_arrivals else None,
        "expected_survivors": round(survival, 3),
        "initially_red_or_yellow": initially_urgent,
        "validation": s["validation"]["status"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="*", help="default: every dynamic scenario")
    ap.add_argument("--policies", nargs="*", default=list(POLICIES), choices=POLICIES)
    ap.add_argument("--seeds", type=int, default=5, help="random-policy seeds per scenario")
    ap.add_argument("--horizon", type=float, default=240.0)
    ap.add_argument("--curve", default="moderate", choices=list(SURVIVAL_CURVES))
    ap.add_argument("--routing", default="valhalla", choices=["valhalla", "approx"])
    ap.add_argument("--verbose", action="store_true", help="show simulator prints")
    args = ap.parse_args()

    if args.routing == "approx":
        integrated_scheduler.get_route = approx_route
        triage_engine.get_route = approx_route

    scenarios = args.scenarios or dynamic_scenarios()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = BASE / "outputs" / "policy_comparison" / stamp
    triage_dir = out_dir / "triage"
    triage_dir.mkdir(parents=True)

    def quiet():
        # The simulators print progress; hide it unless --verbose.
        return (contextlib.nullcontext() if args.verbose
                else contextlib.redirect_stdout(io.StringIO()))

    rows = []
    for name in scenarios:
        scen = SCENARIOS_DIR / name
        with quiet():
            triage = triage_engine.run_triage(scen, include_fire=True, save=False)
        if triage.get("validation", {}).get("status") != "PASS":
            print(f"[skip] {name}: triage validation failed")
            continue
        triage_file = triage_dir / f"{name}.json"
        triage_file.write_text(json.dumps(triage, indent=2), encoding="utf-8")

        for policy in args.policies:
            seeds = range(args.seeds) if policy == "random" else [0]
            for seed in seeds:
                with quiet():
                    raw = integrated_scheduler.run(
                        scen, triage_file, horizon=args.horizon, save=False,
                        policy=policy, seed=seed)
                row = {"scenario": name, "policy": policy, "seed": seed,
                       "routing": args.routing, "curve": args.curve,
                       **metrics(raw, args.curve)}
                rows.append(row)
                print(f"{name:34s} {policy:22s} seed={seed} "
                      f"delivered={row['delivered']:>2} "
                      f"to_black={row['deteriorated_to_black']:>2} "
                      f"survivors={row['expected_survivors']:.2f} "
                      f"makespan={row['makespan_min']}")

    if not rows:
        print("No runs completed.")
        return
    with (out_dir / "runs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Average the random seeds so summary has one row per scenario x policy.
    numeric = ["delivered", "delivered_red", "delivered_yellow", "deteriorated_to_black",
               "deterioration_transitions", "still_waiting", "makespan_min",
               "mean_arrival_min", "last_red_arrival_min", "expected_survivors"]
    summary = []
    for name in dict.fromkeys(r["scenario"] for r in rows):
        for policy in args.policies:
            group = [r for r in rows if r["scenario"] == name and r["policy"] == policy]
            if not group:
                continue
            entry = {"scenario": name, "policy": policy, "runs": len(group)}
            for k in numeric:
                vals = [r[k] for r in group if r[k] is not None]
                entry[k] = round(statistics.mean(vals), 3) if vals else None
            summary.append(entry)
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print(f"\nSaved: {out_dir / 'runs.csv'}\n       {out_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
