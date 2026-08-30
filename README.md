# Autonomous Remediation Pipeline

**Devin as the engine, deterministic orchestration as the shell.**

An event-driven system that turns backlog findings - vulnerable dependencies
from a scanner, code-level defects from a Devin audit, tech debt labeled by a
human - into verified pull requests, or into well-evidenced escalations when
a safe fix does not exist, with near-zero human time per ticket.

Built against a fork of [apache/superset](https://github.com/apache/superset):
[SeoKaEun/devin_takehome_assignment](https://github.com/SeoKaEun/devin_takehome_assignment)
(148 pinned Python dependencies, 267 frontend dependencies).

---

## The problem

Scanners (Dependabot, Snyk, OSV) already *find* vulnerable dependencies.
The backlog rots at the *processing* step, because three ticket types resist
automation:

| Ticket type | waiting reason | Example in this repo |
|---|---|---|
| Major-version upgrade with breaking changes | bumping the pin breaks CI; nobody volunteers | flask 2.3.3 -> 3.1.3 ([#1](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1)) |
| Advisory with **no fixed release** | requires investigation, not a bump | paramiko PYSEC-2026-2858 ([#3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3)) |
| Non-security tech debt | always loses prioritization | react-loadable -> React.lazy ([#4](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4)) |

Each of these costs an engineer half a day to several days. This pipeline
changes the marginal cost of a ticket from engineer-hours to a few ACUs.

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

## Autonomy modes

Teams differ in risk appetite. One knob (`AUTONOMY_MODE`) sets how often the
pipeline stops for a human - it never changes *which* safety lines exist:

| | `supervised` | `balanced` (default) | `autopilot` |
|---|---|---|---|
| Autonomous rework rounds | 0 | 1 | 2 |
| Low-risk scope growth (docs/, tests/) | ask human | auto-accept | auto-accept |
| Review-approved PR | human merges | human merges | **auto-merge** |
| Escalations, out-of-contract source changes, anomalies | human | human | human (always) |

Recommended adoption path: start `supervised`, move to `autopilot` as trust
accumulates.

**The criterion behind the safety lines.** A decision is automatable only when
it is *machine-verifiable* (checkable against the pre-agreed contract: tests,
scope, independent review) **and** *reversible* (bounded blast radius - a merged
PR reverts; docs/tests cannot break production). Three kinds of decisions fail
that test in principle, so they stay human in every mode: **risk acceptance**
(living with an unfixable advisory is an accountability question, not a
technical one - automation prepares the decision, a human signs it), **contract
amendment** (out-of-contract source changes remove the very baseline the
machine verifies against; only the contract's author may widen it), and
**unexplained state** (timeouts, schema violations, contradictions - once the
system cannot prove what is happening, acting automatically means acting on
unknown state, so it stops loudly instead).

## What happened in the live run (all real, verifiable on the fork)

| Issue | Outcome | Story |
|---|---|---|
| [#2 setuptools 80.9.0->83.0.0](https://github.com/SeoKaEun/devin_takehome_assignment/issues/2) | **fixed** ([PR #5](https://github.com/SeoKaEun/devin_takehome_assignment/pull/5)) | Work session opened a PR; the **independent review session rejected it**, catching a real latent break (nodeenv 1.8.0 imports `pkg_resources`, removed in setuptools >= 82 - reproduced, plus doc drift). Findings were routed back automatically; the author fixed them on the same PR; a second review approved. Gate 1 also flagged the widened diff twice; a human accepted the scope growth (that decision is now the `balanced` policy's auto-accept rule for docs/tests). |
| [#3 paramiko PYSEC-2026-2858](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3) | **escalated** | No fixed release exists, and the repo's own constraint (`paramiko <4.0`, sshtunnel still uses DSSKey) blocks the 4.x line. Devin filed an evidence-backed report - advisory status, blocking chain, exposure assessment, mitigation, revisit trigger - instead of forcing a breaking PR. |
| [#1 flask 2.3.3->3.1.3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1) | **fixed** ([PR #9](https://github.com/SeoKaEun/devin_takehome_assignment/pull/9)) | Major-version upgrade. Devin found the one true breaking dependency (flask-babel 3.1.0 imports a helper Flask 3 removed, via the flask-appbuilder chain), bumped it to 4.0.0, and proved no app-code changes were needed: `pytest tests/unit_tests` identical to the master baseline (13,328 passed). The independent review rejected the first attempt, was routed back, then approved: "does exactly what issue #1 requires and nothing more - 3 files, +8/-5." |
| [#4 react-loadable -> React.lazy](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4) | **fixed** ([PR #8](https://github.com/SeoKaEun/devin_takehome_assignment/pull/8)) | Multi-file frontend refactor, review-approved on the first pass. |

**Final tally** (all derived from pipeline records): 4/4 issues terminal -
3 verified PRs + 1 evidence-backed escalation. 9 Devin sessions (4 work,
5 review). 2 defective fixes caught by independent review before any human saw
them. Average detection-to-resolution: ~50 min wall-clock. Human involvement:
4 logged decisions (one timeout extension, scope-policy widenings, one
calibration judgment) and the merge button - no code written or reviewed by a
human.

## Why Devin (and not a rules bot)

Dependabot's three structural limits are exactly issues #1/#3/#4: it cannot
fix the code its own version bump breaks; it does nothing when no fixed
release exists; it does not do refactors. Closing those gaps takes the loop
*read the failure -> find the cause -> change code -> re-test* - which is an
autonomous agent, used here as a programmable primitive in three roles:
worker, independent reviewer, and (in the escalation path) investigator.

## Run it

### Simulate (no credentials, no network, ~30 seconds)

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

The loop scans dependencies every 30 min (files contract-shaped issues for
new advisories), watches for the `devin-remediate` label, and drives every
issue to a terminal state. `python -m orchestrator status` for a terminal
summary; `state/dashboard.html` auto-refreshes with per-issue activity
streams and raw agent logs.

Requirements: Python 3.12+ (stdlib only - there is nothing to pip-install),
or Docker. GitHub fine-grained PAT with Issues/Contents/PRs RW on the fork.
The Devin account needs its GitHub integration connected to the fork.

### CLI

```
python -m orchestrator healthcheck | scan | once | run | status | dashboard
```

### Tests

Stdlib `unittest`, no network, no credentials, sub-second:

```bash
python -m unittest discover -s tests
```

19 tests cover the contract layer (spec matching, closed outcome enums,
prompt invariants), the scanner's parsing and templating, and - via the
simulation clients - the full pipeline: all four terminal narratives, the
bounded rework loop (review rejects -> findings routed back -> re-review
approves), the idle-session nudge, the concurrency cap on every tick, and
the suspicion-lane invariants (a `fixed` claim with no PR, a nonexistent PR,
a `blocked` claim without evidence - each must stop the pipeline, loudly).

## Observability - "how would I know this is working?"

Everything below is derived from `state/state.json`, the pipeline's own
records. Nothing is estimated and nothing comes from the agent's self-report.

| Question | Where to look |
|---|---|
| What is active, what is done? | `state/dashboard.html` tiles: **Fixed, PR verified** / **Escalated with evidence** / **Needs attention** / **In progress**; the Issues table (state, PR, duration); a per-issue timeline of every transition |
| Is it succeeding or failing? | **Quality & control** panel: review rejections caught, autonomous rework rounds vs. the bound, human decisions logged. Failures never go silent: anything unprovable lands in **Needs attention** (red) with a comment saying what happened and what to check |
| Is it moving, and what does it cost? | **Operations** panel: last tick, last scan (+issues filed), pipeline errors, concurrent sessions vs. cap, ACU ceiling; **Avg resolution** tile; `n/m resolved` in the header. The page refreshes every 15 s and shows a **Stale** banner if the loop has been silent for 3 min |
| What did the agent actually do? | Each issue's **Problem -> Fix -> Result** block, the raw agent log (expandable), and links to the work and review sessions on app.devin.ai |
| Without a browser? | `python -m orchestrator status` (one line per issue), `state/orchestrator.log` (every tick), and the comment trail the pipeline leaves on each GitHub issue (session started, fix verified, review verdict, escalation report) |

## Repository layout

```
orchestrator/
  config.py        env-driven knobs, autonomy policies
  contracts.py     task-contract prompts, output schemas, per-issue scope specs
  scanner.py       OSV scan -> contract-shaped issues (the automated event source)
  pipeline.py      the tick: watch -> dispatch -> monitor -> gates -> report
  state.py         single source of truth (state.json, atomic writes, timelines)
  dashboard.py     state.json -> static HTML (KPIs + per-issue streams)
  devin_client.py  the ONLY module that talks to the Devin API
  github_client.py issues in, comments/PR-verification/merge out
  clients.py       real/simulation seam (also the GH-Enterprise adapter point)
  sim.py           offline fixtures for the full pipeline
scripts/
  seed_issues.py   Part-1 backlog seeding (idempotent, --dry-run)
tests/             unittest suite (stdlib-only, offline, sub-second)
```

Two hard guards worth knowing about: `.github/` is on a global denylist -
no session may touch CI workflows in any autonomy mode (workflow edits are a
privilege-escalation surface); and issues without a hand-written scope spec
get a bounded default (manifests, source, docs, tests), never
"anything goes".

## Production notes (what changes in a real engagement)

- Polling -> GitHub App webhooks (the event boundary in code is unchanged).
- Personal PAT -> GitHub App installation; fork -> real repos with branch
  protection; `state.json` -> a database.
- Session latency: pre-built machine **snapshots** (repo cloned, deps
  installed) cut the ~15-min environment setup from every session;
  org-level **Knowledge** teaches repo conventions once instead of per
  session. Together these should roughly halve time-per-issue.
- Reviewer-session reuse across rework rounds (message the same reviewer
  instead of spawning a fresh one) trims the per-issue session count.
