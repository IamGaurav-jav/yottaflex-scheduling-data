# Design decisions

For each feature: what was chosen, what else was possible, and why. "Trade-off" lists what the choice costs.

## 1. What is being built
**Chosen:** a *reproducible synthetic-data generator for a plant-scheduling domain* whose schedule comes from a real
(constructive) scheduler, plus a validator, labelled anomalies, ground truth and evaluation metrics.
**Alternative:** a scheduling *application* (UI/API) with optimisation. **Why not:** the brief is a data-generation
brief ("Synthetic Data Generation Instructions", "Evaluation Focus for Generated Data"); reviewers grade valid joins,
derived values, edge cases, separate ground truth and reproducibility, not UI.

## 2. Stack: Python 3 standard library only
**Alternatives:** pandas/numpy, Faker, SQLAlchemy, OR-Tools. **Why stdlib:** zero install friction ("another person can
install dependencies ... and run it" = nothing to install), no cross-version numeric drift in checksums, and every
rule is explicit. **Trade-off:** more code than pandas one-liners; no vectorised speed (not needed: full run ~7 s).

## 3. Determinism
**Chosen:** `random.Random(f"{seed}:{stage}")` - one independent stream per stage (products, routings, calendar,
orders, lots, anomalies...). No wall-clock, no set/dict-order dependence in outputs (everything sorted).
**Alternative:** one global RNG. **Why not:** adding one draw in one stage would silently change every later stage.
**Proof:** `python run.py reproduce` regenerates twice and compares SHA-256 of all 44 files + row counts.

## 4. Time model
**Chosen:** integer minutes from a Monday horizon start (2026-11-02), exported as plant-local ISO-8601 strings.
5-week horizon. **Alternatives:** floats/hours (rounding drift), timezone-aware datetimes (noise, no value here).
Integer minutes make "no overlap", "fits in window" and reconciliation exact.

## 5. IDs
The brief lists `PROD-2026-000001; ROUTE-<sku>-01; OP-<order-id>-01; MACH-001; SCH-2026-000001`. **Assumption A1:**
`PROD-...` is the *production order* id (an order id is needed for `OP-<order-id>`), so the Product key is a separate
`SKU-001`. Hence `OP-PROD-2026-000001-01` (literal; ugly but exactly what the pattern says). IDs are immutable;
`SCH-` ids are assigned to the clean baseline in start-time order and never renumbered after anomalies are injected.

## 6. Schema as the single source of truth (`schema.py`)
One declarative definition (type, required, enum, unit, unique, range, pattern, rule, derived) drives CSV headers,
SQL DDL (with PK/FK/UNIQUE/CHECK), `docs/data_dictionary.md` and schema validation.
**Alternative:** hand-written dictionary + DDL. **Why not:** they drift apart. **Verified:** exported SQL loads into
SQLite with `PRAGMA foreign_keys=ON` and `foreign_key_check` is empty.

## 7. Entities beyond the ten core ones
Needed to be faithful to the brief's rules: `RoutingStep` (the routing's operation template; the brief's `Operation`
is the order-level instance), bridge tables `routing_step_machine` (eligible machines + speed) and
`routing_step_material` (BOM), `Tooling` (shared tooling), `Shift` (labour shifts), `MachineDowntime`,
`MaterialConsumption`, `StatusEvent` (append-only history), `ScheduleException` (explanations).

## 8. Routing versions and effective dating
~12% of SKUs get a v2 routing effective 2026-11-08; v1 is closed the day before. Orders pick the version valid on their
release date (validated). Discontinued SKUs exist but are never ordered (validated). This gives "effective dates and
lifecycle states" real test coverage instead of decorative columns.

## 9. Calendars, labour shifts, downtime
**Chosen:** `MachineCalendar` rows are *staffed windows* (4 shift patterns; Sat staffing; plant holiday; 7% random
absences) with `crew_size`. Steps need 1 or 2 operators, so a 2-operator step can only run where crew >= 2 (night and
Saturday crews are 1). Downtime is a separate table subtracted from availability. Operations are non-preemptive and may
span *adjacent* shifts (handover assumed) but not a gap.
**Alternatives:** explicit operator entities and assignment (much bigger model); preemptive operations.
**Trade-off:** labour is capacity-by-window, not named people.

## 10. Changeovers
Sequence-dependent matrix per machine group over 8 families, asymmetric, diagonal = short clean-down (never 0).
A machine's occupancy for an operation is `[start - setup_min, end)`. First operation uses the machine's
`initial_family`. **Alternative:** symmetric or per-product setup. **Why this:** it is the standard textbook form and
makes "changeover omitted" a precise, checkable rule: `setup_min < matrix(prev_family -> family)`.

