"""Validation. Two layers:
  * integrity   - keys, types, enums, dates, arithmetic, lifecycle, reconciliation (must always be clean)
  * constraints - hard scheduling rules on the schedule (clean in the baseline; the injected
                  anomalies are exactly the violations reported here).
The validator never reads ground_truth."""
import re
from collections import defaultdict
from datetime import datetime

from . import intervals as iv
from .config import PLANT_HOLIDAYS
from .rules import process_minutes, required_qty, std_process_minutes
from .scheduler import machine_windows
from .schema import BY_NAME, TABLE_ORDER, TRANSITIONS
from .timeutil import DAY, FMT, from_iso, to_iso

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, category, failures, note=""):
        self.checks.append({"check": name, "category": category, "status": "PASS" if not failures else "FAIL",
                            "failures": len(failures), "examples": failures[:5], "note": note})

    @property
    def integrity_failures(self):
        return sum(c["failures"] for c in self.checks if c["category"] != "constraint")


# --------------------------------------------------------------------------- schema level
def _type_ok(f, v):
    if f.type == "str":
        return isinstance(v, str)
    if f.type == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if f.type == "float":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if f.type == "bool":
        return isinstance(v, bool)
    if f.type == "date":
        return isinstance(v, str) and bool(DATE_RE.match(v))
    if f.type == "ts":
        if not (isinstance(v, str) and TS_RE.match(v)):
            return False
        try:
            datetime.strptime(v, FMT)
        except ValueError:
            return False
        return True
    return False


def check_schema(t, rep):
    for name in TABLE_ORDER:
        tab, rows = BY_NAME[name], t[name]
        bad_type, bad_req, bad_enum, bad_pat, bad_rng, bad_cols = [], [], [], [], [], []
        pats = {f.name: re.compile(f.pattern) for f in tab.fields if f.pattern}
        cols = set(tab.cols())
        for r in rows:
            if set(r) != cols:
                bad_cols.append(f"{name}: columns {sorted(set(r) ^ cols)}")
                continue
            for f in tab.fields:
                v = r[f.name]
                if v is None:
                    if f.required:
                        bad_req.append(f"{name}.{f.name} null in {r[tab.pk[0]]}")
                    continue
                if not _type_ok(f, v):
                    bad_type.append(f"{name}.{f.name}={v!r} not {f.type}")
                    continue
                if f.enum and v not in f.enum:
                    bad_enum.append(f"{name}.{f.name}={v!r}")
                if f.pattern and not pats[f.name].match(str(v)):
                    bad_pat.append(f"{name}.{f.name}={v!r}")
                if f.type in ("int", "float"):
                    if (f.min is not None and v < f.min) or (f.max is not None and v > f.max):
                        bad_rng.append(f"{name}.{f.name}={v!r}")
        rep.add(f"{name}: columns match schema", "schema", bad_cols)
        rep.add(f"{name}: types", "schema", bad_type)
        rep.add(f"{name}: required fields", "schema", bad_req)
        rep.add(f"{name}: allowed values", "schema", bad_enum)
        rep.add(f"{name}: ID/format patterns", "schema", bad_pat)
        rep.add(f"{name}: numeric ranges", "schema", bad_rng)


def check_keys(t, rep):
    pk_bad, uq_bad, fk_bad = [], [], []
    for name in TABLE_ORDER:
        tab, rows = BY_NAME[name], t[name]
        seen = set()
        for r in rows:
            k = tuple(r[c] for c in tab.pk)
            if k in seen:
                pk_bad.append(f"{name}: duplicate PK {k}")
            seen.add(k)
        for f in tab.fields:
            if f.unique and f.name not in tab.pk:
                vals = [r[f.name] for r in rows if r[f.name] is not None]
                if len(vals) != len(set(vals)):
                    uq_bad.append(f"{name}.{f.name} not unique")
        for cols in tab.uniques:
            keys = [tuple(r[c] for c in cols) for r in rows]
            if len(keys) != len(set(keys)):
                uq_bad.append(f"{name}: composite {cols} not unique")
        for cols, ref, rcols, card in tab.fks:
            refset = {tuple(r[c] for c in rcols) for r in t[ref]}
            for r in rows:
                k = tuple(r[c] for c in cols)
                if any(x is None for x in k):
                    continue
                if k not in refset:
                    fk_bad.append(f"{name}{cols} -> {ref}: {k} missing")
    rep.add("primary key uniqueness", "keys", pk_bad)
    rep.add("unique constraints", "keys", uq_bad)
    rep.add("foreign key integrity (all joins)", "keys", fk_bad)


