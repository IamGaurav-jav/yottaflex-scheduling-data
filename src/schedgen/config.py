"""Generator configuration and domain constants."""
from dataclasses import dataclass, replace

from .schema import GROUPS

# machine group -> (count weight, cycle min/unit lo-hi, fixed min lo-hi, P(2 operators),
#                   P(needs shared tool), base setup min, has tooling)
GROUP_SPEC = {
    "SAW": (4, (0.8, 1.8), (10, 25), 0.15, 0.0, 12, False),
    "TURN": (6, (1.2, 3.0), (15, 35), 0.20, 0.45, 20, True),
    "MILL": (8, (1.5, 3.5), (15, 40), 0.25, 0.50, 25, True),
    "GRIND": (4, (1.0, 2.5), (10, 30), 0.20, 0.40, 30, True),
    "WELD": (5, (1.5, 3.2), (15, 35), 0.35, 0.30, 20, True),
    "HEAT": (2, (0.5, 1.2), (30, 60), 0.10, 0.0, 40, False),
    "PAINT": (3, (0.6, 1.5), (15, 30), 0.15, 0.0, 35, False),
    "ASSY": (5, (1.5, 3.2), (15, 30), 0.40, 0.35, 10, True),
    "TEST": (3, (0.6, 1.5), (10, 20), 0.30, 0.50, 5, True),
}
assert set(GROUP_SPEC) == set(GROUPS)

# product category -> multiplicative affinity per machine group (shapes routings)
CATEGORY_AFFINITY = {
    "SHAFT": {"TURN": 2.0, "GRIND": 2.0, "HEAT": 1.5},
    "HOUSING": {"SAW": 1.5, "MILL": 2.0, "PAINT": 1.5},
    "GEAR": {"MILL": 1.5, "HEAT": 2.0, "GRIND": 1.5, "TEST": 1.2},
    "BRACKET": {"SAW": 2.0, "WELD": 2.0, "PAINT": 1.5},
    "VALVE": {"TURN": 1.5, "MILL": 1.5, "ASSY": 2.0, "TEST": 2.0},
    "FLANGE": {"SAW": 1.5, "TURN": 1.5, "WELD": 1.5, "TEST": 1.2},
}

PLANT_HOLIDAYS = (22,)  # horizon day index with no staffed shifts (Tue 24 Nov 2026)
MAX_STD_PROCESS_MIN = 300  # keeps every op shorter than a single shift


@dataclass(frozen=True)
class Config:
    seed: int = 20261022
    skus: int = 100
    machines: int = 40
    orders: int = 500
    materials: int = 200
    ops_per_order: float = 4.0
    horizon_weeks: int = 5
    anomalies_per_type: int = 10
    inject_anomalies: bool = True

    @property
    def ops(self):
        return int(round(self.orders * self.ops_per_order))

    @property
    def horizon_min(self):
        return self.horizon_weeks * 7 * 1440


PRESETS = {
    "full": Config(),
    "starter": Config(skus=20, machines=8, orders=100, materials=40, anomalies_per_type=3),
}


def with_overrides(cfg, **kw):
    kw = {k: v for k, v in kw.items() if v is not None}
    return replace(cfg, **kw)
