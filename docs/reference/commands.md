---
title: Commands
description: "The yorath command on the server: status, updates, backups, models, agents, nodes and diagnostics."
---

The `yorath` command on the node. The installer puts it at `/usr/local/bin/yorath` (a link to `installer/yorath.sh`). Run it as root. It is not the core's internal `yorath` command inside the `core` container, even though they share a name.

| Command | What it does |
|---|---|
| `yorath status` | version, running services, what you can roll back to, the list of backups and a `yorath open` hint |
| `yorath egress [status\|on\|off]` | node egress through Yorath: nftables rules `inet yorath` and a local proxy (the `egress` service, Squid in the host network). `on` enables it (a new install enables it itself; `YORATH_EGRESS_LOCKDOWN=off` opts out), `off` removes the rules, `status` shows the mode, rules, proxy, service and alarm. The `yorath-egress` service loads the rules after Docker and restores them within 15 s if they are removed |
| `yorath scan` | agents outside Yorath on the node: processes of known agents on the host (root ones marked separately) and in other containers, their directories, other containers; the core reads the report `installer/.host-report/host.json` for the System screen. The systemd timer `yorath-scan.timer` runs every 5 minutes; the first check installs it |
| `yorath open` | how to open the panel from your computer: `scp` of the certificate and how to trust it, an SSH tunnel if ports are closed, the URL with the setup code until the first sign-in |
| `yorath backup` | backup: database, documents, custom agents, certificates, `.env.local` |
| `yorath update <version> [--from <bundle>]` | update to tag `v<version>`; if the new version doesn't come up, the installation restores the backup itself |
| `yorath rollback [--yes]` | one step back: the backup taken before the last update |
| `yorath restore <backup> [--yes]` | disaster recovery from a backup of the same version |
| `yorath doctor` | check the installation, with a hint for every problem |
| `yorath diag` | archive for developers: logs and state without keys or passwords |
| `yorath model [list \| pull [<name>] [--force]]` | local model: current and recommended, a catalog with a "fits" estimate, pulling and switching `local/default` |


Agents, nodes and models in a cluster:

| Command | What it does |
|---|---|
| `yorath agent add <openclaw\|hermes> [--node <node>] [--version <v>] [--telegram]` | install a connected agent — [OpenClaw and Hermes](../agents/openclaw-and-hermes.md) |
| `yorath agent list \| logs \| remove [--purge] \| upgrade \| internet <on\|off> \| telegram` | manage connected agents |
| `yorath node token \| list \| approve \| revoke \| status \| set-classes` | cluster nodes — [Add a server](../cluster/add-a-server.md) |
| `yorath model pull <name> --node <node> [--gpu N] [--replace]`, `yorath model default`, `yorath model remove` | models on nodes — [Models on cluster nodes](../models/on-cluster-nodes.md) |

## The core's own commands

Inside the `core` container there is another `yorath` command for maintenance: `yorath create-user --login <login> --role owner` (password from stdin) creates a user, `yorath sync-agents` creates agents and revisions from the md files in `agents/`, `yorath sync-policies` loads the built-in Cedar policies into the `policies` table (owner edits are kept), `yorath purge-traces` deletes traces older than `journal.trace_retention_days` from the manifest.
