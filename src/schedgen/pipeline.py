"""End-to-end pipeline: master -> orders/supply -> schedule -> derive -> validate -> inject -> export."""
import json
import os
import shutil
import sys
import tempfile

from . import __version__
from .anomalies import inject
from .config import PRESETS, Config
from .derive import build_exceptions, build_outputs, finalize
from .export import checksums, write_dataset
from .master import build_master
from .metrics import compute_metrics
from .orders import build_lots, build_orders
from .scheduler import Scheduler
from .schema import TABLE_ORDER
from .validate import validate, violation_keys


def build_tables(cfg):
    """Returns (tables, ground_truth, info). Deterministic for a given cfg."""
    t = build_master(cfg)
    build_orders(cfg, t)
    build_lots(cfg, t)
    res = Scheduler(cfg, t).schedule()
    build_outputs(t, res)
    finalize(t)
    build_exceptions(t, res)
    info = {"baseline_validation": validate(t), "baseline_metrics": compute_metrics(t, cfg.horizon_min)}
    gt, injected = [], {}
    if cfg.inject_anomalies:
        gt, injected = inject(cfg, t)
        finalize(t)
        build_exceptions(t, res)
    info["injected_counts"] = injected
    info["unschedulable_orders"] = {k: v["reason"] for k, v in sorted(res.failures.items())}
    t.pop("_meta", None)
    return t, gt, info


def compare_with_ground_truth(validation, gt):
    found = {(v["rule"], v["entity_id"]) for v in validation["constraint_violations"]}
    expected = {(g["rule_id"], g["entity_id"]) for g in gt}
    return {"expected_anomalies": len(expected), "detected_by_validator": len(found & expected),
            "missed": sorted(map(list, expected - found)), "unexpected_violations": sorted(map(list, found - expected)),
            "exact_match": found == expected}


def generate(cfg, out_dir, name="custom"):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    t, gt, info = build_tables(cfg)
    write_dataset(out_dir, t, gt)
    report = validate(t)
    report["ground_truth_check"] = compare_with_ground_truth(report, gt)
    report["baseline_before_injection"] = {
        "integrity_failures": info["baseline_validation"]["integrity_failures"],
        "constraint_violations_total": info["baseline_validation"]["constraint_violations_total"]}
    with open(os.path.join(out_dir, "validation_report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    metrics = {"final_dataset": compute_metrics(t, cfg.horizon_min), "baseline_before_injection": info["baseline_metrics"],
               "note": "final_dataset includes the injected anomalies; baseline_before_injection is the clean schedule."}
    with open(os.path.join(out_dir, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=1)
    files = checksums(out_dir)
    manifest = {
        "generator": "schedgen", "version": __version__, "dataset": name,
        "command": f"python run.py generate --preset {name}" if name in PRESETS else "python run.py generate ...",
        "seed": cfg.seed, "config": cfg.__dict__ | {"ops": cfg.ops},
        "row_counts": {n: len(t[n]) for n in TABLE_ORDER} | {"ground_truth": len(gt)},
        "injected_anomalies": info["injected_counts"],
        "unschedulable_orders": info["unschedulable_orders"],
        "sources": [{"type": "synthetic", "external_data": False,
                     "provenance": "All values are produced by seeded pseudo-random draws in src/schedgen; "
                                   "no public/third-party datasets, no personal data."}],
        "files_sha256": files}
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    return t, gt, report, manifest


def reproduce(cfg, name="custom", compare_dir=None):
    """Generate twice into temp dirs with the same seed; compare row counts and checksums."""
    tmp = tempfile.mkdtemp(prefix="schedgen_repro_")
    try:
        runs = []
        for i in (1, 2):
            _, _, _, man = generate(cfg, os.path.join(tmp, f"run{i}"), name)
            runs.append(man)
        same = runs[0]["files_sha256"] == runs[1]["files_sha256"]
        out = {"seed": cfg.seed, "dataset": name, "runs": 2, "row_counts_identical": runs[0]["row_counts"] == runs[1]["row_counts"],
               "checksums_identical": same, "files_compared": len(runs[0]["files_sha256"]),
               "row_counts": runs[0]["row_counts"], "manifest_sha256_of_files": runs[0]["files_sha256"]}
        if compare_dir and os.path.exists(os.path.join(compare_dir, "manifest.json")):
            with open(os.path.join(compare_dir, "manifest.json")) as fh:
                committed = json.load(fh)
            out["matches_committed_dataset"] = committed["files_sha256"] == runs[0]["files_sha256"]
        out["python"] = sys.version.split()[0]
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
