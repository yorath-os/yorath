---
title: doctor and diag
sidebar:
  order: 1
description: "First run yorath doctor; if that does not help, build the yorath diag archive and send it with your report."
---

```sh
yorath doctor     # check the installation, with a hint for every problem
yorath diag       # an archive for developers without keys or passwords: the path is printed at the end
```

Run `doctor` first: it names the problem and what to do. If that doesn't help, build the `diag` archive and attach it to a [bug report](https://github.com/yorath-os/yorath/issues/new?template=install_problem.yml).

## What to send

1. Which step, and what you expected to see.
2. The last lines of terminal output or a browser screenshot.
3. The diagnostics archive (keys and passwords in it are redacted):
   ```sh
   yorath diag          # prints the archive path in /var/lib/yorath/diag/
   ```
   If the `yorath` command doesn't exist yet (the install failed earlier), the output of:
   ```sh
   cd /opt/yorath && docker compose --project-directory installer -f installer/compose.yaml ps
   docker compose --project-directory installer -f installer/compose.yaml logs --tail 100 core
   ```


Don't send keys, passwords or tokens.

## What doctor checks

`yorath doctor` checks:
- Docker and the `db`, `ollama`, `core`, `web` services;
- `/health` and that the database schema matches the manifest;
- free disk space;
- the age of the last backup and the space taken by safety backups;
- the certificate expiry on :443;
- `runsc`, if the sandbox is running.

The exit code equals the number of problems.

`yorath diag` builds `/var/lib/yorath/diag/yorath-diag-<time>.tar.gz`. The archive contains:
- `status`, `doctor`, `/version`;
- `docker compose ps`, versions;
- the last 500 log lines of every service;
- `.env.local` variable names without values.

Keys `yorath_key_…`, `sk-or-…`, GitHub tokens, `Bearer …`, private keys and secret values from `.env.local` are redacted. Core logs may contain the text of user requests, so review the archive before sending it. `yorath` itself never sends it anywhere.
