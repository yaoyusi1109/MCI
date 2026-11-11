"""Road closures applied to Valhalla routing: real ones (Aug 12, 2014 Baltimore flash flood) and
road blocks a user places on the map.

closures.json is produced by data/flood_2014/locate_closures.py: each NWS closure report placed on the
road it names, as Valhalla `exclude_locations`. A closure is in effect from its report time onward;
reports carry no reopening time, so a closure stays closed for the rest of the run.

The closures in effect live in a context variable, so one request's closures never leak into another
request handled on a different thread. A run can add closures as its clock passes their start time
(add_closure); every later routing call in that run then avoids them.
"""

import contextvars
import json
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

CLOSURES_FILE = Path(__file__).resolve().parents[1] / "data" / "flood_2014" / "closures.json"
EVENT_DATE = "2014-08-12"

_active = contextvars.ContextVar("active_road_closures", default=None)


@lru_cache(maxsize=1)
def all_closures():
    return json.loads(CLOSURES_FILE.read_text())["closures"]


def parse_clock(value):
    """'14:30' -> '14:30'; raises ValueError for anything else."""
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("flood_at must be a 24-hour time such as '14:30'")
    return value


def clock_minutes(clock):
    h, m = parse_clock(clock).split(":")
    return int(h) * 60 + int(m)


def closures_at(clock):
    """Flood closures already reported by `clock` (HH:MM, local time on the event day)."""
    clock = parse_clock(clock)
    return [c for c in all_closures() if c["time_local"][11:16] <= clock]


def closures_after(clock):
    """Flood closures reported after `clock`, with `at_min` = minutes after `clock`."""
    start = clock_minutes(clock)
    return [{**c, "at_min": clock_minutes(c["time_local"][11:16]) - start}
            for c in all_closures() if c["time_local"][11:16] > parse_clock(clock)]


def label(closure):
    return closure.get("road") or closure.get("label") or closure.get("id", "road closure")


def exclusions_for(closures):
    return [loc for c in closures for loc in c["exclude_locations"]]


@contextmanager
def use_closures(closures):
    token = _active.set([dict(c) for c in closures])
    try:
        yield
    finally:
        _active.reset(token)


def add_closure(closure):
    """Put one more closure in effect for the rest of the current run."""
    active = _active.get()
    if active is None:
        raise RuntimeError("add_closure needs an enclosing use_closures()")
    active.append(dict(closure))


def current_closures():
    return list(_active.get() or [])


def current_exclusions():
    return exclusions_for(current_closures()) or None
