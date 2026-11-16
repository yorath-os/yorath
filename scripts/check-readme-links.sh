#!/bin/sh
# After the public export of a release (public-readme 6.4): every link in README.md answers 200.
# A link that doesn't marks the release failed. Run by the release step: sh scripts/check-readme-links.sh
set -u
cd "$(dirname "$0")/.."
bad=0
for url in $(grep -oE 'https://[^ )"<>]+' README.md | sed 's/[.,;]$//' | sort -u); do
  case "$url" in *'<'*|*'…'*) continue ;; esac            # placeholders such as https://<IP>/v1
  code="$(curl -s -o /dev/null -L --max-time 20 -w '%{http_code}' "$url")"
  if [ "$code" = 200 ]; then echo "ok   $url"; else echo "FAIL $code $url"; bad=$((bad + 1)); fi
done
[ "$bad" -eq 0 ] && echo "check-readme-links: ok" || { echo "check-readme-links: $bad link(s) failed"; exit 1; }
