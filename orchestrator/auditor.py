"""Code auditor - the judgment-based detector.

The dependency scanner (scanner.py) is deterministic: it can only see
problems a database already knows about. This module covers the class it
cannot: code-level defects. Detection itself is delegated to a Devin
session (role: auditor) that reads the code and returns findings through a
structured schema; the orchestrator then files each finding as a
contract-shaped issue, deterministically, burst-capped and deduplicated.

Same trust rules as everywhere else: the audit session is report-only
(no code changes), cost-capped, tagged, and its findings enter the normal
pipeline where a *different* session implements and yet another reviews.
"""

import time

from . import config, contracts, devin_client
from .http_util import request_json

POLL_SEC = 30
BLOCKED = {"blocked"}
TERMINAL = {"finished", "expired"}


def _existing_issue_titles():
    titles = []
    for state_q in ("open", "closed"):
        items = request_json(
            "GET",
            f"{config.GITHUB_API_BASE}/repos/{config.FORK_FULL}/issues"
            f"?state={state_q}&per_page=100",
            headers={"Authorization": f"Bearer {config.GITHUB_TOKEN}",
                     "Accept": "application/vnd.github+json"})
        titles += [i["title"] for i in items]
    return titles


def _create_issue(title, body):
    return request_json(
        "POST", f"{config.GITHUB_API_BASE}/repos/{config.FORK_FULL}/issues",
        headers={"Authorization": f"Bearer {config.GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json"},
        body={"title": title, "body": body,
              "labels": [config.TRIGGER_LABEL, "code-quality"]})


BLOCKED_PROTOCOL = """\
## If you get blocked

Do not guess or force a change that breaks constraints. Stop and report:
what you tried, exactly what blocks the fix, and a recommended mitigation.
A well-evidenced "cannot be fixed safely today" report is a successful outcome.
"""


def _build_issue_body(f, scope):
    return f"""\
## Context (auto-detected by code audit)

An automated code audit of `{scope}` in `{config.FORK_FULL}` found:

**{f['title']}**

- **File:** `{f['file']}`
- **Category:** {f.get('category', 'code-quality')}
- **Evidence:**

```
{f['evidence']}
```

**Why it matters:** {f['why_it_matters']}

## Task

1. Verify the finding against the current code (the audit is a report, not
   ground truth - if it does not hold, say so and stop).
2. Fix it minimally: {f.get('suggested_fix', 'apply the smallest correct fix')}
3. No unrelated refactoring.

## Acceptance criteria

- The defect pattern is gone from the cited location (and identical siblings
  in the same file, if any).
- Relevant tests pass; report commands and results.
- Open a PR against `{config.FORK_DEFAULT_BRANCH}` titled
  `fix: {f['title'][:60]}` listing files changed and test evidence.

{BLOCKED_PROTOCOL}"""


def run_audit(scope=None, log=print, timeout_min=60):
    """Fire one audit session, wait for findings, file capped issues.
    Returns the number of issues filed."""
    scope = scope or config.AUDIT_SCOPE
    log(f"[audit] starting audit session over: {scope}")
    resp = devin_client.create_session(
        prompt=contracts.build_audit_prompt(scope),
        title=f"[remediation-bot] audit: {scope[:60]}",
        tags=["remediation-bot", "role-audit"],
        structured_output_schema=contracts.AUDIT_SCHEMA,
        idempotent=True,
        max_acu_limit=max(2, config.MAX_ACU_PER_SESSION // 2),
    )
    sid = resp["session_id"]
    log(f"[audit] session {resp.get('url', sid)}")

    deadline = time.time() + timeout_min * 60
    out = None
    while time.time() < deadline:
        d = devin_client.get_session(sid)
        status = d.get("status_enum") or d.get("status") or "?"
        out = d.get("structured_output")
        if (status in TERMINAL or status in BLOCKED) and isinstance(out, dict) \
                and isinstance(out.get("findings"), list):
            break
        log(f"[audit] waiting... (status: {status})")
        time.sleep(POLL_SEC)
    else:
        log("[audit] timed out waiting for findings - inspect the session")
        return 0

    findings = out["findings"]
    log(f"[audit] session returned {len(findings)} finding(s); "
        f"scope covered: {out.get('scope_covered', scope)}")

    known = "\n".join(_existing_issue_titles())
    filed = 0
    for f in findings:
        title = f"[code-quality] {f['title']} ({f['file']}) - auto-detected"
        if f["file"] in known and f["title"][:40] in known:
            log(f"[audit] already tracked, skipping: {f['title']}")
            continue
        if config.AUDIT_MAX_NEW and filed >= config.AUDIT_MAX_NEW:
            log(f"[audit] burst cap {config.AUDIT_MAX_NEW} reached - "
                f"deferring: {f['title']}")
            continue
        issue = _create_issue(title, _build_issue_body(f, scope))
        filed += 1
        log(f"[audit] filed issue #{issue['number']}: {title}")
    return filed
