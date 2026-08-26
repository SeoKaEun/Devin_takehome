"""CLI entry point.

  python -m orchestrator healthcheck   # verify both credentials, no side effects
  python -m orchestrator once          # single tick (cron-friendly)
  python -m orchestrator run           # continuous loop (the daemon mode)
  python -m orchestrator status        # human-readable state summary
  python -m orchestrator dashboard     # regenerate the HTML dashboard

  SIMULATE=1 python -m orchestrator run   # full offline demo, no credentials
"""

import argparse
import sys
import time

from . import config, dashboard, pipeline, scanner
from . import state as st
from .clients import make_clients, is_simulation
from .http_util import request_json


def log(msg):
    line = f"{st.utcnow()} {msg}"
    print(line, flush=True)
    try:
        config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def cmd_healthcheck():
    ok = True
    if is_simulation():
        print("mode: SIMULATION - credentials not required")
        return 0
    # GitHub
    try:
        u = request_json("GET", f"{config.GITHUB_API_BASE}/user", headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"})
        print(f"[ok] github: authenticated as {u['login']}, fork={config.FORK_FULL}")
    except Exception as e:
        print(f"[FAIL] github: {e}")
        ok = False
    # Devin
    from .devin_client import healthcheck as devin_hc
    good, detail = devin_hc()
    print(f"[{'ok' if good else 'FAIL'}] devin: {detail}")
    ok = ok and good
    return 0 if ok else 1


def cmd_once():
    gh, devin = make_clients()
    state = st.load()
    pipeline.tick(gh, devin, state, log=log)
    path = dashboard.write(state)
    log(f"tick done; dashboard -> {path}")
    return 0


def cmd_run():
    gh, devin = make_clients()
    interval = 2 if is_simulation() else config.POLL_INTERVAL_SEC
    log(f"orchestrator up (mode={'sim' if is_simulation() else 'live'}, "
        f"interval={interval}s, fork={config.FORK_FULL}, "
        f"scan every {config.SCAN_INTERVAL_MIN} min)")
    last_scan = 0.0
    last_scan_result = None
    while True:
        # automated event source: periodic dependency scan files new issues,
        # which the very next tick picks up as events (live mode only)
        if (not is_simulation() and config.SCAN_INTERVAL_MIN > 0
                and time.time() - last_scan >= config.SCAN_INTERVAL_MIN * 60):
            try:
                scan_filed = scanner.scan_once(log=log)
            except Exception as exc:
                scan_filed = None
                log(f"[scan] error (non-fatal, retrying next cycle): {exc}")
            last_scan = time.time()
            last_scan_result = (st.utcnow(), scan_filed)
        state = st.load()
        if last_scan_result:
            state["meta"]["last_scan_at"], state["meta"]["last_scan_filed"] = \
                last_scan_result
        pipeline.tick(gh, devin, state, log=log)
        dashboard.write(state)
        if _all_terminal(state) and state["issues"]:
            log("all tracked issues reached a terminal state; exiting cleanly")
            _print_summary(state)
            return 0
        time.sleep(interval)


def _all_terminal(state):
    return all(e["state"] in st.TERMINAL_STATES for e in state["issues"].values())


def cmd_status():
    state = st.load()
    _print_summary(state)
    return 0


def _print_summary(state):
    if not state["issues"]:
        print("no issues tracked yet")
        return
    width = max(len(e["title"]) for e in state["issues"].values())
    for e in sorted(state["issues"].values(), key=lambda x: x["number"]):
        pr = f"  PR: {e['pr_url']}" if e.get("pr_url") else ""
        attn = f"  [!] {e['attention_reason']}" if e.get("attention_reason") else ""
        print(f"#{e['number']}  {e['title']:<{width}}  {e['state']}{pr}{attn}")


def cmd_dashboard():
    path = dashboard.write(st.load())
    print(f"dashboard -> {path}")
    return 0


def cmd_scan():
    config.require("GITHUB_TOKEN")
    filed = scanner.scan_once(log=print)
    print(f"scan complete: {filed} new issue(s) filed")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="orchestrator")
    ap.add_argument("command", choices=["healthcheck", "once", "run", "status",
                                        "dashboard", "scan"])
    args = ap.parse_args()
    return {
        "healthcheck": cmd_healthcheck,
        "once": cmd_once,
        "run": cmd_run,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "scan": cmd_scan,
    }[args.command]()


if __name__ == "__main__":
    sys.exit(main())
