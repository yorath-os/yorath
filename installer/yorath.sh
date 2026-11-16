#!/bin/sh
# yorath — maintain a Yorath installation on a node (change update-and-rollback, criterion §12 item 9).
# help:begin — `yorath help` prints these lines; every command of the dispatcher below must be here
#   yorath status                         version, services, backups and how to open it in a browser
#   yorath open                           how to open Yorath in a browser from your computer
#   yorath backup                         backup: database, documents, custom agents, TLS, .env.local
#   yorath update <version> [--from <bundle>]  update to tag v<version>; on failure, restore the backup
#   yorath rollback [--yes]               one step back: the backup taken before the last update
#   yorath restore <backup> [--yes]       disaster recovery: a backup of the installed version
#   yorath doctor                         check the installation, with hints
#   yorath scan                           agents outside Yorath on the node (report for the System screen)
#   yorath egress [status|on|off]         node egress through Yorath: nftables rules and a local proxy
#   yorath diag                           archive for developers, without secrets
#   yorath model [list | pull [<name>] [--force]]  local model: current, catalog, pull
#   yorath model pull <name> --node <node> [--gpu N] [--replace]   place a model on a cluster node
#   yorath model remove <name> [--node <node>] [--gpu N]           take it off the node's model servers
#   yorath model default <name>                                    point local/default at a catalog model
#   yorath agent list|add|remove|upgrade|restart|model|telegram|internet|logs  connected agents (OpenClaw, Hermes)
#   yorath node token|list|approve|revoke|sync    cluster: nodes by token, WireGuard tunnel
#   yorath node set-classes <node> <classes…>     data classes that may be sent to a node
#   yorath help                           this list
# help:end
#
# Every data replacement is preceded by a safety backup and followed by an installation.restored audit event.
# Variables: YORATH_DIR (installation directory), YORATH_BACKUP_DIR (/var/lib/yorath/backups),
# YORATH_READY_TIMEOUT (300 s), YORATH_KEEP_BACKUPS (3).

PRODUCT="Yorath"  # visible product name: every human-facing string takes it from here (T6)
set -eu

say() { printf '\033[1;34m[yorath]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[yorath]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[yorath] error:\033[0m %s\n' "$*" >&2; exit 1; }

# git checkout during an update replaces this file, and sh reads the script as it runs, so we run from a copy
# YORATH_SOURCE_ONLY=1 . yorath.sh: the functions only, for tests (no copy, no root check, no command)
if [ -z "${YORATH_SELF_COPY:-}" ] && [ -z "${YORATH_SOURCE_ONLY:-}" ]; then
  self="$(readlink -f "$0")"
  : "${YORATH_DIR:=$(cd "$(dirname "$self")/.." && pwd)}"
  copy="$(mktemp /tmp/yorath.XXXXXX)"
  cp "$self" "$copy"
  YORATH_SELF_COPY="$copy" YORATH_DIR="$YORATH_DIR" exec sh "$copy" "$@"
fi
[ -n "${YORATH_SOURCE_ONLY:-}" ] || trap 'rm -f "$YORATH_SELF_COPY"' EXIT

[ -f "$YORATH_DIR/distribution.yaml" ] || die "no installation found in $YORATH_DIR (set YORATH_DIR)"
cd "$YORATH_DIR"
STATE_DIR=/var/lib/yorath
STATE="$STATE_DIR/state.env"
BACKUPS="${YORATH_BACKUP_DIR:-$STATE_DIR/backups}"
READY_TIMEOUT="${YORATH_READY_TIMEOUT:-300}"
KEEP="${YORATH_KEEP_BACKUPS:-3}"
PROJECT=yorath
VOLUMES="documents-data agents-data caddy_data caddy_config"
env_local="installer/.env.local"
REPORT="$YORATH_DIR/installer/.host-report/host.json"  # the core reads this directory read-only: System screen

# --- common -------------------------------------------------------------------------------
local_set() {  # key value in .env.local
  grep -v "^$1=" "$env_local" > "$env_local.tmp" 2>/dev/null || true
  echo "$1=$2" >> "$env_local.tmp"
  cat "$env_local.tmp" > "$env_local" && rm -f "$env_local.tmp"
}
local_get() { [ -f "$env_local" ] && sed -n "s/^$1=//p" "$env_local" | tail -1 | tr -d '"' || true; }
env_get() { [ -f installer/.env ] && sed -n "s/^$1=//p" installer/.env | tail -1 | tr -d '"' || true; }
state_get() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | tail -1 || true; }
state_set() {  # key value
  mkdir -p "$STATE_DIR"
  touch "$STATE"
  grep -v "^$1=" "$STATE" > "$STATE.tmp" || true
  echo "$1=$2" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}
meta_get() { sed -n "s/^$2=//p" "$1/meta.env" | tail -1; }

compose_files() {
  files="$(local_get YORATH_COMPOSE_FILES)"
  if [ -z "$files" ]; then  # installed before yorath existed: same choice as install.sh
    files="-f installer/compose.yaml"
    [ "$(local_get YORATH_TLS | tr '[:upper:]' '[:lower:]')" = "off" ] || files="$files -f installer/compose.tls.yaml"
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
      files="$files -f installer/compose.gpu.yaml"
    fi
  fi
  echo "$files"
}
# shellcheck disable=SC2046
compose() { docker compose --project-directory installer $(compose_files) "$@"; }

manifest_field() {  # field distribution.<name> from a manifest file (stdin)
  sed -n "/^distribution:/,/^[a-z]/s/^  $1: *\"\{0,1\}\([^\" #]*\).*/\1/p" | head -1
}
current_version() { manifest_field version < distribution.yaml; }

clone_hint() {  # step 1 of "yorath node token": the same version on the new node, ready to paste
  ver="$(manifest_field version < "$YORATH_DIR/distribution.yaml")"
  repo="$(printf '%s' "$1" | sed 's#//[^/@]*@#//#')"  # this server's token is never printed
  echo "  1. Get the same version, $ver:"
  echo "       apt-get update && apt-get install -y git"
  case "$1" in
    https://*@*)
      echo "       # the repository is private: the new node needs a read-only token for it, as this one did"
      echo "       T=<paste a read-only token here>"
      echo "       git clone --branch v$ver \"https://\$T@${repo#https://}\" /opt/yorath" ;;
    "") echo "       git clone --branch v$ver https://github.com/yorath-os/yorath.git /opt/yorath" ;;
    *) echo "       git clone --branch v$ver $repo /opt/yorath" ;;
  esac
}
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$2" ]; }

