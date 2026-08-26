# Autonomous Vulnerability Remediation Pipeline

**Devin as the engine, deterministic orchestration as the shell.**

An event-driven system that turns security-scanner findings into verified
pull requests - or into well-evidenced escalations when a safe fix does not
exist - with near-zero human time per ticket.

Built against a fork of [apache/superset](https://github.com/apache/superset):
[SeoKaEun/devin_takehome_assignment](https://github.com/SeoKaEun/devin_takehome_assignment)
(148 pinned Python dependencies, 267 frontend dependencies).

---

## The problem

Scanners (Dependabot, Snyk, OSV) already *find* vulnerable dependencies.
The backlog rots at the *processing* step, because three ticket types resist
automation:

| Ticket type | Why it rots | Example in this repo |
|---|---|---|
| Major-version upgrade with breaking changes | bumping the pin breaks CI; nobody volunteers | flask 2.3.3 -> 3.1.3 ([#1](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1)) |
| Advisory with **no fixed release** | requires investigation, not a bump | paramiko PYSEC-2026-2858 ([#3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3)) |
| Non-security tech debt | always loses prioritization | react-loadable -> React.lazy ([#4](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4)) |

Each of these costs an engineer half a day to several days. This pipeline
changes the marginal cost of a ticket from engineer-hours to a few ACUs.

## Architecture

```
 EVENT SOURCES                ORCHESTRATOR (deterministic)          LABOR (Devin)
 ┌──────────────┐             ┌───────────────────────────┐
 │ OSV scanner   │──auto──┐   │ 1 Watcher    poll issues   │
 │ (30-min cycle)│        ├──→│ 2 Dispatcher issue→session │──→ work session
 │ humans adding │──opt-in┘   │ 3 Monitor    track/nudge   │←──   (clone, fix,
 │ the label     │            │ 4 Gate 1     mechanical    │       test, PR)
 └──────────────┘             │              verification  │
                              │ 5 Gate 2     independent   │──→ review session
                              │              review        │←──   (judge diff vs
                              │ 6 Reporter   comments+state│       contract)
                              └───────────────────────────┘
                                    │                │
                              state.json         suspicion lane
                              → dashboard        (never silent)
```

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

## What happened in the live run (all real, verifiable on the fork)

| Issue | Outcome | Story |
|---|---|---|
| [#2 setuptools 80.9.0->83.0.0](https://github.com/SeoKaEun/devin_takehome_assignment/issues/2) | **fixed** ([PR #5](https://github.com/SeoKaEun/devin_takehome_assignment/pull/5)) | Work session opened a PR; the **independent review session rejected it**, catching a real latent break (nodeenv 1.8.0 imports `pkg_resources`, removed in setuptools >= 82 - reproduced, plus doc drift). Findings were routed back automatically; the author fixed them on the same PR; a second review approved. |
| [#3 paramiko PYSEC-2026-2858](https://github.com/SeoKaEun/devin_takehome_assignment/issues/3) | **escalated** | No fixed release exists, and the repo's own constraint (`paramiko <4.0`, sshtunnel still uses DSSKey) blocks the 4.x line. Devin filed an evidence-backed report - advisory status, blocking chain, exposure assessment, mitigation, revisit trigger - instead of forcing a breaking PR. |
| [#1 flask 2.3.3->3.1.3](https://github.com/SeoKaEun/devin_takehome_assignment/issues/1) | _in progress at time of writing_ | Major-version upgrade with breaking-change fallout. |
| [#4 react-loadable -> React.lazy](https://github.com/SeoKaEun/devin_takehome_assignment/issues/4) | _in progress at time of writing_ | Multi-file frontend refactor ([PR #8](https://github.com/SeoKaEun/devin_takehome_assignment/pull/8)), under independent review. |

Human interventions across the entire run: **label toggles, one scope-policy
decision, and the merge button.** No code was written or reviewed by a human.

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
```

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
