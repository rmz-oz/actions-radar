# actions-radar

Tells you when a GitHub Actions run fails.

I had a site deploy break for six days without noticing. My publish script
pushed, printed a link to the Actions tab, and exited. A missing secret killed
the workflow every time and nothing ever said so.

## watch

Waits for the run of one commit, then prints the step that failed and the part
of the log that explains it. Exits 1 on failure, so you can put it at the end of
a deploy script.

```
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

## check

Goes through the repositories in your config and reports runs that failed since
the last check. It remembers where it stopped, so you hear about each failure
once. Run it from cron or launchd.

```
$ ./radar.py check
actions-radar: 2 failed run(s)
```

On macOS a failure also raises a notification. A scheduled sweep has nowhere to
print, so `--report sweep.md` leaves a Markdown summary behind as well.

## discover

Walks your repositories, keeps the ones that use Actions, writes the config.

```
$ ./radar.py discover
scanned 128 repositories, 12 use Actions
```

## Setup

Python 3.9 or newer, no dependencies.

```
git clone https://github.com/rmz-oz/actions-radar
cd actions-radar
./radar.py discover
```

The token comes from `GITHUB_TOKEN`, or from the GitHub CLI if you are signed in
with `gh auth login`. Private repositories need `repo` scope. Config and state
go in `~/.config/actions-radar/`.

Hourly sweep with cron:

```
0 * * * * /usr/bin/python3 /path/to/radar.py check --report ~/actions-sweep.md
```

Tests are stdlib only:

```
python3 -m unittest discover
```

## Why not gh run watch

`gh run watch` is fine if you have the run id and one repository. Right after a
push the run for your commit does not exist yet, so `watch` polls for the run of
a specific commit and waits for it to show up. And nothing in the CLI sweeps
many repositories and tells you only what is new, which is the part that matters
once you stop looking at most of your repositories.

## License

MIT
