"""Stage 1: master / parent tables. Everything here is generated before any
transactional record, and every child references an already existing parent."""
import math
import random
from datetime import date, timedelta

from .config import (CATEGORY_AFFINITY, GROUP_SPEC, MAX_STD_PROCESS_MIN, PLANT_HOLIDAYS)
from .schema import CATEGORIES, FAMILIES, GROUPS, MATERIAL_CATEGORIES
from .timeutil import DAY, HORIZON_START, date_str, to_iso, weekday_of_day

STEP_COUNT_CHOICES = (2, 3, 4, 5, 6)
STEP_COUNT_WEIGHTS = (1, 2, 3, 2, 1)  # mean 4.0 steps per routing
V2_EFFECTIVE_FROM = date(2026, 11, 8)  # early-horizon routing change, so both versions get orders
SHIFT_DEFS = {"SHIFT-A": ("Day", "06:00", 480), "SHIFT-B": ("Evening", "14:00", 480),
              "SHIFT-C": ("Night", "22:00", 480)}
PATTERN_SHIFTS = {"P1": ("SHIFT-A",), "P2": ("SHIFT-A", "SHIFT-B"),
                  "P3": ("SHIFT-A", "SHIFT-B"), "P4": ("SHIFT-A", "SHIFT-B", "SHIFT-C")}
PATTERN_WEIGHTS = (0.15, 0.45, 0.25, 0.15)


def make_rng(seed, stage):
    return random.Random(f"{seed}:{stage}")


def allocate_machines(n, weights):
    """Largest-remainder allocation of n machines to groups."""
    total = sum(weights.values())
    quota = {g: n * w / total for g, w in weights.items()}
    alloc = {g: int(math.floor(q)) for g, q in quota.items()}
    order = sorted(quota, key=lambda g: (-(quota[g] - alloc[g]), list(GROUPS).index(g)))
    for g in order[: n - sum(alloc.values())]:
        alloc[g] += 1
    return alloc


def weighted_sample_distinct(r, items, weights, k):
    items, weights, out = list(items), list(weights), []
    for _ in range(min(k, len(items))):
        pick = r.choices(range(len(items)), weights=weights)[0]
        out.append(items.pop(pick))
        weights.pop(pick)
    return out


