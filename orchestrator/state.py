"""State store - the single source of truth for the pipeline.

One JSON file, atomic writes, append-only per-issue timelines. The dashboard,
the status CLI, and every metric are derived from this file; nothing else
holds state. That makes the whole system inspectable with `cat` and
restartable at any point (the tick loop is re-entrant against this store).
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from . import config

# Issue lifecycle states (the whitelist - anything that can't be proven to be
# in one of the healthy states lands in NEEDS_ATTENTION, never in silence).
QUEUED = "queued"                    # discovered, not yet dispatched
WORKING = "working"                  # work session live
REVIEW_PENDING = "review_pending"    # gate 1 passed, review not yet dispatched
REVIEW_WORKING = "review_working"    # review session live
DONE_FIXED = "done_fixed"            # PR opened, gates passed, reported
DONE_ESCALATED = "done_escalated"    # blocked outcome with valid report, reported
NEEDS_ATTENTION = "needs_attention"  # suspicion lane: invariant violated

ACTIVE_STATES = {WORKING, REVIEW_WORKING}
TERMINAL_STATES = {DONE_FIXED, DONE_ESCALATED, NEEDS_ATTENTION}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load():
    if config.STATE_PATH.exists():
        return json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
    return {"issues": {}, "meta": {"created_at": utcnow(), "ticks": 0}}


def save(state):
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: never leave a half-written state file behind
    fd, tmp = tempfile.mkstemp(dir=str(config.STATE_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, config.STATE_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(state, issue_key, event, detail=""):
    """Append a timeline event. The timeline is the audit trail."""
    entry = state["issues"][issue_key]
    entry.setdefault("timeline", []).append(
        {"ts": utcnow(), "event": event, "detail": str(detail)[:500]}
    )


def transition(state, issue_key, new_state, reason=""):
    entry = state["issues"][issue_key]
    old = entry.get("state")
    entry["state"] = new_state
    entry[f"{new_state}_at"] = utcnow()
    record(state, issue_key, f"state: {old} -> {new_state}", reason)


def new_issue_entry(issue):
    return {
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["html_url"],
        "body": issue.get("body", ""),
        "state": QUEUED,
        "queued_at": utcnow(),
        "work_session": None,
        "review_session": None,
        "outcome": None,
        "pr_url": None,
        "attention_reason": None,
        "timeline": [{"ts": utcnow(), "event": "discovered", "detail": issue["html_url"]}],
    }
