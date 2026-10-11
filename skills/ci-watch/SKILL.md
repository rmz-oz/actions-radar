---
name: ci-watch
description: Check whether GitHub Actions passed for a commit and find the log line that explains a failure. Use after pushing, when the user asks if CI is green, or when a run failed and you need to know why.
allowed-tools: Bash(actions-radar *)
---

`actions-radar` is on the PATH while this plugin is enabled.

After a push, the plugin's hook already waits in the background and tells you
if a run fails. To check by hand:

```
actions-radar watch --json            # HEAD of the current repository
actions-radar watch --json --sha <sha> owner/name
```

It waits for every workflow run of the commit and exits 0 when all pass, 1 when
one fails. Progress goes to stderr. On failure, each entry in `failed[].jobs[]`
has `step`, `why` (the lines that explain the failure) and `log` (the part of
the log around them).

When a run fails:

1. Read `why` first. It is usually the failing test or the compiler error.
2. Reproduce locally if you can, fix the cause, commit and push.
3. Do not edit the workflow, skip tests or loosen checks to make it pass unless
   the user asks. Failures about secrets, tokens or signing keys are for the
   user to fix; tell them which secret and what the log says.
4. If `why` is empty, open the `url` of the run or read `log`.

`actions-radar check` sweeps every repository in its config and reports runs
that failed since the last check.