core_get() {  # path on the core: the core port is published on 127.0.0.1 only
  port="${YORATH_CORE_PORT:-$(local_get YORATH_CORE_PORT)}"
  curl -fsS --max-time 5 "http://127.0.0.1:${port:-8000}$1"
}
json_field() {  # dotted path from JSON on stdin
  python3 -c 'import json,sys
try: v=json.load(sys.stdin)
except ValueError: v={}
for k in sys.argv[1].split("."): v=v.get(k) if isinstance(v,dict) else None
print("" if v is None else (str(v).lower() if isinstance(v,bool) else v))' "$1"
}

running_services() {  # project services running now (including the developer profiles)
  docker ps --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.service"}}' | sort -u | tr '\n' ' '
}

wait_ready() {  # version: /health 200, version and schema match
  i=0
  while [ "$i" -lt "$READY_TIMEOUT" ]; do
    if core_get /health >/dev/null 2>&1; then
      v="$(core_get /version 2>/dev/null || echo '{}')"
      if [ "$(echo "$v" | json_field distribution.version)" = "$1" ] \
        && [ "$(echo "$v" | json_field schema_matches_manifest)" = "true" ]; then
        return 0
      fi
    fi
    sleep 3
    i=$((i + 3))
  done
  return 1
}

wait_db() {
  i=0
  until compose exec -T db pg_isready -q -U "$(db_user)" >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -le 60 ] || return 1
    sleep 2
  done
}
db_user() { u="$(local_get YORATH_DB_USER)"; echo "${u:-yorath}"; }
db_name() { n="$(local_get YORATH_DB_NAME)"; echo "${n:-yorath}"; }
volume_path() { docker volume inspect -f '{{.Mountpoint}}' "${PROJECT}_$1" 2>/dev/null || true; }
agent_volumes() { docker volume ls -q --filter name='^yorath-agent-' 2>/dev/null || true; }  # memory of connected agents
stop_agents() {  # connected agent containers: not in the Compose project, node-agent manages them
  docker ps -q --filter label=yorath.connected | xargs -r docker stop -t 30 >/dev/null
}
pg_image() { env_get YORATH_IMAGE_POSTGRES; }
start_services() {  # services...; build output goes to a log, its tail is shown on error
  [ "$(local_get YORATH_ROLE)" = "worker" ] || cluster_units  # an update repairs units from older versions
  log="$STATE_DIR/compose-up.log"
  mkdir -p "$STATE_DIR"
  say "building images and starting services (log: $log)"
  # shellcheck disable=SC2086
  if ! compose up -d --build --quiet-pull "$@" > "$log" 2>&1; then
    # an image pull failing while the node proxy restarts gets one retry (test bench 13.11.2026)
    if grep -qE "failed to do request|server gave HTTP response|i/o timeout|TLS handshake" "$log"; then
      warn "network dropped during the build, retrying in 10 s"
      sleep 10
      compose up -d --build --quiet-pull "$@" >> "$log" 2>&1 && return 0
    fi
    tail -n 15 "$log" >&2
    return 1
  fi
}
human_changes() {  # changes-since JSON → one line
  python3 -c 'import json,sys
d=json.load(sys.stdin)
n=[d.get(k,0) for k in ("executions","messages","documents","audit_events")]
print("Execution %s, messages %s, documents %s, audit records %s" % tuple(n))'
}

confirm() {  # question; --yes skips it
  [ "${YES:-0}" = 1 ] && return 0
  [ -r /dev/tty ] || die "no terminal to confirm: add --yes"
  printf '%s [y/N] ' "$1" > /dev/tty
  read -r answer < /dev/tty || answer=""
  case "$answer" in y|Y|yes) return 0 ;; *) die "cancelled" ;; esac
}

# --- backup -------------------------------------------------------------------------------
estimate_kb() {
  db_bytes="$(compose exec -T db psql -U "$(db_user)" -d "$(db_name)" -tAc \
    'select pg_database_size(current_database())' 2>/dev/null | tr -d '[:space:]' || echo 0)"
  kb=$(( ${db_bytes:-0} / 1024 ))
  for v in $VOLUMES; do
    p="$(volume_path "$v")"
    [ -n "$p" ] && [ -d "$p" ] && kb=$(( kb + $(du -sk "$p" | cut -f1) ))
  done
  for v in $(agent_volumes); do
    p="$(docker volume inspect -f '{{.Mountpoint}}' "$v" 2>/dev/null || true)"
    [ -n "$p" ] && [ -d "$p" ] && kb=$(( kb + $(du -sk "$p" | cut -f1) ))
  done
  echo "$kb"
}

