#!/bin/sh
# End-to-end check of the public install (change public-install 1.6, 5.1) on a CLEAN Ubuntu 24.04 x86_64
# server — run as root on a fresh server, after get.yorath.io serves the release:
#   curl -fsSL https://raw.githubusercontent.com/yorath-os/yorath/main/installer/e2e/public-install.sh | sh -s -- v0.6.0
# Checks: a non-root user is refused with a ready command; a version that doesn't exist is refused before
# anything is installed; the plain command installs exactly the published release; the time from the
# command to the wizard link and to the first answer is recorded (launch condition 4).
set -u
EXPECT="${1:?the release that get.yorath.io should install, e.g. v0.6.0}"
URL="${YORATH_GET_URL:-https://get.yorath.io}"
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails+1)); fi; }

[ "$(id -u)" -eq 0 ] || { echo "run as root on a clean server"; exit 2; }
[ ! -e /opt/yorath ] || { echo "/opt/yorath exists: this is not a clean server"; exit 2; }

echo "=== the published script carries the release tag"
check "get.yorath.io names $EXPECT" "curl -fsSL $URL | grep -q '^YORATH_RELEASE_TAG=\"$EXPECT\"'"

echo "=== not root"
id e2e >/dev/null 2>&1 || useradd -m e2e
out="$(su e2e -c "curl -fsSL $URL | sh" 2>&1)"
check "a normal user is refused with sudo in the command" "echo \"\$out\" | grep -q '| sudo sh'"

echo "=== a version that doesn't exist"
out="$(curl -fsSL $URL | YORATH_VERSION=v9.9.9 sh 2>&1)"
check "v9.9.9 is refused, naming it" "echo \"\$out\" | grep -q 'version v9.9.9 not found'"
check "nothing was installed" "[ ! -e /opt/yorath ] && ! command -v docker >/dev/null"

echo "=== the plain command"
t0=$(date +%s)
curl -fsSL $URL | sh 2>&1 | tee /root/e2e-install.log
t1=$(date +%s)
check "the release $EXPECT is installed" "[ \"\$(git -C /opt/yorath describe --tags --exact-match)\" = $EXPECT ]"
check "yorath status shows ${EXPECT#v}" "yorath status | grep -q '${EXPECT#v}'"
check "the output has the wizard link with a code" "grep -q '?code=' /root/e2e-install.log"
echo "TIME install_to_wizard_link=$((t1 - t0))s (cpu=$(nproc) ram_gb=$(free -g | awk '/Mem/{print $2}') gpu=$(nvidia-smi -L 2>/dev/null | wc -l))"

echo "=== re-running keeps the version"
curl -fsSL $URL | YORATH_VERSION=v0.0.1 sh >/dev/null 2>&1
check "a different YORATH_VERSION on an installed server points to yorath update" "curl -fsSL $URL | YORATH_VERSION=v0.0.1 sh 2>&1 | grep -q 'yorath update'"

[ "$fails" -eq 0 ] && echo "public-install: all passed" || { echo "public-install: $fails failed"; exit 1; }
