"""Contracts - where the system's intelligence lives.

The orchestrator is deterministic; everything that requires judgment was
compressed into these contracts at design time:

  1. Work prompt   - wraps the issue's task contract with repo coordinates
                     and non-negotiable rules (PR target, branch naming).
  2. Output schema - forces Devin to report results as machine-checkable
                     JSON instead of prose. Gate 1 validates against this.
  3. Review prompt - a *separate* Devin session judges the diff against the
                     issue contract (never the session that wrote it).
  4. Issue specs   - per-issue-type allowlists that Gate 1 uses to check
                     that a PR stays inside its contracted scope.
"""

from . import config

# --------------------------------------------------------------------------
# Structured output schemas (JSON Schema draft-07, kept flat on purpose:
# every field is a string/array so gate checks stay trivial)
# --------------------------------------------------------------------------

WORK_SCHEMA = {
    "type": "object",
    "required": ["outcome", "summary"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["fixed", "blocked", "partial"],
            "description": "fixed = PR opened and acceptance criteria met; "
                           "blocked = cannot be remediated safely, report filed; "
                           "partial = some criteria met, needs human decision",
        },
        "summary": {"type": "string", "description": "3-6 sentence engineering summary"},
        "change_summary": {
            "type": "array", "items": {"type": "string", "maxLength": 120},
            "description": "3-6 standalone bullet facts, each under 100 chars, "
                           "plain statements a dashboard can list verbatim: what "
                           "changed, why, key numbers. NOT prose paragraphs.",
        },
        "pr_url": {"type": ["string", "null"], "description": "URL of the opened PR, null if none"},
        "branch": {"type": ["string", "null"]},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "tests_run": {
            "type": "array", "items": {"type": "string"},
            "description": "each entry: '<command> -> <result>'",
        },
        "blocking_reason": {"type": ["string", "null"],
                            "description": "required when outcome=blocked: exact technical "
                                           "cause, 1-2 short sentences, no paragraphs"},
        "mitigation": {"type": ["string", "null"],
                       "description": "required when outcome=blocked: recommended interim "
                                      "mitigation, 1-2 short sentences"},
        "advisory_status": {"type": ["string", "null"],
                            "description": "for investigation tasks: current upstream fix status"},
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "summary"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["approve", "request_changes"],
        },
        "summary": {"type": "string", "description": "what was checked and the conclusion"},
        "checks_performed": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"},
                  "description": "side effects or gaps found; empty if none"},
    },
}

# --------------------------------------------------------------------------
# Per-issue-type specs for Gate 1 scope verification
# --------------------------------------------------------------------------

ISSUE_SPECS = [
    {
        "match": "Flask",
        "expect_pr": True,
        # major upgrade may legitimately touch app code and other pins
        "allowed_paths": ["requirements/", "pyproject.toml", "superset/",
                          "superset-core/", "tests/"],
    },
    {
        "match": "setuptools",
        "expect_pr": True,
        # setuptools >=82 removes pkg_resources: migrating importers in
        # source and updating stale docs is inherent to this upgrade
        # (policy widened after a legitimate Gate-1 scope flag on PR #5)
        "allowed_paths": ["requirements/", "pyproject.toml", "superset/", "docs/",
                          "tests/"],
    },
    {
        "match": "paramiko",
        # investigation task: a blocked outcome with a good report is success
        "expect_pr": False,
        "allowed_paths": ["requirements/", "pyproject.toml", "superset/"],
    },
    {
        "match": "react-loadable",
        "expect_pr": True,
        "allowed_paths": ["superset-frontend/"],
    },
]

DEFAULT_SPEC = {"match": None, "expect_pr": True, "allowed_paths": [""]}


def spec_for(issue_title):
    for spec in ISSUE_SPECS:
        if spec["match"] and spec["match"].lower() in issue_title.lower():
            return spec
    return DEFAULT_SPEC


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

def build_work_prompt(issue):
    """The issue body IS the task contract; this wrapper adds coordinates
    and the non-negotiable mechanics Devin must follow."""
    number = issue["number"]
    return f"""\
You are remediating issue #{number} in the repository {config.FORK_FULL} \
(a fork of apache/superset). Work autonomously and follow the task contract below exactly.

MECHANICS (non-negotiable):
- Clone https://github.com/{config.FORK_FULL} and work on a branch named \
`devin/issue-{number}`.
- If a pull request is required, open it against `{config.FORK_DEFAULT_BRANCH}` of \
{config.FORK_FULL}. NEVER open a PR against apache/superset upstream.
- Follow the "If you get blocked" protocol in the contract: a well-evidenced \
blocked report is a valid outcome. Do not force changes that violate the \
contract's constraints.
- When you finish, fill in the structured output completely and accurately. \
The pipeline machine-verifies your claims against the actual PR; inaccurate \
reporting is treated as a failure.

TASK CONTRACT (issue #{number}: {issue['title']}):
---
{issue['body']}
---
"""


def build_review_prompt(issue, work_output):
    """Independent review session: fresh eyes, no authorship bias."""
    pr_url = work_output.get("pr_url") or "(no PR)"
    return f"""\
You are an independent reviewer. Another engineer (an autonomous agent) claims to \
have remediated issue #{issue['number']} in {config.FORK_FULL} via this pull request:

{pr_url}

Their claimed summary: {work_output.get('summary', '(none)')}
Their claimed tests: {work_output.get('tests_run', [])}

Your job is to verify the claim skeptically, NOT to extend the work:
1. Check out the PR branch and read the full diff.
2. Judge whether the diff actually remediates what the task contract requires \
(advisory IDs, acceptance criteria below), with no unrelated changes smuggled in.
3. Look for side effects the author may have missed (broken imports, behavior \
changes, incomplete migrations of a deprecated API).
4. Spot-verify the test evidence: do the claimed test commands make sense for \
this change, and do they pass?

Do NOT push commits, do NOT open PRs, do NOT merge. Report only, via structured output.

TASK CONTRACT under review (issue #{issue['number']}: {issue['title']}):
---
{issue['body']}
---
"""


def build_rework_message(entry, verdict):
    """Reviewer findings routed back to the work session - one bounded round."""
    risks = "\n".join(f"- {r}" for r in verdict.get("risks", [])) or "- see summary above"
    return f"""\
An independent review of your PR found problems that must be fixed before it can \
be accepted. This is an automated pipeline message; do not wait for further human input.

Reviewer summary: {verdict.get('summary', '')}

Findings to address:
{risks}

Fix these on the SAME branch (devin/issue-{entry['number']}) and update the same PR. \
Stay within the original task contract's scope. When done, update the structured \
output to reflect the new state of the PR (files_changed, tests_run). If a finding \
cannot be fixed within the contract's constraints, set outcome='blocked' with \
blocking_reason and mitigation instead.
"""


NUDGE_MESSAGE = (
    "Automated pipeline notice: this session appears blocked. Re-read the task "
    "contract in the prompt - it includes an 'If you get blocked' protocol. If you "
    "are truly unable to proceed, finish now and fill the structured output with "
    "outcome='blocked', the exact blocking_reason, and a recommended mitigation. "
    "Do not wait for human input inside this session."
)
