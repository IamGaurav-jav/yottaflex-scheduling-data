"""Turn scheduler output into tables, and (re)compute every derived field from source records."""
from .timeutil import from_iso, to_iso


def build_outputs(t, res):
    pls = sorted(res.placements, key=lambda p: (p.start, p.machine))
    sched, cons = [], []
    for i, p in enumerate(pls, 1):
        sched.append({"schedule_id": f"SCH-2026-{i:06d}", "op_id": p.op_id, "machine_id": p.machine,
                      "setup_min": p.setup, "setup_start_ts": None, "start_ts": to_iso(p.start),
                      "end_ts": to_iso(p.end), "process_min": p.dur, "status": "PLANNED"})
        for lot_id, mat, qty in p.allocs:
            cons.append({"consumption_id": f"CON-{len(cons) + 1:06d}", "op_id": p.op_id,
                         "material_id": mat, "lot_id": lot_id, "qty": qty, "consumed_ts": None})
    t["operation_schedule"], t["material_consumption"] = sched, cons

    # append-only lifecycle history
    ev = []

    def add(etype, eid, frm, to, ts, reason=None):
        ev.append({"event_id": f"EVT-{len(ev) + 1:06d}", "entity_type": etype, "entity_id": eid,
                   "from_state": frm, "to_state": to, "event_ts": to_iso(ts), "reason_code": reason})

    ops_by_order = {}
    for op in t["operation"]:
        ops_by_order.setdefault(op["order_id"], []).append(op)
    for o in t["production_order"]:
        created = from_iso(o["created_ts"])
        fail = res.failures.get(o["order_id"])
        add("ORDER", o["order_id"], None, "CREATED", created, "ORDER_ENTRY")
        add("ORDER", o["order_id"], "CREATED", "UNSCHEDULABLE" if fail else "SCHEDULED", 0,
            fail["reason"] if fail else "PLAN_RUN")
        for op in ops_by_order[o["order_id"]]:
            add("OPERATION", op["op_id"], None, "PLANNED", created, "ORDER_ENTRY")
            add("OPERATION", op["op_id"], "PLANNED", "UNSCHEDULED" if fail else "SCHEDULED", 0,
                fail["reason"] if fail else "PLAN_RUN")
    lead = {m["material_id"]: m["lead_time_days"] for m in t["material"]}
    for l in t["inventory_lot"]:
        prom, avail = from_iso(l["promised_ts"]), from_iso(l["available_ts"])
        add("LOT", l["lot_id"], None, "ON_ORDER", prom - lead[l["material_id"]] * 1440, "PURCHASE_ORDER")
        if avail > prom:
            add("LOT", l["lot_id"], "ON_ORDER", "DELAYED", prom, "SUPPLIER_DELAY")
            add("LOT", l["lot_id"], "DELAYED", "AVAILABLE", avail, "RECEIPT")
        else:
            add("LOT", l["lot_id"], "ON_ORDER", "AVAILABLE", avail, "RECEIPT")
    t["status_event"] = ev


def finalize(t):
    """Recompute all derived columns from the source records (never generated independently)."""
    start_of = {}
    for s in t["operation_schedule"]:
        st = from_iso(s["start_ts"])
        s["setup_start_ts"] = to_iso(st - s["setup_min"])
        start_of[s["op_id"]] = st
    for c in t["material_consumption"]:
        c["consumed_ts"] = to_iso(start_of[c["op_id"]])

    used = {}
    for c in t["material_consumption"]:
        used[c["lot_id"]] = used.get(c["lot_id"], 0) + c["qty"]
    latest = {}
    for e in t["status_event"]:
        latest[(e["entity_type"], e["entity_id"])] = e["to_state"]
    for l in t["inventory_lot"]:
        l["delay_min"] = from_iso(l["available_ts"]) - from_iso(l["promised_ts"])
        l["qty_consumed"] = used.get(l["lot_id"], 0)
        l["qty_remaining"] = l["qty_received"] - l["qty_consumed"]
        l["status"] = latest[("LOT", l["lot_id"])]
    for op in t["operation"]:
        op["status"] = latest[("OPERATION", op["op_id"])]

    by_order = {}
    op_order = {op["op_id"]: op["order_id"] for op in t["operation"]}
    for s in t["operation_schedule"]:
        by_order.setdefault(op_order[s["op_id"]], []).append(s)
    for o in t["production_order"]:
        o["status"] = latest[("ORDER", o["order_id"])]
        rows = by_order.get(o["order_id"])
        if rows:
            fin = max(from_iso(r["end_ts"]) for r in rows)
            o["total_process_min"] = sum(r["process_min"] for r in rows)
            o["planned_start_ts"] = to_iso(min(from_iso(r["start_ts"]) for r in rows))
            o["planned_finish_ts"] = to_iso(fin)
            o["lateness_min"] = max(0, fin - from_iso(o["due_ts"]))
        else:
            o["total_process_min"] = o["planned_start_ts"] = o["planned_finish_ts"] = o["lateness_min"] = None


def build_exceptions(t, res):
    """Every infeasible or late order gets an explicit, computed explanation."""
    exc = []
    for o in t["production_order"]:
        oid = o["order_id"]
        fail = res.failures.get(oid)
        if fail:
            exc.append({"order_id": oid, "op_id": fail["op_id"], "exception_type": fail["reason"],
                        "root_cause": fail["reason"], "lateness_min": None,
                        "detail": f"order rolled back, nothing scheduled: {fail['detail']}"})
        elif o["lateness_min"]:
            mat, cap, span = res.order_delay[oid]
            if from_iso(o["release_ts"]) + span > from_iso(o["due_ts"]):
                cause = "DUE_DATE_TOO_TIGHT"
            elif mat > 0 and mat >= cap:
                cause = "MATERIAL_DELAY"
            else:
                cause = "MACHINE_CAPACITY"
            exc.append({"order_id": oid, "op_id": None, "exception_type": "LATE_ORDER", "root_cause": cause,
                        "lateness_min": o["lateness_min"],
                        "detail": (f"finishes {o['lateness_min']} min after due; best-case span {span} min, "
                                   f"waited {mat} min for material and {cap} min for machine/calendar/"
                                   f"changeover/tool availability")})
    for i, e in enumerate(exc, 1):
        e["exception_id"] = f"EXC-{i:06d}"
    t["schedule_exception"] = [{k: e[k] for k in ("exception_id", "order_id", "op_id", "exception_type",
                                                  "root_cause", "lateness_min", "detail")} for e in exc]
