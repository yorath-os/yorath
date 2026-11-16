---
title: Server network
description: "All outbound HTTP and HTTPS of the server goes through Yorath; how to read it, switch it and keep third-party agents inside it."
---

All outbound HTTP and HTTPS of the server — its containers and the server itself — goes through the
Yorath proxy on the same server. Nothing is blocked: apt, Docker, updates and agents work as before, but
Journal → Network shows who contacted which domains. HTTPS isn't decrypted — only the domain is visible.
Other protocols from containers to the outside are closed; from the server itself they are allowed and
logged in the core's journal. Inbound connections (SSH, the panel) aren't affected.

```sh
yorath egress status   # mode, rules, proxy, service
yorath egress on       # enable (a new install enables it itself)
yorath egress off      # disable: traffic bypasses Yorath, and the panel shows it in red
```

If the server's network "disappeared", run `yorath egress status`. "proxy: not responding" means HTTP
and HTTPS are closed until it comes back (usually on its own within seconds); if it doesn't, run
`yorath egress off`, then `yorath doctor` and check the `egress` logs. The `yorath-egress` service
restores manually removed rules within 15 seconds, and a mark stays in the audit and on the System
screen.

Server in Russia: intercepted agent traffic still leaves directly, not through `YORATH_EGRESS_PROXY`
(VPN); the core still goes through the VPN.

## Third-party agents on the server: not as root

An agent with a terminal (Hermes, OpenClaw, Codex, Claude Code) installed on the server as root can do
anything: read Yorath's keys, control its containers through Docker, remove any network rules. Yorath
doesn't restrict such an agent — it only sees its model calls. Yorath's rule: an agent reaches the
network and works only through Yorath. For OpenClaw and Hermes, the best way is to connect them from
Yorath ([OpenClaw and Hermes](../agents/openclaw-and-hermes.md)); for other agents there are two safe ways.

**Way 1: a container (better).** Give the agent its own `compose.yaml`, a non-root user inside the
container and its own network, without access to Yorath's files, database or Ollama; the model is
`https://<IP>/v1` with an API key and the Yorath root certificate. Check from inside the container:
`whoami` is not root, and `ls /opt/yorath` and `ls /var/run/docker.sock` say "No such file or directory".

**Way 2: a separate user on the host.** Using Hermes installed in `/root/.hermes` as the example:

```sh
# 1. a user without sudo and NOT in the docker group (docker group = root)
useradd -m -s /bin/bash hermes
id hermes                              # the group list must not include docker or sudo

# 2. reinstall Hermes as that user: the venv in /root/.hermes is tied to root's paths,
#    so copying the directory won't work. From a Russian IP the installer needs a proxy (VPN).
P=http://$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' yorath-vpn-egress-vpn-1):8888
sudo -u hermes -H env https_proxy=$P http_proxy=$P bash -c \
  'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup'

# 3. move the settings, memory and skills (not the venv)
for f in config.yaml .env SOUL.md memories skills sessions; do
  [ -e /root/.hermes/$f ] && cp -a /root/.hermes/$f /home/hermes/.hermes/
done
chown -R hermes:hermes /home/hermes/.hermes && chmod 700 /home/hermes
chmod 600 /home/hermes/.hermes/.env

# 4. check that Yorath's secrets are hidden from it — all three must say Permission denied
sudo -u hermes cat /opt/yorath/installer/.env.local
sudo -u hermes cat /opt/yorath/installer/.env
sudo -u hermes docker ps

# 5. run it as its own user
sudo -iu hermes hermes

# 6. remove the root install
rm -rf /root/.hermes /root/.local/bin/hermes
```

Yorath finds such agents itself: `yorath doctor` prints "agents outside Yorath" (process, user, a root
or container mark), and the System screen shows them as a warning. The check runs every 5 minutes.

If step 4 showed the contents of `/opt/yorath/installer/.env`, fix the permissions:
`chmod 600 /opt/yorath/installer/.env`.

What still bypasses Yorath with this way: the agent isn't in the Yorath registry, and its HTTP and HTTPS
go through the node proxy and appear in Journal → Network as host traffic, not agent traffic.
