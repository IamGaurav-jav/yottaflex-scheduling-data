import copy
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from schedgen import intervals as iv  # noqa: E402
from schedgen.config import PRESETS, with_overrides  # noqa: E402
from schedgen.export import load_csv_dir, write_dataset  # noqa: E402
from schedgen.pipeline import build_tables, compare_with_ground_truth, reproduce  # noqa: E402
from schedgen.rules import process_minutes, required_qty, std_process_minutes  # noqa: E402
from schedgen.schema import TABLE_ORDER  # noqa: E402
from schedgen.timeutil import from_iso, to_iso  # noqa: E402
from schedgen.validate import validate  # noqa: E402

STARTER = PRESETS["starter"]
TINY = with_overrides(STARTER, skus=10, machines=5, orders=30, materials=15, anomalies_per_type=1)


class Primitives(unittest.TestCase):
    def test_merge_subtract_cover(self):
        self.assertEqual(iv.merge([(5, 10), (0, 5), (12, 14), (13, 20)]), [(0, 10), (12, 20)])
        self.assertEqual(iv.subtract([(0, 100)], [(10, 20), (50, 60)]), [(0, 10), (20, 50), (60, 100)])
        w = [(0, 10), (20, 30)]
        self.assertEqual(iv.covering(w, 2, 10), (0, 10))
        self.assertIsNone(iv.covering(w, 8, 12))
        self.assertIsNone(iv.covering(w, 10, 11))

    def test_rules(self):
        self.assertEqual(process_minutes(100, 1.25), 80)
        self.assertEqual(process_minutes(101, 1.25), 81)  # ceil
        self.assertEqual(std_process_minutes(10, 1.55, 5), 21)
        self.assertEqual(required_qty(100, 0.5, 10), 55)

    def test_time_roundtrip(self):
        for m in (-5000, 0, 1, 1440, 123456):
            self.assertEqual(from_iso(to_iso(m)), m)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t, cls.gt, cls.info = build_tables(STARTER)
        cls.report = validate(cls.t)

    def test_baseline_is_clean(self):
        b = self.info["baseline_validation"]
        self.assertEqual(b["integrity_failures"], 0)
        self.assertEqual(b["constraint_violations_total"], 0, b["constraint_violations_by_rule"])

    def test_final_dataset_integrity(self):
        failed = [c for c in self.report["checks"] if c["status"] == "FAIL" and c["category"] != "constraint"]
        self.assertEqual(failed, [])

    def test_ground_truth_matches_validator_exactly(self):
        res = compare_with_ground_truth(self.report, self.gt)
        self.assertTrue(res["exact_match"], res)
        counts = {}
        for g in self.gt:
            counts[g["issue_type"]] = counts.get(g["issue_type"], 0) + 1
        self.assertEqual(set(counts), {"OPERATIONS_OVERLAP", "PRECEDENCE_VIOLATED",
                                       "MATERIAL_CONSUMED_BEFORE_AVAILABLE", "SCHEDULE_OUTSIDE_SHIFT_CALENDAR",
                                       "CHANGEOVER_OMITTED"})
        self.assertTrue(all(v == STARTER.anomalies_per_type for v in counts.values()), counts)

    def test_ground_truth_not_leaked_into_inputs(self):
        self.assertNotIn("ground_truth", self.t)
        banned = {"issue_type", "anomaly_id", "ground_truth", "is_anomaly", "label"}
        for name in TABLE_ORDER:
            self.assertFalse(banned & set(self.t[name][0]), name)

    def test_exceptions_cover_infeasible_and_late(self):
        types = {e["exception_type"] for e in self.t["schedule_exception"]}
        self.assertIn("LATE_ORDER", types)
        self.assertTrue(types & {"MATERIAL_SHORTAGE", "NO_CAPACITY_IN_HORIZON"})

    def test_edge_case_coverage(self):
        t = self.t
        self.assertTrue(any(o["order_type"] == "URGENT" for o in t["production_order"]))
        self.assertTrue(t["machine_downtime"])
        self.assertTrue(any(o["tool_id"] for o in t["operation"]))
        self.assertTrue(any(l["delay_min"] > 0 for l in t["inventory_lot"]))
        self.assertTrue(any(r["version"] == 2 for r in t["routing"]))

    def test_metrics_computable(self):
        from schedgen.metrics import compute_metrics
        m = compute_metrics(self.t, STARTER.horizon_min)
        self.assertGreater(m["makespan"]["makespan_min"], 0)
        self.assertIn("total_tardiness_min", m["lateness"])

    def test_deterministic(self):
        t2, gt2, _ = build_tables(STARTER)
        self.assertEqual(self.t, t2)
        self.assertEqual(self.gt, gt2)

    def test_seed_changes_output(self):
        t2, _, _ = build_tables(with_overrides(STARTER, seed=1))
        self.assertNotEqual(self.t["production_order"], t2["production_order"])

    def test_parameterised_scale(self):
        t, gt, info = build_tables(TINY)
        self.assertEqual(len(t["production_order"]), 30)
        self.assertEqual(len(t["operation"]), TINY.ops)
        self.assertEqual(len(t["machine"]), 5)
        self.assertEqual(info["baseline_validation"]["integrity_failures"], 0)
        rep = validate(t)
        self.assertEqual(rep["integrity_failures"], 0)
        self.assertTrue(compare_with_ground_truth(rep, gt)["exact_match"])

    def test_no_anomaly_mode(self):
        t, gt, _ = build_tables(with_overrides(TINY, inject_anomalies=False))
        self.assertEqual(gt, [])
        self.assertEqual(validate(t)["constraint_violations_total"], 0)


