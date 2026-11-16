#!/bin/sh
# Yorath: one-command install on a clean Ubuntu 24.04 LTS x86_64 (Requirements §3, decision 10).
#
#   curl -fsSL https://get.yorath.io | sh   # from a stream: installs the release this copy was published with
#   sh /opt/yorath/installer/install.sh     # from a repository clone: installs the clone's version
#
# Variables: YORATH_DIR (/opt/yorath), YORATH_REPO (git URL; for a private repository the token goes
# into the URL), YORATH_VERSION (a release tag such as v0.6.0; by default the release stamped into this
# copy, otherwise the latest tag of YORATH_REPO — never a branch), YORATH_WEB_PORT (80; 8080 with YORATH_TLS=off),
# YORATH_EGRESS_PROXY (http://host:port: core internet access through a proxy; none removes it).
# HTTPS (change model-endpoint), by default an internal CA on the node IP:
#   YORATH_DOMAIN=ai.example.com [YORATH_ACME_EMAIL=...]  — Let's Encrypt, DNS must point at the node
#   YORATH_HOSTNAME=yorath.home.arpa                       — internal CA for a name in your network
#   YORATH_TLS_CERT=/path/cert.pem YORATH_TLS_KEY=/path/key.pem — your own certificate (corporate CA)
#   YORATH_TLS=off                                       — HTTP only: API keys and text travel in the clear
# Cluster worker node: sh installer/install.sh --join https://<main node> <token> (yorath node token).
# Re-running is safe: secrets and the code in installer/.env.local stay unchanged; HTTPS
# variables, when set, overwrite the previous mode.

PRODUCT="Yorath"  # visible product name: every human-facing string takes it from here (T6)
YORATH_RELEASE_TAG=""  # set by the release step in the published copy; empty in the repository
RELEASES_URL="https://github.com/yorath-os/yorath/releases"
set -eu

say() { printf '\033[1;34m[yorath]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[yorath] error:\033[0m %s\n' "$*" >&2; exit 1; }

# YORATH_VERSION: given > the release stamped into this copy > the latest v* tag of the repository.
# Never a branch by default; a version that the repository doesn't have stops the install before it starts.
# distribution.version of an installed copy, without the trailing "# x-release-please-version" marker
release_version() { sed -n 's/^  version: *"\{0,1\}\([^" #]*\).*/\1/p' "$1" | head -1; }

resolve_version() {
  if [ -n "$yorath_version_given" ]; then YORATH_VERSION="$yorath_version_given"
  elif [ -n "$YORATH_RELEASE_TAG" ]; then YORATH_VERSION="$YORATH_RELEASE_TAG"
  else
    YORATH_VERSION="$(git ls-remote --tags --refs --sort=-v:refname "$YORATH_REPO" 'v*' 2>/dev/null | head -1 | sed 's#.*refs/tags/##')"
    [ -n "$YORATH_VERSION" ] || die "no release found in $YORATH_REPO (is it reachable?). Releases: $RELEASES_URL"
  fi
  git ls-remote --exit-code "$YORATH_REPO" "refs/tags/$YORATH_VERSION" "refs/heads/$YORATH_VERSION" >/dev/null 2>&1 \
    || die "version $YORATH_VERSION not found in $YORATH_REPO. Releases: $RELEASES_URL"
}

