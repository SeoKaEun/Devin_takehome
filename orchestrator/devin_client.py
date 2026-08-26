"""Devin API client.

This module is the ONLY place in the system that talks to the Devin API.
Every session the orchestrator creates carries:
  - tags        -> traceability (session list doubles as an audit log)
  - max_acu_limit -> cost ceiling per task
  - idempotent  -> a retried dispatch can never double-fire a session
  - structured_output_schema -> results come back as machine-checkable JSON
"""

from . import config
from .http_util import request_json, ApiError


def _headers():
    return {"Authorization": f"Bearer {config.DEVIN_API_KEY}"}


def healthcheck():
    """Verify credentials without creating a session.

    Returns (ok, detail). Lists sessions - a read-only call.
    """
    if not config.DEVIN_API_KEY:
        return False, "DEVIN_API_KEY is not set"
    try:
        request_json("GET", f"{config.DEVIN_API_BASE}/sessions?limit=1", headers=_headers())
        return True, f"authenticated against {config.DEVIN_API_BASE}"
    except ApiError as e:
        return False, f"HTTP {e.status}: {str(e.body)[:200]}"


def create_session(prompt, title, tags, structured_output_schema=None,
                   max_acu_limit=None, idempotent=True):
    body = {
        "prompt": prompt,
        "title": title,
        "tags": tags,
        "idempotent": idempotent,
        "max_acu_limit": max_acu_limit or config.MAX_ACU_PER_SESSION,
    }
    if structured_output_schema is not None:
        body["structured_output_schema"] = structured_output_schema
    return request_json("POST", f"{config.DEVIN_API_BASE}/sessions",
                        headers=_headers(), body=body)


def get_session(session_id):
    """Fetch session detail. Tries both documented path shapes (v1 has
    appeared as /session/{id} and /sessions/{id} in different doc versions)."""
    try:
        return request_json("GET", f"{config.DEVIN_API_BASE}/session/{session_id}",
                            headers=_headers())
    except ApiError as e:
        if e.status != 404:
            raise
        return request_json("GET", f"{config.DEVIN_API_BASE}/sessions/{session_id}",
                            headers=_headers())


def send_message(session_id, message):
    return request_json("POST", f"{config.DEVIN_API_BASE}/session/{session_id}/message",
                        headers=_headers(), body={"message": message})


# Terminal / non-terminal session states, per API docs.
TERMINAL_STATES = {"finished", "expired"}
BLOCKED_STATES = {"blocked"}