def build_master(cfg):
    t = {}
    seed = cfg.seed

    # ---- shifts ------------------------------------------------------------
    t["shift"] = [{"shift_id": k, "shift_name": v[0], "start_time": v[1], "duration_min": v[2]}
                  for k, v in SHIFT_DEFS.items()]

    # ---- machines ----------------------------------------------------------
    r = make_rng(seed, "machines")
    counts = allocate_machines(cfg.machines, {g: s[0] for g, s in GROUP_SPEC.items()})
    machines = []
    for g in GROUPS:
        for _ in range(counts[g]):
            i = len(machines) + 1
            machines.append({
                "machine_id": f"MACH-{i:03d}", "machine_name": f"{g.title()} cell {i:02d}",
                "machine_group": g, "initial_family": r.choice(FAMILIES),
                "shift_pattern": r.choices(("P1", "P2", "P3", "P4"), weights=PATTERN_WEIGHTS)[0],
                "status": "ACTIVE",
                "commissioned_date": date_str(date(2015, 1, 1) + timedelta(days=r.randint(0, 3600)))})
    t["machine"] = machines
    present = [g for g in GROUPS if counts[g] > 0]
    by_group = {g: [m["machine_id"] for m in machines if m["machine_group"] == g] for g in present}

    # ---- tooling (shared, fewer tools than machines in the group) ---------------
    r = make_rng(seed, "tooling")
    tool_groups = [g for g in present if GROUP_SPEC[g][6]]
    n_tools = max(len(tool_groups), int(round(cfg.machines * 0.6)))
    share = allocate_machines(n_tools, {g: len(by_group[g]) for g in tool_groups})
    tools, pool = [], {}
    for g in tool_groups:
        pool[g] = []
        for _ in range(max(1, share[g])):
            tid = f"TOOL-{len(tools) + 1:03d}"
            tools.append({"tool_id": tid, "tool_name": f"{g.title()} fixture {len(tools) + 1:02d}",
                          "machine_group": g, "status": "ACTIVE"})
            pool[g].append(tid)
    t["tooling"] = tools

    # ---- products ----------------------------------------------------------
    r = make_rng(seed, "products")
    products = []
    for i in range(1, cfg.skus + 1):
        status = r.choices(("ACTIVE", "PHASE_OUT", "DISCONTINUED"), weights=(0.88, 0.07, 0.05))[0]
        eff_from = date(2024, 1, 1) + timedelta(days=r.randint(0, 800))
        products.append({
            "sku": f"SKU-{i:03d}", "product_name": f"Part {i:03d}", "category": r.choice(CATEGORIES),
            "changeover_family": r.choice(FAMILIES), "uom": "EA",
            "standard_batch_qty": r.choice((10, 20, 25, 50, 100)), "status": status,
            "effective_from": date_str(eff_from),
            "effective_to": date_str(date(2026, 10, 1)) if status == "DISCONTINUED" else None})
    t["product"] = products

    # ---- routings, steps, eligibility -------------------------------------
    r = make_rng(seed, "routings")
    routings, steps, bridge = [], [], []

    def make_steps(routing_id, groups, scale=1.0):
        out = []
        for seq, g in enumerate(groups, 1):
            _, (c_lo, c_hi), (f_lo, f_hi), p_two, p_tool, _, has_tool = GROUP_SPEC[g]
            tool = r.choice(pool[g]) if has_tool and g in pool and r.random() < p_tool else None
            step = {"step_id": f"{routing_id}-S{seq:02d}", "routing_id": routing_id, "step_seq": seq,
                    "step_name": f"{g.title()} operation",
                    "machine_group": g,
                    "cycle_min_per_unit": round(r.uniform(c_lo, c_hi) * scale, 2),
                    "fixed_min": r.randint(f_lo, f_hi),
                    "operators_required": 2 if r.random() < p_two else 1, "tool_id": tool}
            out.append(step)
            ms = by_group[g]
            k = r.randint(max(1, (len(ms) + 1) // 2), len(ms))
            for m in sorted(r.sample(ms, k)):
                bridge.append({"step_id": step["step_id"], "machine_id": m,
                               "speed_factor": round(r.uniform(0.85, 1.2), 2)})
        return out

    for p in products:
        sku = p["sku"]
        aff = CATEGORY_AFFINITY[p["category"]]
        k = r.choices(STEP_COUNT_CHOICES, weights=STEP_COUNT_WEIGHTS)[0]
        groups = weighted_sample_distinct(
            r, present, [len(by_group[g]) * aff.get(g, 1.0) for g in present], k)
        groups.sort(key=GROUPS.index)
        rid1 = f"ROUTE-{sku}-01"
        has_v2 = p["status"] == "ACTIVE" and r.random() < 0.12 and len(present) > len(groups)
        if p["status"] == "DISCONTINUED":
            routings.append({"routing_id": rid1, "sku": sku, "version": 1, "status": "RETIRED",
                             "effective_from": p["effective_from"], "effective_to": p["effective_to"]})
        elif has_v2:
            routings.append({"routing_id": rid1, "sku": sku, "version": 1, "status": "RETIRED",
                             "effective_from": p["effective_from"],
                             "effective_to": date_str(V2_EFFECTIVE_FROM - timedelta(days=1))})
        else:
            routings.append({"routing_id": rid1, "sku": sku, "version": 1, "status": "ACTIVE",
                             "effective_from": p["effective_from"], "effective_to": None})
        steps.extend(make_steps(rid1, groups))
        if has_v2:
            rid2 = f"ROUTE-{sku}-02"
            g2 = list(groups)
            extra = [g for g in present if g not in g2]
            if len(g2) > 2 and r.random() < 0.4:
                g2.pop(r.randrange(len(g2)))
            else:
                g2.append(r.choice(extra))
                g2.sort(key=GROUPS.index)
            routings.append({"routing_id": rid2, "sku": sku, "version": 2, "status": "ACTIVE",
                             "effective_from": date_str(V2_EFFECTIVE_FROM), "effective_to": None})
            steps.extend(make_steps(rid2, g2, scale=0.92))
    t["routing"], t["routing_step"], t["routing_step_machine"] = routings, steps, bridge

    # ---- machine calendars ------------------------------------------------
    r = make_rng(seed, "calendar")
    cal = []
    horizon_days = cfg.horizon_weeks * 7
    crew_base = {"SHIFT-A": 2, "SHIFT-B": 2, "SHIFT-C": 1}
    for m in machines:
        for d in range(horizon_days):
            wd = weekday_of_day(d)
            if d in PLANT_HOLIDAYS or wd == 6 or (wd == 5 and m["shift_pattern"] != "P3"):
                continue
            for sid in PATTERN_SHIFTS[m["shift_pattern"]]:
                crew = crew_base[sid]
                if wd == 5:
                    crew = 1
                elif crew == 2 and r.random() < 0.07:
                    crew = 1  # absence
                h, mi = SHIFT_DEFS[sid][1].split(":")
                start = d * DAY + int(h) * 60 + int(mi)
                end = start + SHIFT_DEFS[sid][2]
                cal.append({"calendar_id": f"CAL-{len(cal) + 1:06d}", "machine_id": m["machine_id"],
                            "calendar_date": to_iso(d * DAY)[:10], "shift_id": sid,
                            "shift_start_ts": to_iso(start), "shift_end_ts": to_iso(end),
                            "crew_size": crew, "capacity_min": end - start})
    t["machine_calendar"] = cal

    # ---- downtime ----------------------------------------------------------
    r = make_rng(seed, "downtime")
    raw = []

    def working_day():
        while True:
            d = r.randrange(0, horizon_days)
            if weekday_of_day(d) < 5 and d not in PLANT_HOLIDAYS:
                return d

    for m in machines:
        mid = m["machine_id"]
        if r.random() < 0.6:
            raw.append((mid, "PLANNED_MAINTENANCE", working_day() * DAY + r.choice((6, 14)) * 60,
                        r.randint(240, 480)))
        if r.random() < 0.15:
            raw.append((mid, "CALIBRATION", working_day() * DAY + r.choice((6, 14)) * 60,
                        r.choice((60, 90, 120))))
        for _ in range(r.choice((0, 1, 1, 2, 3))):
            raw.append((mid, "BREAKDOWN", working_day() * DAY + r.randint(6 * 60, 20 * 60),
                        r.randint(60, 360)))
    for _ in range(2 if cfg.machines >= 20 else 1):  # major outages stress the schedule
        m = r.choice(machines)["machine_id"]
        raw.append((m, "BREAKDOWN", r.randint(5, 20) * DAY + 6 * 60, r.randint(2880, 4320)))
    raw.sort(key=lambda x: (x[0], x[2]))
    downtime, last_end = [], {}
    for mid, typ, start, dur in raw:
        if start < last_end.get(mid, -1) or start + dur > cfg.horizon_min:
            continue  # keep one machine's downtimes disjoint and inside the horizon
        last_end[mid] = start + dur
        downtime.append({"downtime_id": f"DT-{len(downtime) + 1:06d}", "machine_id": mid,
                         "downtime_type": typ, "start_ts": to_iso(start), "end_ts": to_iso(start + dur),
                         "duration_min": dur})
    t["machine_downtime"] = downtime

    # ---- changeover matrix ---------------------------------------------------
    r = make_rng(seed, "changeover")
    co = []
    for g in present:
        base = GROUP_SPEC[g][5]
        for i, fi in enumerate(FAMILIES):
            for j, fj in enumerate(FAMILIES):
                if i == j:
                    v = max(3, round(base * 0.15))
                else:
                    v = base * (0.6 + 0.8 * abs(i - j) / 7) + r.uniform(0, 0.25 * base)
                co.append({"changeover_id": f"CO-{len(co) + 1:06d}", "machine_group": g, "from_family": fi,
                           "to_family": fj, "setup_min": int(min(90, max(1, round(v))))})
    t["changeover_matrix"] = co

    # ---- materials and step-material requirements ------------------------------
    r = make_rng(seed, "materials")
    mats = []
    for i in range(1, cfg.materials + 1):
        cat = r.choices(MATERIAL_CATEGORIES, weights=(0.40, 0.35, 0.25))[0]
        uom = {"RAW": "KG", "COMPONENT": "EA", "CONSUMABLE": "L"}[cat]
        lead = {"RAW": (5, 30), "COMPONENT": (7, 40), "CONSUMABLE": (2, 14)}[cat]
        mats.append({"material_id": f"MAT-{i:03d}", "material_name": f"{cat.title()} {i:03d}",
                     "category": cat, "uom": uom, "lead_time_days": r.randint(*lead)})
    t["material"] = mats
    by_cat = {c: [m["material_id"] for m in mats if m["category"] == c] for c in MATERIAL_CATEGORIES}
    cat_of = {m["material_id"]: m["category"] for m in mats}

    def preferred_category(step):
        if step["step_seq"] == 1:
            return "RAW"
        if step["machine_group"] in ("ASSY", "WELD", "TEST"):
            return "COMPONENT"
        return "CONSUMABLE"

    v1_steps = [s for s in steps if s["routing_id"].endswith("-01")]
    step_map = {s["step_id"]: s for s in steps}
    links = {}

    def add_link(step, mid):
        key = (step["step_id"], mid)
        if key in links:
            return
        c = cat_of[mid]
        if c == "RAW":
            qpu, scrap = round(r.uniform(0.1, 3.0), 3), round(r.uniform(1, 8), 1)
        elif c == "COMPONENT":
            qpu, scrap = float(r.randint(1, 4)), round(r.uniform(0, 2), 1)
        else:
            qpu, scrap = round(r.uniform(0.01, 0.2), 3), round(r.uniform(0, 5), 1)
        links[key] = {"step_id": step["step_id"], "material_id": mid, "qty_per_unit": qpu, "scrap_pct": scrap}

    # pass 1: every material is required by at least one step
    for m in mats:
        pref = [s for s in v1_steps if preferred_category(s) == m["category"]] or v1_steps
        add_link(r.choice(pref), m["material_id"])
    # pass 2: extra requirements so ~half the steps consume something
    for s in v1_steps:
        if not any(k[0] == s["step_id"] for k in links) and r.random() < 0.35:
            pool_m = by_cat[preferred_category(s)] or [m["material_id"] for m in mats]
            add_link(s, r.choice(pool_m))
    # routing v2 inherits the requirement of v1 steps with the same machine group
    v2_by_group = {}
    for s in steps:
        if s["routing_id"].endswith("-02"):
            v2_by_group[(s["routing_id"][:-2] + "01", s["machine_group"])] = s
    for (sid, mid), row in list(links.items()):
        s1 = step_map[sid]
        s2 = v2_by_group.get((s1["routing_id"], s1["machine_group"]))
        if s2:
            add_link(s2, mid)
            links[(s2["step_id"], mid)].update(qty_per_unit=row["qty_per_unit"], scrap_pct=row["scrap_pct"])
    t["routing_step_material"] = [links[k] for k in sorted(links)]
    t["_meta"] = {"groups_present": present, "machines_by_group": by_group, "max_std": MAX_STD_PROCESS_MIN}
    return t