rotate() {  # regular backups: the last $KEEP stay; safety and rollback backups are left alone
  keep_rollback="$(state_get ROLLBACK_BACKUP)"
  # shellcheck disable=SC2012
  ls -1d "$BACKUPS"/*/ 2>/dev/null | sed 's#/$##' | sort -r | while read -r d; do
    [ -f "$d/meta.env" ] || continue
    [ "$(meta_get "$d" KIND)" = "safety" ] && continue
    [ "$d" = "$keep_rollback" ] && continue
    echo "$d"
  done | tail -n +"$((KEEP + 1))" | while read -r d; do
    say "removing old backup $(basename "$d")"
    rm -rf "$d"
  done
}

# a volume as tar.gz on stdout; tar's exit 1 ("socket ignored", "file changed as we read it" in the
# memory of a running agent) is a warning, not a failed backup (field-fixes-0-7)
save_volume() {
  docker run --rm -v "$1:/v:ro" "$(pg_image)" sh -c 'tar -C /v -czf - .; rc=$?; [ "$rc" -le 1 ]'
}

do_backup() {  # kind (regular|pre-update|safety) → backup path on stdout
  kind="$1"
  wait_db || die "database is not responding: cannot take a backup (yorath doctor)"
  mkdir -p "$BACKUPS"; chmod 700 "$BACKUPS"
  need=$(( $(estimate_kb) * 2 ))
  free="$(df -Pk "$BACKUPS" | awk 'NR==2 {print $4}')"
  [ "$free" -ge "$need" ] || die "not enough space for a backup: need $((need / 1024)) MB, $((free / 1024)) MB free in $BACKUPS"
  version="$(current_version)"
  created="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
  name="$(date -u +%Y%m%dT%H%M%SZ)-$version"
  [ "$kind" = "regular" ] || name="$name-$kind"
  dir="$BACKUPS/$name"
  mkdir -m 700 "$dir"
  say "backup $name: database"
  compose exec -T db pg_dump -U "$(db_user)" -d "$(db_name)" -Fc > "$dir/db.dump" \
    || { rm -rf "$dir"; die "pg_dump failed"; }
  for v in $VOLUMES; do
    docker volume inspect "${PROJECT}_$v" >/dev/null 2>&1 || continue
    say "backup $name: volume $v"
    save_volume "${PROJECT}_$v" > "$dir/$v.tar.gz" || { rm -rf "$dir"; die "could not save volume $v"; }
  done
  for v in $(agent_volumes); do  # connected agent memory: file volume-<volume name>.tar.gz
    say "backup $name: agent ${v#yorath-agent-}"
    save_volume "$v" > "$dir/volume-$v.tar.gz" || { rm -rf "$dir"; die "could not save volume $v"; }
  done
  cp "$env_local" "$dir/env.local"
  [ -d installer/tls ] && tar -C installer -czf "$dir/tls.tar.gz" tls
  {
    echo "VERSION=$version"
    echo "SCHEMA_VERSION=$(manifest_field schema_version < distribution.yaml)"
    echo "GIT_REF=$(git rev-parse HEAD)"
    echo "CREATED_AT=$created"
    echo "KIND=$kind"
    echo "SERVICES=$(running_services)"
  } > "$dir/meta.env"
  (cd "$dir" && find . -maxdepth 1 -type f ! -name SHA256SUMS | sed 's#^\./##' | sort | xargs sha256sum > SHA256SUMS)
  chmod 600 "$dir"/*
  rotate
  say "backup ready: $dir ($(du -sh "$dir" | cut -f1))"
  echo "$dir"
}

# --- restore ------------------------------------------------------------------------------
changes_since() {  # JSON, or empty if the core does not respond
  compose exec -T core yorath changes-since "$1" 2>/dev/null | tail -1 | grep '^{' || true
}

stop_all_but_db() {  # leave the node proxy alone too: without it the rules would cut the node off (image builds)
  docker ps --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.ID}} {{.Label "com.docker.compose.service"}}' | while read -r id svc; do
    case "$svc" in db|egress) ;; *) docker stop -t 30 "$id" >/dev/null ;; esac
  done
}

do_restore() {  # backup reason(restore|rollback|failed-update)
  dir="$1"; reason="$2"
  [ -f "$dir/meta.env" ] && [ -f "$dir/SHA256SUMS" ] || die "$dir is not a yorath backup"
  say "verifying checksums"
  (cd "$dir" && sha256sum -c --quiet SHA256SUMS) || die "backup $dir is corrupted: cannot restore"
  version_before="$(current_version)"
  version_after="$(meta_get "$dir" VERSION)"
  backup_at="$(meta_get "$dir" CREATED_AT)"
  replaced="$(changes_since "$backup_at")"
  if [ -n "$replaced" ]; then
    say "created after the backup ($backup_at), will be replaced: $(echo "$replaced" | human_changes)"
  else
    warn "core is not responding: cannot count the data created after $backup_at"
    replaced="{}"
  fi
  confirm "Restore $version_after from backup $(basename "$dir")? Data created after the backup will survive only in the safety backup."
  safety="$(do_backup safety)"
  services="$(meta_get "$dir" SERVICES)"
  say "stopping services"
  stop_all_but_db
  stop_agents
  if [ "$(git rev-parse HEAD)" != "$(meta_get "$dir" GIT_REF)" ]; then
    say "sources: $(meta_get "$dir" GIT_REF | cut -c1-12) ($version_after)"
    git checkout --quiet "$(meta_get "$dir" GIT_REF)"
  fi
  # rolling back to a version without egress control (before 0.2.7): its proxy will not start, and the rules
  # would close the node's HTTP/HTTPS, so remove them and the service
  if [ ! -f installer/egress/yorath.nft ] && nft list table inet yorath >/dev/null 2>&1; then
    say "version $version_after has no node egress control: removing the $PRODUCT rules"
    [ -d /run/systemd/system ] && systemctl disable --now yorath-egress >/dev/null 2>&1 || :
    nft delete table inet yorath 2>/dev/null || :
    rm -f /etc/systemd/system/yorath-egress.service "$STATE_DIR/egress.sum" "$STATE_DIR/egress.alarm"
    docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=egress" \
      | xargs -r docker stop >/dev/null
  fi
  cp "$dir/env.local" "$env_local"; chmod 600 "$env_local"
  [ -f "$dir/tls.tar.gz" ] && { rm -rf installer/tls; tar -C installer -xzf "$dir/tls.tar.gz"; }
  python3 installer/render-env.py >/dev/null
  wait_db || die "database did not come up"
  say "database: replacing with the backup contents"
  u="$(db_user)"; n="$(db_name)"
  compose exec -T db psql -q -U "$u" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$n\" WITH (FORCE)" -c "CREATE DATABASE \"$n\" OWNER \"$u\"" >/dev/null
  compose exec -T db pg_restore -U "$u" -d "$n" --no-owner --single-transaction --exit-on-error < "$dir/db.dump" \
    || die "pg_restore failed; safety backup: $safety"
  for v in $VOLUMES; do
    [ -f "$dir/$v.tar.gz" ] || continue
    say "volume $v"
    docker volume create "${PROJECT}_$v" >/dev/null
    docker run --rm -i -v "${PROJECT}_$v:/v" "$(pg_image)" \
      sh -c 'find /v -mindepth 1 -delete && tar -C /v -xzf -' < "$dir/$v.tar.gz"
  done
  for f in "$dir"/volume-yorath-agent-*.tar.gz; do
    [ -f "$f" ] || continue
    v="$(basename "$f" .tar.gz)"; v="${v#volume-}"
    say "agent ${v#yorath-agent-}: memory from the backup"
    docker volume create "$v" >/dev/null
    docker run --rm -i -v "$v:/v" "$(pg_image)" \
      sh -c 'find /v -mindepth 1 -delete && tar -C /v -xzf -' < "$f"
  done
  say "starting $version_after"
  # shellcheck disable=SC2086
  start_services $services || warn "services started with errors, waiting for the core"
  wait_ready "$version_after" || die "core $version_after did not come up within $READY_TIMEOUT s: yorath doctor, yorath diag; safety backup: $safety"
  compose exec -T core yorath record-restore --backup-at "$backup_at" \
    --version-before "$version_before" --version-after "$version_after" \
    --replaced "$replaced" --safety-backup "$safety" --reason "$reason" >/dev/null \
    || warn "installation.restored event was not recorded"
  state_set CURRENT_VERSION "$version_after"
  say "restored: $version_after; safety backup of the previous state: $safety"
}

resolve_backup() {
  case "$1" in
    /*) echo "$1" ;;
    *) echo "$BACKUPS/$1" ;;
  esac
}

# --- commands -----------------------------------------------------------------------------
# How to open the panel from your computer: certificate, tunnel, URL with the code (until the first sign-in).
# Printed by yorath status, yorath open and install.sh at the end of the install.
cmd_open() {
  ip="$(local_get YORATH_PUBLIC_IP)"; ip="${ip:-<server IP>}"
  user="${SUDO_USER:-$(id -un)}"
  mode="$(env_get YORATH_TLS_MODE)"
  site="$(env_get YORATH_SITE)"; site="${site%%,*}"
  port="$(local_get YORATH_WEB_PORT)"; port="${port:-8080}"
  code="$(local_get YORATH_SETUP_CODE)"
  first="$(core_get /auth/me 2>/dev/null | json_field setup_required 2>/dev/null || echo "")"
  query=""; [ "$first" = "true" ] && query="/?code=$code"
  case "$mode" in
    off) url="http://$ip:$port"; ports="port $port on the server is closed"
      tunnel="ssh -L 8080:localhost:$port $user@$ip"; local_url="http://localhost:8080" ;;
    internal) url="https://$site"; ports="ports 80 and 443 on the server are closed"
      tunnel="ssh -L 8443:localhost:443 $user@$ip"; local_url="https://localhost:8443" ;;
    *) url="https://$site"; ports=""; tunnel=""; local_url="" ;;  # Let's Encrypt and own files: by name only
  esac
  n=1
  echo
  echo "How to open $PRODUCT in a browser (run the commands on your computer):"
  if [ "$mode" = "internal" ]; then
    echo "  $n. The $PRODUCT certificate, once:"
    echo "       scp $user@$ip:$YORATH_DIR/installer/yorath-root-ca.crt ~/"
    echo "       macOS:   sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/yorath-root-ca.crt"
    echo "       Windows: certutil -addstore -f Root yorath-root-ca.crt   (terminal as administrator)"
    echo "       Linux:   sudo cp ~/yorath-root-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates"
    echo "       Firefox keeps its own list: Settings → Certificates → Import."
    n=$((n + 1))
  fi
  if [ -n "$tunnel" ]; then
    echo "  $n. If $ports from outside, open a tunnel and keep the window open:"
    echo "       $tunnel"
    echo "     then open $local_url$query instead of the URL below"
    n=$((n + 1))
  fi
  echo "  $n. Open: $url$query"
  if [ -n "$query" ]; then
    echo "     code $code is needed only for the first sign-in: creating the owner"
  fi
  echo
}

cmd_status() {
  echo "version:      $(current_version) ($(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD))"
  echo "directory:    $YORATH_DIR"
  echo "services:     $(running_services)"
  echo "rollback to:  $(state_get PREVIOUS_VERSION) $( [ -n "$(state_get ROLLBACK_BACKUP)" ] && echo "(backup $(basename "$(state_get ROLLBACK_BACKUP)"))" || echo "— none")"
  echo "backups in $BACKUPS:"
  # shellcheck disable=SC2012
  ls -1d "$BACKUPS"/*/ 2>/dev/null | sed 's#/$##' | sort | while read -r d; do
    [ -f "$d/meta.env" ] && printf '  %-48s %-10s %s\n' "$(basename "$d")" "$(meta_get "$d" KIND)" "$(du -sh "$d" | cut -f1)"
  done
  cmd_open
}

