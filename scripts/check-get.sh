#!/bin/sh
# After a release (change public-install 2.2): the script at get.yorath.io installs exactly that release.
#   sh scripts/check-get.sh v0.6.0 [https://get.yorath.io]
set -eu
tag="${1:?release tag, e.g. v0.6.0}"
url="${2:-https://get.yorath.io}"
body="$(curl -fsSL --max-time 20 "$url")" || { echo "check-get: $url does not answer" >&2; exit 1; }
got="$(printf '%s\n' "$body" | sed -n 's/^YORATH_RELEASE_TAG="\([^"]*\)".*/\1/p' | head -1)"
[ "$got" = "$tag" ] || { echo "check-get: $url installs [${got:-nothing}], expected $tag" >&2; exit 1; }
printf '%s\n' "$body" | head -1 | grep -q '^#!/bin/sh' || { echo "check-get: $url is not the install script" >&2; exit 1; }
echo "check-get: $url installs $tag"