# The whole script is one function called on the last line: with `curl … | sh` the shell then reads
# all of it before running anything, and no command can swallow the rest of the script from stdin.
# Yorath is already installed here: the streamed script hands over to the installed copy's own installer,
# which runs on its own files of its own version (secrets are kept); a new version goes through
# yorath update. "0.6.0" and "v0.6.0" are one version (pre-public-hardening R5).
handoff_installed() {
  have="$(git -C "$YORATH_DIR" describe --tags --exact-match 2>/dev/null || sed -n 's/^  version: *\([0-9.]*\).*/v\1/p' "$YORATH_DIR/distribution.yaml" | head -1)"
  if [ -n "$yorath_version_given" ] && [ "${yorath_version_given#v}" != "${have#v}" ]; then
    die "$PRODUCT $have is already installed in $YORATH_DIR.
  To update it (with a backup and rollback): yorath update ${yorath_version_given#v}"
  fi
  say "$PRODUCT $have is already installed in $YORATH_DIR: running its installer again (secrets are kept)"
  YORATH_VERSION="" exec sh "$YORATH_DIR/installer/install.sh" "$@"
}

main() {

# cluster worker node (change cluster-nodes): install.sh --join <main node URL> <token>
JOIN_URL=""; JOIN_TOKEN=""
if [ "${1:-}" = "--join" ]; then
  JOIN_URL="${2:?install.sh --join <main node URL> <token>}"
  JOIN_TOKEN="${3:?install.sh --join <main node URL> <token>}"
fi

YORATH_DIR="${YORATH_DIR:-/opt/yorath}"
YORATH_REPO="${YORATH_REPO:-https://github.com/yorath-os/yorath.git}"
yorath_version_given="${YORATH_VERSION:-}"  # set explicitly: do not silently ignore it in a repository clone
YORATH_VERSION=""
if [ "$(echo "${YORATH_TLS:-}" | tr '[:upper:]' '[:lower:]')" = "off" ]; then
  YORATH_WEB_PORT="${YORATH_WEB_PORT:-8080}"
else
  YORATH_WEB_PORT="${YORATH_WEB_PORT:-80}"
fi


# On a fresh server apt is busy with automatic updates for the first minutes (unattended-upgrades, apt.systemd.daily):
# wait for it instead of failing on the dpkg lock. The unattended-upgrade-shutdown daemon does not count.
apt_busy() {
  command -v pgrep >/dev/null 2>&1 || return 1
  pgrep -x dpkg >/dev/null || pgrep -x apt-get >/dev/null || pgrep -x apt >/dev/null \
    || pgrep -f 'bin/unattended-upgrade($| )' >/dev/null
}
apt_get() {
  waited=0
  while apt_busy; do
    [ "$waited" -eq 0 ] && say >&2 "waiting for apt: automatic system updates are running (usually up to 10 minutes after server start)"
    [ "$waited" -lt 1200 ] || die "apt has been busy for more than 20 minutes: check ps aux | grep -E 'apt|dpkg'"
    sleep 5
    waited=$((waited + 5))
  done
  apt-get -o DPkg::Lock::Timeout=300 "$@"
}

# --- 1. Where the script runs from, root, OS and architecture ------------------------------
# A clone is recognised only by the script file itself next to ../distribution.yaml, never by the
# current directory: with `curl … | sh`, $0 is the shell, and a clone in the working directory is not ours.
from_clone=""
case "$0" in
  */install.sh|install.sh) [ -f "$0" ] && [ -f "$(dirname "$0")/../distribution.yaml" ] && from_clone=1 ;;
esac
[ -n "$from_clone" ] || exec </dev/null  # stdin is the script itself: nothing may read it, nothing asks
if [ "$(id -u)" -ne 0 ]; then
  if [ -n "$from_clone" ]; then die "run as root:
    sudo sh $0${1:+ $*}"
  else die "run as root:
    curl -fsSL https://get.yorath.io | sudo ${yorath_version_given:+YORATH_VERSION=$yorath_version_given }sh${1:+ -s -- $*}"
  fi
fi
[ "$(uname -m)" = "x86_64" ] || die "only x86_64 is supported, you have $(uname -m)"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] \
    || say "warning: tested on Ubuntu 24.04, you have ${PRETTY_NAME:-an unknown OS}"
fi

# --- 1b. Which release: decided before anything is installed --------------------------------
[ -z "$from_clone" ] && [ -f "$YORATH_DIR/distribution.yaml" ] && handoff_installed "$@"
if [ -z "$from_clone" ] && [ ! -f "$YORATH_DIR/distribution.yaml" ]; then
  command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt_get update -qq; apt_get install -y -qq git >/dev/null; }
  resolve_version
  say "installing $PRODUCT $YORATH_VERSION from $YORATH_REPO"
fi

# --- 2. Docker ----------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  say "installing Docker from the official repository"
  export DEBIAN_FRONTEND=noninteractive
  apt_get update -qq
  apt_get install -y -qq ca-certificates curl gnupg >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME:-noble} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt_get update -qq
  apt_get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker_major="$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d. -f1 || echo 0)"
[ "${docker_major:-0}" -ge 24 ] 2>/dev/null || die "Docker >= 24 is required, found $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'not running')"
command -v python3 >/dev/null 2>&1 || apt_get install -y -qq python3 python3-yaml >/dev/null
python3 -c 'import yaml' 2>/dev/null || apt_get install -y -qq python3-yaml >/dev/null

