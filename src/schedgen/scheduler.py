"""Stage 3: constructive scheduler (list scheduling).

Dispatch order : priority (urgent first), then earliest due date.
Per operation  : earliest-finish eligible machine, back-filling idle gaps.
Constraints    : routing precedence, release date, eligibility, machine calendar (crew-aware),
                 downtime, sequence-dependent changeovers, shared tooling, material lots.
An order is scheduled atomically: if any operation cannot be placed the whole order is
rolled back and recorded as UNSCHEDULABLE with an explicit reason.
"""
from bisect import bisect_left, bisect_right

from . import intervals as iv
from .rules import process_minutes, required_qty
from .timeutil import from_iso


class Placement:
    __slots__ = ("op_id", "order_id", "machine", "family", "start", "end", "setup", "tool", "req",
                 "allocs", "base", "ready_mat", "dur")

    @property
    def setup_start(self):
        return self.start - self.setup


class Result:
    def __init__(self):
        self.placements = []      # list[Placement]
        self.failures = {}        # order_id -> dict(reason, op_id, detail)
        self.order_delay = {}     # order_id -> (material_delay_min, capacity_delay_min, min_span)


def machine_windows(cal_rows, downtime_rows, horizon, use_downtime):
    """Per machine and crew level: merged available windows."""
    out = {}
    by_m = {}
    for c in cal_rows:
        by_m.setdefault(c["machine_id"], []).append(c)
    dts = {}
    for d in downtime_rows:
        dts.setdefault(d["machine_id"], []).append((from_iso(d["start_ts"]), from_iso(d["end_ts"])))
    for m, rows in by_m.items():
        lv = {}
        for level in (1, 2):
            w = iv.merge([(from_iso(c["shift_start_ts"]), from_iso(c["shift_end_ts"]))
                          for c in rows if c["crew_size"] >= level])
            w = iv.clip(w, 0, horizon)
            if use_downtime:
                w = iv.subtract(w, dts.get(m, []))
            lv[level] = w
        out[m] = lv
    return out


