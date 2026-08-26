"""The orchestration core: one deterministic tick, run in a loop.

Design rules this file follows:
  - No LLM calls here. Judgment lives in Devin sessions (work / review);
    this code only detects, dispatches, verifies, and reports.
  - "Healthy" is a whitelist. Any state that cannot be proven healthy goes
    to the suspicion lane (NEEDS_ATTENTION) and is reported - never ignored.
  - Every external effect (session create, comment) is guarded so a crashed
    tick can be re-run: sessions are idempotent, comments are recorded in
    state before being considered done.
"""

import traceback
from datetime import datetime, timezone

from . import config, contracts
from . import state as st
from .devin_client import TERMINAL_STATES, BLOCKED_STATES

NUDGE_GRACE_MIN = 15  # blocked session gets one nudge, then this long to comply


def _age_minutes(iso_ts):
    then = datetime.fromisoformat(iso_ts)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60


def tick(gh, devin, state, log=print):
    """One full pass: watch -> dispatch -> monitor -> gate -> report."""
    state["meta"]["ticks"] += 1

    _watch(gh, state, log)

    for key in sorted(state["issues"], key=int):
        entry = state["issues"][key]
        try:
            _advance(gh, devin, state, key, entry, log)
        except Exception as exc:  # one issue's failure never stalls the others
            log(f"[!] issue #{key}: tick error: {exc}")
            st.record(state, key, "tick_error", f"{exc}\n{traceback.format_exc()[:400]}")

    st.save(state)


# --- 1 Watcher --------------------------------------------------------------

def _watch(gh, state, log):
    for issue in gh.list_open_labeled_issues():
        key = str(issue["number"])
        if key not in state["issues"]:
            state["issues"][key] = st.new_issue_entry(issue)
            log(f"[+] discovered issue #{key}: {issue['title']}")


# --- per-issue state machine ------------------------------------------------

def _advance(gh, devin, state, key, entry, log):
    s = entry["state"]
    if s == st.QUEUED:
        _dispatch_work(gh, devin, state, key, entry, log)
    elif s == st.WORKING:
        _monitor_work(gh, devin, state, key, entry, log)
    elif s == st.REVIEW_PENDING:
        _dispatch_review(gh, devin, state, key, entry, log)
    elif s == st.REVIEW_WORKING:
        _monitor_review(gh, devin, state, key, entry, log)
    # terminal states: nothing to do


# --- 2 Dispatcher -----------------------------------------------------------

def _live_sessions(state):
    return sum(1 for e in state["issues"].values() if e["state"] in st.ACTIVE_STATES)


def _dispatch_work(gh, devin, state, key, entry, log):
    if _live_sessions(state) >= config.MAX_CONCURRENT_SESSIONS:
        return  # stay queued; next tick retries
    resp = devin.create_session(
        prompt=contracts.build_work_prompt(entry),
        title=f"[remediation-bot] issue #{key}: {entry['title'][:60]}",
        tags=["remediation-bot", f"issue-{key}", "role-work"],
        structured_output_schema=contracts.WORK_SCHEMA,
        idempotent=True,  # a re-run tick can never double-fire
    )
    entry["work_session"] = {
        "id": resp["session_id"], "url": resp.get("url", ""),
        "created_at": st.utcnow(), "nudged_at": None, "last_status": "created",
    }
    st.transition(state, key, st.WORKING,
                  f"cap {config.MAX_ACU_PER_SESSION} ACU · "
                  f"timeout {config.SESSION_TIMEOUT_MIN}m")
    log(f"[>] issue #{key}: work session started {resp.get('url', '')}")
    gh.comment_on_issue(entry["number"], _msg_dispatched(entry))


# --- 3 Monitor (work) -------------------------------------------------------

def _capture_activity(state, key, sess, role, detail):
    """Surface Devin's messages so the dashboard can show what the agent is
    actually doing. Keeps a per-session cursor and appends anything new to a
    global feed - the dashboard's live activity stream."""
    devin_msgs = [m for m in detail.get("messages", [])
                  if m.get("type") == "devin_message"]
    if not devin_msgs:
        return
    sess["last_activity"] = (devin_msgs[-1].get("message") or "")[:280]
    seen = sess.get("feed_cursor", 0)
    feed = state.setdefault("feed", [])
    for m in devin_msgs[seen:]:
        feed.append({
            "ts": (m.get("timestamp") or st.utcnow())[:19],
            "issue": key, "role": role,
            "message": (m.get("message") or "")[:400],
        })
    sess["feed_cursor"] = len(devin_msgs)
    del feed[:-100]  # keep the last 100 entries


