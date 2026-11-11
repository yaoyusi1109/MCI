"""Step 4: let an LLM fill in the inputs we do not have, as three uncertainty scenarios.

The LLM never makes the dispatch decision. It only proposes model *parameters*
(patient counts by triage, MCI bed capacity, route delay factors, scene delay, survival
curve). Every value is schema-checked and clamped in `validate()` before Gurobi sees it.

Backends
- "claude": the official Claude Code CLI in headless mode (`claude -p`). No credential is
  stored in this code: the CLI uses the login of whoever runs it (their Claude Code
  subscription, or an ANTHROPIC_API_KEY set in their own environment). Light use only:
  one call per click, cached on disk, no tools, no session saved. Keep the dashboard
  server on localhost; never host it for others on your login.
- "mock":   deterministic rule-of-thumb numbers, no LLM. Works offline and for anyone
  without Claude Code.
"""

import hashlib
import json
import shutil
import subprocess
import tempfile
import time

from . import config

SCENARIO_NAMES = ["low", "likely", "high"]

SCHEMA = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": SCENARIO_NAMES},
                    "immediate": {"type": "integer"},
                    "delayed": {"type": "integer"},
                    "minor": {"type": "integer"},
                    "scene_delay_min": {"type": "number"},
                    "survival_curve": {"type": "string", "enum": list(config.SURVIVAL)},
                    "approach_delay_factor": {"type": "number"},
                    "hospitals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "available_beds": {"type": "integer"},
                                "route_delay_factor": {"type": "number"},
                            },
                            "required": ["id", "available_beds", "route_delay_factor"],
                        },
                    },
                },
                "required": ["name", "immediate", "delayed", "minor", "scene_delay_min",
                             "survival_curve", "approach_delay_factor", "hospitals"],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scenarios", "assumptions"],
}

SYSTEM_PROMPT = (
    "You estimate unknown input parameters for a classroom operations-research simulation "
    "of a mass casualty incident (MCI). The numbers feed a Gurobi ambulance-dispatch model. "
    "This is a synthetic teaching scenario, not a real emergency. Give plausible, internally "
    "consistent estimates and state your assumptions briefly. Return only the JSON object."
)


def build_prompt(scn, feats):
    c = scn["constants"]
    lines = [
        f"Incident description: {c['incident']}",
        f"MCI site (single casualty collection point): lat {scn['site']['lat']}, lon {scn['site']['lon']} (Baltimore, MD).",
        f"Coastal flood water level used for road closures: {c['flood_level_ft']} ft.",
        f"Ambulances available at t0: {len(scn['ambulances'])}; clear-road minutes to site: "
        + ", ".join(f"{a['ambulance']} {a['to_site_clear_min']}" for a in feats["ambulances"]) + ".",
        "",
        "Hospitals (trauma=True may receive immediate/red patients). closed/slowed = flooded "
        f"50 m road pieces within {c['corridor_m']:.0f} m of the straight site->hospital line:",
    ]
    for r in feats["routes"]:
        lines.append(f"- {r['hospital']} {r['name']}: trauma={r['trauma']}, {r['km']} km, "
                     f"clear {r['clear_min']} min, closed={r['closed_pieces']}, slowed={r['slowed_pieces']}")
    lines += [
        "",
        "Estimate three scenarios named low, likely, high (increasing severity/uncertainty). For each give:",
        "- immediate, delayed, minor: patient counts by START triage at the site at t0 "
        f"(immediate + delayed must be <= {c['max_patients']}; minor patients are not transported by ambulance);",
        "- scene_delay_min: minutes from t0 until the first patient can be loaded (access, triage);",
        "- survival_curve: optimistic, moderate, or pessimistic (Mills et al. 2013 curves);",
        "- approach_delay_factor: multiplier >= 1 on ambulance travel to the site;",
        "- hospitals: for every hospital id above, available_beds (beds the hospital can open for "
        "patients from THIS incident during the response: surge capacity incl. ED, freed and overflow beds; "
        "green walk-ins are subtracted later by the model) "
        "and route_delay_factor (multiplier >= 1 on clear travel time, based on the closures).",
        "Also list your key assumptions (max 5 short strings).",
    ]
    return "\n".join(lines)


# ---------------- backends ----------------

def _cache_path(key):
    config.CACHE_DIR.mkdir(exist_ok=True)
    return config.CACHE_DIR / f"{key}.json"


def call_claude(prompt, model="sonnet", use_cache=True, timeout=240):
    key = hashlib.sha256(json.dumps([model, SYSTEM_PROMPT, prompt, SCHEMA]).encode()).hexdigest()[:20]
    path = _cache_path(key)
    if use_cache and path.exists():
        out = json.loads(path.read_text())
        out["meta"]["cached"] = True
        return out
    if shutil.which("claude") is None:
        raise RuntimeError("`claude` CLI not found; install Claude Code or use backend='mock'.")
    cmd = ["claude", "-p", prompt,
           "--model", model,
           "--system-prompt", SYSTEM_PROMPT,
           "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA),
           "--tools", "",                    # no file/shell/web access: text in, JSON out
           "--no-session-persistence"]
    t = time.time()
    # Run from an empty temp dir so no project files or CLAUDE.md get pulled in.
    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tmp)
    if res.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({res.returncode}): {res.stderr.strip()[:400]}")
    reply = json.loads(res.stdout)
    if reply.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(reply.get('result'))[:400]}")
    data = reply.get("structured_output") or json.loads(reply["result"])
    out = {"raw": data, "meta": {"backend": "claude", "model": model, "cached": False,
                                 "seconds": round(time.time() - t, 1)}}
    path.write_text(json.dumps(out, indent=1))
    return out