# --- 3. Sources ---------------------------------------------------------------------------
script_dir=""
[ -n "$from_clone" ] && script_dir="$(cd "$(dirname "$0")" && pwd)"
if [ -n "$script_dir" ]; then
  YORATH_DIR="$(cd "$script_dir/.." && pwd)"
  say "using the repository clone: $YORATH_DIR"
  if [ -n "$yorath_version_given" ] && [ -d "$YORATH_DIR/.git" ]; then
    have="$(git -C "$YORATH_DIR" describe --tags --exact-match 2>/dev/null || git -C "$YORATH_DIR" rev-parse --abbrev-ref HEAD)"
    [ "$have" = "$yorath_version_given" ] || die "YORATH_VERSION=$yorath_version_given does not apply: the script runs from a repository clone ($have).
  To update the installed $PRODUCT (with a backup and rollback): yorath update ${yorath_version_given#v}
  To install another version from scratch: git -C $YORATH_DIR checkout $yorath_version_given, then run install.sh again"
  fi
else
  say "cloning $YORATH_REPO ($YORATH_VERSION) into $YORATH_DIR"
  git clone --quiet --depth 1 --branch "$YORATH_VERSION" "$YORATH_REPO" "$YORATH_DIR" 2>/dev/null \
    || git clone --quiet --branch "$YORATH_VERSION" "$YORATH_REPO" "$YORATH_DIR"
fi
cd "$YORATH_DIR"

if [ -z "$JOIN_URL" ] && grep -q '^YORATH_ROLE=worker$' installer/.env.local 2>/dev/null; then
  # a joined worker node run again (new version, a GPU added): model servers and node services are
  # brought up to date; the tunnel and the node identity stay as they are
  say "this server is a cluster worker node: updating its services"
  sh installer/node-models.sh
  python3 installer/render-env.py >/dev/null
  docker compose --project-directory installer -f installer/compose.worker.yaml -f installer/compose.models.yaml \
    up -d --build --quiet-pull --remove-orphans 2>&1 | grep -v '^$' | tail -3 || true
  say "worker node services are up to date; models are placed from the main node: yorath model pull <model> --node <node>"
  exit 0
fi

if [ -n "$JOIN_URL" ]; then
  # worker node: no database, core or panel; the main node's secrets never get here
  sh installer/join.sh "$JOIN_URL" "$JOIN_TOKEN"
  ln -sf "$YORATH_DIR/installer/yorath.sh" /usr/local/bin/yorath
  YORATH_DIR="$YORATH_DIR" sh installer/yorath.sh egress on \
    || say "warning: egress control was not enabled (yorath egress status)"
  exit 0
fi

# --- 4. Local secrets and the wizard code -------------------------------------------------
env_local="installer/.env.local"
rand() { head -c 64 /dev/urandom | base64 | tr -dc "$1" | head -c "$2"; }
if [ ! -f "$env_local" ]; then
  say "generating $env_local (database password, secrets key, one-time code)"
  {
    echo "YORATH_DB_PASSWORD=$(rand 'A-Za-z0-9' 32)"
    echo "YORATH_SECRET_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
    echo "YORATH_SETUP_CODE=$(rand 'A-HJ-NP-Z2-9' 8)"
    echo "YORATH_WEB_BIND=0.0.0.0"
    echo "YORATH_WEB_PORT=$YORATH_WEB_PORT"
  } > "$env_local"
  chmod 600 "$env_local"
fi

# --- 4a. HTTPS: mode in .env.local, certificate files in installer/tls ---------------------
set_local() {  # key=value in .env.local: replace the line or append it
  grep -v "^$1=" "$env_local" > "$env_local.tmp" || true
  echo "$1=$2" >> "$env_local.tmp"
  mv "$env_local.tmp" "$env_local"
  chmod 600 "$env_local"
}
get_local() { sed -n "s/^$1=//p" "$env_local" | tail -1; }
unset_local() { grep -v "^$1=" "$env_local" > "$env_local.tmp" || true; mv "$env_local.tmp" "$env_local"; chmod 600 "$env_local"; }

# GPU on every run: cards are often added after the install (field run 11.11.2026)
gpu_name=""; gpu_vram=""; gpu_count=0
# Provider images often ship nvidia-smi without a GPU: it prints an error and exits non-zero.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
  gpu_count="$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ' || true)"
  gpu_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || true)"
  case "$gpu_mib" in
    ''|*[!0-9]*) gpu_mib="" ;;
  esac
  [ -n "$gpu_mib" ] && gpu_vram="$(( gpu_mib / 1024 ))"
fi
gpu_before="$(get_local YORATH_GPU_COUNT):$(get_local YORATH_GPU_NAME)"
set_local YORATH_GPU_COUNT "${gpu_count:-0}"
if [ -n "$gpu_name" ]; then set_local YORATH_GPU_NAME "$gpu_name"; else unset_local YORATH_GPU_NAME; fi
if [ -n "$gpu_vram" ]; then set_local YORATH_GPU_VRAM_GB "$gpu_vram"; else unset_local YORATH_GPU_VRAM_GB; fi
if [ "$gpu_before" != ":" ] && [ "$gpu_before" != "${gpu_count:-0}:$gpu_name" ]; then
  say "hardware changed (GPU: ${gpu_name:-none}); after the install check the model: yorath model"
