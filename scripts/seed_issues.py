"""Seed the Superset fork with the remediation backlog.

Part 1 of the workflow: these issues are the *event source* for the
automation. Each issue body is a task contract for Devin - context,
evidence, acceptance criteria, and an explicit blocked-protocol - so the
orchestrator can hand it to a session without human rewriting.

Idempotent: existing issues with the same title are left untouched.

Usage:
    python scripts/seed_issues.py            # create labels + issues
    python scripts/seed_issues.py --dry-run  # print what would be created
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def gh(method, path, token, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "devin-remediation-bot",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise SystemExit(f"GitHub {method} {path} -> HTTP {e.code}: {detail}") from e


LABELS = [
    ("devin-remediate", "1d76db", "Queued for autonomous remediation by Devin"),
    ("security", "d73a4a", "Security vulnerability"),
    ("code-quality", "a2eeef", "Code quality / tech debt"),
]

BLOCKED_PROTOCOL = """\
## If you get blocked

Do not guess or force a change that breaks the constraint. Instead stop and report:
1. what you tried,
2. exactly what blocks the upgrade (error output, incompatible dependency chain),
3. a recommended mitigation the team can apply in the meantime.

A well-evidenced "cannot be fixed safely today" report is a successful outcome for this task.
"""

ISSUES = [
    {
        "title": "[security] Upgrade Flask 2.3.3 to 3.1.3 (PYSEC-2026-2151)",
        "labels": ["devin-remediate", "security"],
        "body": f"""\
## Context

`requirements/base.txt` pins `flask==2.3.3`. Two published advisories affect this version:

- **PYSEC-2026-2151 / GHSA-68rp-wp8r-4726** - Flask sessions may miss the `Vary: Cookie` header in some access patterns. Fixed in **3.1.3**.

Source of truth checked on the current `master` of this fork:
- `requirements/base.txt`: `flask==2.3.3` (compiled lockfile)
- `pyproject.toml`: `"flask>=2.2.5, <4.0.0"` - so 3.1.3 is **inside** the declared constraint; only the lockfile pin is behind.
- `werkzeug==3.1.6` is already in the lockfile, which is the Werkzeug line Flask 3.x expects.

## Task

1. Upgrade the `flask` pin in `requirements/base.txt` to `3.1.3`.
2. This crosses a major version (2.x to 3.x). Identify and fix any breaking-change fallout in the Superset codebase (deprecated `flask.*` APIs, extension incompatibilities, config changes). Keep the fix minimal - do not refactor beyond what the upgrade requires.
3. If other pins in `requirements/base.txt` must move to stay consistent (e.g. flask extensions), move them the minimum necessary and list each one with a reason in the PR description.

## Acceptance criteria

- `pip install -r requirements/base.txt` resolves cleanly with `flask==3.1.3`.
- `python -c "import superset.app"` (app factory import) succeeds.
- The unit test suite relevant to the change passes: run `pytest tests/unit_tests` and report the result. If specific tests fail for reasons **unrelated** to this upgrade (pre-existing failures on master), demonstrate that by showing they fail on master too.
- Open a PR against `master` of this fork titled `fix(security): upgrade Flask to 3.1.3 (PYSEC-2026-2151)`. The PR description must list: advisory IDs, every file changed and why, tests run and their results.

{BLOCKED_PROTOCOL}""",
    },
    {
        "title": "[security] Upgrade setuptools 80.9.0 to 83.0.0 (PYSEC-2026-3447)",
        "labels": ["devin-remediate", "security"],
        "body": f"""\
## Context

`requirements/base.txt` pins `setuptools==80.9.0`, affected by:

- **PYSEC-2026-3447 / GHSA-h35f-9h28-mq5c** (severity: MODERATE) - `MANIFEST.in` exclusion bypass in sdist via Unicode normalization collision. Fixed in **83.0.0**.

`setuptools` enters the lockfile via `requirements/base.in`, not via `pyproject.toml` runtime deps. Note `pyproject.toml` build-system requires `setuptools>=40.9.0` (unaffected by this change, but verify the build still works).

## Task

1. Upgrade the `setuptools` pin in `requirements/base.txt` to `83.0.0`.
2. Verify nothing in the dependency tree requires `setuptools<83`.

## Acceptance criteria

- `pip install -r requirements/base.txt` resolves cleanly with `setuptools==83.0.0`.
- `python -c "import superset.app"` succeeds.
- A source build sanity check passes (e.g. `python -m build --sdist` or equivalent import-time check if a full build is impractical in the workspace).
- Open a PR against `master` titled `fix(security): upgrade setuptools to 83.0.0 (PYSEC-2026-3447)` with advisory IDs, files changed, and test evidence in the description.

