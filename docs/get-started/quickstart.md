---
title: Quickstart
description: "Install Yorath on a clean Ubuntu server with one command and get the first answer from a local model."
sidebar:
  order: 1
---

You need a clean Ubuntu 24.04 x86_64 server with root SSH access, at least 4 vCPU / 8 GB RAM / 40 GB disk, and ports 22, 80 and 443 open. Full list: [Requirements](requirements.md).

## 1. Install

On the server, as root:

```sh
curl -fsSL https://get.yorath.io | sh
```

The script installs Docker, generates passwords and keys, then builds and starts the services. On a freshly created server it first waits for Ubuntu's automatic updates (up to 20 minutes) — that's normal. At the end it prints a link:

```
[yorath] done. Open the setup wizard:

    https://<server IP>/?code=<code>
```

## 2. Go through the wizard

Open the link. The browser warns about the certificate: Yorath issued it for the server's IP. Click "Advanced → Proceed" — or trust the certificate once, see [Certificate](../operate/certificate.md).

Create the owner, pull the local models the wizard suggests, and enable the HR demo set. Step by step: [First hour](first-hour.md).

## 3. Ask the first question

In Chat, pick `document-assistant` and ask "How many vacation days does an employee get?"

It worked if the answer cites the vacation policy as `[1]`, and Journal shows the run with the model call and access checks.

If it didn't: run `yorath doctor` on the server — every line should be `ok`; otherwise see [doctor and diag](../troubleshooting/doctor-and-diag.md).

## Next

- [Connect Codex CLI](../agents/codex-cli.md) to the Yorath model (GPU server).
- [Add OpenClaw or Hermes](../agents/openclaw-and-hermes.md) (GPU server).
- [Call the model from your code](../reference/api.md).
- [Add a server](../cluster/add-a-server.md) to the cluster.
