# actions-radar

A small tool that tells you when a GitHub Actions run fails, instead of letting
it fail quietly.

It exists because of a deploy that broke for six days without anyone noticing.
A push went out, the workflow started, a missing secret killed it, and nothing
ever said so. The terminal had already printed a link to the Actions tab and
moved on.

## What it does

**`watch`** blocks until the run for one commit finishes, then prints the step
that failed along with the part of the log that explains why. Put it at the end
of a deploy script and a broken deploy becomes impossible to walk away from.

```console
$ ./radar.py watch --sha "$(git rev-parse HEAD)"
actions-radar: watching owner/site @ e648a73
  queued ...
  in_progress ...
  failure:

  Publish the site
  https://github.com/owner/site/actions/runs/35645016123
  job 'deploy' failed at step 'Upload to Cloudflare Workers'
  ------------------------------------------------------------
  | ERROR  In a non-interactive environment, it's necessary to set a
  | CLOUDFLARE_API_TOKEN environment variable for wrangler to work.
  | ##[error]The process '/opt/.../npx' failed with exit code 1
  ------------------------------------------------------------
```

It exits 0 on success and 1 on failure, so `./deploy.sh && radar.py watch` does
the obvious thing.

**`check`** sweeps every repository you configure, reports the runs that failed
since the last sweep, and remembers where it stopped so nothing is reported
twice. Run it from cron or launchd and forgotten repositories stop rotting in
silence.

```console
$ ./radar.py check
actions-radar: 2 failed run(s)
...
```

**`discover`** walks your repositories once, keeps the ones that actually use
Actions, and writes them to the config file.

```console
$ ./radar.py discover
scanned 128 repositories, 12 use Actions
```

On macOS a failure also raises a notification, so a sweep running in the
background still reaches you.

## Install

Python 3.9 or newer, no dependencies.

```sh
git clone https://github.com/rmz-oz/actions-radar
cd actions-radar
./radar.py discover
```

Authentication comes from `GITHUB_TOKEN`, or from the GitHub CLI when you are
already signed in with `gh auth login`. A token needs `repo` scope for private
repositories, and nothing at all for public ones.

Configuration and state live in `~/.config/actions-radar/`.

## Scheduling a sweep

On macOS, with launchd:

```xml
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>/path/to/actions-radar/radar.py</string>
  <string>check</string>
</array>
<key>StartInterval</key>
<integer>3600</integer>
```

With cron:

```cron
0 * * * * /usr/bin/python3 /path/to/actions-radar/radar.py check
```

## How this differs from `gh run watch`

The GitHub CLI can already watch a single run, and if that is all you need then
use it. Two things pushed this tool into existence:

- `gh run watch` wants a run id, and right after a push the run for your commit
  may not exist yet. `watch` polls for the run belonging to a specific commit
  and waits for it to appear, which is what a deploy script actually needs.
- Nothing in the CLI sweeps many repositories and reports only what is new. Once
  you have more than a handful of repositories, the failures you never hear
  about are the expensive ones.

The log excerpt is also narrower on purpose. A plain tail of a job log shows the
runner cleaning up after itself, so this anchors on the `##[error]` markers and
shows the commands that led to them.

## License

MIT
