"""Scanner - the automated event source (scan-to-backlog).

Closes the detection half of the loop: reads the fork's dependency lockfile,
checks every pin against the OSV.dev advisory database, and opens a
contract-shaped issue (carrying the trigger label) for anything vulnerable.
The pipeline then picks those issues up like any other event.

Deliberately deterministic:
  - detection is a database join, not an LLM guess (complete + auditable);
  - issue bodies come from templates - an upgrade contract when a fixed
    version exists, an investigation contract when none does;
  - dedupe is by advisory ID: if any advisory already appears in an issue
    title (open or closed), it is not re-filed.
"""

import json
import re
import urllib.request

from . import config
from .http_util import request_json

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_GET = "https://api.osv.dev/v1/vulns/"
PIN = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*==\s*([A-Za-z0-9._+!-]+)")
MANIFEST = "requirements/base.txt"


def _fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "remediation-scanner/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _parse_pins(text):
    pins = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            m = PIN.match(line)
            if m:
                pins[m.group(1).lower()] = m.group(2)
    return pins


def _osv_query(pins):
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
               for n, v in sorted(pins.items())]
    body = json.dumps({"queries": queries}).encode()
    req = urllib.request.Request(OSV_BATCH, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        results = json.loads(r.read().decode())["results"]
    findings = []
    for q, res in zip(queries, results):
        vulns = res.get("vulns") or []
        if vulns:
            findings.append({
                "package": q["package"]["name"],
                "version": q["version"],
                "advisory_ids": [v["id"] for v in vulns],
            })
    return findings


def _advisory_detail(vid):
    d = json.loads(_fetch_text(OSV_GET + vid))
    fixed = set()
    for a in d.get("affected", []):
        for rng in a.get("ranges", []):
            for ev in rng.get("events", []):
                if "fixed" in ev:
                    fixed.add(ev["fixed"])
    return {
        "id": vid,
        "summary": (d.get("summary") or d.get("details", ""))[:200],
        "fixed_versions": sorted(fixed),
        "severity": d.get("database_specific", {}).get("severity", ""),
    }


BLOCKED_PROTOCOL = """\
## If you get blocked

Do not guess or force a change that breaks constraints. Stop and report:
what you tried, exactly what blocks the fix, and a recommended mitigation.
A well-evidenced "cannot be fixed safely today" report is a successful outcome.
"""


def _build_body(pkg, version, details):
    ids = ", ".join(d["id"] for d in details)
    fixed = sorted({v for d in details for v in d["fixed_versions"]})
    advisory_lines = "\n".join(
        f"- **{d['id']}**{(' (' + d['severity'] + ')') if d['severity'] else ''}: "
        f"{d['summary']}" for d in details)

    if fixed:
        target = fixed[-1]
        task = f"""\
## Task

1. Upgrade the `{pkg}` pin in `{MANIFEST}` to `{target}`.
2. Identify and fix any breaking-change fallout this causes in the codebase.
   Keep changes minimal - nothing beyond what the upgrade requires.
3. If other pins must move to stay consistent, move them the minimum
   necessary and justify each in the PR description.

## Acceptance criteria

- `pip install -r {MANIFEST}` resolves cleanly with `{pkg}=={target}`.
- `python -c "import superset.app"` succeeds.
- The relevant unit tests pass; report commands and results. If failures are
  pre-existing on master, demonstrate that.
- Open a PR against `{config.FORK_DEFAULT_BRANCH}` titled
  `fix(security): upgrade {pkg} to {target} ({details[0]['id']})` listing
  advisory IDs, files changed and why, and test evidence."""
    else:
        task = f"""\
## Task

No fixed release is listed for these advisories. This is an **investigation**
task, not a blind upgrade:

1. Verify the advisories' current status upstream.
2. Check whether any release within the project's declared constraints
   remediates them.
3. Assess actual exposure: where does this codebase use `{pkg}`, and is the
   vulnerable code path reachable?

## Acceptance criteria

- If a safe remediation exists within constraints: implement it as a PR
  (same evidence standard as other security issues).
- If not: report (a) advisory status, (b) the exact blocking dependency
  chain, (c) real exposure assessment, (d) recommended mitigation and the
  trigger condition to revisit. Recommend a human owner decision - do not
  close this issue yourself."""

    return f"""\
## Context (auto-generated by dependency scanner)

`{MANIFEST}` pins `{pkg}=={version}`, which is affected by:

{advisory_lines}

Fixed version(s) per OSV.dev: {', '.join(fixed) if fixed else '**none published**'}.
Detected automatically by the pipeline's OSV scan of `{config.FORK_FULL}`.

{task}

{BLOCKED_PROTOCOL}"""


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
              "labels": [config.TRIGGER_LABEL, "security"]})


def scan_once(log=print):
    """One scan pass: manifest -> OSV -> new contract-shaped issues.
    Returns the number of newly filed issues."""
    url = (f"https://raw.githubusercontent.com/{config.FORK_FULL}/"
           f"{config.FORK_DEFAULT_BRANCH}/{MANIFEST}")
    pins = _parse_pins(_fetch_text(url))
    log(f"[scan] {len(pins)} pinned packages in {MANIFEST}")
    findings = _osv_query(pins)
    log(f"[scan] {len(findings)} package(s) with advisories: "
        + ", ".join(f['package'] for f in findings))

    known = "\n".join(_existing_issue_titles())
    filed = 0
    for f in findings:
        if any(vid in known for vid in f["advisory_ids"]):
            log(f"[scan] {f['package']}: already tracked, skipping")
            continue
        details = [_advisory_detail(vid) for vid in f["advisory_ids"][:4]]
        lead = f["advisory_ids"][0]
        title = f"[security] {f['package']} {f['version']} vulnerable ({lead}) - auto-detected"
        issue = _create_issue(title, _build_body(f["package"], f["version"], details))
        filed += 1
        log(f"[scan] filed issue #{issue['number']}: {title}")
    return filed
