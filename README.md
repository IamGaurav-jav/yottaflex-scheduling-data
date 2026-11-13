# Plant scheduling - synthetic dataset generator

Generates a reproducible, relational manufacturing dataset - master data, orders, operations, a **feasible schedule**
that respects routing, machine capacity, sequence-dependent changeovers, labour shifts, downtime, shared tooling and
material availability - then injects **five labelled anomaly types**, validates everything, and writes a separate
`ground_truth` file. Python standard library only; nothing to install.

> Submission for the YottaFlex AI 72-hour challenge. Read [`docs/DECISIONS.md`](docs/DECISIONS.md) for the reasoning and
> trade-offs, [`docs/AI_USAGE.md`](docs/AI_USAGE.md) for the AI disclosure, and [`prompts/PROMPTS.md`](prompts/PROMPTS.md)
> for the prompt history.

## Quick start

Requires Python 3.9+ (tested on 3.14.8). No dependencies.

```bash
python run.py generate --preset starter      # 100 orders / 400 ops, <1 s      -> data/starter
python run.py generate --preset full         # 500 orders / 2,000 ops, ~7 s    -> data/full  (seed 20261022)
python run.py validate data/full             # re-validate the exported CSVs
python run.py reproduce --preset full        # generate twice, compare checksums + committed files
python -m unittest discover -s tests         # 24 tests, ~8 s
```

Scale is parameterised and shared by both presets, e.g.
`python run.py generate --preset full --orders 800 --machines 60 --seed 7 --out /tmp/big`
(flags: `--seed --skus --machines --orders --materials --anomalies-per-type --horizon-weeks`).

## What you get (`data/<preset>/`)

| Path | Content |
|---|---|
| `csv/`, `json/` | 20 tables (same rows in both formats) |
| `sql/schema.sql`, `sql/seed.sql` | SQLite DDL with PK/FK/UNIQUE/CHECK + INSERTs (loads with foreign keys on) |
| `ground_truth.csv/json` | labels for the injected anomalies - **separate from the inputs** |
| `validation_report.json` | every check, PASS/FAIL, examples, violations, ground-truth comparison |
| `metrics.json` | makespan, lateness, on-time %, utilisation, changeover share, exceptions (before and after injection) |
| `manifest.json` | generator command, seed, config, row counts, **provenance/source manifest**, SHA-256 of every file |
| `reproducibility_report.json` | two fresh runs compared: row counts and checksums |

Schema and rules: [`docs/data_dictionary.md`](docs/data_dictionary.md) (generated from `src/schedgen/schema.py`: field type,
required, allowed values, units, uniqueness, validation rule, plus PK/FK and cardinality for every table).

## Volumes (full preset, seed 20261022)

100 SKUs - 40 machines - 500 orders (41 urgent) - 2,000 operations - 200 materials - 490 lots (some delayed) -
24 shared tools - 77 downtime events - 1,942 calendar windows - 576 changeover rows. Exact counts: `data/full/manifest.json`.

## How the key behaviour is verified

| Claim | Evidence |
|---|---|
| Baseline schedule satisfies all hard constraints | `validation_report.json -> baseline_before_injection`: 0 integrity failures, 0 constraint violations |
| Calendars, downtime, crew, tools, changeovers, precedence, materials respected | validator rules `OUTSIDE_SHIFT_CALENDAR`, `MACHINE_DOWNTIME_CONFLICT`, `TOOL_DOUBLE_BOOKED`, `CHANGEOVER_OMITTED`, `PRECEDENCE_VIOLATED`, `OPERATIONS_OVERLAP`, `MATERIAL_CONSUMED_BEFORE_AVAILABLE`, `LOT_OVERDRAWN` |
| Anomaly labels are exactly right | `ground_truth_check.exact_match = true` (50/50 detected, 0 unexpected) - validator never reads ground truth |
| Makespan and lateness computable; infeasibility explained | `metrics.json`; 16 unschedulable + 105 late orders each have one `schedule_exception` with a computed root cause |
| Valid joins / IDs / reconciliation | 143 PASS integrity checks (PK, FK, enums, dates, arithmetic, lifecycle); SQL loads with `foreign_key_check` empty |
| Reproducible | `reproducibility_report.json`: identical checksums across two runs and against committed files |

Headline results (clean baseline, full preset): 484 of 500 orders scheduled, 78% of scheduled orders on time
(urgent 100%, low priority 39%), makespan 32.9 days, overall utilisation 41.5%, changeovers 7.5% of busy time.
The injected anomalies are 10 of each: overlap, precedence, material-before-availability, outside shift calendar,
changeover omitted.

## Pipeline

`master.py` (parents: products, routings, machines, calendars, downtime, tooling, changeovers, materials/BOM) ->
`orders.py` (orders/operations with valid parent ids, load calibration, lots sized from demand) ->
`scheduler.py` (priority + EDD list scheduling with back-filling, atomic per order) ->
`derive.py` (events, consumption, derived columns, exceptions) -> `validate.py` ->
`anomalies.py` (inject; each edit accepted only if the validator reports exactly the intended violation) ->
`export.py` / `pipeline.py` / `cli.py`.

## Assumptions (full list with alternatives in `docs/DECISIONS.md`)

- A1 `PROD-2026-NNNNNN` is the production-order id; the product key is `SKU-NNN`.
- A2 One offline planning run at the horizon start with a fully known order book; urgent orders are just top priority.
- A3 Operations are non-preemptive and may span adjacent staffed shifts, never a gap or downtime.
- A4 Labour = staffed crew windows (steps need 1 or 2 operators); no named operators.
- A5 All data is synthetic: no external datasets, no personal data (see `manifest.json -> sources`).

## Known limitations

Heuristic (not optimal) scheduler; strict priority starves low-priority orders; no re-scheduling after disruptions;
due dates are not tied to work content; validator shares small formula helpers with the scheduler (unit-tested
separately). See "Limitations" in `docs/DECISIONS.md`.

## Layout

```
run.py                 launcher (python run.py <command>)
src/schedgen/          generator, scheduler, validator, injector, exporters
tests/                 unittest suite (incl. negative tests for the validator)
data/starter, data/full  committed outputs
docs/                  DECISIONS.md, AI_USAGE.md, data_dictionary.md
prompts/PROMPTS.md     prompt history
```