cmd_update() {
  target=""; bundle=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --from) bundle="${2:-}"; shift 2 ;;
      --yes) shift ;;
      *) target="${1#v}"; shift ;;
    esac
  done
  [ -n "$target" ] || die "specify a version: yorath update 0.2.1"
  current="$(current_version)"
  if [ -n "$bundle" ]; then
    [ -r "$bundle" ] || die "no file $bundle"
    git fetch --quiet "$bundle" "refs/tags/v$target:refs/tags/v$target" || die "$bundle has no tag v$target"
  else
    git fetch --quiet --tags origin || die "could not fetch tags from origin (network, token), or use --from <bundle>"
  fi
  git rev-parse -q --verify "refs/tags/v$target^{commit}" >/dev/null || die "no tag v$target"
  tagged="$(git show "v$target:distribution.yaml" | manifest_field version)"
  [ "$tagged" = "$target" ] || die "tag v$target carries distribution.version $tagged"
  newer "$current" "$target" || die "version $target is not newer than the installed $current; to go back use yorath rollback"
  services="$(running_services)"
  backup="$(do_backup pre-update)"
  say "updating $current → $target"
  started="$(date +%s)"
  if update_steps "$target" "$services"; then
    state_set PREVIOUS_VERSION "$current"
    state_set ROLLBACK_BACKUP "$backup"
    state_set CURRENT_VERSION "$target"
    say "done: $target in $(( $(date +%s) - started )) s; rollback: yorath rollback"
    return 0
  fi
  failed_log="$STATE_DIR/failed-update-$target.log"
  compose logs --no-color --tail 200 core > "$failed_log" 2>&1 || true  # the log is gone after the restore
  warn "update to $target failed. Last core log lines:"
  tail -n 8 "$failed_log" >&2
  warn "restoring $current from $backup"
  YES=1 do_restore "$backup" failed-update
  die "update failed, the installation is back on $current. Core log of $target: $failed_log"
}

update_steps() {  # version services; no set -e inside if, so every step is checked explicitly
  git checkout --quiet "v$1" || return 1
  python3 installer/render-env.py >/dev/null || return 1
  # shellcheck disable=SC2086
  start_services $2 || return 1
  say "waiting for $1 to be ready (up to $READY_TIMEOUT s)"
  wait_ready "$1"
}

cmd_rollback() {
  backup="$(state_get ROLLBACK_BACKUP)"
  [ -n "$backup" ] || die "nothing to roll back: there were no updates or the rollback is already done"
  [ -d "$backup" ] || die "rollback backup $backup not found"
  say "rolling back $(current_version) → $(meta_get "$backup" VERSION)"
  do_restore "$backup" rollback
  state_set ROLLBACK_BACKUP ""
  state_set PREVIOUS_VERSION ""
}

