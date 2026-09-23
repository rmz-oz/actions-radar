#!/usr/bin/env python3
"""Watch GitHub Actions runs.

watch     wait for the run of one commit and print why it failed
check     report runs that failed since the last check
discover  list the repositories that use Actions

Standard library only. The token comes from GITHUB_TOKEN, or from `gh auth token`.
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
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

BAD = {"failure", "timed_out", "startup_failure", "action_required"}


class RadarError(Exception):
    pass


def resolve_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise RadarError("`gh auth token` timed out")
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    raise RadarError("No token. Set GITHUB_TOKEN or run `gh auth login`.")


def request(token, path):
    url = path if path.startswith("http") else API + path
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "actions-radar")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise RadarError("Rate limited, resets at " + fmt_epoch(exc.headers.get("X-RateLimit-Reset")))
        if exc.code == 404:
            raise RadarError("Not found: " + url)
        raise RadarError("HTTP %s for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise RadarError("Network error for %s: %s" % (url, exc.reason))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_log(token, path):
    """Logs answer with a 302 to blob storage, and that host rejects our token."""
    req = urllib.request.Request(API + path)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("User-Agent", "actions-radar")
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise RadarError("HTTP %s while fetching the log" % exc.code)
        location = exc.headers.get("Location")
        if not location:
            raise RadarError("Redirect without a location")

    plain = urllib.request.Request(location)
    plain.add_header("User-Agent", "actions-radar")
    try:
        with urllib.request.urlopen(plain, timeout=60) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RadarError("Could not download the log: %s" % exc)


def fmt_epoch(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return "soon"


def load_config():
    if not CONFIG_PATH.exists():
        return {"repos": []}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state():
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def write_json(path, data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def failing_jobs(token, repo, run_id):
    data = request(token, "/repos/%s/actions/runs/%s/jobs?per_page=100" % (repo, run_id))
    return [j for j in data.get("jobs", []) if j.get("conclusion") in BAD]


def log_excerpt(token, repo, job_id, lines):
    try:
        blob = fetch_log(token, "/repos/%s/actions/jobs/%s/logs" % (repo, job_id))
    except RadarError:
        return ""  # logs expire after 90 days
    if blob[:2] == b"PK":
        blob = unzip(blob)
    return pick_error(blob.decode("utf-8", errors="replace"), lines)


def unzip(blob):
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".txt")]
            return zf.read(names[-1]) if names else b""
    except zipfile.BadZipFile:
        return b""


def pick_error(text, lines):
    """A plain tail shows the runner cleaning up, so anchor on the ##[error] marks."""
    clean = [TIMESTAMP.sub("", ln, count=1) for ln in text.splitlines() if ln.strip()]
    if not clean:
        return ""
    marks = [i for i, ln in enumerate(clean) if "##[error]" in ln]
    if not marks:
        return "\n".join(clean[-lines:])
    first, last = marks[0], marks[-1]
    start = max(0, first - max(lines - (last - first) - 1, 0))
    return "\n".join(clean[start:last + 1])


def report(token, repo, run, lines):
    print("\n  " + (run.get("display_title") or run.get("name") or "?"))
    print("  " + run.get("html_url", ""))
    for job in failing_jobs(token, repo, run["id"]):
        steps = [s for s in job.get("steps", []) if s.get("conclusion") in BAD]
        where = steps[0]["name"] if steps else "unknown step"
        print("  job '%s' failed at step '%s'" % (job["name"], where))
        if lines <= 0:
            continue
        tail = log_excerpt(token, repo, job["id"], lines)
        if tail:
            print("  " + "-" * 60)
            for line in tail.splitlines():
                print("  | " + line)
            print("  " + "-" * 60)


def notify(title, message):
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    script = "display notification %s with title %s" % (quote(message), quote(title))
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        pass


def quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def git(*args):
    out = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def current_repo():
    url = git("remote", "get-url", "origin")
    if not url:
        raise RadarError("No origin remote, so name the repository.")
    if url.endswith(".git"):
        url = url[:-4]
    return url.split(":", 1)[-1] if url.startswith("git@") else "/".join(url.split("/")[-2:])


