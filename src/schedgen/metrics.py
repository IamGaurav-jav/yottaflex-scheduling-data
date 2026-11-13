"""Evaluation metrics computed from the tables (makespan, lateness, utilisation, ...)."""
from collections import defaultdict

from .scheduler import machine_windows
from .timeutil import from_iso

PRIORITY_WEIGHT = {1: 4, 2: 3, 3: 2, 4: 1}


def compute_metrics(t, horizon_min):
    orders = t["production_order"]
    sched = t["operation_schedule"]
    sched_orders = [o for o in orders if o["status"] == "SCHEDULED"]
    late = [o for o in sched_orders if o["lateness_min"]]
    m = {"orders": {
        "total": len(orders), "scheduled": len(sched_orders),
        "unschedulable": sum(1 for o in orders if o["status"] == "UNSCHEDULABLE"),
        "late": len(late),
        "on_time_pct_of_scheduled": round(100 * (1 - len(late) / max(1, len(sched_orders))), 2)}}
    lat = [o["lateness_min"] for o in late]
    m["lateness"] = {
        "total_tardiness_min": sum(lat), "avg_tardiness_min_of_late": round(sum(lat) / len(lat), 1) if lat else 0,
        "max_tardiness_min": max(lat, default=0),
        "weighted_tardiness_min": sum(o["lateness_min"] * PRIORITY_WEIGHT[o["priority"]] for o in late),
        "weights_by_priority": PRIORITY_WEIGHT}
    by_pri = defaultdict(lambda: [0, 0])
    for o in sched_orders:
        by_pri[o["priority"]][0] += 1
        by_pri[o["priority"]][1] += 1 if o["lateness_min"] else 0
    m["on_time_pct_by_priority"] = {str(p): round(100 * (1 - l / n), 1) for p, (n, l) in sorted(by_pri.items())}
    if sched:
        starts = [from_iso(s["start_ts"]) for s in sched]
        ends = [from_iso(s["end_ts"]) for s in sched]
        m["makespan"] = {"first_start": min(s["start_ts"] for s in sched), "last_end": max(s["end_ts"] for s in sched),
                         "makespan_min": max(ends) - min(starts), "makespan_days": round((max(ends) - min(starts)) / 1440, 2)}
    win = machine_windows(t["machine_calendar"], t["machine_downtime"], horizon_min, True)
    group = {x["machine_id"]: x["machine_group"] for x in t["machine"]}
    busy, setup, avail = defaultdict(int), defaultdict(int), defaultdict(int)
    for s in sched:
        g = group[s["machine_id"]]
        busy[g] += from_iso(s["end_ts"]) - from_iso(s["start_ts"])
        setup[g] += s["setup_min"]
    for mid, w in win.items():
        avail[group[mid]] += sum(b - a for a, b in w[1])
    m["utilisation_by_machine_group"] = {
        g: {"busy_pct": round(100 * (busy[g] + setup[g]) / avail[g], 1) if avail[g] else 0,
            "process_min": busy[g], "setup_min": setup[g], "available_min": avail[g]}
        for g in sorted(avail)}
    tb, ts_, ta = sum(busy.values()), sum(setup.values()), sum(avail.values())
    m["utilisation_overall_pct"] = round(100 * (tb + ts_) / ta, 1) if ta else 0
    m["changeover_share_of_busy_pct"] = round(100 * ts_ / (tb + ts_), 1) if tb + ts_ else 0
    exc = defaultdict(int)
    for e in t["schedule_exception"]:
        exc[f"{e['exception_type']}/{e['root_cause']}"] += 1
    m["exceptions"] = dict(sorted(exc.items()))
    m["material"] = {"lots": len(t["inventory_lot"]), "delayed_lots": sum(1 for l in t["inventory_lot"] if l["delay_min"] > 0),
                     "urgent_orders": sum(1 for o in orders if o["order_type"] == "URGENT")}
    return m
