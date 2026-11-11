"""Thin local backend for the demo dashboard. All modelling lives in ../mci_ai.

    ~/Documents/JHU26_venv/bin/python dashboard/server.py      # then open http://127.0.0.1:8765

Needs gurobipy in that Python. The "AI estimate" button uses the `claude` CLI of whoever
runs this server (their own login or their own ANTHROPIC_API_KEY); no credential is stored
here. The server listens on 127.0.0.1 only. Do not change that to share it: anyone reaching
it would spend the runner's Claude usage. Teammates run their own copy instead.

Endpoints (JSON in / JSON out; stateless, the page sends the full inputs every time):
  GET  /api/defaults   fixed defaults, hospital list, what AI backends exist here
  POST /api/features   steps 1-3: scenario + travel times + flooded roads per route
  POST /api/ai         step 4: AI (or mock) estimates, validated
  POST /api/solve      step 5: Gurobi plan for one scenario (low / likely / high)
"""

import json
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mci_ai import ai_estimator, config, gurobi_model, scenario  # noqa: E402

HOST, PORT = "127.0.0.1", 8765


def _scenario(body):
    return scenario.make_scenario(site=body.get("site"), ambulances=body.get("ambulances"),
                                  hospital_ids=body.get("hospital_ids"), constants=body.get("constants"))


def api_defaults(_body):
    return {"site": config.DEFAULT_SITE, "ambulances": config.DEFAULT_AMBULANCES,
            "hospitals": config.HOSPITALS, "hospital_ids": config.DEFAULT_HOSPITAL_IDS,
            "constants": config.DEFAULT_CONSTANTS, "assumptions": config.ASSUMPTIONS,
            "survival_curves": list(config.SURVIVAL), "backends": ai_estimator.available_backends()}


def api_features(body):
    scn = _scenario(body)
    return {"scenario": scn, "features": scenario.route_features(scn)}


def api_ai(body):
    scn = _scenario(body)
    feats = scenario.route_features(scn)
    return ai_estimator.estimate(scn, feats, backend=body.get("backend", "mock"),
                                 model=body.get("model") or "sonnet", use_cache=not body.get("fresh"))


def api_solve(body):
    scn = _scenario(body)
    params, log = ai_estimator.validate(body["raw"], scn)       # same checks as AI output
    name = body.get("name", "likely")
    data = gurobi_model.build_data(scn, params[name])
    res = gurobi_model.solve(data)
    res["validation_log"] = log
    res["inputs"] = params[name]
    return res


ROUTES = {"/api/defaults": api_defaults, "/api/features": api_features,
          "/api/ai": api_ai, "/api/solve": api_solve}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api(self, body):
        fn = ROUTES.get(self.path.split("?")[0])
        if fn is None:
            return self._send(404, {"error": "unknown endpoint"})
        try:
            self._send(200, fn(body))
        except Exception as e:  # report to the page instead of crashing the server
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api({})
        if self.path in ("/", ""):
            self.path = "/demo.html"
        if self.path.split("?")[0] == "/road_closures_data.js":  # the repo-root copy, see config.ROAD_DATA_PATH
            data = config.ROAD_DATA_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._api(json.loads(self.rfile.read(n) or b"{}"))

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write(f"{self.command} {self.path}\n")


if __name__ == "__main__":
    print(f"MCI demo dashboard: http://{HOST}:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
