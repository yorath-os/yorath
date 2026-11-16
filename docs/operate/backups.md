---
title: Backups
description: "What a Yorath backup contains, where it lives and how long it is kept."
---

```sh
yorath backup
```

Backups live in `/var/lib/yorath/backups/<time>-<version>[-kind]/` (mode 700, a backup contains secrets):
- `db.dump` — `pg_dump -Fc`;
- archives of the documents, custom agents and Caddy volumes;
- `env.local`;
- `meta.env` — version, git ref, time, running services;
- `SHA256SUMS`.

Ollama models are not included: after a restore they are still in place, and on a new node they are pulled again.

Backup kinds:
- **regular** — `yorath backup`; the last three are kept;
- **`pre-update`** — taken before an update and used by `rollback`; rotation keeps it while rollback is possible;
- **`safety`** — taken before any data replacement; rotation never deletes it, so delete it manually once you are sure you don't need it. `yorath doctor` shows how much space these backups take.

Before a backup, `yorath` checks that there is room for two backups of that size.

Restore: [Update and rollback](update-and-rollback.md#rollback-and-restore). Keep copies off the server: while the main node is down, the cluster doesn't accept requests.