class Scheduler:
    def __init__(self, cfg, t):
        self.cfg, self.t = cfg, t
        self.H = cfg.horizon_min
        self.fam_of_sku = {p["sku"]: p["changeover_family"] for p in t["product"]}
        self.co = {(c["machine_group"], c["from_family"], c["to_family"]): c["setup_min"]
                   for c in t["changeover_matrix"]}
        win = machine_windows(t["machine_calendar"], t["machine_downtime"], self.H, True)
        self.mach = {}
        for m in t["machine"]:
            w = win.get(m["machine_id"], {1: [], 2: []})
            self.mach[m["machine_id"]] = {"group": m["machine_group"], "init": m["initial_family"],
                                          "avail": w, "ends": {k: [b for _, b in v] for k, v in w.items()}}
        self.elig = {}
        for b in t["routing_step_machine"]:
            self.elig.setdefault(b["step_id"], []).append((b["machine_id"], b["speed_factor"]))
        self.bom = {}
        for b in t["routing_step_material"]:
            self.bom.setdefault(b["step_id"], []).append(b)
        self.lots = {}
        for l in t["inventory_lot"]:
            self.lots.setdefault(l["material_id"], []).append(
                {"id": l["lot_id"], "avail": from_iso(l["available_ts"]), "rem": l["qty_received"]})
        for v in self.lots.values():
            v.sort(key=lambda x: (x["avail"], x["id"]))
        self.tl = {m: [] for m in self.mach}
        self.tool_pl = {}

    # ---- material ---------------------------------------------------------------
    def plan_material(self, op, undo):
        allocs, ready = [], 0
        for b in self.bom.get(op["step_id"], []):
            need = required_qty(op["qty"], b["qty_per_unit"], b["scrap_pct"])
            have = sum(l["rem"] for l in self.lots.get(b["material_id"], []))
            if have < need:
                return None, f"{b['material_id']}: required {need}, only {have} in all lots"
            for l in self.lots[b["material_id"]]:
                if need == 0:
                    break
                take = min(l["rem"], need)
                if take:
                    l["rem"] -= take
                    need -= take
                    allocs.append((l["id"], b["material_id"], take))
                    ready = max(ready, l["avail"])
                    undo.append(lambda l=l, take=take: l.__setitem__("rem", l["rem"] + take))
        return (allocs, ready), None

    # ---- machine slot search --------------------------------------------------------
    def _tool_conflict(self, tool, a, b, ignore=None):
        for p in self.tool_pl.get(tool, ()):
            if p is ignore:
                continue
            if p.setup_start < b and a < p.end:
                return p
        return None

    def _growth_ok(self, mid, nxt, s_n):
        if s_n <= nxt.setup:
            return True
        w = self.mach[mid]["avail"][nxt.req]
        if iv.covering(w, nxt.start - s_n, nxt.end) is None:
            return False
        if nxt.tool and self._tool_conflict(nxt.tool, nxt.start - s_n, nxt.start - nxt.setup, ignore=nxt):
            return False
        return True

    def find_slot(self, mid, t_lo, dur, fam, req, tool):
        m = self.mach[mid]
        avail, ends = m["avail"][req], m["ends"][req]
        tl = self.tl[mid]
        for i in range(len(tl) + 1):
            prev = tl[i - 1] if i else None
            nxt = tl[i] if i < len(tl) else None
            if nxt is not None and nxt.start <= t_lo:
                continue
            s_in = self.co[(m["group"], prev.family if prev else m["init"], fam)]
            s_n = self.co[(m["group"], fam, nxt.family)] if nxt else 0
            if nxt is not None and not self._growth_ok(mid, nxt, s_n):
                continue
            t = max(t_lo, (prev.end if prev else 0) + s_in)
            while True:
                j = bisect_left(ends, t + dur)
                placed = False
                while j < len(avail):
                    a, b = avail[j]
                    cand = max(t, a + s_in)
                    if cand + dur <= b:
                        t, placed = cand, True
                        break
                    j += 1
                if not placed:
                    return None
                if nxt is not None and t + dur + s_n > nxt.start:
                    break
                if tool:
                    c = self._tool_conflict(tool, t - s_in, t + dur)
                    if c is not None:
                        t = max(t, c.end + s_in)
                        continue
                return t, s_in, i, s_n
        return None

    # ---- order level -------------------------------------------------------------
    def schedule(self):
        res = Result()
        t = self.t
        ops_of = {}
        for op in t["operation"]:
            ops_of.setdefault(op["order_id"], []).append(op)
        for v in ops_of.values():
            v.sort(key=lambda o: o["op_seq"])
        orders = sorted(t["production_order"], key=lambda o: (o["priority"], from_iso(o["due_ts"]),
                                                              from_iso(o["release_ts"]), o["order_id"]))
        for o in orders:
            self._schedule_order(o, ops_of[o["order_id"]], res)
        return res

    def _schedule_order(self, o, ops, res):
        fam = self.fam_of_sku[o["sku"]]
        release = from_iso(o["release_ts"])
        undo, placed = [], []
        pred_end = release
        mat_delay = cap_delay = min_span = 0
        for op in ops:
            plan, why = self.plan_material(op, undo)
            if plan is None:
                return self._fail(o, op, "MATERIAL_SHORTAGE", why, undo, res)
            allocs, ready_mat = plan
            base = max(release, pred_end)
            t_lo = max(base, ready_mat)
            best = None
            for mid, speed in self.elig[op["step_id"]]:
                dur = process_minutes(op["std_process_min"], speed)
                slot = self.find_slot(mid, t_lo, dur, fam, op["operators_required"], op["tool_id"])
                if slot and (best is None or (slot[0] + dur, mid) < (best[0][0] + best[1], best[2])):
                    best = (slot, dur, mid)
            if best is None:
                return self._fail(o, op, "NO_CAPACITY_IN_HORIZON",
                                  f"no eligible machine can host {op['op_id']} after minute {t_lo} "
                                  f"within the {self.cfg.horizon_weeks}-week horizon", undo, res)
            (start, s_in, idx, s_n), dur, mid = best
            p = Placement()
            p.op_id, p.order_id, p.machine, p.family = op["op_id"], o["order_id"], mid, fam
            p.start, p.end, p.setup, p.tool, p.req = start, start + dur, s_in, op["tool_id"], op["operators_required"]
            p.allocs, p.base, p.ready_mat, p.dur = allocs, base, ready_mat, dur
            tl = self.tl[mid]
            nxt = tl[idx] if idx < len(tl) else None
            old_setup = nxt.setup if nxt else None
            tl.insert(idx, p)
            if nxt is not None:
                nxt.setup = s_n
            if p.tool:
                self.tool_pl.setdefault(p.tool, []).append(p)

            def undo_place(p=p, mid=mid, nxt=nxt, old=old_setup):
                self.tl[mid].remove(p)
                if nxt is not None:
                    nxt.setup = old
                if p.tool:
                    self.tool_pl[p.tool].remove(p)
            undo.append(undo_place)
            placed.append(p)
            mat_delay += max(0, ready_mat - base)
            cap_delay += start - max(base, ready_mat)
            min_span += min(process_minutes(op["std_process_min"], sp) for _, sp in self.elig[op["step_id"]])
            pred_end = p.end
        res.placements.extend(placed)
        res.order_delay[o["order_id"]] = (mat_delay, cap_delay, min_span)

    def _fail(self, o, op, reason, detail, undo, res):
        for u in reversed(undo):
            u()
        res.failures[o["order_id"]] = {"reason": reason, "op_id": op["op_id"], "detail": detail}