## 11. Load calibration (not hand-tuned constants)
First run: one machine group sat at 78% while others were at 25%, and 23% of orders were infeasible.
**Chosen:** after orders are drafted, rescale each group's cycle times so demand/capacity hits a seeded target
(0.40-0.85). **Alternatives:** hand-tune constants (breaks when scale changes), random durations (unrealistic
bottlenecks). **Result:** starter and full both land at 40-55% overall utilisation with visible bottlenecks.

## 12. Scheduler algorithm
**Chosen:** constructive list scheduling: orders sorted by (priority, due date); each operation goes to the eligible
machine giving the earliest finish, back-filling idle gaps; inserting into a gap re-checks the successor's changeover,
calendar and tool window. An order is scheduled atomically - any failure rolls the whole order back (undo log).
**Alternatives:** CP-SAT/MIP (optimal, but heavy dependency and hard to express sequence-dependent setups over
calendars within the time budget), genetic algorithm (non-trivial reproducibility), append-only dispatch (simple but
wastes early idle time, so urgent late-released orders would be placed badly).
**Trade-off:** not optimal; strict priority starves priority-4 orders (39% on time vs ~100% for urgent) - visible in
`metrics.json` and listed as a limitation, not hidden.

## 13. Material model
Each material has 1-4 lots: an on-hand lot at day 0 plus later receipts; ~25% of later lots arrive late vs their
promise; ~6% of materials are deliberately under-supplied. Operations draw from lots FIFO by availability and consume at
`start_ts`. If total remaining stock is short the order is rolled back with `MATERIAL_SHORTAGE`.
**Alternative:** independent random availability dates. **Why not:** the brief wants rules, not random labels.
**Trade-off:** FIFO allocation is greedy in *scheduling* order, not time order.

## 14. Shared tooling
A tool is one physical unit; two operations on different machines cannot hold it at the same time
(`[setup_start, end)` windows). Tool pools are smaller than machine groups to create contention.

## 15. Exceptions and explanations
Every unschedulable or late order gets exactly one `ScheduleException` with a computed root cause:
`MATERIAL_SHORTAGE`, `NO_CAPACITY_IN_HORIZON`, `DUE_DATE_TOO_TIGHT` (even zero-wait processing cannot meet the due
date), `MATERIAL_DELAY` or `MACHINE_CAPACITY` (largest accumulated wait). The validator fails if an explanation is
missing or contradicts the schedule.

## 16. History is append-only
`StatusEvent` records every order/operation/lot transition (e.g. lot `ON_ORDER -> DELAYED -> AVAILABLE`). Current
`status` columns are derived from the last event and re-checked. Validated: allowed transitions, unbroken chains,
non-decreasing timestamps.

## 17. Anomaly injection (the core design choice)
**Chosen:** build a *clean* baseline, then mutate rows; a mutation is accepted only if re-running the validator
reports **exactly one new violation - the intended one - and nothing else appears or disappears**. Five types:
overlap, precedence, material consumed before availability, outside shift calendar, changeover omitted.
**Alternatives:** random corruption (collateral damage, labels can be wrong), separate "bad" dataset (loses the
mixed realistic case), `is_anomaly` columns (leaks labels into features).
**Why:** labels are guaranteed correct and unrelated rows are provably untouched.

## 18. Ground truth separation
`ground_truth.csv/json` sit outside `csv/`; no input table has a label column; the validator never reads them;
a test asserts that, and another asserts validator output == ground truth exactly.

## 19. Validator
Two layers: **integrity** (schema, PK/FK, enums, dates, arithmetic reconciliation, lifecycle, derived values) must be
clean; **constraints** (hard scheduling rules) are *reported*, and are clean in the baseline. It can run on exported CSVs
(`python run.py validate data/full`). Honest limitation: it shares `rules.py` formulas and the calendar-window builder
with the scheduler, so a bug in those would be mirrored; they are unit-tested separately and the rest of the checks
(overlap, precedence, tooling, changeover) are independently implemented.

## 20. Derived values
`derive.finalize()` recomputes every derived column (setup start, consumption time, lot balances, order totals,
lateness, statuses) from source rows, and is run again after injection, so anomalies never leave stale totals.

## 21. Testing approach
`python -m unittest discover -s tests`: primitives, determinism, seed sensitivity, clean baseline, exact ground-truth
match, no label leakage, parameterised scale, CSV round-trip + SQLite FK load, reproducibility, the full-preset volumes
(100/40/500/2000/200), and **negative tests** that corrupt data and assert the validator catches it.

## Limitations / next steps
- One offline planning run with a fully known order book; no re-scheduling after downtime (a v2 could emit
  superseded schedule versions instead of overwriting).
- Heuristic, not optimal; strict priority starves low priority orders.
- Labour is modelled as crew-size windows, not individual operators; no preemption or splitting of operations.
- Due dates are drawn independently of routing content (a realistic promise would scale with work content).
- Tested on Python 3.14.8 only; the code uses `dict | dict`, so Python 3.9+ is required.
