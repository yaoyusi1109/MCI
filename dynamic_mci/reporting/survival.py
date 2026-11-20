"""Survival curves used to score hospital arrivals (reporting only; dispatch never reads them).

P(survive | arrival t) = b0 / ((t / b1) ** b2 + 1)  (Chang et al. 2026 / Mills et al. 2013).
"""

SURVIVAL_CURVES = {
    'pessimistic': {'RED': (0.09, 17, 1.01), 'YELLOW': (0.57, 61, 2.03)},
    'moderate':    {'RED': (0.24, 47, 1.30), 'YELLOW': (0.76, 138, 2.17)},
    'optimistic':  {'RED': (0.56, 91, 1.58), 'YELLOW': (0.81, 160, 2.41)},
}


def survival_probability(triage, arrival_min, curve='moderate'):
    """Return P(survive) for a RED/YELLOW arrival, or None for other triage levels."""
    params = SURVIVAL_CURVES[curve].get(str(triage or '').upper())
    if params is None or arrival_min is None:
        return None
    b0, b1, b2 = params
    return b0 / ((max(float(arrival_min), 0.0) / b1) ** b2 + 1.0)