def cmd_watch(args):
    token = resolve_token()
    repo = args.repo or current_repo()
    sha = args.sha or git("rev-parse", "HEAD")
    if not sha:
        raise RadarError("Not a git repository, so --sha is required.")
    deadline = time.monotonic() + args.timeout

    print("actions-radar: watching %s @ %s" % (repo, sha[:7]))
    run = None
    while run is None:
        runs = request(token, "/repos/%s/actions/runs?head_sha=%s&per_page=10" % (repo, sha))
        found = runs.get("workflow_runs", [])
        if args.workflow:
            found = [r for r in found if r.get("name") == args.workflow]
        if found:
            run = sorted(found, key=lambda r: r["created_at"])[-1]
        elif time.monotonic() > deadline:
            print("  no run was queued for this commit")
            return 2
        else:
            time.sleep(args.interval)

    last = None
    while run.get("status") != "completed":
        if run.get("status") != last:
            last = run.get("status")
            print("  %s ..." % last)
        if time.monotonic() > deadline:
            print("  still %s after %ss: %s" % (last, args.timeout, run.get("html_url")))
            return 3
        time.sleep(args.interval)
        run = request(token, "/repos/%s/actions/runs/%s" % (repo, run["id"]))

    if run.get("conclusion") == "success":
        print("  success: " + run.get("html_url", ""))
        return 0

    print("  %s:" % run.get("conclusion"))
    report(token, repo, run, args.log_lines)
    notify("%s deploy %s" % (repo, run.get("conclusion")), run.get("display_title") or sha[:7])
    return 1


def cmd_check(args):
    token = resolve_token()
    repos = args.repo or load_config().get("repos") or []
    if not repos:
        raise RadarError("No repositories configured. Run `radar.py discover` first.")

    state = load_state()
    found = []
    for repo in repos:
        try:
            data = request(token, "/repos/%s/actions/runs?per_page=%s&status=completed" % (repo, args.per_repo))
        except RadarError as exc:
            print("! %s: %s" % (repo, exc), file=sys.stderr)
            continue
        seen = state.get(repo, {}).get("last_run_id", 0)
        newest = seen
        for run in data.get("workflow_runs", []):
            if run["id"] <= seen:
                continue
            newest = max(newest, run["id"])
            if run.get("conclusion") in BAD:
                found.append((repo, run))
        state[repo] = {"last_run_id": newest, "checked_at": datetime.now(timezone.utc).isoformat()}

    if not args.dry_run:
        write_json(STATE_PATH, state)

    if not found:
        print("actions-radar: %s repositories clean" % len(repos))
        return 0

    print("actions-radar: %s failed run(s)" % len(found))
    for repo, run in found:
        print("\n" + repo)
        report(token, repo, run, args.log_lines)
    notify("%s failed GitHub Actions run(s)" % len(found), ", ".join(sorted({r for r, _ in found})[:3]))
    return 1


def cmd_discover(args):
    token = resolve_token()
    print("actions-radar: looking for repositories that run workflows")
    repos, scanned, page = [], 0, 1
    while True:
        batch = request(token, "/user/repos?per_page=100&page=%s&affiliation=owner&sort=pushed" % page)
        if not batch:
            break
        for item in batch:
            if item.get("archived") or item.get("fork"):
                continue
            scanned += 1
            try:
                runs = request(token, "/repos/%s/actions/runs?per_page=1" % item["full_name"])
            except RadarError:
                continue
            if runs.get("total_count", 0) > 0:
                repos.append(item["full_name"])
                print("  + " + item["full_name"])
        if len(batch) < 100:
            break
        page += 1

    cfg = load_config()
    cfg["repos"] = repos
    write_json(CONFIG_PATH, cfg)
    print("\nscanned %s repositories, %s use Actions" % (scanned, len(repos)))
    print("written to %s" % CONFIG_PATH)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="radar.py", description="Watch GitHub Actions runs.")
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watch", help="wait for the run of one commit to finish")
    w.add_argument("repo", nargs="?", help="owner/name, defaults to the origin remote")
    w.add_argument("--sha", help="commit to watch, defaults to HEAD")
    w.add_argument("--workflow", help="only watch the run with this workflow name")
    w.add_argument("--timeout", type=int, default=900, help="seconds to wait (900)")
    w.add_argument("--interval", type=int, default=10, help="poll seconds (10)")
    w.add_argument("--log-lines", type=int, default=30, help="log excerpt length (30)")
    w.set_defaults(func=cmd_watch)

    c = sub.add_parser("check", help="report runs that failed since the last check")
    c.add_argument("repo", nargs="*", help="repositories, defaults to the config file")
    c.add_argument("--per-repo", type=int, default=10, help="runs to inspect (10)")
    c.add_argument("--log-lines", type=int, default=0, help="log excerpt length (0)")
    c.add_argument("--dry-run", action="store_true", help="do not advance the state")
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("discover", help="write a config of repositories using Actions")
    d.set_defaults(func=cmd_discover)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except RadarError as exc:
        print("actions-radar: %s" % exc, file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
