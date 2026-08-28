"""Publish the solution to the public GitHub repo via the Contents API.

Exists because this workstation has no git binary; also serves as the
reviewer-visible proof that the whole project is reproducible over plain
REST. Idempotent: files whose remote content already matches are skipped,
changed files are updated in place.

Usage: python scripts/publish_solution.py [--repo Devin_takehome]
"""

import argparse
import base64
import os
import sys
import urllib.error
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

INCLUDE = ["README.md", "LICENSE", "Dockerfile", "docker-compose.yml",
           ".env.example", ".gitignore"]
INCLUDE_DIRS = ["orchestrator", "scripts", "tests"]
EXCLUDE_NAMES = {"__pycache__", ".env", "state"}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def gh(method, path, token, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "publish-solution"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}"), None
    except urllib.error.HTTPError as e:
        return None, (e.code, e.read().decode(errors="replace")[:200])


def collect_files():
    files = [ROOT / f for f in INCLUDE if (ROOT / f).exists()]
    for d in INCLUDE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not any(x in EXCLUDE_NAMES for x in p.parts):
                files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Devin_takehome")
    args = ap.parse_args()

    load_env()
    token = os.environ["GITHUB_TOKEN"]
    owner = os.environ.get("GITHUB_OWNER", "SeoKaEun")
    full = f"{owner}/{args.repo}"

    for path in collect_files():
        rel = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        b64 = base64.b64encode(content).decode()

        existing, err = gh("GET", f"/repos/{full}/contents/{rel}", token)
        body = {"message": f"pipeline: publish {rel}", "content": b64}
        if existing and isinstance(existing, dict) and existing.get("sha"):
            remote = (existing.get("content") or "").replace("\n", "")
            if remote == b64:
                print(f"[=] {rel} (unchanged)")
                continue
            body["sha"] = existing["sha"]

        _, err = gh("PUT", f"/repos/{full}/contents/{rel}", token, body)
        if err:
            print(f"[!] {rel} FAILED: {err}")
            return 1
        print(f"[+] {rel}")

    print(f"\npublished to https://github.com/{full}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
