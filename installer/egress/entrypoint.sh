#!/bin/sh
# Dummy certificate for https_port: needed only by the intercept syntax, HTTPS is not decrypted.
set -eu
state=/var/lib/yorath-egress
mkdir -p "$state" /var/log/yorath-egress
if [ ! -s "$state/bump.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=yorath-egress" \
    -keyout "$state/bump.key" -out "$state/bump.crt" 2>/dev/null
  cat "$state/bump.crt" "$state/bump.key" > "$state/bump.pem"
  rm -f "$state/bump.key"
fi
# agent denials (written by node-agent); squid.conf will not load without the file
# the same text node-agent writes (deny_conf): otherwise its first pass triggers a reconfigure
[ -f "$state/yorath-deny.conf" ] || printf '# Written by the Yorath node service (connected-agents): agents with internet turned off.\nacl yorath_off src 255.255.255.255/32\n' > "$state/yorath-deny.conf"
chown -R proxy:proxy "$state" /var/log/yorath-egress
chmod 600 "$state/bump.pem"
# Squid is PID 1 here: any pid file is from a previous run. After an abnormal stop (power loss,
# dockerd killed) it stays with PID 1 and Squid refuses to start, while the node rules
# block HTTP/HTTPS until the proxy is back (cluster bench 13.11.2026)
rm -f /run/squid.pid
exec squid -N -f /etc/squid/squid.conf
