---
title: Update and rollback
description: "Update Yorath to a new release, roll back one step, or restore from a backup."
---

<!-- x-release-please-start-version -->
```sh
yorath status            # which version is installed now
yorath update 0.7.0      # update to this version
```
<!-- x-release-please-end -->

What happens: a backup (database, documents, custom agents, certificates, settings) → switch to the
new version → check that the core came up and the database schema matches. If the new version doesn't
come up, Yorath goes back to the backup on its own and saves the failed version's log to
`/var/lib/yorath/failed-update-<version>.log`.

To go back one step when the new version works but you don't like it:

```sh
yorath rollback          # shows how much data appeared after the update, and asks
```

Rollback goes only one version back and only once; data created after the update is lost on rollback
(the command shows how much).

**Don't update by re-running `install.sh`.** It rebuilds the services but doesn't change the version;
with a `YORATH_VERSION` that doesn't match the checkout, it stops and suggests `yorath update`.


## What the update does

```sh
yorath update <version> --from yorath.bundle     # from a git bundle: offline node or private build
```

Steps:
1. Check that the version is newer than the installed one. You can't go back with `update`; that's what `rollback` is for.
2. A `pre-update` backup.
3. Switch the source tree to the tag.
4. Build images and start the same services, including the developer profile if it was running.
5. Wait until `/health` responds and `/version` shows the new version and the database schema from its manifest (up to 5 minutes, `YORATH_READY_TIMEOUT`).

If any step after the backup fails, `yorath` restores the backup itself and exits with an error. `yorath diag` shows the cause.

Core and web images are built on the node and tagged `yorath-*:<version>`. The previous version's image stays on the node, so a rollback doesn't rebuild it.

## Rollback and restore

`yorath rollback` brings back the version that was installed before the last update, together with the data as of the update. It works once: you can't step back in a chain (0.3 → 0.2 → 0.1).

`yorath restore <backup>` is for disasters: a broken database or deleted data. It only accepts a backup of the installed version.

These limits are deliberate, because a far rollback:
- erases the audit journal for the whole period;
- brings back revoked API keys, users and sessions;
- bypasses security fixes.

Before replacing data, `yorath`:
1. shows how many executions, messages, documents and audit records appeared after the backup and asks for confirmation (`--yes` skips the question);
2. takes a safety backup of the current state: everything being replaced stays in it.

After startup the core writes an `installation.restored` audit event: the backup time, versions before and after, the number of replaced records and the path of the safety backup.
