---
title: Install variables
description: "Variables read by the installer: install directory, version, HTTPS mode, ports and the egress proxy."
---

Set them before the install command, for example `YORATH_DOMAIN=ai.example.com sh /opt/yorath/installer/install.sh`. Re-running the installer is safe: secrets are kept and services are rebuilt.

## Installation

| Variable | Default | What it sets |
|---|---|---|
| `YORATH_DIR` | `/opt/yorath` | where the code and settings live |
| `YORATH_REPO` | `https://github.com/yorath-os/yorath.git` | the repository to clone |
| `YORATH_VERSION` | the release the script was published with | the release tag to install, for example `v0.6.0` |
| `YORATH_WEB_PORT` | `80` (`8080` with `YORATH_TLS=off`) | the panel's HTTP port |
| `YORATH_EGRESS_LOCKDOWN` | on | `off` keeps the server's outbound traffic outside Yorath — see [Server network](../operate/server-network.md) |
| `YORATH_NODE_MODEL` | off | `on` runs a CPU model server on a worker node without a GPU |

## HTTPS

The mode is chosen with variables before `install.sh`; when set on a repeated run, they overwrite the previous mode.

| Variable | Certificate |
|---|---|
| — (default) | internal CA for the node's IP; root certificate: `installer/yorath-root-ca.crt` |
| `YORATH_HOSTNAME=yorath.home.arpa` | internal CA for a name in your network |
| `YORATH_DOMAIN=ai.example.com` | Let's Encrypt; DNS must point to the node, port 80 open |
| `YORATH_TLS_CERT=…` `YORATH_TLS_KEY=…` | your own certificate (corporate CA) |
| `YORATH_TLS=off` | HTTP only on 8080: API keys and text travel in the clear |


`YORATH_ACME_EMAIL` sets the contact address for Let's Encrypt.

## Proxy for outbound requests

Core egress through a proxy (a server in a country that Tavily and OpenRouter block): `YORATH_EGRESS_PROXY=http://host:port` before `install.sh` — see [Web search behind a proxy](../operate/web-search-behind-a-proxy.md).
