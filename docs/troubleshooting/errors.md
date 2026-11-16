---
title: Errors by message
description: "Find the message you see — curl, Codex, the API or the installer — and what to do about it."
---

Search this page for the text of the message.

## curl: (77) error setting certificate verify locations

```
curl: (77) error setting certificate verify locations: CAfile: yorath-root-ca.crt CApath: none
```

curl didn't find the certificate file: the command runs in a different folder from the one the browser saved it to (usually `~/Downloads`). Give the full path — `curl --cacert ~/Downloads/yorath-root-ca.crt …` — or `cd ~/Downloads` first. See [Certificate](../operate/certificate.md).

## SSL certificate problem: unable to get local issuer certificate

The client doesn't trust the certificate Yorath issued for the server's IP. Trust the Yorath root certificate once — [Certificate](../operate/certificate.md) — or install with a domain (`YORATH_DOMAIN`).

## Missing environment variable: `YORATH_API_KEY`

Codex didn't get the key: it was set without `export`, in another terminal window, or Codex was started outside the terminal. In the same window:

```sh
export YORATH_API_KEY=<key from the API keys screen>
codex --profile yorath
```

See [Codex CLI](../agents/codex-cli.md).

## Model metadata for local/default not found

A Codex warning; harmless. Codex doesn't know the context window of a model it hasn't seen; the profile sets `model_context_window`.

## invalid_credentials

Wrong login or password. In the `curl` examples, replace `<login>` with your own and drop the angle brackets. If you can't sign in to the panel at all, see [Can't sign in](cant-sign-in.md).

## 401 from /v1

The key was revoked, or its user was disabled. Create a new key on the API keys screen.

## 403 model_not_allowed

The key or the agent may not call this model: an agent key opens only `agent/<name>`; a regular key opens only local models; an agent calls only the models in its file. See [API](../reference/api.md).

## model_unavailable

No server of the requested model is ready: for example `node node-2 is lost`. Yorath never answers with another model instead. Check `yorath node list` and [Models on cluster nodes](../models/on-cluster-nodes.md).

## queue_timeout (HTTP 503) and queue_full (HTTP 429)

Every server of the model is busy: a request waited more than 120 s, or 100 requests already wait. Repeat later or place the model on another card — [Queue and routing](../cluster/queue-and-routing.md).

## server_busy, window_mismatch, does_not_fit, node_class_denied

`yorath model pull --node` refused to place a model; the table of reasons is in [Models on cluster nodes](../models/on-cluster-nodes.md).

## 403 "Access denied by security policy" from OpenRouter, 403 from Tavily

The service blocks the country of your server. `yorath doctor` tells a country block from a wrong key. Route the core's requests through a proxy — [Web search behind a proxy](../operate/web-search-behind-a-proxy.md).

## waiting for apt: automatic system updates are running

Normal on a fresh server: the installer waits up to 20 minutes for Ubuntu's automatic updates, then continues.
