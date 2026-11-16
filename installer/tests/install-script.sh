#!/bin/sh
# Tests of installer/install.sh that need neither root nor network nor Docker (change public-install 1.1–1.5).
# A local bare repository with tags stands in for github.com/yorath-os/yorath.
#   sh installer/tests/install-script.sh
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/installer/install.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fails=0
ok()   { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n     %s\n' "$1" "$2"; fails=$((fails + 1)); }
expect() { # name, actual, expected
  if [ "$2" = "$3" ]; then ok "$1"; else fail "$1" "expected [$3], got [$2]"; fi
}
contains() { # name, text, substring
  case "$2" in *"$3"*) ok "$1" ;; *) fail "$1" "no [$3] in: $2" ;; esac
}

# a repository with releases v0.5.1, v0.6.0, v0.10.0 and a main branch ahead of them
git init -q -b main "$T/src" && git -C "$T/src" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
for v in v0.5.1 v0.6.0 v0.10.0; do git -C "$T/src" tag "$v"; done
git -C "$T/src" -c user.email=t@t -c user.name=t commit -q --allow-empty -m dev
git clone -q --bare "$T/src" "$T/repo.git"

resolve() { # YORATH_VERSION given, stamped tag → prints the resolved version or the error
  ( YORATH_SOURCE_ONLY=1 . "$SCRIPT"
    YORATH_REPO="$T/repo.git"; yorath_version_given="$1"; YORATH_RELEASE_TAG="$2"; YORATH_VERSION=""
    resolve_version && echo "$YORATH_VERSION" ) 2>&1
}

expect "no version given: the latest release tag, by version order, not main" "$(resolve '' '')" "v0.10.0"
expect "the release stamped into the published copy wins over the latest tag" "$(resolve '' 'v0.6.0')" "v0.6.0"
expect "YORATH_VERSION picks another release" "$(resolve 'v0.5.1' 'v0.6.0')" "v0.5.1"
out="$(resolve 'v9.9.9' '')"
contains "a version the repository doesn't have is refused, naming it" "$out" "version v9.9.9 not found"
contains "the refusal points to the list of releases" "$out" "https://github.com/yorath-os/yorath/releases"

# the closing line names the version without the release tool's marker comment
printf 'distribution:\n  name: yorath\n  version: 0.6.0 # x-release-please-version\n' > "$T/distribution.yaml"
expect "the installed version is read without the marker comment" \
  "$( ( YORATH_SOURCE_ONLY=1 . "$SCRIPT"; release_version "$T/distribution.yaml" ) 2>&1)" "0.6.0"

# yorath node token: the commands for a new node are ready to paste (version without the marker comment)
mkdir -p "$T/inst" && printf 'distribution:\n  name: yorath\n  version: 0.7.3 # x-release-please-version\n' > "$T/inst/distribution.yaml"
hint() { ( export YORATH_SOURCE_ONLY=1 YORATH_DIR="$T/inst"; . "$ROOT/installer/yorath.sh"; clone_hint "$1" ) 2>&1; }
out="$(hint https://github.com/yorath-os/yorath.git)"
contains "a public repository: a plain clone of the same version" "$out" "git clone --branch v0.7.3 https://github.com/yorath-os/yorath.git /opt/yorath"
case "$out" in *x-release-please*|*token*) fail "no marker comment and no token step for a public repository" "$out" ;; *) ok "no marker comment and no token step for a public repository" ;; esac
out="$(hint https://ghp_secret123@github.com/yorath-os/private.git)"
contains "a private repository: the token goes into a variable first" "$out" 'T=<paste a read-only token here>'
contains "and the clone uses that variable" "$out" 'git clone --branch v0.7.3 "https://$T@github.com/yorath-os/private.git" /opt/yorath'
case "$out" in *ghp_secret123*) fail "the token of this server is never printed" "$out" ;; *) ok "the token of this server is never printed" ;; esac

# re-run on an installed server (R5): the installed copy's own installer runs, with the same arguments
mkdir -p "$T/old/installer" && printf 'distribution:\n  name: yorath\n  version: 0.6.0 # x-release-please-version\n' > "$T/old/distribution.yaml"
printf 'echo "installed copy runs: $* version=[${YORATH_VERSION:-}]"\n' > "$T/old/installer/install.sh"
handoff() { ( YORATH_SOURCE_ONLY=1 . "$SCRIPT"; YORATH_DIR="$T/old"; yorath_version_given="$1"; shift; handoff_installed "$@" ) 2>&1; }
out="$(handoff '' --join https://main tok)"
contains "a re-run hands over to the installed installer with the same arguments" "$out" "installed copy runs: --join https://main tok"
contains "and without a version of its own" "$out" "version=[]"
contains "0.6.0 and v0.6.0 are one version" "$(handoff 0.6.0)" "installed copy runs"
out="$(handoff v0.7.0)"
contains "another version is refused with the update command" "$out" "yorath update 0.7.0"
case "$out" in *"installed copy runs"*) fail "a refused re-run does not start the installed installer" "$out" ;; *) ok "a refused re-run does not start the installed installer" ;; esac

if [ "$(id -u)" -ne 0 ]; then
  # from a stream as a normal user: refused with the ready command with sudo, before anything else
  out="$(cd "$T" && sh < "$SCRIPT" 2>&1)"; code=$?
  expect "a stream run as a normal user exits with an error" "$code" "1"
  contains "it prints the same command with sudo" "$out" "curl -fsSL https://get.yorath.io | sudo sh"
  out="$(cd "$T" && YORATH_VERSION=v0.5.1 sh < "$SCRIPT" 2>&1)"
  contains "the sudo command keeps the chosen version" "$out" "| sudo YORATH_VERSION=v0.5.1 sh"
  # a clone in the working directory is not taken for ours when the script comes from a stream
  out="$(cd "$ROOT/installer" && sh < "$SCRIPT" 2>&1)"
  contains "a stream run inside a clone is still a stream run" "$out" "curl -fsSL https://get.yorath.io | sudo sh"
  # from the clone itself: the sudo command names the script
  out="$(cd "$ROOT" && sh installer/install.sh 2>&1)"
  contains "a clone run as a normal user prints sudo sh with the script path" "$out" "sudo sh installer/install.sh"
  # nothing reads the terminal: the stream run finishes with stdin closed (no prompt hangs)
  out="$(cd "$T" && sh -c "sh < '$SCRIPT'" </dev/null 2>&1)"; code=$?
  expect "a stream run never waits for input" "$code" "1"
else
  echo "skip: the non-root checks need a normal user"
fi
code_only="$(sed 's/#.*//' "$SCRIPT")"   # comments may say "read"; commands may not
if printf '%s\n' "$code_only" | grep -nE '(^|[^_a-z])read( |$)|/dev/tty' >/dev/null; then
  fail "install.sh reads from the terminal" "$(printf '%s\n' "$code_only" | grep -nE '(^|[^_a-z])read( |$)|/dev/tty')"
else ok "install.sh has no read and no /dev/tty"; fi

[ "$fails" -eq 0 ] && echo "install-script: all passed" || { echo "install-script: $fails failed"; exit 1; }