def _work_output_complete(out):
    """Calibration finding: Devin parks in 'blocked' (awaiting-user) after
    finishing its work; 'finished' may never come. A complete, contract-
    shaped structured output while parked IS completion."""
    return isinstance(out, dict) and out.get("outcome") in ("fixed", "blocked", "partial")


def _monitor_work(gh, devin, state, key, entry, log):
    ws = entry["work_session"]
    detail = devin.get_session(ws["id"])
    status = detail.get("status_enum") or detail.get("status") or "unknown"
    ws["last_status"] = status
    _capture_activity(state, key, ws, "work", detail)

    out = detail.get("structured_output")
    parked = status in TERMINAL_STATES or status in BLOCKED_STATES
    # after a rework request, the previous output is stale: do not re-gate
    # until Devin has produced something new
    fresh = out != ws.get("stale_output")
    if parked and fresh and _work_output_complete(out):
        _gate1(gh, devin, state, key, entry, detail, log)
    elif status in TERMINAL_STATES:
        # terminal without usable output -> gate1 routes it to attention
        _gate1(gh, devin, state, key, entry, detail, log)
    elif status in BLOCKED_STATES:
        _handle_blocked(gh, devin, state, key, entry, ws, log)
    elif _age_minutes(ws["created_at"]) > config.SESSION_TIMEOUT_MIN:
        # suspicion lane: alive but overdue = not provably healthy
        _attention(gh, state, key, entry,
                   f"work session exceeded {config.SESSION_TIMEOUT_MIN} min "
                   f"(status: {status})", log)


def _handle_blocked(gh, devin, state, key, entry, sess, log):
    """A blocked session gets exactly one automated nudge, then escalates."""
    if sess["nudged_at"] is None:
        devin.send_message(sess["id"], contracts.NUDGE_MESSAGE)
        sess["nudged_at"] = st.utcnow()
        st.record(state, key, "nudged", "session blocked; sent protocol reminder")
        log(f"[~] issue #{key}: session blocked, nudged once")
    elif _age_minutes(sess["nudged_at"]) > NUDGE_GRACE_MIN:
        _attention(gh, state, key, entry,
                   "session still blocked after nudge - needs a human look", log)


# --- 4 Gate 1: mechanical verification (code, no judgment) ------------------

def _gate1(gh, devin, state, key, entry, session_detail, log):
    out = session_detail.get("structured_output")
    spec = contracts.spec_for(entry["title"])

    # invariant: terminal session must produce contract-shaped output
    if not isinstance(out, dict) or out.get("outcome") not in ("fixed", "blocked", "partial"):
        return _attention(gh, state, key, entry,
                          f"session finished without valid structured output: {out!r}", log)

    entry["outcome"] = out["outcome"]
    entry["work_output"] = out
    if out["outcome"] == "fixed":
        claim = (f"claims fixed · {len(out.get('files_changed') or [])} files · "
                 f"{len(out.get('tests_run') or [])} test commands")
    elif out["outcome"] == "blocked":
        claim = "claims blocked · " + (out.get("blocking_reason") or "no reason")[:90]
    else:
        claim = "claims partial completion"
    st.record(state, key, "gate1", claim)

    if out["outcome"] == "blocked":
        # invariant: a blocked claim must carry its evidence
        if not out.get("blocking_reason") or not out.get("mitigation"):
            return _attention(gh, state, key, entry,
                              "blocked outcome missing blocking_reason/mitigation", log)
        st.transition(state, key, st.DONE_ESCALATED,
                      "blocked report accepted · "
                      + (out.get("blocking_reason") or "")[:80])
        gh.comment_on_issue(entry["number"], _msg_escalation(entry, out))
        log(f"[E] issue #{key}: escalated with report")
        return

    if out["outcome"] == "partial":
        return _attention(gh, state, key, entry,
                          f"partial outcome - human decision needed: {out.get('summary')}", log)

    # outcome == fixed: verify the claim against reality
    pr_url = out.get("pr_url")
    if not pr_url:
        return _attention(gh, state, key, entry, "outcome=fixed but no pr_url", log)
    pr = gh.get_pull_request(pr_url)
    if pr is None:
        return _attention(gh, state, key, entry,
                          f"claimed PR does not exist: {pr_url}", log)

    files = [f["filename"] for f in gh.get_pull_request_files(pr_url)]
    off_scope = [f for f in files
                 if not any(f.startswith(p) for p in spec["allowed_paths"])]
    if off_scope:
        # autonomy policy: low-risk scope growth (docs, tests) can be
        # auto-accepted; anything touching source outside contract cannot
        auto_ok = config.POLICY["auto_accept_scope"]
        risky = [f for f in off_scope
                 if not any(f.startswith(p) for p in auto_ok)]
        if risky:
            sample = ", ".join(risky[:2])
            return _attention(gh, state, key, entry,
                              f"PR touches {len(risky)} file(s) outside contracted "
                              f"scope · e.g. {sample}", log)
        st.record(state, key, "scope_auto_accepted",
                  f"{len(off_scope)} low-risk file(s) (docs/tests) accepted by "
                  f"{config.AUTONOMY_MODE} policy")
    if not out.get("tests_run"):
        return _attention(gh, state, key, entry, "fixed claim with no test evidence", log)

    entry["pr_url"] = pr_url
    if config.REVIEW_MODE == "off":
        st.transition(state, key, st.DONE_FIXED, "gate1 passed (review disabled)")
        gh.comment_on_issue(entry["number"], _msg_fixed(entry, out, review=None))
        log(f"[Y] issue #{key}: fixed, gate1 passed")
    else:
        st.transition(state, key, st.REVIEW_PENDING, "gate1 passed")
        log(f"[Y] issue #{key}: gate1 passed, queuing independent review")


