# AI usage disclosure

## Tools used
- **GitHub Copilot app (agent chat)** running **Claude Sonnet 5.5** - the only AI tool used for this project.
- No other LLM, code generator or code from other people was used. No external datasets or existing scheduling code
  was imported; the only dependency is the Python standard library.
- Prompt history: [`../prompts/PROMPTS.md`](../prompts/PROMPTS.md) (every prompt I sent, verbatim). The Copilot app does
  not export a transcript, so the log was reconstructed from the chat by the assistant at the end of each session
  and may omit trivial confirmations such as "ok"/"yes".

## How the work was divided
I used the AI agent as a pair-programmer. **I set the direction and constraints; the agent did most of the typing.**

**Direction and decisions that were mine** (all visible in `prompts/PROMPTS.md`):
- Chose to read the brief and FAQ first and agree on what was expected before creating any code or repo.
- Required that every feature come with the option chosen, the alternatives, and the reason, which is why
  `docs/DECISIONS.md` exists.
- Chose full, honest AI disclosure and a complete prompt history over a polished-looking story.
- Chose which improvement to make during the review window: from four options offered, I selected tying due dates to
  each order's work content (see section 14b of `docs/DECISIONS.md`) and the agent implemented it.

**What the agent did:** analysed the brief, proposed the architecture, wrote the code, tests and documentation, found and
fixed its own issues (load imbalance, weak assertions, a mis-dated routing version), and produced the evidence below.

| Area | Reliance on AI |
|---|---|
| Brief analysis, architecture | Agent proposed, I approved the direction |
| Code (`schema`, `master`, `orders`, `scheduler`, `derive`, `validate`, `anomalies`, `export`, `pipeline`, `cli`) | Written by the agent |
| Tests, README, data dictionary, decisions doc | Written by the agent |
| Due-date rule (section 14b) | My choice; implemented by the agent |

Heaviest reliance: the scheduler (gap insertion with setup re-checks and rollback), the validator rule set, and the
anomaly injector's "accept only if exactly one new violation" loop.

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
- [ ] Other changes I made myself (list them, with commit hashes): _..._
- [ ] Decisions I disagreed with or overrode: _..._
- [ ] Things I do not fully understand yet and would study before the walkthrough: _..._

## Process note
The repository history reflects local development followed by staged commits grouped by layer; commit timestamps are
real and unaltered.