{BLOCKED_PROTOCOL}""",
    },
    {
        "title": "[security] paramiko 3.5.1 SHA-1 advisory (PYSEC-2026-2858) - investigate; no fixed release exists",
        "labels": ["devin-remediate", "security"],
        "body": f"""\
## Context

`requirements/base.txt` pins `paramiko==3.5.1`, affected by:

- **PYSEC-2026-2858 / GHSA-r374-rxx8-8654** (severity: LOW) - `rsakey.py` allows the SHA-1 algorithm. **OSV lists no fixed release** at scan time.

There is a known upgrade ceiling documented in this repo's own `pyproject.toml`:

    "paramiko>=3.4.0, <4.0", # 4.0 removed DSSKey, still referenced by sshtunnel

and the lockfile has `sshtunnel==0.4.0`. So even if a fix lands in paramiko 4.x, this project cannot take it while sshtunnel 0.4.x is in the tree.

## Task

This is an **investigation** task, not a blind upgrade:

1. Verify the advisory's current status (is a fixed version available now? which version?).
2. Check whether any paramiko release inside the `<4.0` constraint remediates it.
3. Check the sshtunnel project: is there a release or master commit that drops the `DSSKey` usage, which would unlock paramiko 4.x?
4. Assess actual exposure in Superset: where does Superset use paramiko/sshtunnel, and is the SHA-1 code path reachable in that usage?

## Acceptance criteria

- If a safe remediation exists within constraints: implement it as a PR (same evidence standard as the other security issues).
- If not: post a comment on this issue containing (a) advisory status, (b) why the upgrade is blocked with the exact dependency chain, (c) real exposure assessment in Superset's usage, (d) recommended mitigation and the trigger condition to revisit (e.g. "sshtunnel releases DSSKey-free version"). Recommend a human owner decision - do not close the issue yourself.

{BLOCKED_PROTOCOL}""",
    },
    {
        "title": "[code-quality] Replace deprecated react-loadable with React.lazy/Suspense",
        "labels": ["devin-remediate", "code-quality"],
        "body": f"""\
## Context

`superset-frontend/package.json` depends on `react-loadable ^5.5.0` (plus `@types/react-loadable` in devDependencies). react-loadable has been unmaintained since ~2018 and predates React's built-in code-splitting; the project is on React 18.3 where `React.lazy` + `Suspense` is the supported mechanism.

## Task

1. Find every usage of `react-loadable` in `superset-frontend/` (imports, wrapper components, type imports).
2. Replace each with `React.lazy` + `Suspense`, preserving the existing loading-fallback behavior of each call site.
3. Remove `react-loadable` and `@types/react-loadable` from `package.json`.
4. Keep the change mechanical and reviewable - no unrelated refactoring.

## Acceptance criteria

- No references to `react-loadable` remain in source or package manifests.
- TypeScript compiles: `npm run type` (or the project's type-check script) passes in `superset-frontend/`.
- Jest tests covering the touched components pass; run the closest test scope and report results.
- Open a PR against `master` titled `chore: replace react-loadable with React.lazy` listing every touched file and the test evidence.

{BLOCKED_PROTOCOL}""",
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    token = os.environ.get("GITHUB_TOKEN")
    owner = os.environ.get("GITHUB_OWNER")
    repo = os.environ.get("FORK_REPO", "devin_takehome_assignment")
    if not token or not owner:
        raise SystemExit("GITHUB_TOKEN / GITHUB_OWNER missing (.env)")

    full = f"{owner}/{repo}"
    if args.dry_run:
        for i in ISSUES:
            print(f"WOULD CREATE: {i['title']}  labels={i['labels']}")
        return

    existing_labels = {l["name"] for l in gh("GET", f"/repos/{full}/labels?per_page=100", token)}
    for name, color, desc in LABELS:
        if name not in existing_labels:
            gh("POST", f"/repos/{full}/labels", token, {"name": name, "color": color, "description": desc})
            print(f"[+] label created: {name}")
        else:
            print(f"[=] label exists: {name}")

    existing_titles = {
        i["title"]: i["number"]
        for i in gh("GET", f"/repos/{full}/issues?state=all&per_page=100", token)
        if "pull_request" not in i
    }
    for spec in ISSUES:
        if spec["title"] in existing_titles:
            print(f"[=] issue exists: #{existing_titles[spec['title']]} {spec['title']}")
            continue
        r = gh("POST", f"/repos/{full}/issues", token,
               {"title": spec["title"], "body": spec["body"], "labels": spec["labels"]})
        print(f"[+] issue created: #{r['number']} {spec['title']}")
        print(f"    {r['html_url']}")


if __name__ == "__main__":
    sys.exit(main())