cmd_restore() {
  [ $# -ge 1 ] || die "specify a backup: yorath restore <name> (list: yorath status)"
  dir="$(resolve_backup "$1")"
  [ -f "$dir/meta.env" ] || die "no backup $dir"
  [ "$(meta_get "$dir" VERSION)" = "$(current_version)" ] \
    || die "backup is of version $(meta_get "$dir" VERSION), installed is $(current_version): restore works only within the same version; to go back to the previous one use yorath rollback"
  do_restore "$dir" restore
}

# yorath scan timer every 5 minutes (systemd); installed by install and update. A node without systemd
# is checked only by yorath doctor and yorath scan.
install_units() {
  [ -d /run/systemd/system ] || return 0
  cp installer/systemd/yorath-scan.service installer/systemd/yorath-scan.timer /etc/systemd/system/
  systemctl daemon-reload && systemctl enable --now yorath-scan.timer >/dev/null 2>&1 || warn "yorath-scan timer was not enabled: systemctl status yorath-scan.timer"
}

# Node egress through Yorath (change node-egress, decision 36). Rules: installer/egress/yorath.nft,
# proxy: the egress service (Squid in the host network). The yorath-egress service loads the rules and watches:
# table removed → restored within 15 s; proxy down for a minute → alarm (traffic is blocked).
EGRESS_SUM="$STATE_DIR/egress.sum"
EGRESS_ALARM="$STATE_DIR/egress.alarm"
egress_load() {
  command -v nft >/dev/null 2>&1 || die "nft not found: apt-get install -y nftables"
  nft -f installer/egress/yorath.nft || return 1
  mkdir -p "$STATE_DIR"
  python3 installer/host_scan.py --egress-fingerprint > "$EGRESS_SUM"
}
egress_watch() {
  down=0
  while :; do
    if ! nft list table inet yorath >/dev/null 2>&1; then
      egress_load && echo "$(date -u +%FT%TZ) rules were removed and have been restored" > "$EGRESS_ALARM"
    fi
    if python3 -c 'import socket; socket.create_connection(("127.0.0.1", 3130), 2)' 2>/dev/null; then down=0; [ -f "$EGRESS_ALARM" ] && grep -q "proxy" "$EGRESS_ALARM" && rm -f "$EGRESS_ALARM"
    else
      down=$((down + 1))
      [ "$down" -ge 4 ] && echo "$(date -u +%FT%TZ) node proxy has not responded for a minute: node HTTP/HTTPS is blocked" > "$EGRESS_ALARM"
    fi
    sleep 15
  done
}
cmd_egress() {
  case "${1:-status}" in
    load) egress_load ;;
    watch) egress_watch ;;
    on)
      mkdir -p "$STATE_DIR"
      local_set YORATH_EGRESS_LOCKDOWN on
      compose up -d --build egress >> "$STATE_DIR/compose-up.log" 2>&1 || die "node proxy did not start: $STATE_DIR/compose-up.log"
      if [ -d /run/systemd/system ]; then
        cp installer/systemd/yorath-egress.service /etc/systemd/system/
        systemctl daemon-reload && systemctl enable --now yorath-egress >/dev/null 2>&1 \
          || die "yorath-egress service did not start: systemctl status yorath-egress"
        systemctl restart yorath-egress
      else
        egress_load || die "rules did not load"
        warn "node without systemd: the rules will not survive a reboot and nothing watches them; run yorath egress on after boot"
      fi
      say "node egress goes through $PRODUCT: HTTP/HTTPS via the logging proxy, everything else from containers is blocked"
      cmd_scan 2>/dev/null || : ;;
    off)
      [ -d /run/systemd/system ] && systemctl disable --now yorath-egress >/dev/null 2>&1 || :
      nft delete table inet yorath 2>/dev/null || :
      rm -f "$EGRESS_SUM" "$EGRESS_ALARM"
      local_set YORATH_EGRESS_LOCKDOWN off
      warn "egress control is off: node traffic bypasses $PRODUCT, and the panel shows this permanently"
      cmd_scan 2>/dev/null || : ;;
    status)
      cmd_scan 2>/dev/null || :
      python3 - "$REPORT" <<'PY'
import json, sys
e = json.load(open(sys.argv[1], encoding="utf-8")).get("egress", {})
print(f"mode:    {e.get('mode')}")
print(f"rules:   {'in place' if e.get('table') else 'none'}{'' if e.get('intact') or not e.get('table') else ', modified'}")
print(f"proxy:   {'responding' if e.get('proxy') else 'not responding'}")
print(f"service: {e.get('service') or '—'}")
if e.get("alarm"):
    print(f"alarm:   {e['alarm']}")
PY
      ;;
    *) die "yorath egress [status|on|off]" ;;
  esac
}

# Agents outside Yorath (decision 36): processes and containers of known agents, their directories.
cmd_scan() {
  # an update from 0.2.5 runs the old yorath.sh, so the first scan of the new version installs the timer
  [ -d /run/systemd/system ] && [ ! -f /etc/systemd/system/yorath-scan.timer ] && install_units
  python3 installer/host_scan.py --out "$REPORT" --state "$STATE_DIR" --env-local "$env_local" >/dev/null
}

# Print the scanner report for doctor: agents as warnings, other containers as info.
scan_lines() {
  python3 - "$REPORT" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
for a in r["agents"]:
    if a["where"] == "host":
        who = "as root" if a["root"] else f"as {a['user']}"
        print(f"agent\t{a['agent']}: on the host, pid {a['pid']}, {who}")
    else:
        print(f"agent\t{a['agent']}: container {a['container']} ({a['image']})")
running = {a["agent"] for a in r["agents"]}
for t in r["traces"]:
    if t["agent"] not in running:
        print(f"trace\t{t['path']} (owner {t['owner']})")
if r["containers"]:
    print("other\t" + ", ".join(c["name"] for c in r["containers"]))
PY
}