fi

# Core internet access through a proxy (YORATH_EGRESS_PROXY=http://host:port; none removes it):
# Tavily and OpenRouter block some countries, and without them the agent silently answers "from memory"
if [ -n "${YORATH_EGRESS_PROXY:-}" ]; then
  if [ "$YORATH_EGRESS_PROXY" = "none" ]; then unset_local YORATH_EGRESS_PROXY
  else set_local YORATH_EGRESS_PROXY "$YORATH_EGRESS_PROXY"; fi
fi
unset YORATH_EGRESS_PROXY  # otherwise docker compose takes the value from the environment (none) instead of .env
[ -n "$(get_local YORATH_EGRESS_PROXY)" ] && say "core outbound requests go through proxy $(get_local YORATH_EGRESS_PROXY | sed 's#//[^@/]*@#//#')"

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$ip" ] || ip="$(curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || echo 127.0.0.1)"
set_local YORATH_PUBLIC_IP "$ip"
for var in YORATH_TLS YORATH_DOMAIN YORATH_HOSTNAME YORATH_ACME_EMAIL; do
  eval "value=\${$var:-}"
  if [ -n "$value" ]; then set_local "$var" "$value"; fi
done
if [ -n "${YORATH_TLS_CERT:-}" ] || [ -n "${YORATH_TLS_KEY:-}" ]; then
  [ -r "${YORATH_TLS_CERT:-}" ] && [ -r "${YORATH_TLS_KEY:-}" ] \
    || die "YORATH_TLS_CERT and YORATH_TLS_KEY must point to readable files"
  mkdir -p installer/tls
  cp "$YORATH_TLS_CERT" installer/tls/cert.pem
  cp "$YORATH_TLS_KEY" installer/tls/key.pem
  chmod 600 installer/tls/key.pem
  set_local YORATH_TLS_CERT installer/tls/cert.pem
  set_local YORATH_TLS_KEY installer/tls/key.pem
fi
tls="$(get_local YORATH_TLS | tr '[:upper:]' '[:lower:]')"
domain="$(get_local YORATH_DOMAIN)"
hostname_="$(get_local YORATH_HOSTNAME)"
web_port="$(get_local YORATH_WEB_PORT)"; web_port="${web_port:-$YORATH_WEB_PORT}"
mkdir -p installer/tls

busy() {  # port is taken by something other than our docker-proxy
  command -v ss >/dev/null 2>&1 || return 1
  ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -v docker-proxy | grep -q .
}
if [ "$tls" != "off" ]; then
  for port in "$web_port" 443; do
    if busy "$port"; then die "port $port is used by another process (ss -ltnp): free it or set YORATH_TLS=off"; fi
  done
  if [ -n "$domain" ] && [ "$web_port" != "80" ]; then
    say "warning: Let's Encrypt needs port 80, but YORATH_WEB_PORT=$web_port"
  fi
fi

# --- 5. GPU: Ollama gets the GPU through nvidia-container-toolkit (spike local-model-cpu) -
compose_files="-f installer/compose.yaml"
[ "$tls" = "off" ] || compose_files="$compose_files -f installer/compose.tls.yaml"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  if ! command -v nvidia-ctk >/dev/null 2>&1; then
    say "installing nvidia-container-toolkit"
    # provider images hold the NVIDIA packages: unhold only the toolkit, leave the driver alone
    apt-mark unhold nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1 >/dev/null 2>&1 || true
    if ! apt_get install -y -qq nvidia-container-toolkit >/dev/null 2>&1; then
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit.gpg --yes
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
      apt_get update -qq
      apt_get install -y -qq nvidia-container-toolkit >/dev/null
    fi
  fi
  if ! docker info 2>/dev/null | grep -q 'Runtimes:.*nvidia'; then
    nvidia-ctk runtime configure --runtime=docker >/dev/null
    systemctl restart docker
  fi
  say "GPU found: Ollama will start with the GPU (installer/compose.gpu.yaml)"
  compose_files="$compose_files -f installer/compose.gpu.yaml"
fi

# --- 5a. Provider system proxy: containers do not inherit it, host commands go through it
proxy="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}"
if [ -z "$proxy" ] && [ -r /etc/environment ]; then
  proxy="$(sed -n 's/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}\(https_proxy\|HTTPS_PROXY\|http_proxy\|HTTP_PROXY\)=//p' /etc/environment | head -1 | tr -d "\"'")"