class FullPreset(unittest.TestCase):
    """Volumes and coverage requested in the brief."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = PRESETS["full"]
        cls.t, cls.gt, cls.info = build_tables(cls.cfg)

    def test_required_volumes(self):
        t = self.t
        self.assertEqual(len(t["product"]), 100)
        self.assertEqual(len(t["machine"]), 40)
        self.assertEqual(len(t["production_order"]), 500)
        self.assertEqual(len(t["operation"]), 2000)
        self.assertEqual(len(t["material"]), 200)
        self.assertEqual(self.cfg.seed, 20261022)

    def test_coverage_and_exactness(self):
        t = self.t
        self.assertTrue(any(o["order_type"] == "URGENT" for o in t["production_order"]))
        self.assertTrue(any(p["status"] == "DISCONTINUED" for p in t["product"]))
        self.assertTrue(any(o["routing_id"].endswith("-02") for o in t["production_order"]))
        self.assertTrue(any(l["delay_min"] > 0 for l in t["inventory_lot"]))
        self.assertTrue(any(d["downtime_type"] == "BREAKDOWN" for d in t["machine_downtime"]))
        types = {e["root_cause"] for e in t["schedule_exception"]}
        self.assertTrue({"MATERIAL_SHORTAGE", "NO_CAPACITY_IN_HORIZON", "MACHINE_CAPACITY"} <= types, types)
        rep = validate(t)
        self.assertEqual(rep["integrity_failures"], 0)
        self.assertTrue(compare_with_ground_truth(rep, self.gt)["exact_match"])
        self.assertEqual(len(self.gt), 5 * self.cfg.anomalies_per_type)


class ExportAndRepro(unittest.TestCase):
    def test_csv_roundtrip_validates_and_sql_loads(self):
        t, gt, _ = build_tables(TINY)
        with tempfile.TemporaryDirectory() as d:
            write_dataset(d, t, gt)
            loaded = load_csv_dir(os.path.join(d, "csv"))
            self.assertEqual(validate(loaded)["integrity_failures"], 0)
            self.assertEqual(loaded["operation_schedule"], t["operation_schedule"])
            con = sqlite3.connect(":memory:")
            for fn in ("schema.sql", "seed.sql"):
                with open(os.path.join(d, "sql", fn)) as fh:
                    con.executescript(fh.read())
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("select count(*) from production_order").fetchone()[0], 30)
            con.close()

    def test_reproducibility_checksums(self):
        res = reproduce(TINY, "tiny")
        self.assertTrue(res["checksums_identical"])
        self.assertTrue(res["row_counts_identical"])


class ValidatorNegativeTests(unittest.TestCase):
    """The validator must actually catch corruption, not just pass clean data."""

    @classmethod
    def setUpClass(cls):
        cls.base, _, _ = build_tables(with_overrides(TINY, inject_anomalies=False))

    def corrupt(self, fn):
        t = copy.deepcopy(self.base)
        fn(t)
        return validate(t)

    def failing(self, rep):
        return [c["check"] for c in rep["checks"] if c["status"] == "FAIL" and c["category"] != "constraint"]

    def test_duplicate_primary_key(self):
        rep = self.corrupt(lambda t: t["machine"].append(dict(t["machine"][0])))
        self.assertIn("primary key uniqueness", self.failing(rep))

    def test_broken_foreign_key(self):
        rep = self.corrupt(lambda t: t["production_order"][0].__setitem__("sku", "SKU-999"))
        self.assertIn("foreign key integrity (all joins)", self.failing(rep))

    def test_invalid_enum(self):
        rep = self.corrupt(lambda t: t["machine"][0].__setitem__("status", "BROKEN"))
        self.assertIn("machine: allowed values", self.failing(rep))

    def test_derived_value_mismatch(self):
        rep = self.corrupt(lambda t: t["operation_schedule"][0].__setitem__("process_min", 1))
        self.assertTrue(any("arithmetic" in c for c in self.failing(rep)))

    def test_illegal_lifecycle_transition(self):
        def f(t):
            e = next(e for e in t["status_event"] if e["entity_type"] == "ORDER" and e["to_state"] == "SCHEDULED")
            e["from_state"], e["to_state"] = "SCHEDULED", "CREATED"
        self.assertTrue(any("lifecycle" in c for c in self.failing(self.corrupt(f))))

    def test_overlap_is_detected_as_constraint_violation(self):
        def f(t):
            by_m = {}
            for s in t["operation_schedule"]:
                by_m.setdefault(s["machine_id"], []).append(s)
            lst = next(v for v in by_m.values() if len(v) > 1)
            lst.sort(key=lambda s: s["start_ts"])
            lst[1]["start_ts"] = lst[0]["start_ts"]
            lst[1]["end_ts"] = lst[0]["end_ts"]
        rep = self.corrupt(f)
        self.assertIn("OPERATIONS_OVERLAP", rep["constraint_violations_by_rule"])


if __name__ == "__main__":
    unittest.main()