cmd_doctor() {
  fails=0
  ok() { printf '  \033[32mok\033[0m    %s\n' "$*"; }
  bad() { printf '  \033[31mfail\033[0m  %s\n' "$*"; fails=$((fails + 1)); }
  meh() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
  echo "$PRODUCT $(current_version), $YORATH_DIR"
  docker info >/dev/null 2>&1 && ok "Docker is running" || bad "Docker is not responding → systemctl status docker"
  running=" $(running_services) "
  for svc in db ollama core web; do
    case "$running" in
      *" $svc "*) ok "service $svc is running" ;;
      *) bad "service $svc is not running → docker compose --project-directory $YORATH_DIR/installer logs --tail 50 $svc" ;;
    esac
  done
  if core_get /health >/dev/null 2>&1; then
    ok "core: /health"
    match="$(core_get /version 2>/dev/null | json_field schema_matches_manifest)"
    [ "$match" = "true" ] && ok "database schema matches the manifest" || bad "database schema does not match the manifest → core logs: migrations"
  else
    bad "core does not respond on /health → yorath diag"
  fi
  free_mb=$(( $(df -Pk /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}') / 1024 ))
  if [ "$free_mb" -lt 2048 ]; then bad "$free_mb MB free on /var/lib/docker → docker system prune, remove old backups"
  elif [ "$free_mb" -lt 5120 ]; then meh "$free_mb MB free on /var/lib/docker"
  else ok "disk: $free_mb MB free"; fi
  # shellcheck disable=SC2012
  last="$(ls -1d "$BACKUPS"/*/ 2>/dev/null | sed 's#/$##' | sort | tail -1)"
  if [ -z "$last" ]; then meh "no backups → yorath backup"
  else
    age=$(( ($(date +%s) - $(stat -c %Y "$last")) / 86400 ))
    [ "$age" -le 7 ] && ok "last backup $(basename "$last"), $age days old" || meh "last backup is $age days old → yorath backup"
  fi
  safety_kb=0
  for sd in "$BACKUPS"/*-safety; do [ -d "$sd" ] && safety_kb=$(( safety_kb + $(du -sk "$sd" | cut -f1) )); done
  [ "$safety_kb" -eq 0 ] || meh "safety backups take $((safety_kb / 1024 + 1)) MB: remove them by hand once you are sure they are not needed"
  if [ "$(local_get YORATH_TLS | tr '[:upper:]' '[:lower:]')" != "off" ]; then
    site="$(env_get YORATH_SITE)"; site="${site%%,*}"
    end="$(echo | timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$site" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
    if [ -z "$end" ]; then bad "no certificate on :443 → web logs (Caddy)"
    elif [ "$(env_get YORATH_TLS_MODE)" = "internal" ]; then
      # the internal CA issues a 12 h certificate and renews it itself: all that matters is that it is valid
      [ "$(date -d "$end" +%s)" -gt "$(date +%s)" ] && ok "certificate for $site is valid (internal CA, renews itself)" \
        || bad "certificate for $site has expired → web logs (Caddy)"
    else
      days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
      [ "$days" -ge 14 ] && ok "certificate for $site valid for $days more days" || meh "certificate for $site expires in $days days → web logs (Caddy)"
    fi
  fi
  if core_get /health >/dev/null 2>&1; then  # external services as the core sees them (through its proxy)
    reach="$(compose exec -T core python -c '
import httpx
for name, method, url in (("Tavily", "POST", "https://api.tavily.com/search"), ("OpenRouter", "GET", "https://openrouter.ai/api/v1/models")):
    try:
        r = httpx.request(method, url, json={"query": "t"} if method == "POST" else None, headers={"Authorization": "Bearer yorath-doctor"}, timeout=10)
        print(name, r.status_code)
    except Exception as e:
        print(name, type(e).__name__)
' 2>/dev/null </dev/null || true)"
    via=""; [ -n "$(local_get YORATH_EGRESS_PROXY)" ] && via=" via proxy"
    echo "$reach" | while read -r name code; do
      [ -n "$name" ] || continue
      case "$code" in
        403) meh "$name blocks this server$via (403 for any key means a country block) → YORATH_EGRESS_PROXY, yorath.io/docs/operate/web-search-behind-a-proxy/" ;;
        [0-9]*) ok "$name is reachable$via" ;;
        *) meh "$name is unreachable$via: $code" ;;
      esac
    done
  fi
  case "$running" in
    *" sandbox-runner "*)
      docker info 2>/dev/null | grep -q runsc && ok "sandbox runtime runsc" \
        || bad "runsc is not registered in Docker → docs/agents/developer-agent.md" ;;
  esac
  if cmd_scan 2>/dev/null; then
    egress="$(python3 -c 'import json,sys; e=json.load(open(sys.argv[1]))["egress"]; print(e["mode"], int(e["table"]), int(e["intact"]), int(e["proxy"]), e.get("service") or "-")' "$REPORT")"
    # shellcheck disable=SC2086  # mode and flags are separate words
    set -- $egress
    case "$1" in
      off) meh "egress control is off (YORATH_EGRESS_LOCKDOWN=off): node traffic bypasses $PRODUCT → yorath egress on" ;;
      unset) meh "node egress is not controlled by $PRODUCT → yorath egress on (decision 36)" ;;
      *)
        if [ "$2" = 0 ]; then bad "egress rules were removed: node traffic bypasses $PRODUCT → yorath egress on"
        elif [ "$3" = 0 ]; then bad "egress rules were modified by hand → yorath egress on"
        elif [ "$4" = 0 ]; then bad "node proxy is not responding: node HTTP/HTTPS is blocked → docker compose ... logs egress; way out: yorath egress off"
        else ok "node egress goes through $PRODUCT: rules in place, proxy responding"; fi
        [ "$5" = "-" ] || [ "$5" = "active" ] || bad "yorath-egress service: $5 → systemctl status yorath-egress" ;;
    esac
    found="$(scan_lines)"
    if echo "$found" | grep -q "^agent"; then
      meh "agents outside $PRODUCT: $PRODUCT does not control them (network, files, Docker):"
      echo "$found" | sed -n 's/^agent\t/          /p'
      echo "          → move them into $PRODUCT or at least do not run them as root: yorath.io/docs/operate/server-network/"
    else
      ok "no agents outside $PRODUCT found"
    fi
    echo "$found" | grep -q "^trace" && { echo "  info  agent directories with no running process:"; echo "$found" | sed -n 's/^trace\t/          /p'; }
    echo "$found" | sed -n "s/^other\t/  info  other containers not from $PRODUCT: /p"
  else
    meh "check for agents outside $PRODUCT failed → yorath scan"
  fi
  doctor_agents
  [ "$fails" -eq 0 ] && echo "result: all good" || echo "result: $fails problems"
  return "$fails"
}

doctor_agents() {  # connected agents (change connected-agents): the node service and containers
  case "$running" in *" core "*) ;; *) return 0 ;; esac
  list="$(compose exec -T core yorath agent list < /dev/null 2>/dev/null | tail -n +2 || true)"
  echo "$list" | grep -q " yes " || return 0  # no agent connected, nothing to check
  case "$running" in
    *" node-agent "*) ok "node service node-agent is running" ;;
    *) bad "node service node-agent is not running: agents cannot start → docker compose ... up -d node-agent" ;;
  esac
  while read -r name connected node rest; do  # not in a pipeline: bad must count failures in this shell
    [ "$connected" = "yes" ] || continue
    if [ "$node" != "main" ]; then  # agent on a worker node: status comes from the node service report
      case "$rest" in
        *" running "*) ok "agent $name is running on node $node" ;;
        *) bad "agent $name on node $node is not running → yorath agent list, yorath node list" ;;
      esac
      continue
    fi
    if docker ps --filter "name=^yorath-agent-$name\$" --filter status=running -q | grep -q .; then
      case "$rest" in
        *"not verified"*) meh "agent $name runs a version not verified with $PRODUCT $(current_version) → yorath agent upgrade $name" ;;
        *) ok "agent $name is running" ;;
      esac
    else
      bad "agent $name is connected, but its container is not running → yorath agent logs $name; yorath agent list"
    fi
  done <<AGENTS
$list
AGENTS
}

cmd_diag() {
  out_dir="$STATE_DIR/diag"; mkdir -p "$out_dir"; chmod 700 "$out_dir"
  name="yorath-diag-$(date -u +%Y%m%dT%H%M%SZ)"
  work="$(mktemp -d)"; diag_dir="$work/$name"; mkdir "$diag_dir"
  say "collecting $name"
  # in a subshell: the status and doctor variables do not leak into diag_dir
  ( cmd_status ) > "$diag_dir/status.txt" 2>&1 || true
  ( cmd_doctor ) > "$diag_dir/doctor.txt" 2>&1 || true
  core_get /version > "$diag_dir/version.json" 2>/dev/null || echo "core did not respond" > "$diag_dir/version.json"
  docker version > "$diag_dir/docker-version.txt" 2>&1 || true
  compose ps -a > "$diag_dir/compose-ps.txt" 2>&1 || true
  { uname -a; df -h; free -m; } > "$diag_dir/host.txt" 2>&1 || true
  sed 's/=.*//' "$env_local" > "$diag_dir/env-local-keys.txt"
  for svc in $(compose config --services 2>/dev/null); do
    compose logs --no-color --tail 500 "$svc" > "$diag_dir/log-$svc.txt" 2>&1 || true
  done
  python3 - "$env_local" "$diag_dir" <<'PY'
import pathlib, re, sys
env, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
secret_keys = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|CODE", re.I)
values = []
for line in env.read_text(encoding="utf-8").splitlines():
    k, _, v = line.partition("=")
    v = v.strip().strip('"')
    if secret_keys.search(k) and len(v) >= 6 and not v.startswith(("installer/", "/")):
        values.append(v)
patterns = [re.compile(p) for p in (
    r"yorath_key_[A-Za-z0-9_\-]+", r"sk-or-[A-Za-z0-9_\-]+", r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]+", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
)]
for f in sorted(out.glob("*.txt")) + sorted(out.glob("*.json")):
    text = f.read_text(encoding="utf-8", errors="replace")
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, "***")
    for p in patterns:
        text = p.sub("***", text)
    f.write_text(text, encoding="utf-8")