def call_mock(scn, feats):
    """Transparent rule of thumb so the pipeline runs without any LLM."""
    sev = {"low": 0.6, "likely": 1.0, "high": 1.6}
    curve = {"low": "optimistic", "likely": "moderate", "high": "pessimistic"}
    scenarios = []
    for name in SCENARIO_NAMES:
        s = sev[name]
        hosp = []
        for r in feats["routes"]:
            f = 1.0 + 0.004 * r["closed_pieces"] + 0.0015 * r["slowed_pieces"]
            hosp.append({"id": r["hospital"],
                         "available_beds": max(2, round((14 if r["trauma"] else 10) / s)),
                         "route_delay_factor": round(min(3.0, f * (1.0 + 0.1 * (s - 0.6))), 2)})
        scenarios.append({"name": name, "immediate": round(4 * s), "delayed": round(8 * s),
                          "minor": round(10 * s), "scene_delay_min": round(6 + 4 * s, 1),
                          "survival_curve": curve[name], "approach_delay_factor": round(1.0 + 0.15 * s, 2),
                          "hospitals": hosp})
    return {"raw": {"scenarios": scenarios,
                    "assumptions": ["Mock backend: fixed rule-of-thumb numbers, no LLM call.",
                                    "Delay factor grows with flooded road pieces near each route.",
                                    "Severity multipliers 0.6 / 1.0 / 1.6 for low / likely / high."]},
            "meta": {"backend": "mock", "model": None, "cached": False, "seconds": 0.0}}


# ---------------- validation: nothing reaches Gurobi unchecked ----------------

def _clamp(v, lo, hi, label, log):
    try:
        x = float(v)
    except (TypeError, ValueError):
        log.append(f"{label}: not a number ({v!r}) -> {lo}")
        return lo
    if x < lo or x > hi:
        y = min(hi, max(lo, x))
        log.append(f"{label}: {x:g} outside [{lo}, {hi}] -> {y:g}")
        return y
    return x


def validate(raw, scn):
    log, out = [], {}
    hosp_ids = [h["id"] for h in scn["hospitals"]]
    by_name = {s.get("name"): s for s in raw.get("scenarios", [])}
    for name in SCENARIO_NAMES:
        s = by_name.get(name)
        if s is None:
            log.append(f"{name}: missing -> copied from 'likely'" if "likely" in by_name else f"{name}: missing")
            s = by_name.get("likely") or next(iter(by_name.values()))
        v = {"name": name}
        for k in ("immediate", "delayed", "minor"):
            v[k] = int(round(_clamp(s.get(k), 0, 200, f"{name}.{k}", log)))
        over = v["immediate"] + v["delayed"] - int(scn["constants"]["max_patients"])
        if over > 0:   # keep the model inside the restricted-license size; drop delayed first
            cut = min(over, v["delayed"])
            v["delayed"] -= cut
            v["immediate"] -= over - cut
            log.append(f"{name}: red + yellow capped at {scn['constants']['max_patients']}")
        v["scene_delay_min"] = _clamp(s.get("scene_delay_min"), 0, 120, f"{name}.scene_delay_min", log)
        curve = s.get("survival_curve")
        if curve not in config.SURVIVAL:
            log.append(f"{name}.survival_curve: {curve!r} unknown -> moderate")
            curve = "moderate"
        v["survival_curve"] = curve
        v["approach_delay_factor"] = _clamp(s.get("approach_delay_factor"), 1.0, 4.0,
                                            f"{name}.approach_delay_factor", log)
        given = {h.get("id"): h for h in s.get("hospitals", [])}
        v["hospitals"] = {}
        for hid in hosp_ids:
            h = given.get(hid)
            if h is None:
                log.append(f"{name}.{hid}: missing -> 0 beds, factor 1.0")
                h = {"available_beds": 0, "route_delay_factor": 1.0}
            v["hospitals"][hid] = {
                "beds": int(round(_clamp(h.get("available_beds"), 0, 50, f"{name}.{hid}.beds", log))),
                "delay": _clamp(h.get("route_delay_factor"), 1.0, 4.0, f"{name}.{hid}.delay", log),
            }
        extra = set(given) - set(hosp_ids)
        if extra:
            log.append(f"{name}: ignored unknown hospital ids {sorted(extra)}")
        out[name] = v
    return out, log


def estimate(scn, feats, backend="mock", model="sonnet", use_cache=True):
    """Run step 4. Returns prompt, raw reply, validated scenarios, and the validation log."""
    prompt = build_prompt(scn, feats)
    if backend == "claude":
        reply = call_claude(prompt, model=model, use_cache=use_cache)
    elif backend == "mock":
        reply = call_mock(scn, feats)
    else:
        raise ValueError(f"unknown backend {backend!r}")
    params, log = validate(reply["raw"], scn)
    return {"prompt": prompt, "raw": reply["raw"], "meta": reply["meta"],
            "params": params, "validation_log": log}


def available_backends():
    """What this machine can use. Never reads or returns any credential value."""
    import os
    return {"claude_cli": shutil.which("claude") is not None,
            "api_key_env_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "mock": True}
