---
title: Developer Agent setup
description: "Run the Developer Agent on GitHub issues inside a gVisor sandbox."
---

You need: a Linux host with `runsc`, a model in the registry, and a public address with TLS for the webhook.

1. **Install gVisor:** the `runsc` package from the gVisor repository, then `runsc install` and a Docker restart.
2. **Create a GitHub App:** write access to Contents, Pull requests and Issues, and the Issues event. Install the app on the repository you need and note the App ID, Installation ID, private key and webhook secret. Webhook URL: `https://<address>/api/github/webhook`.
3. **Build the image and start the sandbox** from the repository directory:

   ```bash
   make render-env
   C="docker compose --project-directory installer -f installer/compose.yaml -f installer/compose.tls.yaml"
   $C --profile build-sandbox build sandbox-image
   $C --profile developer up -d
   ```

   The `compose.tls.yaml` overlay is needed in every command when TLS is on (the default): without it,
   `up` recreates `web` without port 443. With `YORATH_TLS=off`, leave it out.

4. **Pass the settings** as the owner: `PUT /api/github/app` with the fields `app_id`, `installation_id`, `private_key`, `webhook_secret`, `repositories` (for example `["acme/webapp"]`). The values go to the secret store; there is no screen yet.
5. **Put the `yorath` label on an issue.** The label, not the issue itself, starts the agent: only someone with rights to the repository can set it.

Protect the main branch on GitHub: the agent's token can write to branches of this repository. Only a human merges.

**What the sandbox sees.** A gVisor container with Codex in `danger-full-access` mode: Codex's own `workspace-write` sandbox can't edit files under gVisor. Outbound traffic goes only through the egress proxy: GitHub from the allow-list and, of the whole core, only `/gateway/v1`. Sign-in, the wizard, the webhook and the rest of the core API are unreachable from the sandbox. The allow-list is in `installer/egress-allow.txt`: GitHub and the npm and PyPI registries (`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`), so the agent can install dependencies and run the project's tests. Add another registry or mirror as a line in this file, then `docker compose restart egress-proxy`. Everything else is `egress.blocked` in the journal.

**PR title.** The agent uses the first line of its summary in Conventional Commits format; if that line doesn't fit, the title is `chore: <issue title>`.

By default the model is external: the code and issue text go to OpenRouter. From Russian IPs OpenRouter answers 403 "Access denied by security policy", and the wizard says so: you need egress through another region or a local model. For private code, set a local model in `agents/developer-agent.md`: on a GPU, `qwen3.5:9b` and `qwen3.6:27b` solve 13–14 of 15 tasks in our tests, but only with a context window of at least 32,768 (on a node with a GPU the wizard sets it itself; it's shown on the Models screen); with the 4,096 window on CPU, Codex doesn't work on a local model.
