# Autonomous Remediation Pipeline

**Devin as the engine, deterministic orchestration as the shell.**

An event-driven system that turns backlog findings - vulnerable dependencies
from a scanner, code-level defects from a Devin audit, tech debt labeled by a
human - into verified pull requests, or into well-evidenced escalations when
a safe fix does not exist, with near-zero human time per ticket.

Built against a fork of [apache/superset](https://github.com/apache/superset):
[SeoKaEun/devin_takehome_assignment](https://github.com/SeoKaEun/devin_takehome_assignment)
(148 pinned Python dependencies, 267 frontend dependencies).

**Contents:**
[The problem](#the-problem) ·
[Architecture](#architecture) ·
[How findings are detected](#how-findings-are-detected) ·
[From finding to verified pull request](#from-finding-to-verified-pull-request) ·
[Configuration](#configuration) ·
[Autonomy modes](#autonomy-modes) ·
[Results](#results) ·
[Why an autonomous agent](#why-an-autonomous-agent) ·
[Getting started](#getting-started) ·
[Observability](#observability) ·
[Project structure](#project-structure) ·
[Roadmap](#roadmap)

---

## The problem

Every engineering organization carries a backlog of remediation work that is
known, understood, and not being done: dependencies with published
advisories, defects that a careful reading of the code would reveal, and
deprecated code that everyone agrees should be replaced. Detection is not the
bottleneck. Scanners such as Dependabot, Snyk, and OSV find vulnerable
dependencies continuously, and code review or audit surfaces the rest. The
bottleneck is remediation: each item requires an engineer to reproduce the
problem, change code, chase whatever the change breaks, run the tests, and
open a reviewable pull request. That is between half a day and several days
of skilled time per ticket, and it competes with feature work for the same
people. The predictable result is that these tickets stay open for weeks or
months.

This matters for two reasons. A known vulnerability that stays open is
exposure the organization has chosen to keep, usually without anyone having
made that choice explicitly. And the backlog does not merely persist; it
compounds, because every unapplied upgrade widens the gap to the next one.

The pipeline described here changes the marginal cost of a remediation ticket
from engineer-hours to a small number of ACUs, and does so without trusting
the agent's own account of its work. It is built for the three kinds of ticket
that resist rule-based automation in particular:

| Ticket type | Why it stays open | Example in this repository |
|---|---|---|
| Major-version upgrade with breaking changes | Bumping the pin breaks CI and nobody volunteers to chase the breakage | flask 2.3.3 -> 3.1.3 ([#1](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1)) |
| Advisory with **no fixed release** | Requires investigation and a judgment, not a version bump | paramiko PYSEC-2026-2858 ([#3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3)) |
| Non-security tech debt | Always loses prioritization against feature work | react-loadable -> React.lazy ([#4](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4)) |

## Architecture

```
 EVENT SOURCES                 ORCHESTRATOR (deterministic)          LABOR (Devin)
 ┌───────────────┐             ┌───────────────────────────┐
 │ OSV dep scan   │──auto──┐   │ 1 Watcher    poll issues   │
 │ (30-min cycle) │        │   │ 2 Dispatcher issue→session │──→ work session
 │ code audit     │──auto──┼──→│ 3 Monitor    track/nudge   │←──   (clone, fix,
 │ (Devin session)│        │   │ 4 Gate 1     mechanical    │       test, PR)
 │ humans adding  │──opt-in┘   │              verification  │
 │ the label      │            │ 5 Gate 2     independent   │──→ review session
 └───────────────┘             │              review        │←──   (judge diff vs
                               │ 6 Reporter   comments+state│       contract)
                               └───────────────────────────┘
                                     │                │
                               state.json         suspicion lane
                               → dashboard        (never silent)
```

Three event sources feed the same pipeline. The dependency scanner is
deterministic (a database join against OSV - complete and auditable for the
class of *known* advisories). The **code audit** covers the class a database
cannot: code-level defects. Detection there is itself delegated to a Devin
session (role: auditor, report-only, cost-capped) whose structured findings
the orchestrator files as contract-shaped issues - judgment where judgment
is required, deterministic filing after. Humans remain the third source:
labeling any issue is delegation.

Design rules, and why:

- **No LLM inside the orchestrator.** Judgment lives in two places only:
  the *task contracts* (written at design time) and the *Devin sessions*
  (runtime). The shell is deterministic - same input, same behavior,
  auditable. Untrusted (non-deterministic) workers wrapped in a trusted
  (deterministic) harness is the same trust model as CI.
- **Contracts over prose.** Every issue is a 4-part task contract
  (Context / Task / Acceptance criteria / If-blocked protocol). Sessions
  must answer through a `structured_output_schema` - results come back as
  machine-checkable JSON, never parsed prose.
- **Don't trust the agent's self-report.** Gate 1 (code) verifies the PR
  exists, the diff stays inside the contracted file scope, and test evidence
  is present. Gate 2 (a *separate* Devin session - never the author) judges
  whether the diff actually remediates the advisory. In our live run the
  reviewer caught a real latent bug the author had missed (see Results).
- **"Healthy" is a whitelist.** Any state that cannot be proven healthy -
  including *wrong values that raise no errors* - lands in the suspicion
  lane (`needs_attention`) with a comment that says what happened, what to
  check, and what the options are. The pipeline may stop; it never stops
  silently.
- **Bounded autonomy.** A review rejection triggers one autonomous rework
  round (findings are routed back to the author session); beyond the bound a
  human decides. Every session carries `tags` (audit), `max_acu_limit`
  (cost ceiling), and `idempotent` (a retried dispatch can never double-fire).

## How findings are detected

Three sources feed the same queue. Each one produces a GitHub issue whose
body is the task contract; nothing downstream needs to know where an issue
came from.

### Dependency scanner (deterministic)

- Reads the manifests listed in `SCAN_MANIFESTS` (default
  `requirements/base.txt`; add `requirements/development.txt` to widen
  coverage) from the fork's default branch.
- Parses exact pins only (`name==version`). Loose specifiers are ignored by
  design: the scanner reports what is actually installed, not what might be.
- Sends all pins to OSV in a single `querybatch` call (PyPI ecosystem) and
  receives the advisories that apply to each exact package/version pair.
- Checks every advisory ID against the titles of all open and closed issues on
  the fork, so an advisory is filed once and never again.
- Files each new finding as
  `[security] <package> <version> vulnerable (<advisory>) - auto-detected`,
  labeled for pickup. If OSV lists a fixed version, the body is an **upgrade
  contract** (bump the pin to the fixed release, fix the fallout, prove it
  with `pip install`, an import check and the relevant tests, open a PR with
  the required title). If no fixed version exists, the body is an
  **investigation contract** (assess exposure, propose a mitigation, define a
  revisit trigger; a well-evidenced "cannot be fixed safely today" report is
  the successful outcome).
- Runs on startup and every `SCAN_INTERVAL_MIN` minutes inside
  `orchestrator run`, or on demand with `orchestrator scan`.

Examples on the fork: issues
[#12](https://github.com/SeoKaEun/devin_takehome_assignment/issues/12),
[#16](https://github.com/SeoKaEun/devin_takehome_assignment/issues/16),
[#18](https://github.com/SeoKaEun/devin_takehome_assignment/issues/18),
[#19](https://github.com/SeoKaEun/devin_takehome_assignment/issues/19),
[#20](https://github.com/SeoKaEun/devin_takehome_assignment/issues/20).

### Code audit (judgment-based)

- `orchestrator audit` starts a Devin session in the **auditor** role with a
  scope (`AUDIT_SCOPE`, default `superset/utils/` for Python code-level
  defects) and a fixed set of defect categories. The session is report-only
  and capped at half the normal ACU ceiling.
- The session must answer through `AUDIT_SCHEMA`: every finding requires the
  file, the exact pattern as evidence, and a statement of why it matters.
  Findings without evidence are dropped before filing.
- The orchestrator files each finding as
  `[code-quality] <title> (<file>) - auto-detected`, de-duplicated against
  existing issues by file and title, with the same contract structure as
  scanner findings.

Examples on the fork: issues
[#10](https://github.com/SeoKaEun/devin_takehome_assignment/issues/10)
(`excel.py`) and
[#11](https://github.com/SeoKaEun/devin_takehome_assignment/issues/11)
(`json.py`), both subsequently fixed by the pipeline.

### Human label (opt-in)

Any issue that carries the trigger label (`TRIGGER_LABEL`, default
`devin-remediate`) is picked up on the next tick. This is how tech-debt work
such as [#4](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4)
enters the same pipeline as security findings.

## From finding to verified pull request

Every 30 seconds (`POLL_INTERVAL_SEC`) the orchestrator runs one tick over
every tracked issue. The states are a closed set; anything that cannot be
proven to be in a healthy state goes to `needs_attention`.

| Step | What happens | Resulting state |
|---|---|---|
| Watch | Issues with the trigger label that are not yet tracked are recorded | `queued` |
| Brakes | Pause file, ACU budget and circuit breaker are evaluated once for the tick | - |
| Dispatch | While fewer than `MAX_CONCURRENT_SESSIONS` are live, the next queued issue gets a Devin **work** session bound to its contract, with an ACU ceiling and an idempotency key | `working` |
| Monitor | The session is polled; an idle session is nudged once, a session past `SESSION_TIMEOUT_MIN` is escalated | `working` |
| Gate 1 (mechanical) | The typed result is checked against the contract: the PR exists on the fork, the diff stays inside the allowed paths, test evidence is present. A `blocked` result must carry a reason and a mitigation | `review_pending` / `done_escalated` / `needs_attention` |
| Gate 2 (independent) | A **separate** Devin session reviews the diff against the contract and returns `approve` or `request_changes` with findings | `review_working` |
| Rework | Findings are sent back to the original work session for one bounded round, then Gate 1 and Gate 2 run again | `working` |
| Report | The outcome is posted to the issue as a comment, `state.json` is updated, the dashboard is regenerated | `done_fixed` / `done_escalated` / `needs_attention` |

Merging a review-approved PR remains a human action except in `autopilot`
mode.

## Configuration

All settings are environment variables, read from `.env` if present
(`.env.example` lists every one with its default). The ones that govern cost
are below; the scanner files every finding it can attribute to a pinned
version, so spending is decided here, at dispatch time, rather than by
limiting what gets detected.

| Control | Default | Effect |
|---|---|---|
| `MAX_CONCURRENT_SESSIONS` | 2 | Work and review sessions in flight at any moment |
| `MAX_ACU_PER_SESSION` | 10 (reviews: 5) | Hard ceiling passed to Devin per session |
| `SESSION_TIMEOUT_MIN` | 90 | A silent session is escalated, not waited on |
| `ACU_BUDGET` | 0 (unlimited) | Total ACU the pipeline may commit across all sessions. New work stops when the next session would exceed it; reviews of work already started still run |
| `MAX_CONSECUTIVE_ATTENTION` | 3 | Circuit breaker: that many suspicion-lane outcomes in a row halt all dispatch until a human runs `resume` |
| `orchestrator pause` / `resume` | - | Manual brake (a `state/PAUSE` file). New sessions stop; running ones finish through the normal gates |

Every brake stops *new* sessions only. Sessions already running are
monitored and gated to completion, so a halt always winds down to a clean,
resumable state, and the dashboard shows why dispatch is closed.
`SCAN_MAX_NEW` / `AUDIT_MAX_NEW` remain available for teams that also want
burst control at the filing step (0 = file everything, the default).

## Autonomy modes

Teams differ in risk appetite. One setting (`AUTONOMY_MODE`) determines how
often the pipeline stops for a human. It never changes *which* safety lines
exist:

| | `supervised` | `balanced` (default) | `autopilot` |
|---|---|---|---|
| Autonomous rework rounds | 0 | 1 | 2 |
| Low-risk scope growth (docs/, tests/) | ask human | auto-accept | auto-accept |
| Review-approved PR | human merges | human merges | **auto-merge** |
| Escalations, out-of-contract source changes, anomalies | human | human | human (always) |

The recommended adoption path is to start in `supervised` and move toward
`autopilot` as the track record accumulates.

**The criterion behind the safety lines.** A decision is automatable only
when it is *machine-verifiable* (checkable against the pre-agreed contract:
tests, scope, independent review) **and** *reversible* (bounded blast radius:
a merged PR reverts; docs and tests cannot break production). Three kinds of
decision fail that test in principle and therefore stay with a human in every
mode: **risk acceptance** (living with an unfixable advisory is an
accountability question - automation prepares the decision, a human signs
it), **contract amendment** (an out-of-contract source change removes the very
baseline the machine verifies against, so only the contract's author may widen
it), and **unexplained state** (timeouts, schema violations, contradictions -
once the system cannot prove what is happening, acting automatically means
acting on unknown state, so it stops loudly instead).

## Results

Every outcome below links to the corresponding issue or pull request on the
fork; the timelines, session links and review verdicts are recorded there as
issue comments.

**First run - the seeded backlog.**

| Issue | Outcome | What happened |
|---|---|---|
| [#2 setuptools 80.9.0 -> 83.0.0](https://github.com/SeoKaEun/devin_takehome_assignment/issues/2) | **fixed** ([PR #5](https://github.com/SeoKaEun/devin_takehome_assignment/pull/5)) | The work session opened a PR; the **independent review session rejected it**, catching a real latent break (nodeenv 1.8.0 imports `pkg_resources`, removed in setuptools >= 82 - reproduced, plus documentation drift). The findings were routed back automatically, the author fixed them on the same PR, and a second review approved. Gate 1 also flagged the widened diff twice; a human accepted the scope growth, and that decision is now the `balanced` policy's auto-accept rule for docs and tests. |
| [#3 paramiko PYSEC-2026-2858](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3) | **escalated** | No fixed release exists, and the repository's own constraint (`paramiko <4.0`; sshtunnel still uses DSSKey) blocks the 4.x line. Devin filed an evidence-backed report - advisory status, blocking chain, exposure assessment, mitigation, revisit trigger - instead of forcing a breaking PR. |
| [#1 flask 2.3.3 -> 3.1.3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1) | **fixed** ([PR #9](https://github.com/SeoKaEun/devin_takehome_assignment/pull/9)) | Major-version upgrade. Devin found the one true breaking dependency (flask-babel 3.1.0 imports a helper Flask 3 removed, via the flask-appbuilder chain), bumped it to 4.0.0, and proved no application-code changes were needed: `pytest tests/unit_tests` identical to the master baseline (13,328 passed). The review rejected the first attempt, it was routed back, then approved: "does exactly what issue #1 requires and nothing more - 3 files, +8/-5." |
| [#4 react-loadable -> React.lazy](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4) | **fixed** ([PR #8](https://github.com/SeoKaEun/devin_takehome_assignment/pull/8)) | Multi-file frontend refactor, review-approved on the first pass. |

Summary: 4 of 4 issues reached a terminal state - 3 verified pull requests
and 1 evidence-backed escalation. 9 Devin sessions were used (4 work,
5 review). 2 defective fixes were caught by independent review before any
human saw them. Average time from detection to resolution was approximately
50 minutes. Human involvement was limited to 4 logged decisions (one timeout
extension, scope-policy widenings, one calibration judgment) and the merge
itself; no code was written or reviewed by a human.

**Automated sources.** The code audit filed
[#10](https://github.com/SeoKaEun/devin_takehome_assignment/issues/10) and
[#11](https://github.com/SeoKaEun/devin_takehome_assignment/issues/11)
(defects in `excel.py` and `json.py` that no dependency scanner can see);
the dependency scanner filed
[#12](https://github.com/SeoKaEun/devin_takehome_assignment/issues/12),
[#16](https://github.com/SeoKaEun/devin_takehome_assignment/issues/16),
[#18](https://github.com/SeoKaEun/devin_takehome_assignment/issues/18),
[#19](https://github.com/SeoKaEun/devin_takehome_assignment/issues/19) and
[#20](https://github.com/SeoKaEun/devin_takehome_assignment/issues/20).
Each was picked up by the pipeline and carried to a pull request
(#13, #14, #15, #17, #21, #22, #23) with no manual step between detection and
dispatch; #18 and #20 passed both gates and independent review within
50 minutes of being filed.

## Why an autonomous agent

Dependabot's three structural limits are exactly issues #1, #3 and #4: it
cannot fix the code its own version bump breaks, it does nothing when no fixed
release exists, and it does not refactor. Closing those gaps requires the loop
*read the failure -> find the cause -> change code -> re-test*, which is an
autonomous agent. Here that agent is used as a programmable primitive in four
roles - auditor, implementer, independent reviewer, and investigator - under
the same separation of duties applied between human engineers.

## Getting started

### Simulation (no credentials, no network, about 30 seconds)

Exercises every state transition - dispatch, gates, independent review,
rework, escalation, nudge - against scripted fixtures modeled on the real
backlog:

```bash
docker compose run --rm simulate         # or: SIMULATE=1 python -m orchestrator run
```

Then open `state/dashboard.html`.

### Live

```bash
cp .env.example .env      # fill in DEVIN_API_KEY, GITHUB_TOKEN, GITHUB_OWNER
python -m orchestrator healthcheck       # verifies both credentials, no side effects
docker compose up         # or: python -m orchestrator run
```

The loop scans dependencies every 30 minutes (filing contract-shaped issues
for new advisories), watches for the `devin-remediate` label, and drives every
issue to a terminal state. `python -m orchestrator status` prints a terminal
summary; `state/dashboard.html` refreshes itself with per-issue activity
streams and raw agent logs.

Requirements: Python 3.12+ (standard library only - there is nothing to
install) or Docker; a GitHub fine-grained PAT with Issues, Contents and Pull
requests read/write on the fork; a Devin account whose GitHub integration is
connected to the fork.

### CLI

```
python -m orchestrator healthcheck | scan | audit | once | run | status | dashboard | pause | resume
```

### Tests

Standard-library `unittest`, no network, no credentials, about one second:

```bash
python -m unittest discover -s tests
```

26 tests cover the contract layer (spec matching, closed outcome enums,
prompt invariants), the scanner's parsing and templating, the full pipeline
via the simulation clients (all four terminal narratives, the bounded rework
loop, the idle-session nudge, the concurrency cap on every tick), the
suspicion-lane invariants (a `fixed` claim with no PR, a nonexistent PR, a
`blocked` claim without evidence - each must stop the pipeline, loudly), and
the three brakes (pause, ACU budget, circuit breaker - each must stop new
sessions, let running ones finish, and be resumable).

## Observability

The question an engineering leader needs answered is whether the system is
working. Everything below is derived from `state/state.json`, the pipeline's
own records; nothing is estimated and nothing comes from the agent's
self-report.

| Question | Where to look |
|---|---|
| What is active, what is done? | `state/dashboard.html` tiles: **Fixed, PR verified** / **Escalated with evidence** / **Needs attention** / **In progress**; the Issues table (state, PR, duration); a per-issue timeline of every transition |
| Is it succeeding or failing? | **Quality & control** panel: review rejections caught, autonomous rework rounds against the bound, human decisions logged. Failures never go silent: anything unprovable lands in **Needs attention** (red) with a comment saying what happened and what to check |
| Is it moving, and what does it cost? | **Operations** panel: last tick, last scan (+issues filed), pipeline errors, concurrent sessions against the cap, **ACU committed** against the budget, **Dispatch** (open / paused / budget reached / circuit open), circuit-breaker streak. The page refreshes every 15 seconds and shows a **Stale** banner if the loop has been silent for 3 minutes |
| What did the agent actually do? | Each issue's **Problem -> Fix -> Result** block, the raw agent log (expandable), and links to the work and review sessions on app.devin.ai |
| Without a browser? | `python -m orchestrator status` (one line per issue), `state/orchestrator.log` (every tick and every brake change), and the comment trail the pipeline leaves on each GitHub issue (session started, fix verified, review verdict, escalation report) |

## Project structure

```
orchestrator/
  config.py        env-driven knobs, autonomy policies, brakes
  contracts.py     task-contract prompts, output schemas, per-issue scope specs
  scanner.py       OSV scan -> contract-shaped issues (the automated event source)
  auditor.py       Devin code audit -> contract-shaped issues (the judgment-based source)
  pipeline.py      the tick: watch -> brakes -> dispatch -> monitor -> gates -> report
  state.py         single source of truth (state.json, atomic writes, timelines, counters)
  dashboard.py     state.json -> static HTML (KPIs, control panels, per-issue streams)
  devin_client.py  the ONLY module that talks to the Devin API
  github_client.py issues in, comments / PR verification / merge out
  clients.py       real/simulation seam (also the GH-Enterprise adapter point)
  sim.py           offline fixtures for the full pipeline
scripts/
  seed_issues.py   Part-1 backlog seeding (idempotent, --dry-run)
tests/             unittest suite (stdlib-only, offline, about one second)
```

Two hard guards apply regardless of configuration: `.github/` is on a global
denylist, so no session may touch CI workflows in any autonomy mode (workflow
edits are a privilege-escalation surface); and issues without a hand-written
scope specification receive a bounded default (manifests, source, docs,
tests) rather than unrestricted access.

## Roadmap

- **Prioritization.** Detection is already complete and spend is already
  bounded; what is missing is ordering. A triage step between detection and
  dispatch would rank the queue by severity and exploitability so that a
  portfolio-level budget is spent on the most dangerous findings first rather
  than on the first ones filed.
- **Event delivery.** Polling -> GitHub App webhooks (the event boundary in
  code is unchanged).
- **Tenancy.** Personal PAT -> GitHub App installation; fork -> real
  repositories with branch protection; `state.json` -> a database.
- **Session latency.** Pre-built machine **snapshots** (repository cloned,
  dependencies installed) remove the ~15-minute environment setup from every
  session; organization-level **Knowledge** with a playbook per ticket type
  teaches repository conventions once instead of per session. Together these
  should roughly halve time-per-issue.
- **Reviewer reuse.** Messaging the same reviewer across rework rounds instead
  of spawning a fresh one trims the per-issue session count.
