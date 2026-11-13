"""Stage 5: inject labelled edge cases into a clean baseline schedule.

A mutation is accepted only if re-running the validator reports exactly one new violation, the
intended one, and no other violation appears or disappears. That keeps ground_truth precise and
guarantees unrelated rows are never silently corrupted."""
import json
from collections import defaultdict

from . import intervals as iv
from .master import make_rng
from .timeutil import from_iso, to_iso
from .validate import build_context, constraint_violations, violation_keys

TYPES = ("OPERATIONS_OVERLAP", "PRECEDENCE_VIOLATED", "MATERIAL_CONSUMED_BEFORE_AVAILABLE",
         "SCHEDULE_OUTSIDE_SHIFT_CALENDAR", "CHANGEOVER_OMITTED")
RULE_OF = {"OPERATIONS_OVERLAP": "OPERATIONS_OVERLAP", "PRECEDENCE_VIOLATED": "PRECEDENCE_VIOLATED",
           "MATERIAL_CONSUMED_BEFORE_AVAILABLE": "MATERIAL_CONSUMED_BEFORE_AVAILABLE",
           "SCHEDULE_OUTSIDE_SHIFT_CALENDAR": "OUTSIDE_SHIFT_CALENDAR", "CHANGEOVER_OMITTED": "CHANGEOVER_OMITTED"}
MAX_TRIES = 600