# --------------------------------------------------------------------------- business rules
def check_business(t, rep):
    prod = {p["sku"]: p for p in t["product"]}
    routing = {r["routing_id"]: r for r in t["routing"]}
    step = {s["step_id"]: s for s in t["routing_step"]}
    mach = {m["machine_id"]: m for m in t["machine"]}
    order = {o["order_id"]: o for o in t["production_order"]}
    op = {o["op_id"]: o for o in t["operation"]}
    lot = {l["lot_id"]: l for l in t["inventory_lot"]}
    elig = {(b["step_id"], b["machine_id"]): b["speed_factor"] for b in t["routing_step_machine"]}
    sched_by_op = {s["op_id"]: s for s in t["operation_schedule"]}

    f = []
    for p in t["product"]:
        if (p["status"] == "DISCONTINUED") != (p["effective_to"] is not None):
            f.append(p["sku"])
        if p["effective_to"] and p["effective_to"] < p["effective_from"]:
            f.append(p["sku"])
    rep.add("product: effective dates and DISCONTINUED lifecycle", "dates", f)

    f = []
    for r in t["routing"]:
        if r["routing_id"] != f"ROUTE-{r['sku']}-{r['version']:02d}":
            f.append(f"{r['routing_id']} id/sku/version mismatch")
        if r["effective_to"] and r["effective_to"] < r["effective_from"]:
            f.append(f"{r['routing_id']} dates")
    by_sku = defaultdict(list)
    for r in t["routing"]:
        by_sku[r["sku"]].append(r)
    for sku, rs in by_sku.items():
        rs = sorted(rs, key=lambda x: x["version"])
        for a, b in zip(rs, rs[1:]):
            if a["effective_to"] is None or a["effective_to"] >= b["effective_from"]:
                f.append(f"{sku}: routing versions overlap")
    rep.add("routing: id convention, effective dates, no version overlap", "dates", f)

    f = []
    seqs = defaultdict(list)
    for s in t["routing_step"]:
        seqs[s["routing_id"]].append(s["step_seq"])
        if s["step_id"] != f"{s['routing_id']}-S{s['step_seq']:02d}":
            f.append(f"{s['step_id']} id mismatch")
    for rid, v in seqs.items():
        if sorted(v) != list(range(1, len(v) + 1)):
            f.append(f"{rid}: non-contiguous step_seq")
    for rid in routing:
        if rid not in seqs:
            f.append(f"{rid}: routing without steps")
    rep.add("routing steps: id convention and contiguous sequence", "keys", f)

    f = [f"{b['step_id']}/{b['machine_id']}" for b in t["routing_step_machine"]
         if mach[b["machine_id"]]["machine_group"] != step[b["step_id"]]["machine_group"]]
    elig_steps = {b["step_id"] for b in t["routing_step_machine"]}
    f += [f"{s} has no eligible machine" for s in step if s not in elig_steps]
    rep.add("eligibility bridge: machine group matches step, every step has a machine", "keys", f)

    groups = {m["machine_group"] for m in t["machine"]}
    co = {(c["machine_group"], c["from_family"], c["to_family"]) for c in t["changeover_matrix"]}
    fams = sorted({p["changeover_family"] for p in t["product"]} | {m["initial_family"] for m in t["machine"]})
    f = [f"{g}:{a}->{b}" for g in groups for a in fams for b in fams if (g, a, b) not in co]
    rep.add("changeover matrix complete for every group and family pair", "keys", f)

    f = []
    cal_by_m = defaultdict(list)
    for c in t["machine_calendar"]:
        s, e = from_iso(c["shift_start_ts"]), from_iso(c["shift_end_ts"])
        if e <= s or c["capacity_min"] != e - s:
            f.append(f"{c['calendar_id']} capacity/date order")
        if c["calendar_date"] != c["shift_start_ts"][:10]:
            f.append(f"{c['calendar_id']} calendar_date != shift start date")
        if (s // DAY) in PLANT_HOLIDAYS:
            f.append(f"{c['calendar_id']} staffed on plant holiday")
        cal_by_m[c["machine_id"]].append((s, e, c["calendar_id"]))
    for m, rows in cal_by_m.items():
        rows.sort()
        for a, b in zip(rows, rows[1:]):
            if b[0] < a[1]:
                f.append(f"{m}: overlapping calendar windows {a[2]} {b[2]}")
    rep.add("machine calendar: date ordering, capacity arithmetic, no overlapping windows", "arithmetic", f)

    f = []
    dts = defaultdict(list)
    for d in t["machine_downtime"]:
        s, e = from_iso(d["start_ts"]), from_iso(d["end_ts"])
        if e <= s or d["duration_min"] != e - s:
            f.append(d["downtime_id"])
        dts[d["machine_id"]].append((s, e, d["downtime_id"]))
    for m, rows in dts.items():
        rows.sort()
        for a, b in zip(rows, rows[1:]):
            if b[0] < a[1]:
                f.append(f"{m}: overlapping downtime {a[2]} {b[2]}")
    rep.add("machine downtime: date ordering, duration arithmetic, no overlaps", "arithmetic", f)

    f = []
    for l in t["inventory_lot"]:
        if from_iso(l["available_ts"]) < from_iso(l["promised_ts"]):
            f.append(l["lot_id"])
    rep.add("inventory lot: available_ts >= promised_ts", "dates", f)

    # orders
    f_dates, f_eff, f_prio, f_ops = [], [], [], []
    ops_of = defaultdict(list)
    for o in t["operation"]:
        ops_of[o["order_id"]].append(o)
    for o in t["production_order"]:
        c, r, d = from_iso(o["created_ts"]), from_iso(o["release_ts"]), from_iso(o["due_ts"])
        if not (c <= 0 and c <= r < d):
            f_dates.append(o["order_id"])
        ro, p = routing[o["routing_id"]], prod[o["sku"]]
        rd = o["release_ts"][:10]
        if ro["sku"] != o["sku"] or ro["effective_from"] > rd or (ro["effective_to"] and rd > ro["effective_to"]) \
                or p["status"] == "DISCONTINUED":
            f_eff.append(o["order_id"])
        if (o["order_type"] == "URGENT") != (o["priority"] == 1):
            f_prio.append(o["order_id"])
    step_count = defaultdict(int)
    for s in t["routing_step"]:
        step_count[s["routing_id"]] += 1
    for oid, o in order.items():
        got = sorted(x["op_seq"] for x in ops_of[oid])
        if got != list(range(1, step_count[o["routing_id"]] + 1)):
            f_ops.append(oid)
    rep.add("orders: created <= plan run, release < due, due after release", "dates", f_dates)
    rep.add("orders: routing effective on release date, SKU orderable", "dates", f_eff)
    rep.add("orders: urgent <=> priority 1", "keys", f_prio)
    rep.add("orders: one operation per routing step, contiguous sequence", "keys", f_ops)

    f = []
    for o in t["operation"]:
        s, od = step[o["step_id"]], order[o["order_id"]]
        if o["op_id"] != f"OP-{o['order_id']}-{o['op_seq']:02d}":
            f.append(f"{o['op_id']} id")
        if s["routing_id"] != od["routing_id"] or s["step_seq"] != o["op_seq"] or o["qty"] != od["qty"] \
                or s["machine_group"] != o["machine_group"] or s["operators_required"] != o["operators_required"] \
                or s["tool_id"] != o["tool_id"]:
            f.append(f"{o['op_id']} not consistent with step/order")
        if o["std_process_min"] != std_process_minutes(o["qty"], s["cycle_min_per_unit"], s["fixed_min"]):
            f.append(f"{o['op_id']} std_process_min")
    rep.add("operations: ID convention, copy-from-step consistency, std duration arithmetic", "arithmetic", f)

    # schedule rows
    f_elig, f_arith, f_status = [], [], []
    for s in t["operation_schedule"]:
        o = op[s["op_id"]]
        sf = elig.get((o["step_id"], s["machine_id"]))
        if sf is None:
            f_elig.append(f"{s['schedule_id']} machine {s['machine_id']} not eligible")
            continue
        st, en = from_iso(s["start_ts"]), from_iso(s["end_ts"])
        if s["process_min"] != process_minutes(o["std_process_min"], sf) or en - st != s["process_min"] \
                or s["setup_start_ts"] != to_iso(st - s["setup_min"]):
            f_arith.append(s["schedule_id"])
    for o in t["operation"]:
        has = o["op_id"] in sched_by_op
        if (o["status"] == "SCHEDULED") != has:
            f_status.append(f"{o['op_id']} status {o['status']} vs schedule row {has}")
    rep.add("schedule: machine eligibility", "keys", f_elig)
    rep.add("schedule: process_min / end-start / setup_start arithmetic", "arithmetic", f_arith)
    rep.add("schedule: one row per SCHEDULED operation, none otherwise", "lifecycle", f_status)

    # material reconciliation
    bom = defaultdict(dict)
    for b in t["routing_step_material"]:
        bom[b["step_id"]][b["material_id"]] = b
    got = defaultdict(int)
    f_mat, f_ts = [], []
    for c in t["material_consumption"]:
        got[(c["op_id"], c["material_id"])] += c["qty"]
        if lot[c["lot_id"]]["material_id"] != c["material_id"]:
            f_mat.append(f"{c['consumption_id']} lot material mismatch")
        s = sched_by_op.get(c["op_id"])
        if s is None:
            f_mat.append(f"{c['consumption_id']} consumes for an unscheduled op")
        elif c["consumed_ts"] != s["start_ts"]:
            f_ts.append(c["consumption_id"])
    for o in t["operation"]:
        want = {}
        if o["status"] == "SCHEDULED":
            for mid, b in bom[o["step_id"]].items():
                want[mid] = required_qty(o["qty"], b["qty_per_unit"], b["scrap_pct"])
        for mid, q in want.items():
            if got.get((o["op_id"], mid), 0) != q:
                f_mat.append(f"{o['op_id']}/{mid}: consumed {got.get((o['op_id'], mid), 0)} != required {q}")
    for (opid, mid) in got:
        if mid not in bom[op[opid]["step_id"]]:
            f_mat.append(f"{opid}/{mid} not in BOM")
    rep.add("material consumption reconciles to BOM x quantity (incl. scrap)", "arithmetic", f_mat)
    rep.add("material consumed_ts equals operation start_ts", "arithmetic", f_ts)

    used = defaultdict(int)
    for c in t["material_consumption"]:
        used[c["lot_id"]] += c["qty"]
    f = [l["lot_id"] for l in t["inventory_lot"]
         if l["qty_consumed"] != used[l["lot_id"]] or l["qty_remaining"] != l["qty_received"] - used[l["lot_id"]]
         or l["delay_min"] != from_iso(l["available_ts"]) - from_iso(l["promised_ts"])]
    rep.add("inventory lot balances reconcile to consumption", "arithmetic", f)

    # order derived values
    f = []
    by_order = defaultdict(list)
    for s in t["operation_schedule"]:
        by_order[op[s["op_id"]]["order_id"]].append(s)
    for o in t["production_order"]:
        rows = by_order.get(o["order_id"], [])
        if rows:
            fin = max(from_iso(r["end_ts"]) for r in rows)
            ok = (o["total_process_min"] == sum(r["process_min"] for r in rows)
                  and o["planned_start_ts"] == to_iso(min(from_iso(r["start_ts"]) for r in rows))
                  and o["planned_finish_ts"] == to_iso(fin)
                  and o["lateness_min"] == max(0, fin - from_iso(o["due_ts"])))
        else:
            ok = o["total_process_min"] is None and o["lateness_min"] is None and o["planned_finish_ts"] is None
        if not ok:
            f.append(o["order_id"])
    rep.add("order derived values (totals, start/finish, lateness) reconcile to schedule", "arithmetic", f)

    # lifecycle
    ev_by = defaultdict(list)
    for e in t["status_event"]:
        ev_by[(e["entity_type"], e["entity_id"])].append(e)
    f_tr, f_chain, f_time, f_final, f_exist = [], [], [], [], []
    ents = {"ORDER": order, "OPERATION": op, "LOT": lot}
    for (et, eid), evs in ev_by.items():
        if eid not in ents[et]:
            f_exist.append(f"{et}:{eid}")
            continue
        prev_state, prev_ts = None, None
        for e in evs:
            if e["to_state"] not in TRANSITIONS[et].get(e["from_state"], set()):
                f_tr.append(f"{eid}: {e['from_state']}->{e['to_state']}")
            if e["from_state"] != prev_state:
                f_chain.append(f"{eid}: event {e['event_id']} breaks chain")
            ts = from_iso(e["event_ts"])
            if prev_ts is not None and ts < prev_ts:
                f_time.append(f"{eid}: {e['event_id']} out of time order")
            prev_state, prev_ts = e["to_state"], ts
        if ents[et][eid]["status"] != prev_state:
            f_final.append(f"{eid}: status {ents[et][eid]['status']} != last event {prev_state}")
    for et, d in ents.items():
        for eid in d:
            if (et, eid) not in ev_by:
                f_exist.append(f"{et}:{eid} has no history")
    f_coh = []
    for o in t["production_order"]:
        want = {"SCHEDULED": "SCHEDULED", "UNSCHEDULABLE": "UNSCHEDULED"}[o["status"]] if o["status"] != "CREATED" else None
        for x in ops_of[o["order_id"]]:
            if want and x["status"] != want:
                f_coh.append(f"{x['op_id']} {x['status']} vs order {o['status']}")
    rep.add("lifecycle: only allowed transitions", "lifecycle", f_tr)
    rep.add("lifecycle: history chain continuity and timestamp order", "lifecycle", f_chain + f_time)
    rep.add("lifecycle: current status equals last event; every entity has history", "lifecycle", f_final + f_exist)
    rep.add("lifecycle: operation states coherent with order state", "lifecycle", f_coh)

    # exceptions explain every infeasible / late order
    f = []
    exc_by = defaultdict(list)
    for e in t["schedule_exception"]:
        exc_by[e["order_id"]].append(e)
    for o in t["production_order"]:
        es = exc_by.get(o["order_id"], [])
        if o["status"] == "UNSCHEDULABLE":
            if len(es) != 1 or es[0]["exception_type"] == "LATE_ORDER":
                f.append(f"{o['order_id']} unschedulable without single explanation")
        elif o["lateness_min"]:
            if len(es) != 1 or es[0]["exception_type"] != "LATE_ORDER" or es[0]["lateness_min"] != o["lateness_min"]:
                f.append(f"{o['order_id']} late without matching explanation")
        elif es:
            f.append(f"{o['order_id']} has exception but is neither late nor unschedulable")
    rep.add("every infeasible or late order has exactly one explanation", "lifecycle", f)

    # duplicates
    f = []
    for name in ("status_event", "material_consumption"):
        keys = [tuple(v for k, v in r.items() if k not in BY_NAME[name].pk) for r in t[name]]
        if name == "material_consumption":
            keys = [(r["op_id"], r["material_id"], r["lot_id"]) for r in t[name]]
        if len(keys) != len(set(keys)):
            f.append(f"{name}: duplicate business rows")
    rep.add("duplicate business rows (events, consumption)", "keys", f)


# --------------------------------------------------------------------------- constraints
def build_context(t):
    ctx = {}
    order = {o["order_id"]: o for o in t["production_order"]}
    prod = {p["sku"]: p for p in t["product"]}
    ctx["op"] = {}
    for o in t["operation"]:
        od = order[o["order_id"]]
        ctx["op"][o["op_id"]] = {"order": o["order_id"], "seq": o["op_seq"], "group": o["machine_group"],
                                 "req": o["operators_required"], "tool": o["tool_id"],
                                 "release": from_iso(od["release_ts"]),
                                 "family": prod[od["sku"]]["changeover_family"]}
    ctx["windows"] = machine_windows(t["machine_calendar"], [], 10 ** 9, False)
    dts = defaultdict(list)
    for d in t["machine_downtime"]:
        dts[d["machine_id"]].append((from_iso(d["start_ts"]), from_iso(d["end_ts"])))
    ctx["downtime"] = {m: iv.merge(v) for m, v in dts.items()}
    ctx["co"] = {(c["machine_group"], c["from_family"], c["to_family"]): c["setup_min"]
                 for c in t["changeover_matrix"]}
    ctx["machine"] = {m["machine_id"]: m for m in t["machine"]}
    ctx["lot"] = {l["lot_id"]: (from_iso(l["available_ts"]), l["qty_received"]) for l in t["inventory_lot"]}
    return ctx


def constraint_violations(ctx, sched, cons):
    """Hard-constraint rules. Returns list of dict(rule, entity_id, related_id, message)."""
    out = []

    def v(rule, ent, rel, msg):
        out.append({"rule": rule, "entity_id": ent, "related_id": rel, "message": msg})

    rows, by_machine, by_op, by_tool = [], defaultdict(list), {}, defaultdict(list)
    for s in sched:
        st, en = from_iso(s["start_ts"]), from_iso(s["end_ts"])
        r = {"id": s["schedule_id"], "op": s["op_id"], "m": s["machine_id"], "start": st, "end": en,
             "occ": st - s["setup_min"], "setup": s["setup_min"]}
        r.update(ctx["op"][s["op_id"]])
        rows.append(r)
        by_machine[r["m"]].append(r)
        by_op[(r["order"], r["seq"])] = r
        if r["tool"]:
            by_tool[r["tool"]].append(r)

    for r in rows:
        w = ctx["windows"].get(r["m"], {1: [], 2: []})[r["req"]]
        if iv.covering(w, r["occ"], r["end"]) is None:
            v("OUTSIDE_SHIFT_CALENDAR", r["id"], None,
              f"occupancy {to_iso(r['occ'])}..{to_iso(r['end'])} not inside a staffed window with crew>={r['req']}")
        for a, b in ctx["downtime"].get(r["m"], ()):
            if iv.overlaps(r["occ"], r["end"], a, b):
                v("MACHINE_DOWNTIME_CONFLICT", r["id"], None, "occupancy overlaps machine downtime")
                break
        if r["start"] < r["release"]:
            v("START_BEFORE_RELEASE", r["id"], None, "process starts before order release")
        pred = by_op.get((r["order"], r["seq"] - 1))
        if pred is not None and r["start"] < pred["end"]:
            v("PRECEDENCE_VIOLATED", r["id"], pred["id"], "starts before predecessor operation ends")

    for m, lst in by_machine.items():
        lst.sort(key=lambda r: (r["occ"], r["start"], r["id"]))
        hold, init = None, ctx["machine"][m]["initial_family"]
        for i, r in enumerate(lst):
            if hold is not None and r["occ"] < hold["end"]:
                v("OPERATIONS_OVERLAP", r["id"], hold["id"], "machine occupancy overlaps another operation")
            prev_fam = lst[i - 1]["family"] if i else init
            need = ctx["co"][(r["group"], prev_fam, r["family"])]
            if r["setup"] < need:
                v("CHANGEOVER_OMITTED", r["id"], lst[i - 1]["id"] if i else None,
                  f"setup {r['setup']} min < required {need} min ({prev_fam}->{r['family']})")
            if hold is None or r["end"] > hold["end"]:
                hold = r
    for tool, lst in by_tool.items():
        lst.sort(key=lambda r: (r["occ"], r["id"]))
        hold = None
        for r in lst:
            if hold is not None and r["occ"] < hold["end"]:
                v("TOOL_DOUBLE_BOOKED", r["id"], hold["id"], f"shared tool {tool} used concurrently")
            if hold is None or r["end"] > hold["end"]:
                hold = r

    used = defaultdict(int)
    for c in cons:
        avail, cap = ctx["lot"][c["lot_id"]]
        if from_iso(c["consumed_ts"]) < avail:
            v("MATERIAL_CONSUMED_BEFORE_AVAILABLE", c["consumption_id"], c["lot_id"],
              f"consumed {c['consumed_ts']} before lot available {to_iso(avail)}")
        used[c["lot_id"]] += c["qty"]
    for lot_id, q in used.items():
        if q > ctx["lot"][lot_id][1]:
            v("LOT_OVERDRAWN", lot_id, None, f"drawn {q} > received {ctx['lot'][lot_id][1]}")
    return out


def violation_keys(vs):
    return {(x["rule"], x["entity_id"]) for x in vs}


def validate(t):
    rep = Report()
    check_schema(t, rep)
    check_keys(t, rep)
    broken = rep.integrity_failures
    violations = []
    if broken == 0:
        check_business(t, rep)
        ctx = build_context(t)
        violations = constraint_violations(ctx, t["operation_schedule"], t["material_consumption"])
    by_rule = defaultdict(int)
    for x in violations:
        by_rule[x["rule"]] += 1
    rep.add("hard scheduling constraints (violations are listed, not failures of the dataset)", "constraint",
            violations, note="counted separately; see constraint_violations")
    return {"integrity_failures": rep.integrity_failures, "checks": rep.checks,
            "constraint_violations_total": len(violations), "constraint_violations_by_rule": dict(sorted(by_rule.items())),
            "constraint_violations": violations,
            "row_counts": {n: len(t[n]) for n in TABLE_ORDER}}
