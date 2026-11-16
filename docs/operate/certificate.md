---
title: Certificate
description: "Why the browser warns after install, how to trust the Yorath certificate, and how to open the panel through an SSH tunnel."
---

By default Yorath issues its own certificate for the server's IP. So:

- the **browser** warns about an insecure connection: "Advanced → Proceed";
- **curl** says `SSL certificate problem: unable to get local issuer certificate`;
- **Python clients** and **Codex** don't trust the certificate either.

The right fix is to download the Yorath root certificate once and trust it: the "download
yorath-root-ca.crt" link on the API keys screen, or

```sh
scp root@<IP>:/opt/yorath/installer/yorath-root-ca.crt ~/yorath-root-ca.crt
```

| Where | What to do |
|---|---|
| macOS (browsers, Codex) | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/yorath-root-ca.crt` |
| curl | `curl --cacert ~/yorath-root-ca.crt https://<IP>/...` |
| Python, OpenAI clients | `export SSL_CERT_FILE=~/yorath-root-ca.crt` |
| Codex, if it still complains | `SSL_CERT_FILE=~/yorath-root-ca.crt codex --profile yorath` |

`curl -k` turns verification off entirely — fine for a one-off check, not for everyday work.
With a domain (`YORATH_DOMAIN`, Let's Encrypt) none of this is needed — see [Install variables](../reference/install-variables.md#https).

`yorath open` (and the end of `yorath status`) prints all the steps for your server, with its IP and
paths: the certificate, a tunnel if ports are closed, and the URL with the setup code.

On Windows: double-click `yorath-root-ca.crt` → Install Certificate → Local Machine → "Trusted Root Certification Authorities". On Linux: copy it to `/usr/local/share/ca-certificates/` and run `update-ca-certificates`.

## What each install mode needs on the client

| Install mode | What to do on the client |
|---|---|
| Domain (`YORATH_DOMAIN`, Let's Encrypt) | nothing |
| Corporate certificate (`YORATH_TLS_CERT`) | nothing, if the CA root is already on the laptops |
| Internal CA (default) | trust `installer/yorath-root-ca.crt` from the server once |
| `YORATH_TLS=off` | nothing, but the key and the text travel over the network in the clear |

## SSH tunnel

If the server's ports 80 and 443 are closed from outside, open the panel through SSH:

```sh
ssh -L 8443:localhost:443 root@<IP>     # keep this window open
# in the browser: https://localhost:8443/?code=<code>
```
