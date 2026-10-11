# actions-radar

[![test](https://github.com/rmz-oz/actions-radar/actions/workflows/test.yml/badge.svg)](https://github.com/rmz-oz/actions-radar/actions/workflows/test.yml)

Tells you when a GitHub Actions run fails, and which line of the log explains it.

![actions-radar watching two commits](docs/demo.gif)

My notes site failed to deploy on every push for five days, 14 runs in a row,
before I noticed. The publish script pushed, printed a link to the Actions tab
and exited. The deploy step called `npx wrangler` without `--yes`, and in CI
npx refuses to install a package it would have to ask about.

## watch

Waits for every workflow run of one commit. If one fails, it prints the step,
the lines that say why, and the part of the log around them. Exits 1 on
failure, so it can sit at the end of a deploy script.

```
$ actions-radar watch
actions-radar: watching pallets/flask @ 329c32b
  failure:

  fix dev
  https://github.com/pallets/flask/actions/runs/37633292268
  job 'typing' failed at step 'Run uv run --locked --no-default-groups --group dev tox run -e typing'
    why: src/flask/app.py:206: error: Incompatible types in assignment (expression has
    why: Found 1 error in 1 file (checked 27 source files)
  ------------------------------------------------------------
  | typing: commands[0]> mypy
  | src/flask/app.py:206: error: Incompatible types in assignment (expression has
  | type "Mapping[str, Any]", base class "App" defined the type as "dict[str, Any]")
  ------------------------------------------------------------
```

The runner's own last line is almost always `Process completed with exit code 1`,
and most tools print the real reason well above it. The `why` lines come from
patterns taken from real failing logs: pytest, mypy, jest, vitest, cargo, go,
gradle, swift, prettier, eslint and docker builds.

## check

Goes through the repositories in your config and reports runs that failed since
the last check. It remembers where it stopped, so you hear about each failure
once. Run it from cron or launchd.

```
$ actions-radar check
actions-radar: 2 failed run(s)
```

On macOS a failure also raises a notification. A scheduled sweep has nowhere to
print, so `--report sweep.md` leaves a Markdown summary behind, with the `why`
line for each run.

## discover

Walks your repositories, keeps the ones that use Actions, writes the config.

```
$ actions-radar discover
scanned 128 repositories, 12 use Actions
```

## In Claude Code

The repo is also a Claude Code plugin:

```
/plugin install actions-radar --marketplace rmz-oz/actions-radar
```

After a `git push`, a hook waits for the runs in the background while Claude
keeps working. If a run fails, Claude gets the `why` lines and the run link.
The bundled skill asks it to fix the cause rather than edit the workflow, and
to leave secrets and signing keys to you.

Other agents can call `actions-radar watch --json` after a push and read
`failed[].jobs[].why`.

## Setup

Python 3.9 or newer, no dependencies.

```
pipx install actions-radar
```

or run it once with `uvx actions-radar watch`, or clone the repo and run
`./radar.py`.

The token comes from `GITHUB_TOKEN`, or from the GitHub CLI if you are signed in
with `gh auth login`. Private repositories need `repo` scope. Config and state
go in `~/.config/actions-radar/`.

Hourly sweep with cron:

```
0 * * * * /path/to/actions-radar check --report ~/actions-sweep.md
```

`watch --json` and `check --json` print the result for scripts, with progress
on stderr.

Tests are stdlib only:

```
python3 -m unittest discover
```

## Why not GitHub's notifications

GitHub can email you when a run fails, if Actions notifications are on. It
notifies whoever triggered the run, or whoever created a scheduled workflow, so
runs started by a teammate, a bot or another account go to them. The email
says that a run failed; to see why you still open the run and scroll.

## Why not gh run watch

`gh run watch` needs a run id and works on one repository. Right after a push
the run for your commit may not exist yet, so `watch` here polls for the runs
of a commit and waits for all of them. `gh run view --log-failed` prints the
whole failed log; this prints the line. The CLI also has no command that sweeps
many repositories and reports only new failures.

## License

MIT
