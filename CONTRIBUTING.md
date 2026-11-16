# Contributing to Yorath

Thank you for wanting to help.

## Pull requests

Yorath is developed in a private repository and published here release by release, one commit per release. **We don't accept pull requests yet**: a change made here would be overwritten by the next release, and we can't merge it upstream as is. A pull request will be closed with a pointer to this file.

## What helps most: a good bug report

Fixes land in the next release and reference your issue. A report we can act on has:

1. **The step** — what you ran or clicked, and what you expected to see.
2. **What happened** — the last lines of the terminal output, or a screenshot of the browser.
3. **The version** — the output of `yorath status`.
4. **The diagnostics archive** — run `yorath diag` on the server; it prints the path of an archive in `/var/lib/yorath/diag/`. Keys, tokens and passwords in it are redacted; core logs may contain the text of requests, so look through it before attaching.

If the install failed before the `yorath` command existed, attach instead:

```sh
cd /opt/yorath && docker compose --project-directory installer -f installer/compose.yaml ps
docker compose --project-directory installer -f installer/compose.yaml logs --tail 100 core
```

Use the issue templates: **Bug report** for a problem with a running Yorath, **Install problem** for a failed install.

## Questions and ideas

Ask in [Discussions](https://github.com/yorath-os/yorath/discussions). Ideas that become plans show up on the [roadmap](https://yorath.io/roadmap/).

## Security

Don't report vulnerabilities in public issues — see [SECURITY.md](SECURITY.md).
