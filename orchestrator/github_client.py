"""GitHub REST client - issues in, comments/PR-verification out."""

import re

from . import config
from .http_util import request_json


def _headers():
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _url(path):
    return f"{config.GITHUB_API_BASE}{path}"


def list_open_labeled_issues():
    """Open issues on the fork carrying the trigger label (PRs excluded)."""
    items = request_json(
        "GET",
        _url(f"/repos/{config.FORK_FULL}/issues"
             f"?state=open&labels={config.TRIGGER_LABEL}&per_page=100"),
        headers=_headers(),
    )
    return [i for i in items if "pull_request" not in i]


def comment_on_issue(number, body):
    return request_json("POST", _url(f"/repos/{config.FORK_FULL}/issues/{number}/comments"),
                        headers=_headers(), body={"body": body})


PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def get_pull_request(pr_url):
    """Resolve a PR URL to API data, or None if it does not exist.

    Gate 1 uses this to verify Devin's claimed PR actually exists.
    """
    m = PR_URL_RE.search(pr_url or "")
    if not m:
        return None
    owner, repo, num = m.groups()
    try:
        return request_json("GET", _url(f"/repos/{owner}/{repo}/pulls/{num}"),
                            headers=_headers())
    except Exception:
        return None


def get_pull_request_files(pr_url):
    m = PR_URL_RE.search(pr_url or "")
    if not m:
        return []
    owner, repo, num = m.groups()
    return request_json("GET", _url(f"/repos/{owner}/{repo}/pulls/{num}/files?per_page=100"),
                        headers=_headers())


def merge_pull_request(pr_url):
    """Merge a PR (autopilot mode only). Returns None on failure."""
    m = PR_URL_RE.search(pr_url or "")
    if not m:
        return None
    owner, repo, num = m.groups()
    try:
        return request_json("PUT", _url(f"/repos/{owner}/{repo}/pulls/{num}/merge"),
                            headers=_headers(), body={"merge_method": "squash"})
    except Exception:
        return None
