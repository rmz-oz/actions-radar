#!/usr/bin/env python3
"""actions-radar: keep an eye on GitHub Actions runs across many repositories.

Two jobs:

  watch    Block until the workflow run for one commit finishes, then print the
           failing step's log tail. Meant to be the last line of a deploy script.
  check    Sweep every configured repository for runs that failed since the last
           sweep and report them once. Meant to run from cron or launchd.

Only the standard library is used. The token comes from GITHUB_TOKEN, or from
`gh auth token` when the GitHub CLI is signed in.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s")
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "actions-radar"
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_PATH = CONFIG_DIR / "state.json"

TERMINAL_STATUS = {"completed"}
BAD_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "action_required"}


class RadarError(Exception):
    pass


# --------------------------------------------------------------------------- auth


def resolve_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    if shutil.which("gh"):
        try:
            out = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=15
            )
        except subprocess.TimeoutExpired:
            raise RadarError("`gh auth token` timed out")
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    raise RadarError(
        "No token found. Set GITHUB_TOKEN, or sign in with `gh auth login`."
    )


# --------------------------------------------------------------------------- http


def request(token: str, path: str, *, raw: bool = False):
    """GET an API path. Returns parsed JSON, or raw bytes when raw=True."""
    url = path if path.startswith("http") else f"{API}{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "actions-radar")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            reset = exc.headers.get("X-RateLimit-Reset", "")
            raise RadarError(f"Rate limited. Resets at {fmt_epoch(reset)}.")
        if exc.code == 404:
            raise RadarError(f"Not found: {url}")
        raise RadarError(f"HTTP {exc.code} for {url}: {exc.read()[:200].decode(errors='replace')}")
    except urllib.error.URLError as exc:
        raise RadarError(f"Network error for {url}: {exc.reason}")
    return body if raw else json.loads(body or b"{}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib from replaying our Authorization header on the storage host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_redirected(token: str, path: str) -> bytes:
    """Follow one API redirect by hand, without carrying credentials across.

    Log downloads answer with a 302 to signed blob storage, and that host
    rejects the request outright if the GitHub token rides along.
    """
    req = urllib.request.Request(f"{API}{path}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "actions-radar")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise RadarError(f"HTTP {exc.code} while fetching {path}")
        location = exc.headers.get("Location")
        if not location:
            raise RadarError(f"Redirect without a location for {path}")
    plain = urllib.request.Request(location)
    plain.add_header("User-Agent", "actions-radar")
    try:
        with urllib.request.urlopen(plain, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RadarError(f"HTTP {exc.code} while downloading the log")
    except urllib.error.URLError as exc:
        raise RadarError(f"Network error while downloading the log: {exc.reason}")


def fmt_epoch(value: str) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return "soon"


# --------------------------------------------------------------------------- config


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"repos": []}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- logs


def failing_jobs(token: str, repo: str, run_id: int) -> list[dict]:
    data = request(token, f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    return [j for j in data.get("jobs", []) if j.get("conclusion") in BAD_CONCLUSIONS]


def job_log_excerpt(token: str, repo: str, job_id: int, lines: int) -> str:
    """Return the part of a job log that explains the failure."""
    try:
        blob = fetch_redirected(token, f"/repos/{repo}/actions/jobs/{job_id}/logs")
    except RadarError:
        return ""  # logs expire after 90 days, and an old run is not worth a crash
    return summarize_log(decode_log(blob), lines)


def summarize_log(text: str, lines: int) -> str:
    """Pick the interesting window out of a job log.

    The log ends with the runner's own cleanup, so a plain tail shows housekeeping
    rather than the error. Runners mark real failures with ##[error], so anchor on
    the first one and keep the commands that led up to it.
    """
    clean = [strip_timestamp(ln) for ln in text.splitlines() if ln.strip()]
    if not clean:
        return ""
    anchors = [i for i, ln in enumerate(clean) if "##[error]" in ln]
    if not anchors:
        return "\n".join(clean[-lines:])
    first, last = anchors[0], anchors[-1]
    start = max(0, first - max(lines - (last - first) - 1, 0))
    return "\n".join(clean[start : last + 1])


def strip_timestamp(line: str) -> str:
    """Drop the ISO timestamp the runner prefixes to every line."""
    return TIMESTAMP.sub("", line, count=1)


def decode_log(blob: bytes) -> str:
    """Job logs arrive as plain text, but archives show up for some runs."""
    if blob[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                names = [n for n in zf.namelist() if n.endswith(".txt")]
                if not names:
                    return ""
                return zf.read(names[-1]).decode("utf-8", errors="replace")
        except zipfile.BadZipFile:
            return ""
    return blob.decode("utf-8", errors="replace")


def report_failure(token: str, repo: str, run: dict, log_lines: int) -> None:
    print(f"\n  {run.get('display_title') or run.get('name')}")
    print(f"  {run.get('html_url')}")
    for job in failing_jobs(token, repo, run["id"]):
        steps = [
            s for s in job.get("steps", []) if s.get("conclusion") in BAD_CONCLUSIONS
        ]
        where = steps[0]["name"] if steps else "unknown step"
        print(f"  job '{job['name']}' failed at step '{where}'")
        if log_lines <= 0:
            continue
        tail = job_log_excerpt(token, repo, job["id"], log_lines)
        if tail:
            print("  " + "-" * 60)
            for line in tail.splitlines():
                print(f"  | {line}")
            print("  " + "-" * 60)


# --------------------------------------------------------------------------- notify


def notify(title: str, message: str) -> None:
    """Best effort macOS notification. Never raises, never blocks."""
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    script = (
        f'display notification {applescript_string(message)} '
        f'with title {applescript_string(title)}'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        pass


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- watch


def current_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    if out.returncode != 0:
        raise RadarError("Not a git repository, so --sha is required.")
    return out.stdout.strip()


def current_repo() -> str:
    out = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True
    )
    if out.returncode != 0:
        raise RadarError("No origin remote, so the repository must be given.")
    url = out.stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git@"):
        url = url.split(":", 1)[-1]
    else:
        url = "/".join(url.split("/")[-2:])
    return url


def cmd_watch(args: argparse.Namespace) -> int:
    token = resolve_token()
    repo = args.repo or current_repo()
    sha = args.sha or current_sha()
    deadline = time.monotonic() + args.timeout

    print(f"actions-radar: watching {repo} @ {sha[:7]}")
    run = None
    while run is None:
        runs = request(token, f"/repos/{repo}/actions/runs?head_sha={sha}&per_page=10")
        candidates = runs.get("workflow_runs", [])
        if args.workflow:
            candidates = [r for r in candidates if r.get("name") == args.workflow]
        if candidates:
            run = sorted(candidates, key=lambda r: r["created_at"])[-1]
            break
        if time.monotonic() > deadline:
            print("  no run was queued for this commit before the timeout")
            print("  (a push to a branch the workflow ignores does not start one)")
            return 2
        time.sleep(args.interval)

    last = None
    while run.get("status") not in TERMINAL_STATUS:
        shown = run.get("status", "?")
        if shown != last:
            print(f"  {shown} ...")
            last = shown
        if time.monotonic() > deadline:
            print(f"  still {run.get('status')} after {args.timeout}s, giving up on waiting")
            print(f"  {run.get('html_url')}")
            return 3
        time.sleep(args.interval)
        run = request(token, f"/repos/{repo}/actions/runs/{run['id']}")

    conclusion = run.get("conclusion")
    if conclusion == "success":
        print(f"  success: {run.get('html_url')}")
        return 0

    print(f"  {conclusion}:")
    report_failure(token, repo, run, args.log_lines)
    notify(f"{repo} deploy {conclusion}", run.get("display_title") or sha[:7])
    return 1


# --------------------------------------------------------------------------- check


def cmd_check(args: argparse.Namespace) -> int:
    token = resolve_token()
    cfg = load_config()
    repos = args.repo or cfg.get("repos") or []
    if not repos:
        raise RadarError("No repositories configured. Run `radar.py discover` first.")

    state = load_state()
    found: list[tuple[str, dict]] = []

    for repo in repos:
        try:
            data = request(
                token,
                f"/repos/{repo}/actions/runs?per_page={args.per_repo}&status=completed",
            )
        except RadarError as exc:
            print(f"! {repo}: {exc}", file=sys.stderr)
            continue
        seen = state.get(repo, {}).get("last_run_id", 0)
        newest = seen
        for run in data.get("workflow_runs", []):
            if run["id"] <= seen:
                continue
            newest = max(newest, run["id"])
            if run.get("conclusion") in BAD_CONCLUSIONS:
                found.append((repo, run))
        state.setdefault(repo, {})["last_run_id"] = newest
        state[repo]["checked_at"] = datetime.now(timezone.utc).isoformat()

    if not args.dry_run:
        save_state(state)

    if not found:
        print(f"actions-radar: {len(repos)} repositories clean")
        return 0

    print(f"actions-radar: {len(found)} failed run(s)")
    for repo, run in found:
        print(f"\n{repo}")
        report_failure(token, repo, run, args.log_lines)

    summary = ", ".join(sorted({repo for repo, _ in found})[:3])
    notify(f"{len(found)} failed GitHub Actions run(s)", summary)
    return 1


# --------------------------------------------------------------------------- discover


def cmd_discover(args: argparse.Namespace) -> int:
    token = resolve_token()
    print("actions-radar: looking for repositories that run workflows")
    repos: list[str] = []
    page = 1
    scanned = 0
    while True:
        batch = request(
            token, f"/user/repos?per_page=100&page={page}&affiliation=owner&sort=pushed"
        )
        if not batch:
            break
        for item in batch:
            if item.get("archived") or item.get("fork"):
                continue
            scanned += 1
            name = item["full_name"]
            try:
                runs = request(token, f"/repos/{name}/actions/runs?per_page=1")
            except RadarError:
                continue
            if runs.get("total_count", 0) > 0:
                repos.append(name)
                print(f"  + {name}")
        if len(batch) < 100:
            break
        page += 1

    cfg = load_config()
    cfg["repos"] = repos
    save_config(cfg)
    print(f"\nscanned {scanned} repositories, {len(repos)} use Actions")
    print(f"written to {CONFIG_PATH}")
    return 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar.py", description="Watch GitHub Actions runs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watch", help="wait for the run of one commit to finish")
    w.add_argument("repo", nargs="?", help="owner/name, defaults to the origin remote")
    w.add_argument("--sha", help="commit to watch, defaults to HEAD")
    w.add_argument("--workflow", help="only watch the run with this workflow name")
    w.add_argument("--timeout", type=int, default=900, help="seconds to wait (900)")
    w.add_argument("--interval", type=int, default=10, help="poll seconds (10)")
    w.add_argument("--log-lines", type=int, default=30, help="log tail length (30)")
    w.set_defaults(func=cmd_watch)

    c = sub.add_parser("check", help="report runs that failed since the last check")
    c.add_argument("repo", nargs="*", help="repositories, defaults to the config file")
    c.add_argument("--per-repo", type=int, default=10, help="runs to inspect (10)")
    c.add_argument("--log-lines", type=int, default=0, help="log tail length (0)")
    c.add_argument("--dry-run", action="store_true", help="do not advance the state")
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("discover", help="write a config of repositories using Actions")
    d.set_defaults(func=cmd_discover)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RadarError as exc:
        print(f"actions-radar: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
