"""Business formulas shared by the generator and the validator so that
derived values are computed in exactly one way."""
import math


def std_process_minutes(qty, cycle_min_per_unit, fixed_min):
    return int(math.ceil(round(qty * cycle_min_per_unit, 6))) + int(fixed_min)


def process_minutes(std_min, speed_factor):
    """ceil(std / speed) using integer arithmetic on a 2-decimal speed factor."""
    sp = int(round(speed_factor * 100))
    return -((-std_min * 100) // sp)


def required_qty(op_qty, qty_per_unit, scrap_pct):
    return int(math.ceil(round(op_qty * qty_per_unit * (1 + scrap_pct / 100.0), 6)))