PY
  tar -C "$work" -czf "$out_dir/$name.tar.gz" "$name"
  rm -rf "$work"
  chmod 600 "$out_dir/$name.tar.gz"
  say "archive: $out_dir/$name.tar.gz"
  warn "keys and passwords are masked, but core logs may contain the text of user requests: review the archive before sending it"
}

cmd_agent() {  # connected agents (change connected-agents): same as the My Agents buttons
  running="$(compose ps --services --status running 2>/dev/null || true)"
  echo "$running" | grep -qx core || die "core is not running → yorath doctor"
  sub="${1:-list}"; [ $# -gt 0 ] && shift
  case "$sub" in
    logs)
      [ -n "${1:-}" ] || die "yorath agent logs <name>"
      docker logs --tail "${2:-200}" "yorath-agent-$1" ;;
    add|telegram)
      need_token=0
      for a in "$@"; do [ "$a" = "--telegram" ] && need_token=1; done
      [ "$sub" = "telegram" ] && need_token=1
      for a in "$@"; do [ "$a" = "--off" ] && need_token=0; done
      if [ "$need_token" = 1 ]; then
        # the bot token is not an argument: file YORATH_TELEGRAM_TOKEN_FILE or input without echo
        if [ -n "${YORATH_TELEGRAM_TOKEN_FILE:-}" ]; then
          token="$(head -n1 "$YORATH_TELEGRAM_TOKEN_FILE")"
        else
          printf 'Telegram bot token (input hidden): ' >&2
          stty -echo 2>/dev/null || :; read -r token < /dev/tty || token=""; stty echo 2>/dev/null || :
          echo >&2
        fi
        printf '%s\n' "$token" | compose exec -T core yorath agent "$sub" "$@"
      else
        compose exec -T core yorath agent "$sub" "$@" < /dev/null
      fi ;;
    *) compose exec -T core yorath agent "$sub" "$@" < /dev/null ;;
  esac
}

cmd_help() {  # the lines between the help markers at the top of this file
  sed -n '/^# help:begin/,/^# help:end/p' "$YORATH_SELF_COPY" | sed '1d;$d' | sed 's/^# \{0,1\}//'
}

# --- cluster (change cluster-nodes, decision 35) ----------------------------------------------
WG_DIR=/var/lib/yorath/wg
WG_CONF=/etc/wireguard/yorath-wg.conf
CLUSTER_DIR=installer/.cluster

wg_up() {  # yorath-wg tunnel from $WG_CONF
  if [ -d /run/systemd/system ]; then
    systemctl enable --now wg-quick@yorath-wg >/dev/null 2>&1 || systemctl restart wg-quick@yorath-wg
  else
    ip link show yorath-wg >/dev/null 2>&1 || wg-quick up yorath-wg >/dev/null
  fi
}

