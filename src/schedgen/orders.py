"""Stage 2: dependent transaction records (orders, operations) and the supply side
(inventory lots) which is sized from the demand those orders create."""
import math

from .master import make_rng
from .rules import required_qty, std_process_minutes
from .scheduler import machine_windows
from .timeutil import DAY, to_iso

URGENT_SHARE = 0.08


def capped_qty(t, steps, qty_raw):
    """Quantity limited so that no single operation exceeds MAX_STD_PROCESS_MIN (must fit in a shift)."""
    cap = min(int((t["_meta"]["max_std"] - s["fixed_min"]) // s["cycle_min_per_unit"]) for s in steps)
    return max(5, min(qty_raw, cap))


def calibrate_load(cfg, t, draft, steps_of):
    """Rescale each machine group's cycle times so demand/capacity equals a seeded target utilisation
    (0.40-0.85 of horizon capacity). Keeps load realistic and comparable at any scale instead of
    hand-tuning constants per preset. Runs on master data before operations are emitted."""
    r = make_rng(cfg.seed, "calibration")
    windows = machine_windows(t["machine_calendar"], t["machine_downtime"], cfg.horizon_min, True)
    group_of = {m["machine_id"]: m["machine_group"] for m in t["machine"]}
    cap = {}
    for mid, w in windows.items():
        cap[group_of[mid]] = cap.get(group_of[mid], 0) + sum(b - a for a, b in w[1])
    target = {g: r.uniform(0.40, 0.85) for g in sorted(cap)}
    for _ in range(3):
        demand = {g: 0.0 for g in cap}
        for o in draft:
            steps = steps_of[o["routing"]["routing_id"]]
            q = capped_qty(t, steps, o["qty_raw"])
            for s in steps:
                demand[s["machine_group"]] += std_process_minutes(q, s["cycle_min_per_unit"], s["fixed_min"]) * 1.12
        for s in t["routing_step"]:
            g = s["machine_group"]
            if demand.get(g):
                k = min(3.0, max(0.4, target[g] * cap[g] / demand[g]))
                s["cycle_min_per_unit"] = round(max(0.05, s["cycle_min_per_unit"] * (0.5 + 0.5 * k)), 2)


def build_orders(cfg, t):
    r = make_rng(cfg.seed, "orders")
    horizon_days = cfg.horizon_weeks * 7
    products = {p["sku"]: p for p in t["product"]}
    orderable = [s for s, p in products.items() if p["status"] != "DISCONTINUED"]
    routings_of = {}
    for ro in t["routing"]:
        routings_of.setdefault(ro["sku"], []).append(ro)
    steps_of = {}
    for s in t["routing_step"]:
        steps_of.setdefault(s["routing_id"], []).append(s)
    for v in steps_of.values():
        v.sort(key=lambda s: s["step_seq"])

    def routing_for(sku, release_min):
        d = to_iso(release_min)[:10]
        for ro in routings_of[sku]:
            if ro["effective_from"] <= d and (ro["effective_to"] is None or d <= ro["effective_to"]):
                return ro
        return None

    weights = [r.paretovariate(2.0) for _ in orderable]
    draft = []
    for _ in range(cfg.orders):
        urgent = r.random() < URGENT_SHARE
        if urgent:
            day = r.randint(int(0.12 * horizon_days), int(0.5 * horizon_days))
        else:
            day = r.randint(0, int(0.30 * horizon_days))
        release = day * DAY + 8 * 60
        sku = r.choices(orderable, weights=weights)[0]
        while routing_for(sku, release) is None:
            sku = r.choices(orderable, weights=weights)[0]
        draft.append({"urgent": urgent, "release": release, "sku": sku})

    # hit the requested operation total exactly by re-assigning SKUs of random orders
    def n_ops(o):
        return len(steps_of[routing_for(o["sku"], o["release"])["routing_id"]])

    total = sum(n_ops(o) for o in draft)
    guard = 0
    while total != cfg.ops and guard < 200000:
        guard += 1
        o = r.choice(draft)
        cur = n_ops(o)
        want = cur + (1 if total < cfg.ops else -1)
        cands = [s for s in orderable
                 if routing_for(s, o["release"]) is not None
                 and len(steps_of[routing_for(s, o["release"])["routing_id"]]) == want]
        if cands:
            o["sku"] = r.choice(cands)
            total += want - cur

    for o in draft:
        o["created"] = -r.randint(60, 14 * DAY)
    draft.sort(key=lambda o: (o["created"], o["release"], o["sku"]))

    for o in draft:
        o["qty_raw"] = int(min(400, max(10, round(r.lognormvariate(math.log(70), 0.6)))))
        o["routing"] = routing_for(o["sku"], o["release"])
    calibrate_load(cfg, t, draft, steps_of)

    orders, ops = [], []
    for i, o in enumerate(draft, 1):
        oid = f"PROD-2026-{i:06d}"
        ro = o["routing"]
        steps = steps_of[ro["routing_id"]]
        qty = capped_qty(t, steps, o["qty_raw"])
        if o["urgent"]:
            priority, offset = 1, r.randint(2, 5)
        else:
            priority, offset = r.choices((2, 3, 4), weights=(0.2, 0.55, 0.25))[0], r.randint(5, 24)
        due = (o["release"] // DAY + offset) * DAY + 17 * 60
        orders.append({
            "order_id": oid, "sku": o["sku"], "routing_id": ro["routing_id"], "qty": qty,
            "order_type": "URGENT" if o["urgent"] else "STANDARD", "priority": priority,
            "created_ts": to_iso(o["created"]), "release_ts": to_iso(o["release"]), "due_ts": to_iso(due),
            "status": "CREATED", "total_process_min": None, "planned_start_ts": None,
            "planned_finish_ts": None, "lateness_min": None})
        for s in steps:
            ops.append({
                "op_id": f"OP-{oid}-{s['step_seq']:02d}", "order_id": oid, "step_id": s["step_id"],
                "op_seq": s["step_seq"], "machine_group": s["machine_group"],
                "operators_required": s["operators_required"], "tool_id": s["tool_id"], "qty": qty,
                "std_process_min": std_process_minutes(qty, s["cycle_min_per_unit"], s["fixed_min"]),
                "status": "PLANNED"})
    t["production_order"], t["operation"] = orders, ops


def build_lots(cfg, t):
    """Supply sized from demand: most materials are slightly over-supplied, ~6% are deliberately
    short, and ~25% of later lots arrive late versus their promise."""
    r = make_rng(cfg.seed, "lots")
    step_bom = {}
    for b in t["routing_step_material"]:
        step_bom.setdefault(b["step_id"], []).append(b)
    demand = {m["material_id"]: 0 for m in t["material"]}
    for op in t["operation"]:
        for b in step_bom.get(op["step_id"], []):
            demand[b["material_id"]] += required_qty(op["qty"], b["qty_per_unit"], b["scrap_pct"])

    lots = []
    for m in t["material"]:
        mid, d = m["material_id"], demand[m["material_id"]]
        if d == 0:
            supply = r.randint(50, 500)
        elif r.random() < 0.06:
            supply = max(1, int(d * r.uniform(0.55, 0.9)))
        else:
            supply = int(math.ceil(d * r.uniform(1.05, 1.6)))
        n = r.randint(1, 4) if supply >= 4 else 1
        w = [r.uniform(2, 4)] + [r.uniform(0.5, 1.2) for _ in range(n - 1)]
        qtys = [max(1, int(supply * x / sum(w))) for x in w]
        qtys[0] += supply - sum(qtys)
        for i, q in enumerate(qtys):
            if i == 0:
                promised = avail = 0
            else:
                promised = r.randint(int(0.05 * cfg.horizon_min / DAY), int(0.8 * cfg.horizon_min / DAY)) * DAY + 8 * 60
                delay = r.randint(1, 6) * DAY if r.random() < 0.25 else 0
                avail = promised + delay
            lots.append({"material_id": mid, "qty_received": q, "promised_ts": promised, "available_ts": avail})
    lots.sort(key=lambda x: (x["material_id"], x["available_ts"], x["promised_ts"]))
    rows = []
    for i, l in enumerate(lots, 1):
        rows.append({"lot_id": f"LOT-{i:06d}", "material_id": l["material_id"], "qty_received": l["qty_received"],
                     "promised_ts": to_iso(l["promised_ts"]), "available_ts": to_iso(l["available_ts"]),
                     "delay_min": l["available_ts"] - l["promised_ts"], "qty_consumed": 0,
                     "qty_remaining": l["qty_received"], "status": "AVAILABLE"})
    t["inventory_lot"] = rows