fi
if [ -n "$proxy" ]; then
  proxy="$(echo "$proxy" | sed 's#//[^@/]*@#//#')"  # without login and password
  say "warning: the server has a system proxy $proxy (providers often set one)."
  say "  $PRODUCT containers do not use it; host commands (curl, apt) go through it."
fi

# --- 6. Start -----------------------------------------------------------------------------
set_local YORATH_COMPOSE_FILES "\"$compose_files\""  # the same set of files for the yorath command
say "generating installer/.env from distribution.yaml"
python3 installer/render-env.py >/dev/null
say "building and starting services (a few minutes the first time)"
# shellcheck disable=SC2086
docker compose --project-directory installer $compose_files up -d --build --quiet-pull 2>&1 | grep -v '^$' | tail -5 || true

say "waiting for the core to be ready"
i=0
until curl -fsS "http://127.0.0.1:${YORATH_CORE_PORT:-8000}/version" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -le 90 ] || die "core did not respond within 3 minutes: docker compose --project-directory installer -f installer/compose.yaml logs core"
  sleep 2
done

code="$(get_local YORATH_SETUP_CODE)"
if [ "$tls" = "off" ]; then
  url="http://$ip:$web_port"
  say "warning: TLS is off (YORATH_TLS=off): API keys and request text travel over the network in the clear"
else
  site="${domain:-${hostname_:-$ip}}"
  url="https://$site"
  if [ -z "$domain" ] && [ -z "$(get_local YORATH_TLS_CERT)" ]; then
    # internal CA: clients add the root certificate to their trusted store
    i=0
    # shellcheck disable=SC2086
    until docker compose --project-directory installer $compose_files cp \
      web:/data/caddy/pki/authorities/local/root.crt installer/yorath-root-ca.crt >/dev/null 2>&1; do
      i=$((i + 1)); [ "$i" -le 15 ] || { say "warning: Caddy root certificate did not appear"; break; }
      sleep 2
    done
    if [ -f installer/yorath-root-ca.crt ]; then
      say "root certificate for clients: $YORATH_DIR/installer/yorath-root-ca.crt (curl --cacert, SSL_CERT_FILE, keychain)"
    fi
  fi
fi
# --- 7. The yorath command: backups, update, rollback, diagnostics (docs/reference/commands.md) ----
chmod +x installer/yorath.sh
ln -sf "$YORATH_DIR/installer/yorath.sh" /usr/local/bin/yorath
mkdir -p /var/lib/yorath && chmod 700 /var/lib/yorath
version="$(release_version distribution.yaml)"
grep -v '^CURRENT_VERSION=' /var/lib/yorath/state.env 2>/dev/null > /var/lib/yorath/state.env.tmp || true
echo "CURRENT_VERSION=$version" >> /var/lib/yorath/state.env.tmp
mv /var/lib/yorath/state.env.tmp /var/lib/yorath/state.env
# agents outside Yorath: the first scan, which also installs the 5-minute timer (decision 36)
YORATH_DIR="$YORATH_DIR" sh installer/yorath.sh scan || say "warning: check for agents outside $PRODUCT failed (yorath scan)"
# node egress through Yorath by default (decision 36); YORATH_EGRESS_LOCKDOWN=off is an explicit opt-out
if [ "${YORATH_EGRESS_LOCKDOWN:-$(get_local YORATH_EGRESS_LOCKDOWN)}" = "off" ]; then
  YORATH_DIR="$YORATH_DIR" sh installer/yorath.sh egress off
else
  command -v nft >/dev/null 2>&1 || apt-get install -y -qq nftables >/dev/null
  YORATH_DIR="$YORATH_DIR" sh installer/yorath.sh egress on \
    || say "warning: egress control was not enabled (yorath egress status, yorath doctor)"
fi
unset YORATH_EGRESS_LOCKDOWN
say "$PRODUCT $version; maintenance: yorath status | backup | update | rollback | doctor | diag"

say "done. Open the setup wizard:"
# certificate, tunnel, URL with the code; yorath status and yorath open print the same
YORATH_DIR="$YORATH_DIR" sh installer/yorath.sh open || printf '\n    %s/?code=%s\n\n' "$url" "$code"
say "the setup code is in $YORATH_DIR/$env_local (YORATH_SETUP_CODE)"
}

# Tests load the functions without running the install: YORATH_SOURCE_ONLY=1 . installer/install.sh
[ "${YORATH_SOURCE_ONLY:-}" = 1 ] || main "$@"
