"""Configuration - all knobs in one place, loaded from environment / .env.

The orchestrator is deliberately dependency-free (stdlib only), so .env
parsing lives here instead of pulling python-dotenv.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# --- credentials -----------------------------------------------------------
DEVIN_API_KEY = os.environ.get("DEVIN_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# --- targets ---------------------------------------------------------------
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "SeoKaEun")
FORK_REPO = os.environ.get("FORK_REPO", "devin_takehome_assignment")
FORK_FULL = f"{GITHUB_OWNER}/{FORK_REPO}"
FORK_DEFAULT_BRANCH = os.environ.get("FORK_DEFAULT_BRANCH", "master")

# --- API bases -------------------------------------------------------------
DEVIN_API_BASE = os.environ.get("DEVIN_API_BASE", "https://api.devin.ai/v1").rstrip("/")
GITHUB_API_BASE = "https://api.github.com"

# --- behavior knobs --------------------------------------------------------
TRIGGER_LABEL = os.environ.get("TRIGGER_LABEL", "devin-remediate")
POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "30"))
SESSION_TIMEOUT_MIN = int(os.environ.get("SESSION_TIMEOUT_MIN", "90"))
MAX_ACU_PER_SESSION = int(os.environ.get("MAX_ACU_PER_SESSION", "10"))
MAX_CONCURRENT_SESSIONS = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "2"))
# REVIEW_MODE: "all" -> every fixed PR gets a Devin review session,
#              "off" -> Gate 1 only (saves ACUs)
REVIEW_MODE = os.environ.get("REVIEW_MODE", "all")
# minutes between automatic dependency scans in `run` mode; 0 disables
SCAN_INTERVAL_MIN = int(os.environ.get("SCAN_INTERVAL_MIN", "30"))
# lockfiles the scanner watches (comma-separated, repo-relative)
SCAN_MANIFESTS = [m.strip() for m in os.environ.get(
    "SCAN_MANIFESTS", "requirements/base.txt").split(",") if m.strip()]
# optional burst control: max new issues filed per scan pass (0 = no cap,
# every finding is filed). Spend is bounded downstream regardless:
# MAX_CONCURRENT_SESSIONS and MAX_ACU_PER_SESSION decide what actually runs.
SCAN_MAX_NEW = int(os.environ.get("SCAN_MAX_NEW", "0"))

# --- code audit (judgment-based detector, runs on demand or scheduled) -----
AUDIT_SCOPE = os.environ.get(
    "AUDIT_SCOPE", "superset/utils/ - Python code-level defects")
AUDIT_MAX_NEW = int(os.environ.get("AUDIT_MAX_NEW", "0"))  # 0 = file every finding

# --- autonomy mode ---------------------------------------------------------
# Teams differ in risk appetite. One knob sets how much the pipeline decides
# on its own vs. how often it stops for a human:
#   supervised - human confirms everything unusual; no autonomous rework
#   balanced   - autonomous rework + auto-accept low-risk scope growth (default)
#   autopilot  - adds auto-merge of review-approved PRs
AUTONOMY_MODE = os.environ.get("AUTONOMY_MODE", "balanced")

_POLICIES = {
    "supervised": {"max_reworks": 0, "auto_accept_scope": (), "auto_merge": False},
    "balanced":   {"max_reworks": 1, "auto_accept_scope": ("docs/", "tests/"),
                   "auto_merge": False},
    "autopilot":  {"max_reworks": 2, "auto_accept_scope": ("docs/", "tests/"),
                   "auto_merge": True},
}
POLICY = _POLICIES.get(AUTONOMY_MODE, _POLICIES["balanced"])

# --- paths -----------------------------------------------------------------
STATE_PATH = Path(os.environ.get("STATE_PATH", str(ROOT / "state" / "state.json")))
DASHBOARD_PATH = Path(os.environ.get("DASHBOARD_PATH", str(ROOT / "state" / "dashboard.html")))
LOG_PATH = Path(os.environ.get("LOG_PATH", str(ROOT / "state" / "orchestrator.log")))


def require(*names):
    """Fail fast with a clear message when a credential is missing."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            f"missing required config: {', '.join(missing)} "
            f"(set in {ROOT / '.env'} or environment)"
        )
