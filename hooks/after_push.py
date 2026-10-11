"""PostToolUse hook: after a git push, wait for the runs of that commit.

It runs in the background (asyncRewake), so Claude keeps working. Exit 2 wakes
Claude with stderr; anything else stays quiet.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RADAR = Path(__file__).resolve().parent.parent / "radar.py"
PUSH = re.compile(r"\bgit\b(\s+-C\s+\S+)?\s+push\b")


def repo_dir(command, cwd):
    """Where the push ran: `git -C dir push`, `cd dir && git push`, or the session cwd."""
    m = re.search(r"\bgit\s+-C\s+(\S+)\s+push\b", command)
    if m:
        return os.path.join(cwd, os.path.expanduser(shlex.split(m.group(1))[0]))
    m = re.match(r"\s*cd\s+(\S+)\s*(&&|;)", command)
    if m:
        return os.path.join(cwd, os.path.expanduser(shlex.split(m.group(1))[0]))
    return cwd


def summary(result):
    lines = ["CI failed after the push (%s @ %s):" % (result["repo"], result["sha"][:7])]
    for run in result["failed"]:
        lines.append("")
        lines.append("%s  %s" % (run["title"], run["url"]))
        for job in run["jobs"]:
            lines.append("job '%s' failed at step '%s'" % (job["job"], job["step"]))
            lines += ["  why: " + w for w in job["why"]]
            if not job["why"] and job["log"]:
                lines += ["  | " + ln for ln in job["log"].splitlines()[-15:]]
    lines.append("")
    lines.append("Run `actions-radar watch --json` again after the fix is pushed.")
    return "\n".join(lines)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    if not PUSH.search(command) or "--dry-run" in command:
        return 0
    where = repo_dir(command, data.get("cwd") or os.getcwd())
    try:
        out = subprocess.run(
            [sys.executable, str(RADAR), "watch", "--json", "--timeout", "1800", "--log-lines", "15"],
            cwd=where, capture_output=True, text=True, timeout=2000,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if out.returncode != 1:
        return 0  # passed, no workflow, or still running after half an hour
    try:
        result = json.loads(out.stdout)
    except ValueError:
        return 0
    print(summary(result), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