# --- 5 Gate 2: independent Devin review session -----------------------------

def _dispatch_review(gh, devin, state, key, entry, log):
    if _live_sessions(state) >= config.MAX_CONCURRENT_SESSIONS:
        return
    resp = devin.create_session(
        prompt=contracts.build_review_prompt(entry, entry["work_output"]),
        title=f"[remediation-bot] review #{key}: {entry['title'][:56]}",
        tags=["remediation-bot", f"issue-{key}", "role-review"],
        structured_output_schema=contracts.REVIEW_SCHEMA,
        idempotent=True,
        max_acu_limit=max(2, config.MAX_ACU_PER_SESSION // 2),  # reviews are cheaper
    )
    entry["review_session"] = {
        "id": resp["session_id"], "url": resp.get("url", ""),
        "created_at": st.utcnow(), "nudged_at": None, "last_status": "created",
    }
    st.transition(state, key, st.REVIEW_WORKING, f"review session {resp['session_id']}")
    log(f"[>] issue #{key}: review session started")


def _review_output_complete(out):
    return isinstance(out, dict) and out.get("verdict") in ("approve", "request_changes")


def _monitor_review(gh, devin, state, key, entry, log):
    rs = entry["review_session"]
    detail = devin.get_session(rs["id"])
    status = detail.get("status_enum") or detail.get("status") or "unknown"
    rs["last_status"] = status
    _capture_activity(state, key, rs, "review", detail)

    parked = status in TERMINAL_STATES or status in BLOCKED_STATES
    if status in TERMINAL_STATES or (parked and _review_output_complete(
            detail.get("structured_output"))):
        verdict = detail.get("structured_output")
        if not isinstance(verdict, dict) or verdict.get("verdict") not in (
                "approve", "request_changes"):
            return _attention(gh, state, key, entry,
                              f"review finished without valid verdict: {verdict!r}", log)
        entry["review_output"] = verdict
        st.record(state, key, "review_verdict",
                  f"{verdict['verdict']} · {(verdict.get('summary') or '')[:110]}")
        if verdict["verdict"] == "approve":
            st.transition(state, key, st.DONE_FIXED, "independent review approved the PR")
            gh.comment_on_issue(entry["number"],
                                _msg_fixed(entry, entry["work_output"], verdict))
            log(f"[Y] issue #{key}: review approved -> done")
            if config.POLICY["auto_merge"]:
                merged = gh.merge_pull_request(entry["pr_url"])
                if merged:
                    st.record(state, key, "auto_merged",
                              f"PR merged by {config.AUTONOMY_MODE} policy")
                    log(f"[Y] issue #{key}: PR auto-merged (autopilot)")
                else:
                    _attention(gh, state, key, entry,
                               "autopilot merge failed - merge manually", log)
        elif entry.get("rework_count", 0) < MAX_REWORKS:
            _dispatch_rework(gh, devin, state, key, entry, verdict, log)
        else:
            _attention(gh, state, key, entry,
                       "review still requests changes after "
                       f"{MAX_REWORKS} rework round(s): "
                       + "; ".join(verdict.get("risks", [])[:3]), log)
    elif status in BLOCKED_STATES:
        _handle_blocked(gh, devin, state, key, entry, rs, log)
    elif _age_minutes(rs["created_at"]) > config.SESSION_TIMEOUT_MIN:
        _attention(gh, state, key, entry, "review session timed out", log)


# --- bounded rework loop ----------------------------------------------------

MAX_REWORKS = config.POLICY["max_reworks"]  # autonomy-mode dependent


def _dispatch_rework(gh, devin, state, key, entry, verdict, log):
    """Send the independent reviewer's findings back to the work session so
    it can fix its own PR. Bounded: after MAX_REWORKS rounds the issue goes
    to the suspicion lane instead of looping forever."""
    entry["rework_count"] = entry.get("rework_count", 0) + 1
    ws = entry["work_session"]
    ws["stale_output"] = entry.get("work_output")  # ignore old output at the gate
    ws["nudged_at"] = None                          # blocked-handling resets too
    devin.send_message(ws["id"], contracts.build_rework_message(entry, verdict))
    risks = verdict.get("risks", [])
    st.record(state, key, "rework_dispatched",
              f"round {entry['rework_count']}/{MAX_REWORKS} · {len(risks)} finding(s)"
              + (f" · e.g. {risks[0][:80]}" if risks else ""))
    st.transition(state, key, st.WORKING, "fixing reviewer findings on the same PR")
    gh.comment_on_issue(entry["number"], _msg_rework(entry, verdict))
    log(f"[~] issue #{key}: review requested changes -> sent back for rework "
        f"(round {entry['rework_count']}/{MAX_REWORKS})")


# --- suspicion lane ---------------------------------------------------------

def _attention(gh, state, key, entry, reason, log):
    """Anything not provably healthy lands here - loudly, never silently."""
    entry["attention_reason"] = reason
    st.transition(state, key, st.NEEDS_ATTENTION, reason)
    gh.comment_on_issue(entry["number"], _msg_attention(entry, reason))
    log(f"[!] issue #{key}: NEEDS ATTENTION - {reason}")


# --- 6 Reporter: observable outputs (issue comments) ------------------------

def _session_link(sess):
    return sess["url"] if sess else "n/a"


def _msg_dispatched(entry):
    return (f"**Remediation pipeline: session started.**\n\n"
            f"Devin is working on this issue autonomously.\n"
            f"Session: {_session_link(entry['work_session'])}\n"
            f"Cost ceiling: {config.MAX_ACU_PER_SESSION} ACU | "
            f"timeout: {config.SESSION_TIMEOUT_MIN} min")


def _msg_fixed(entry, out, review):
    lines = [
        "**Remediation pipeline: fix verified.**", "",
        f"Pull request: {entry['pr_url']}",
        f"Files changed: {', '.join(out.get('files_changed', [])[:10]) or 'see PR'}",
        "Test evidence:",
    ]
    lines += [f"- {t}" for t in out.get("tests_run", [])[:6]]
    lines += ["", f"Work session: {_session_link(entry['work_session'])}"]
    if review:
        lines += [
            "",
            "**Independent review (separate Devin session): approved.**",
            f"> {review.get('summary', '')}",
            f"Review session: {_session_link(entry['review_session'])}",
        ]
    lines += ["", "_Merge decision remains with a human maintainer._"]
    return "\n".join(lines)


def _msg_escalation(entry, out):
    return "\n".join([
        "**Remediation pipeline: escalated to human decision.**", "",
        "Devin investigated and determined this cannot be auto-remediated safely.", "",
        f"**Advisory status:** {out.get('advisory_status') or 'n/a'}",
        f"**Blocking cause:** {out.get('blocking_reason')}",
        f"**Recommended mitigation:** {out.get('mitigation')}", "",
        f"Full investigation: {_session_link(entry['work_session'])}", "",
        "_This issue stays open for a human owner decision, per contract._",
    ])


def _msg_rework(entry, verdict):
    risks = "\n".join(f"- {r}" for r in verdict.get("risks", [])[:5])
    return "\n".join([
        "**Remediation pipeline: independent review requested changes.**", "",
        f"> {verdict.get('summary', '')}", "",
        "Findings sent back to the work session for one autonomous fix round:", "",
        risks or "- (see review summary)", "",
        f"Work session: {_session_link(entry['work_session'])}",
        f"Review session: {_session_link(entry['review_session'])}",
    ])


def _msg_attention(entry, reason):
    pr = entry.get("pr_url")
    return "\n".join([
        "**Remediation pipeline: needs human attention.**", "",
        f"**What happened:** {reason}", "",
        "**What to check:**",
        f"1. The work session log - what Devin actually did: "
        f"{_session_link(entry.get('work_session'))}",
        *( [f"2. The PR under question: {pr}"] if pr else [] ),
        *( [f"3. The reviewer's full reasoning: "
            f"{_session_link(entry.get('review_session'))}"]
           if entry.get("review_session") else [] ),
        "",
        "**Your options:**",
        "- Accept as-is: merge the PR / close this issue yourself.",
        "- Retry: remove and re-add the `devin-remediate` label to re-run "
        "the pipeline from scratch on this issue.",
        "- Drop: remove the label and close; the pipeline will not touch it again.",
        "",
        "_The pipeline stops here on purpose: it does not guess on your behalf._",
    ])
