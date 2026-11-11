"""Command-line version of the t0 demo: prints each pipeline step.

Defaults are the fixed scenario in mci_ai/config.py. Override anything:

    python run_demo.py                                   # defaults, mock AI (no LLM)
    python run_demo.py --backend claude                  # AI step via your Claude Code CLI login
    python run_demo.py --site 39.285,-76.61 --hospitals H1,H2,H6
    python run_demo.py --ambulances "39.31,-76.64;39.27,-76.66;39.33,-76.58"
    python run_demo.py --set speed_kmh=30 --set flood_level_ft=6 --set load_min=4
    python run_demo.py --json out.json                   # save everything for a notebook
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mci_ai import config, pipeline, scenario  # noqa: E402


def _value(text, like):
    if isinstance(like, bool):
        return text.strip().lower() in ("1", "true", "yes", "y")
    if isinstance(like, (int, float)):
        return type(like)(float(text))
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", help="lat,lon of the MCI site")
    ap.add_argument("--ambulances", help='"lat,lon;lat,lon;..." ambulance positions')
    ap.add_argument("--hospitals", help="comma-separated target hospital ids, e.g. H1,H2,H6")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a constant or assumption from config.py (repeatable)")
    ap.add_argument("--backend", choices=["mock", "claude"], default="mock")
    ap.add_argument("--model", default="sonnet", help="Claude model alias for --backend claude")
    ap.add_argument("--no-cache", action="store_true", help="force a fresh AI call")
    ap.add_argument("--json", help="write the full result to this file")
    args = ap.parse_args()

    known = {**config.ASSUMPTIONS, **config.DEFAULT_CONSTANTS}
    constants = {}
    for kv in args.set:
        k, _, v = kv.partition("=")
        if k not in known:
            ap.error(f"unknown key {k!r}; choose from {sorted(known)}")
        constants[k] = _value(v, known[k])
    site = None
    if args.site:
        lat, lon = (float(v) for v in args.site.split(","))
        site = {"lat": lat, "lon": lon}
    ambulances = None
    if args.ambulances:
        ambulances = []
        for i, pair in enumerate(args.ambulances.split(";")):
            lat, lon = (float(v) for v in pair.split(","))
            ambulances.append({"id": f"A{i + 1}", "lat": lat, "lon": lon})
    hospital_ids = args.hospitals.split(",") if args.hospitals else None

    print("== Steps 1-3: scenario ==")
    scn = scenario.make_scenario(site=site, ambulances=ambulances, hospital_ids=hospital_ids, constants=constants)
    print(f"site {scn['site']}; ambulances: " + ", ".join(f"{a['id']}({a['lat']},{a['lon']})" for a in scn["ambulances"]))
    print("target hospitals: " + ", ".join(f"{h['id']} {h['name']}{' [trauma]' if h['trauma'] else ''}" for h in scn["hospitals"]))
    print("constants: " + ", ".join(f"{k}={v}" for k, v in scn["constants"].items() if k != "incident"))
    feats = pipeline.step_features(scn)
    for r in feats["routes"]:
        print(f"  {r['hospital']}: {r['km']} km, clear {r['clear_min']} min, "
              f"flooded road pieces near route: closed {r['closed_pieces']}, slowed {r['slowed_pieces']}")

    print(f"\n== Step 4: AI estimates ({args.backend}) ==")
    ai = pipeline.step_ai(scn, feats, backend=args.backend, model=args.model, use_cache=not args.no_cache)
    print(f"meta: {ai['meta']}")
    for a in ai["raw"].get("assumptions", []):
        print(f"  assumption: {a}")
    for name, p in ai["params"].items():
        beds = " ".join(f"{h}:{v['beds']}b x{v['delay']:.2f}" for h, v in p["hospitals"].items())
        print(f"  {name:6s} red {p['immediate']:2d} yellow {p['delayed']:2d} green {p['minor']:2d} "
              f"TD {p['scene_delay_min']:.0f} min, {p['survival_curve']}, approach x{p['approach_delay_factor']:.2f} | {beds}")
    print("  validation: " + ("; ".join(ai["validation_log"]) or "all values accepted"))

    print("\n== Step 5: Gurobi ==")
    plans = pipeline.step_solve(scn, ai["params"])
    for name, res in plans.items():
        print(f"\n[{name}] {res['status_name']} in {res['seconds']} s, size {res['size']}")
        if "trips" not in res:
            continue
        print(f"  expected survivors {res['expected_survivors']} of {res['transported']} transported; "
              f"unserved {res['unserved']}; mean arrival {res['mean_arrival']}; last arrival {res['makespan']} min")
        if name == "likely":
            for t in res["trips"]:
                print(f"   {t['ambulance']} trip {t['trip']}: {t['patient']:>3s} pickup {t['pickup']:6.1f} -> "
                      f"{t['hospital']} at {t['hospital_arrival']:6.1f}  P(survive) {t['survival']:.2f}")
            print("  beds: " + ", ".join(f"{h} {v['used']}/{v['usable']} (walk-ins {v['walk_ins']})" for h, v in res["beds"].items()))

    if args.json:
        Path(args.json).write_text(json.dumps({"scenario": scn, "features": feats, "ai": ai, "plans": plans}, indent=1))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
