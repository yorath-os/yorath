#!/bin/sh
# Join a server to a Yorath cluster as a worker node (change cluster-nodes, decision 35).
# Called by install.sh --join <main node URL> <token> after Docker and the sources are installed; run as
# root from the installation directory. Node: node proxy, node service, node gateway, model servers (one
# Ollama per GPU, installer/node-models.sh); link to the main node is an outgoing WireGuard tunnel, no
# inbound ports from the internet.
#
# Variables: YORATH_NODE_NAME (node name; hostname by default), YORATH_NODE_MODEL=on (an Ollama on a node
# without a GPU; a node with GPUs always runs its model servers).

PRODUCT="Yorath"  # visible product name: every human-facing string takes it from here (T6)
set -eu

MAIN="${1:?main node URL: https://<IP or domain>}"; MAIN="${MAIN%/}"
TOKEN="${2:?join token: yorath node token on the main node}"
NAME="${YORATH_NODE_NAME:-$(hostname -s)}"
WG_DIR=/var/lib/yorath/wg
env_local="installer/.env.local"

say() { printf '\033[1;34m[yorath]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[yorath] error:\033[0m %s\n' "$*" >&2; exit 1; }
set_local() {
  touch "$env_local"; chmod 600 "$env_local"
  grep -v "^$1=" "$env_local" > "$env_local.tmp" || true
  echo "$1=$2" >> "$env_local.tmp"; mv "$env_local.tmp" "$env_local"; chmod 600 "$env_local"
}

[ "$(id -u)" -eq 0 ] || die "run as root"
[ -f distribution.yaml ] || die "run from the $PRODUCT installation directory"

# --- 1. WireGuard ---------------------------------------------------------------------------
if ! command -v wg >/dev/null 2>&1; then
  say "installing WireGuard"
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 install -y -qq wireguard-tools >/dev/null
fi
mkdir -p "$WG_DIR"; chmod 700 "$WG_DIR"
[ -s "$WG_DIR/node.key" ] || (umask 077; wg genkey > "$WG_DIR/node.key")
pub="$(wg pubkey < "$WG_DIR/node.key")"

# --- 2. Main node certificate: fingerprint from the token, no trust on first answer -------------
fp="${TOKEN##*.}"
curl_tls=""
case "$MAIN" in
  https://*)
    if [ "$fp" != "-" ] && [ "$fp" != "$TOKEN" ]; then
      curl -fsSk "$MAIN/yorath-root-ca.crt" -o "$WG_DIR/main-ca.crt" || die "main node $MAIN did not serve the root certificate"
      got="$(openssl x509 -in "$WG_DIR/main-ca.crt" -outform der | sha256sum | cut -c1-64)"
      [ "$got" = "$fp" ] || die "main node certificate does not match the token: spoofing, or a token from another installation"
      curl_tls="--cacert $WG_DIR/main-ca.crt"
    fi ;;
esac

# --- 3. Join --------------------------------------------------------------------------------
vcpu="$(nproc)"
ram_gb="$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)"
# cards and model servers: the toolkit, installer/compose.models.yaml and YORATH_NODE_GPU_* in .env.local
sh installer/node-models.sh
local_value() { sed -n "s/^$1=//p" "$env_local" | tail -1 | tr -d '"'; }
gpu_count="$(local_value YORATH_NODE_GPU_COUNT)"; gpu_name="$(local_value YORATH_NODE_GPU_NAME)"
gpu_vram="$(local_value YORATH_NODE_GPU_VRAM_GB)"; node_model="$(local_value YORATH_NODE_MODEL)"
body="$(python3 -c 'import json,sys; print(json.dumps({"token": sys.argv[1], "name": sys.argv[2], "wg_public_key": sys.argv[3], "cpu_count": int(sys.argv[4]), "ram_gb": int(sys.argv[5]), "gpu_count": int(sys.argv[6] or 0), "gpu": sys.argv[7] or None, "vram_gb": int(sys.argv[9]) if sys.argv[9] else None, "has_model": sys.argv[8] == "on"}))' \
  "$TOKEN" "$NAME" "$pub" "$vcpu" "$ram_gb" "$gpu_count" "$gpu_name" "$node_model" "$gpu_vram")"
say "joining $MAIN as $NAME"
# shellcheck disable=SC2086
answer="$(curl -fsS $curl_tls -H 'content-type: application/json' -d "$body" "$MAIN/api/cluster/join" 2>&1)" \
  || die "main node refused: $answer"
field() { echo "$answer" | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
overlay="$(field overlay_ip)"; main_ip="$(field main_overlay_ip)"
main_pub="$(field main_public_key)"; endpoint="$(field endpoint)"

# --- 4. Tunnel ------------------------------------------------------------------------------
umask 077
cat > /etc/wireguard/yorath-wg.conf <<WG
# Yorath: tunnel from node $NAME to the main node (change cluster-nodes). Written by join.sh.
[Interface]
Address = $overlay/32
PrivateKey = $(cat "$WG_DIR/node.key")

[Peer]
PublicKey = $main_pub
Endpoint = $endpoint
AllowedIPs = 10.88.0.0/24
PersistentKeepalive = 25
WG
umask 022
if [ -d /run/systemd/system ]; then
  systemctl enable --now wg-quick@yorath-wg >/dev/null 2>&1 || systemctl restart wg-quick@yorath-wg
else
  wg-quick down yorath-wg >/dev/null 2>&1 || :; wg-quick up yorath-wg >/dev/null
fi

# --- 5. Node services -----------------------------------------------------------------------
set_local YORATH_ROLE worker
set_local YORATH_NODE_NAME "$NAME"
set_local YORATH_NODE_TOKEN "$(field node_token)"
set_local YORATH_NODE_OVERLAY_IP "$overlay"
set_local YORATH_MAIN_OVERLAY_IP "$main_ip"
set_local YORATH_NODE_CORE_URL "http://$main_ip:8000"
python3 installer/render-env.py >/dev/null
say "building and starting node services (a few minutes the first time)"
docker compose --project-directory installer -f installer/compose.worker.yaml -f installer/compose.models.yaml \
  up -d --build --quiet-pull --remove-orphans 2>&1 | grep -v '^$' | tail -3 || true

say "node $NAME has joined and awaits approval: on the main node, System → Nodes, or yorath node approve $NAME"
