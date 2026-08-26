"""Simulation clients - the full pipeline with zero credentials and zero network.

`SIMULATE=1 python -m orchestrator run` exercises every state transition
(dispatch, monitoring, gates, review, escalation, suspicion lane) against
scripted fixtures modeled on the real 4-issue backlog. Reviewers can watch
the whole system work without a Devin key, ACUs, or GitHub tokens.

The real and simulated clients expose the same five methods; pipeline.py is
injected with one or the other and cannot tell the difference.
"""

import itertools

# Fixtures mirror the real seeded backlog (see scripts/seed_issues.py).
FIXTURE_ISSUES = [
    {"number": 1, "title": "[security] Upgrade Flask 2.3.3 to 3.1.3 (PYSEC-2026-2151)",
     "html_url": "sim://issues/1", "body": "(sim) flask upgrade contract"},
    {"number": 2, "title": "[security] Upgrade setuptools 80.9.0 to 83.0.0 (PYSEC-2026-3447)",
     "html_url": "sim://issues/2", "body": "(sim) setuptools upgrade contract"},
    {"number": 3, "title": "[security] paramiko 3.5.1 SHA-1 advisory (PYSEC-2026-2858) - investigate",
     "html_url": "sim://issues/3", "body": "(sim) paramiko investigation contract"},
    {"number": 4, "title": "[code-quality] Replace deprecated react-loadable with React.lazy/Suspense",
     "html_url": "sim://issues/4", "body": "(sim) react-loadable refactor contract"},
]

# Scripted session lifecycles: issue -> list of successive get_session() states.
# Covers all four demo narratives: clean fix, fix-after-work, escalation, refactor.
_WORK_SCRIPTS = {
    1: [  # flask: takes a while, then fixed
        {"status_enum": "working"},
        {"status_enum": "working"},
        {"status_enum": "finished", "structured_output": {
            "outcome": "fixed", "summary": "(sim) upgraded flask, fixed 3 breaking call sites",
            "pr_url": "sim://pr/101", "branch": "devin/issue-1",
            "files_changed": ["requirements/base.txt", "superset/app.py"],
            "tests_run": ["pytest tests/unit_tests -> passed"]}},
    ],
    2: [  # setuptools: quick fix
        {"status_enum": "working"},
        {"status_enum": "finished", "structured_output": {
            "outcome": "fixed", "summary": "(sim) bumped setuptools pin",
            "pr_url": "sim://pr/102", "branch": "devin/issue-2",
            "files_changed": ["requirements/base.txt"],
            "tests_run": ["pip install -r requirements/base.txt -> ok"]}},
    ],
    3: [  # paramiko: investigation concludes blocked -> escalation lane
        {"status_enum": "working"},
        {"status_enum": "finished", "structured_output": {
            "outcome": "blocked", "summary": "(sim) no fixed release; sshtunnel pins DSSKey",
            "pr_url": None, "branch": None, "files_changed": [], "tests_run": [],
            "blocking_reason": "(sim) paramiko <4.0 constraint; no fixed version exists",
            "mitigation": "(sim) disable SHA-1 via transport config; revisit on sshtunnel release",
            "advisory_status": "(sim) no upstream fix as of scan"}},
    ],
    4: [  # react-loadable: goes quiet once (blocked) to exercise the nudge path
        {"status_enum": "working"},
        {"status_enum": "blocked"},
        {"status_enum": "finished", "structured_output": {
            "outcome": "fixed", "summary": "(sim) replaced 6 react-loadable call sites",
            "pr_url": "sim://pr/104", "branch": "devin/issue-4",
            "files_changed": ["superset-frontend/src/components/x.tsx"],
            "tests_run": ["npm run type -> passed"]}},
    ],
}

_REVIEW_SCRIPT = [
    {"status_enum": "working"},
    {"status_enum": "finished", "structured_output": {
        "verdict": "approve", "summary": "(sim) diff matches contract, tests credible",
        "checks_performed": ["(sim) read diff", "(sim) reran type check"], "risks": []}},
]


class SimDevin:
    def __init__(self):
        self._counter = itertools.count(1)
        self._sessions = {}   # session_id -> {"script": [...], "cursor": int}
        self.messages = []    # nudges sent, for assertion/inspection

    def healthcheck(self):
        return True, "simulation mode (no network)"

    def create_session(self, prompt, title, tags, structured_output_schema=None,
                       max_acu_limit=None, idempotent=True):
        sid = f"sim-session-{next(self._counter)}"
        issue_no = next((int(t.split("-")[1]) for t in tags if t.startswith("issue-")), 0)
        script = (_REVIEW_SCRIPT if "role-review" in tags
                  else _WORK_SCRIPTS.get(issue_no, _WORK_SCRIPTS[2]))
        self._sessions[sid] = {"script": script, "cursor": 0}
        return {"session_id": sid, "url": f"sim://session/{sid}", "is_new_session": True}

    def get_session(self, session_id):
        s = self._sessions[session_id]
        step = s["script"][min(s["cursor"], len(s["script"]) - 1)]
        s["cursor"] += 1
        return {"session_id": session_id, **step}

    def send_message(self, session_id, message):
        self.messages.append((session_id, message))
        return {}


class SimGithub:
    """Read fixtures; log writes instead of performing them."""

    def __init__(self):
        self.comments = []  # (issue_number, body) - inspectable observable output

    def list_open_labeled_issues(self):
        return FIXTURE_ISSUES

    def comment_on_issue(self, number, body):
        self.comments.append((number, body))
        print(f"[sim] comment on issue #{number}:\n{_indent(body)}")
        return {"html_url": f"sim://issues/{number}#comment"}

    def get_pull_request(self, pr_url):
        if pr_url and pr_url.startswith("sim://pr/"):
            return {"html_url": pr_url, "state": "open",
                    "base": {"repo": {"full_name": "sim/fork"}}}
        return None

    def merge_pull_request(self, pr_url):
        print(f"[sim] merge PR {pr_url}")
        return {"merged": True}

    def get_pull_request_files(self, pr_url):
        # scripted diffs match each issue's allowed paths
        by_pr = {
            "sim://pr/101": ["requirements/base.txt", "superset/app.py"],
            "sim://pr/102": ["requirements/base.txt"],
            "sim://pr/104": ["superset-frontend/src/components/x.tsx"],
        }
        return [{"filename": f} for f in by_pr.get(pr_url, [])]


def _indent(text):
    return "\n".join("    " + line for line in text.splitlines()[:12])
