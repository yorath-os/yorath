#!/bin/sh
# Change translate-english (decision 38): product files contain no Cyrillic.
# Searches every tracked file; extra git pathspecs (for example ':!notes/') may be passed as arguments
# to leave out places that are not part of the product.
#   ./scripts/check-lang.sh [pathspec...]      (make check-lang)
set -eu
cd "$(dirname "$0")/.."
if git grep -nIP '[\x{0400}-\x{04FF}]' -- . "$@"; then
  echo "check-lang: Cyrillic found in product files (lines above); translate to English" >&2
  exit 1
fi
echo "check-lang: ok"
