#!/bin/sh
# T11 of the rename (change rename-yorath): the old product name does not come back.
# Searches for five spellings, case-insensitive, in all tracked files; extra git pathspecs passed as
# arguments leave out places where the name remains as history.
# The pattern is assembled from parts so this file does not match itself.
#   ./scripts/check-old-name.sh [pathspec...]      (make check-name)
set -eu
cd "$(dirname "$0")/.."
old="a""i[ _-]?os"
if git grep -nIiE "${old}" -- . "$@"; then
  echo "check-name: found the old name (lines above); replace it with Yorath / yorath" >&2
  exit 1
fi
echo "check-name: ok"
