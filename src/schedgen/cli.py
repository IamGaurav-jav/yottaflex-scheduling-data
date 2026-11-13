import argparse
import json
import os
import sys

from .config import PRESETS, with_overrides
from .export import load_csv_dir, load_ground_truth
from .pipeline import compare_with_ground_truth, generate, reproduce
from .validate import validate


def _cfg(args):
    base = PRESETS[args.preset]
    return with_overrides(base, seed=args.seed, skus=args.skus, machines=args.machines, orders=args.orders,
                          materials=args.materials, anomalies_per_type=args.anomalies_per_type,
                          horizon_weeks=args.horizon_weeks)


def main(argv=None):
    p = argparse.ArgumentParser(prog="schedgen", description="Synthetic production-scheduling dataset generator")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "reproduce"):
        s = sub.add_parser(name)
        s.add_argument("--preset", choices=sorted(PRESETS), default="full")
        s.add_argument("--out", default=None)
        for a in ("seed", "skus", "machines", "orders", "materials", "anomalies_per_type", "horizon_weeks"):
            s.add_argument(f"--{a.replace('_', '-')}", dest=a, type=int, default=None)
    s = sub.add_parser("validate", help="validate an exported dataset directory (CSV files)")
    s.add_argument("dir")
    sub.add_parser("docs", help="regenerate docs/data_dictionary.md")
    args = p.parse_args(argv)

    if args.cmd == "generate":
        cfg = _cfg(args)
        out = args.out or os.path.join("data", args.preset)
        _, gt, report, man = generate(cfg, out, args.preset)
        print(f"wrote {out}  rows={man['row_counts']}")
        print(f"integrity failures: {report['integrity_failures']}; constraint violations: "
              f"{report['constraint_violations_total']} {report['constraint_violations_by_rule']}")
        gtc = report["ground_truth_check"]
        print(f"ground-truth check: {gtc['detected_by_validator']}/{gtc['expected_anomalies']} detected, "
              f"exact match: {gtc['exact_match']}")
        return 0 if report["integrity_failures"] == 0 and gtc["exact_match"] else 1
    if args.cmd == "reproduce":
        cfg = _cfg(args)
        out = args.out or os.path.join("data", args.preset)
        res = reproduce(cfg, args.preset, compare_dir=out)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "reproducibility_report.json"), "w") as fh:
            json.dump(res, fh, indent=1)
        print(json.dumps({k: v for k, v in res.items() if k != "manifest_sha256_of_files"}, indent=1))
        ok = res["checksums_identical"] and res["row_counts_identical"] and res.get("matches_committed_dataset", True)
        return 0 if ok else 1
    if args.cmd == "validate":
        t = load_csv_dir(os.path.join(args.dir, "csv"))
        rep = validate(t)
        gt_path = os.path.join(args.dir, "ground_truth.csv")
        print(f"integrity failures: {rep['integrity_failures']}")
        for c in rep["checks"]:
            if c["status"] == "FAIL" and c["category"] != "constraint":
                print("FAIL", c["check"], c["examples"])
        print(f"constraint violations: {rep['constraint_violations_total']} {rep['constraint_violations_by_rule']}")
        if os.path.exists(gt_path):
            gt = load_ground_truth(gt_path)
            # compare only after validation ran; the validator itself never reads ground truth
            print("ground-truth check:", json.dumps({k: v for k, v in compare_with_ground_truth(rep, gt).items()
                                                      if k != "missed" or v}))
        return 0 if rep["integrity_failures"] == 0 else 1
    if args.cmd == "docs":
        from .docs import write_data_dictionary
        write_data_dictionary(os.path.join("docs", "data_dictionary.md"))
        print("wrote docs/data_dictionary.md")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