cluster_main() {  # main node: key, interface 10.88.0.1, core in the tunnel, peers from the database
  command -v wg >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 install -y -qq wireguard-tools >/dev/null
  mkdir -p "$WG_DIR" "$CLUSTER_DIR"; chmod 700 "$WG_DIR"
  [ -s "$WG_DIR/main.key" ] || (umask 077; wg genkey > "$WG_DIR/main.key")
  if [ ! -f "$WG_CONF" ]; then
    (umask 077; printf '# Yorath: cluster main node (change cluster-nodes). Peers: yorath node sync.\n[Interface]\nAddress = 10.88.0.1/24\nListenPort = 51820\nPrivateKey = %s\n' "$(cat "$WG_DIR/main.key")" > "$WG_CONF")
  fi
  wg_up
  files="$(local_get YORATH_COMPOSE_FILES)"
  case "$files" in
    *compose.cluster.yaml*) ;;
    *) local_set YORATH_COMPOSE_FILES "\"${files:--f installer/compose.yaml} -f installer/compose.cluster.yaml\""
       python3 installer/render-env.py >/dev/null
       say "the core will be reachable for nodes in the tunnel: 10.88.0.1:8000"
       compose up -d core >> "$STATE_DIR/compose-up.log" 2>&1 || die "core did not restart: $STATE_DIR/compose-up.log"
       wait_ready "$(current_version)" || die "core did not come up → yorath doctor" ;;
  esac
  cluster_units
  node_sync
}

# Peers live in peers.conf, not in $WG_CONF: after a reboot wg-quick brings the interface up without them.
# yorath-wg-sync.service is enabled at boot (after wg-quick) and on peers.conf changes (path unit).
# Field run 13.11.2026: without this the main node kept all nodes lost after a reboot.
cluster_units() {
  [ -d /run/systemd/system ] && [ -f "$WG_CONF" ] || return 0
  want_path="$(sed "s#@YORATH_DIR@#$YORATH_DIR#" installer/systemd/yorath-wg-sync.path)"
  if [ "$want_path" != "$(cat /etc/systemd/system/yorath-wg-sync.path 2>/dev/null)" ] \
    || ! cmp -s installer/systemd/yorath-wg-sync.service /etc/systemd/system/yorath-wg-sync.service; then
    printf '%s\n' "$want_path" > /etc/systemd/system/yorath-wg-sync.path
    cp installer/systemd/yorath-wg-sync.service /etc/systemd/system/
    systemctl daemon-reload
  fi
  systemctl enable yorath-wg-sync.service >/dev/null 2>&1 || :
  systemctl enable --now yorath-wg-sync.path >/dev/null 2>&1 || :
}

node_sync() {  # peers from installer/.cluster/peers.conf (written by the core) → interface without dropping tunnels
  [ -f "$WG_CONF" ] || return 0
  tmp="$(mktemp)"; chmod 600 "$tmp"
  wg-quick strip yorath-wg > "$tmp"
  [ -f "$CLUSTER_DIR/peers.conf" ] && cat "$CLUSTER_DIR/peers.conf" >> "$tmp"
  wg syncconf yorath-wg "$tmp"; rm -f "$tmp"
}

main_url() {  # the address worker nodes use to reach the main node: the same as the panel
  mode="$(env_get YORATH_TLS_MODE)"
  if [ "$mode" = "off" ]; then
    ip="$(local_get YORATH_PUBLIC_IP)"; port="$(local_get YORATH_WEB_PORT)"
    echo "http://${ip:-<main node address>}:${port:-8080}"
  else
    site="$(env_get YORATH_SITE)"; echo "https://${site%%,*}"
  fi
}

cmd_node() {
  sub="${1:-list}"; [ $# -gt 0 ] && shift
  [ "$(local_get YORATH_ROLE)" = "worker" ] && case "$sub" in
    status) wg show yorath-wg; return ;;
    *) die "this is a worker node: nodes are managed by the main node (yorath node $sub on the main node)" ;;
  esac
  case "$sub" in
    token)
      cluster_main
      endpoint="${YORATH_CLUSTER_ENDPOINT:-$(local_get YORATH_CLUSTER_ENDPOINT)}"
      [ -n "$endpoint" ] || endpoint="$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p'):51820"
      local_set YORATH_CLUSTER_ENDPOINT "$endpoint"
      fp="-"
      [ -f installer/yorath-root-ca.crt ] && fp="$(openssl x509 -in installer/yorath-root-ca.crt -outform der | sha256sum | cut -c1-64)"
      main_url="$(main_url)"
      echo "On the new server (Ubuntu 24.04, as root):"
      clone_hint "$(git -C "$YORATH_DIR" remote get-url origin 2>/dev/null || true)"
      echo "  2. Join the cluster:"
      compose exec -T core yorath node token --endpoint "$endpoint" --url "$main_url" \
        --main-key "$(wg pubkey < "$WG_DIR/main.key")" --ca-fingerprint "$fp" < /dev/null ;;
    sync) node_sync ;;
    approve|revoke)
      compose exec -T core yorath node "$sub" "$@" < /dev/null && node_sync ;;
    status) wg show yorath-wg 2>/dev/null || echo "cluster tunnel is not up: yorath node token" ;;
    *) compose exec -T core yorath node "$sub" "$@" < /dev/null ;;
  esac
}

cmd_model() {  # the same pull as the wizard; rights belong to root on the node, no owner password needed
  [ "$(local_get YORATH_ROLE)" = "worker" ] \
    && die "this is a worker node: models are placed from the main node (yorath model pull <name> --node <this node>)"
  running="$(compose ps --services --status running 2>/dev/null || true)"
  echo "$running" | grep -qx core || die "core is not running → yorath doctor"
  compose exec -T core yorath model "$@"
}

# --- argument parsing ---------------------------------------------------------------------
[ -z "${YORATH_SOURCE_ONLY:-}" ] || return 0
[ "$(id -u)" -eq 0 ] || die "run as root (sudo yorath ...)"
cmd="${1:-status}"; [ $# -gt 0 ] && shift
YES=0
for a in "$@"; do [ "$a" = "--yes" ] && YES=1; done
args=""
for a in "$@"; do [ "$a" = "--yes" ] || args="$args $a"; done
# shellcheck disable=SC2086
set -- $args
case "$cmd" in
  status) cmd_status ;;
  open) cmd_open ;;
  scan) cmd_scan ;;
  egress) cmd_egress "$@" ;;
  backup) do_backup regular >/dev/null ;;
  update) cmd_update "$@" ;;
  rollback) cmd_rollback ;;
  restore) cmd_restore "$@" ;;
  doctor) cmd_doctor ;;
  diag) cmd_diag ;;
  model) cmd_model "$@" ;;
  agent) cmd_agent "$@" ;;
  node) cmd_node "$@" ;;
  -h|--help|help) cmd_help ;;
  *) die "unknown command $cmd (yorath help)" ;;
esac
