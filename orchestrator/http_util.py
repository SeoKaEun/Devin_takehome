"""Tiny stdlib HTTP helper shared by both API clients.

One retry layer, one error type, JSON in/out. Nothing clever.
"""

import json
import time
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, method, url, status, body):
        self.method, self.url, self.status, self.body = method, url, status, body
        super().__init__(f"{method} {url} -> HTTP {status}: {str(body)[:300]}")


def request_json(method, url, headers=None, body=None, timeout=60, retries=2):
    """JSON request with small backoff on 429/5xx. Raises ApiError otherwise."""
    payload = json.dumps(body).encode() if body is not None else None
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, method=method, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "devin-remediation-orchestrator/0.1",
            **(headers or {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
            last_err = ApiError(method, url, e.code, parsed)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 2)
                continue
            raise last_err from e
        except urllib.error.URLError as e:
            last_err = ApiError(method, url, 0, str(e.reason))
            if attempt < retries:
                time.sleep(2 ** attempt * 2)
                continue
            raise last_err from e
    raise last_err