class Injector:
    def __init__(self, cfg, t):
        self.cfg, self.t = cfg, t
        self.r = make_rng(cfg.seed, "anomalies")
        self.ctx = build_context(t)
        self.sched, self.cons = t["operation_schedule"], t["material_consumption"]
        self.cons_by_op = defaultdict(list)
        for c in self.cons:
            self.cons_by_op[c["op_id"]].append(c)
        self.by_id = {s["schedule_id"]: s for s in self.sched}
        self.sched_of_op = {s["op_id"]: s for s in self.sched}
        self.accepted = violation_keys(constraint_violations(self.ctx, self.sched, self.cons))
        if self.accepted:
            raise RuntimeError(f"baseline is not clean: {sorted(self.accepted)[:5]}")
        self.touched = set()
        self.gt = []
        self.shortfall = {}
        self.dur = {s["schedule_id"]: from_iso(s["end_ts"]) - from_iso(s["start_ts"]) for s in self.sched}
        self.index()

    # ---- helpers -------------------------------------------------------------------
    def index(self):
        ml = defaultdict(list)
        for s in self.sched:
            ml[s["machine_id"]].append(s)
        for lst in ml.values():
            lst.sort(key=lambda s: (from_iso(s["start_ts"]), s["schedule_id"]))
        self.ml = ml
        self.pos = {s["schedule_id"]: (m, i) for m, lst in ml.items() for i, s in enumerate(lst)}

    def neighbours(self, s):
        m, i = self.pos[s["schedule_id"]]
        lst = self.ml[m]
        return (lst[i - 1] if i else None), (lst[i + 1] if i + 1 < len(lst) else None)

    def op_neighbours(self, s):
        info = self.ctx["op"][s["op_id"]]
        oid, seq = info["order"], info["seq"]
        pred = succ = None
        for cand_seq, which in ((seq - 1, "p"), (seq + 1, "s")):
            op_id = f"OP-{oid}-{cand_seq:02d}"
            x = self.sched_of_op.get(op_id)
            if which == "p":
                pred = x
            else:
                succ = x
        return pred, succ

    def move(self, s, new_start, new_setup=None):
        setup = s["setup_min"] if new_setup is None else new_setup
        ch = [(s, {"start_ts": to_iso(new_start), "end_ts": to_iso(new_start + self.dur[s["schedule_id"]]),
                   "setup_min": setup, "setup_start_ts": to_iso(new_start - setup)})]
        for c in self.cons_by_op.get(s["op_id"], ()):
            ch.append((c, {"consumed_ts": to_iso(new_start)}))
        return ch

    def trial(self, changes, expected):
        old = [(row, {k: row[k] for k in nv}) for row, nv in changes]
        for row, nv in changes:
            row.update(nv)
        keys = violation_keys(constraint_violations(self.ctx, self.sched, self.cons))
        if keys - self.accepted == {expected} and self.accepted <= keys:
            self.accepted = keys
            return old
        for row, ov in old:
            row.update(ov)
        return None

    def record(self, issue, rule, entity_type, entity_id, op_id, related, desc, old, new):
        self.gt.append({"anomaly_id": f"GT-{len(self.gt) + 1:04d}", "issue_type": issue, "rule_id": rule,
                        "entity_type": entity_type, "entity_id": entity_id, "op_id": op_id,
                        "related_entity_id": related, "description": desc,
                        "original_value": json.dumps(old, sort_keys=True),
                        "injected_value": json.dumps(new, sort_keys=True)})

    def pr(self, s):
        return from_iso(s["start_ts"]), from_iso(s["end_ts"]), s["setup_min"]

    # ---- the five anomaly generators ------------------------------------------------------
    def candidates(self):
        c = list(self.sched)
        self.r.shuffle(c)
        return c[:MAX_TRIES]

    def inject_changeover(self):
        for B in self.candidates():
            if B["schedule_id"] in self.touched:
                continue
            A, _ = self.neighbours(B)
            if A is None or A["schedule_id"] in self.touched:
                continue
            bs, be, bsu = self.pr(B)
            a_end = self.pr(A)[1]
            old = {"start_ts": B["start_ts"], "end_ts": B["end_ts"], "setup_min": bsu}
            for new_start in (a_end, bs):
                if new_start == bs and bsu == 0:
                    continue
                res = self.trial(self.move(B, new_start, 0), (RULE_OF["CHANGEOVER_OMITTED"], B["schedule_id"]))
                if res:
                    self.touched.update({B["schedule_id"], A["schedule_id"]})
                    self.record("CHANGEOVER_OMITTED", "CHANGEOVER_OMITTED", "OPERATION_SCHEDULE",
                                B["schedule_id"], B["op_id"], A["schedule_id"],
                                f"changeover of {bsu} min before {B['op_id']} removed (setup_min=0) and the "
                                f"operation pulled forward to start right after {A['op_id']} ends",
                                old, {"start_ts": B["start_ts"], "end_ts": B["end_ts"], "setup_min": 0})
                    self.index()
                    return True
        return False

    def inject_overlap(self):
        for B in self.candidates():
            if B["schedule_id"] in self.touched:
                continue
            A, _ = self.neighbours(B)
            if A is None or A["schedule_id"] in self.touched:
                continue
            a_s, a_e, a_su = self.pr(A)
            bs, be, bsu = self.pr(B)
            dur = be - bs
            hi = max(6, min(a_e - a_s, dur) // 2)
            for _ in range(8):
                ov = self.r.randint(5, hi)
                new_start = a_e - ov + bsu
                if new_start >= bs or a_e - ov <= a_s - a_su:
                    continue
                old = {"start_ts": B["start_ts"], "end_ts": B["end_ts"]}
                res = self.trial(self.move(B, new_start), (RULE_OF["OPERATIONS_OVERLAP"], B["schedule_id"]))
                if res:
                    self.touched.update({B["schedule_id"], A["schedule_id"]})
                    self.record("OPERATIONS_OVERLAP", "OPERATIONS_OVERLAP", "OPERATION_SCHEDULE",
                                B["schedule_id"], B["op_id"], A["schedule_id"],
                                f"{B['op_id']} moved {bs - new_start} min earlier so it occupies "
                                f"{B['machine_id']} for {ov} min while {A['op_id']} is still running",
                                old, {"start_ts": B["start_ts"], "end_ts": B["end_ts"]})
                    self.index()
                    return True
        return False

    def inject_precedence(self):
        for B in self.candidates():
            if B["schedule_id"] in self.touched:
                continue
            P, _ = self.op_neighbours(B)
            if P is None or P["machine_id"] == B["machine_id"] or P["schedule_id"] in self.touched:
                continue
            A, _ = self.neighbours(B)
            bs, be, bsu = self.pr(B)
            p_end = self.pr(P)[1]
            lower = (self.pr(A)[1] if A else 0) + bsu
            hi = p_end - 1
            if lower > hi:
                continue
            for _ in range(8):
                new_start = self.r.randint(lower, hi)
                old = {"start_ts": B["start_ts"], "end_ts": B["end_ts"]}
                res = self.trial(self.move(B, new_start), (RULE_OF["PRECEDENCE_VIOLATED"], B["schedule_id"]))
                if res:
                    self.touched.update({B["schedule_id"], P["schedule_id"]})
                    self.record("PRECEDENCE_VIOLATED", "PRECEDENCE_VIOLATED", "OPERATION_SCHEDULE",
                                B["schedule_id"], B["op_id"], P["schedule_id"],
                                f"{B['op_id']} starts {p_end - new_start} min before its predecessor "
                                f"{P['op_id']} finishes",
                                old, {"start_ts": B["start_ts"], "end_ts": B["end_ts"]})
                    self.index()
                    return True
        return False

    def inject_calendar(self):
        for B in self.candidates():
            if B["schedule_id"] in self.touched:
                continue
            A, N = self.neighbours(B)
            if (A and A["schedule_id"] in self.touched) or (N and N["schedule_id"] in self.touched):
                continue
            bs, be, bsu = self.pr(B)
            dur = be - bs
            lo = (self.pr(A)[1] if A else 0) + bsu
            hi = (self.pr(N)[0] - self.pr(N)[2] if N else self.cfg.horizon_min) - dur
            if hi <= lo:
                continue
            info = self.ctx["op"][B["op_id"]]
            win = self.ctx["windows"][B["machine_id"]][info["req"]]
            for _ in range(40):
                ns = self.r.randint(lo, hi)
                if ns == bs or iv.covering(win, ns - bsu, ns + dur) is not None:
                    continue
                old = {"start_ts": B["start_ts"], "end_ts": B["end_ts"]}
                res = self.trial(self.move(B, ns), (RULE_OF["SCHEDULE_OUTSIDE_SHIFT_CALENDAR"], B["schedule_id"]))
                if res:
                    self.touched.update({B["schedule_id"]} | {x["schedule_id"] for x in (A, N) if x})
                    self.record("SCHEDULE_OUTSIDE_SHIFT_CALENDAR", "OUTSIDE_SHIFT_CALENDAR",
                                "OPERATION_SCHEDULE", B["schedule_id"], B["op_id"], None,
                                f"{B['op_id']} moved from {to_iso(bs)} to {to_iso(ns)}, outside the staffed "
                                f"calendar (crew>={info['req']}) of {B['machine_id']}",
                                old, {"start_ts": B["start_ts"], "end_ts": B["end_ts"]})
                    self.index()
                    return True
        return False

    def inject_material(self):
        used = defaultdict(int)
        for c in self.cons:
            used[c["lot_id"]] += c["qty"]
        lots_of = defaultdict(list)
        for l in self.t["inventory_lot"]:
            lots_of[l["material_id"]].append(l)
        cands = list(self.cons)
        self.r.shuffle(cands)
        for c in cands[:MAX_TRIES]:
            if c["op_id"] in self.touched:
                continue
            ts = from_iso(c["consumed_ts"])
            later = [l for l in lots_of[c["material_id"]]
                     if l["lot_id"] != c["lot_id"] and from_iso(l["available_ts"]) > ts
                     and l["qty_received"] - used[l["lot_id"]] >= c["qty"]]
            if not later:
                continue
            l2 = self.r.choice(later)
            old = {"lot_id": c["lot_id"]}
            res = self.trial([(c, {"lot_id": l2["lot_id"]})],
                             (RULE_OF["MATERIAL_CONSUMED_BEFORE_AVAILABLE"], c["consumption_id"]))
            if res:
                self.touched.add(c["op_id"])
                self.record("MATERIAL_CONSUMED_BEFORE_AVAILABLE", "MATERIAL_CONSUMED_BEFORE_AVAILABLE",
                            "MATERIAL_CONSUMPTION", c["consumption_id"], c["op_id"], l2["lot_id"],
                            f"{c['qty']} of {c['material_id']} drawn from {l2['lot_id']} which only becomes "
                            f"available {l2['available_ts']}, after the consumption at {c['consumed_ts']}",
                            old, {"lot_id": l2["lot_id"]})
                return True
        return False

    def run(self):
        fns = {"OPERATIONS_OVERLAP": self.inject_overlap, "PRECEDENCE_VIOLATED": self.inject_precedence,
               "MATERIAL_CONSUMED_BEFORE_AVAILABLE": self.inject_material,
               "SCHEDULE_OUTSIDE_SHIFT_CALENDAR": self.inject_calendar,
               "CHANGEOVER_OMITTED": self.inject_changeover}
        for _ in range(self.cfg.anomalies_per_type):
            for typ in TYPES:  # interleave types so a shortfall in one does not starve the others
                if self.shortfall.get(typ):
                    continue
                if not fns[typ]():
                    self.shortfall[typ] = True
        return self.gt


def inject(cfg, t):
    inj = Injector(cfg, t)
    gt = inj.run()
    counts = defaultdict(int)
    for g in gt:
        counts[g["issue_type"]] += 1
    return gt, dict(counts)
