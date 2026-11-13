# AI usage disclosure

## Tools used
- **GitHub Copilot app (agent chat)** running **Claude Sonnet 5.5** - the only AI tool used for this project.
- No other LLM, code generator or code from other people was used. No external datasets or existing scheduling code
  was imported; the only dependency is the Python standard library.
- Prompt history: [`../prompts/PROMPTS.md`](../prompts/PROMPTS.md) (every prompt I sent, verbatim). The Copilot app does
  not export a transcript, so the log was reconstructed from the chat by the assistant at the end of each session
  and may omit trivial confirmations such as "ok"/"yes".

## Where AI accelerated the work (stated plainly)
The AI agent **wrote essentially all of the code and documentation in this repository** in one working session, following
my direction to (1) read the brief and FAQ first, (2) explain each design decision with alternatives before/while building,
and (3) keep the work reviewable:

| Area | AI contribution |
|---|---|
| Brief analysis | Summarised the problem statement, entity list, edge cases and acceptance criteria |
| Architecture | Proposed the stdlib-only layout, schema-as-source-of-truth, staged pipeline |
| Code | `schema`, `master`, `orders`, `scheduler`, `derive`, `validate`, `anomalies`, `export`, `pipeline`, `cli`, tests |
| Documentation | `README.md`, `docs/DECISIONS.md`, generated `docs/data_dictionary.md` |
| Debugging / tuning | Found and fixed load imbalance (one group at 78% utilisation, 23% infeasible orders) by adding load calibration; moved the routing-v2 effective date so effective-dating is exercised; removed placeholder lines in the validator; replaced weak `or True` test assertions |

Parts where the work leaned **most heavily** on AI: the scheduler (gap insertion with sequence-dependent setup
re-checks and the undo log), the validator rule set, and the anomaly injector's "accept only if exactly one new violation"
loop.

## How the AI output was checked (evidence, not assertion)
These are mechanical checks that were run against the AI-written code:
- 24 unit tests, including negative tests that corrupt data and require the validator to notice.
- Clean baseline: 0 integrity failures and 0 hard-constraint violations before injection.
- Injected anomalies vs `ground_truth`: exact match (50/50 detected, 0 unexpected); validator never reads ground truth.
- Exported SQL loads into SQLite with foreign keys on; `foreign_key_check` empty.
- Two independent regenerations produce identical SHA-256 checksums and match the committed files.
- Manual inspection of the 13 initial `NO_CAPACITY_IN_HORIZON` failures: traced to one hot SKU needing 2 operators on
  two eligible machines (one day-shift only) - a legitimate bottleneck, not a scheduler bug.

## What I personally reviewed, changed or validated
> **TO BE COMPLETED BY THE CANDIDATE BEFORE SUBMISSION - write only what you actually did.**
> Reviewers will ask you to walk through the solution; this section must be true.

- [ ] Read `scheduler.py` end to end and can explain `find_slot`, the gap-insertion/setup re-check and the rollback.
- [ ] Read `validate.py` rules and can explain which rule each anomaly triggers and why the injector's acceptance test is exact.
- [ ] Ran `python run.py generate --preset full`, `python run.py reproduce --preset full` and the test suite myself.
- [ ] Opened a sample of `csv/` tables and spot-checked IDs, joins and a few schedule rows by hand.
- [ ] Changes I made myself (list them, with commit hashes): _..._
- [ ] Decisions I disagreed with or overrode: _..._
- [ ] Things I do not fully understand yet and would study before the walkthrough: _..._

## Process note
The repository history reflects local development followed by staged commits grouped by layer; commit timestamps are
real and unaltered.
