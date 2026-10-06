#!/usr/bin/env bash
# platform-parent repeats three versions that live in platform-dependencies (a BOM import brings no
# properties). Fail when they drift.
set -euo pipefail
cd "$(dirname "$0")/.."
prop() { sed -n "s|^ *<$2>\(.*\)</$2>.*|\1|p" "$1" | head -1; }
status=0
for p in lombok.version mapstruct.version spring-boot.version; do
  a="$(prop platform-dependencies/pom.xml "$p")"; b="$(prop platform-parent/pom.xml "$p")"
  if [ -z "$a" ] || [ "$a" != "$b" ]; then
    echo "::error::$p differs: platform-dependencies=${a:-unset}, platform-parent=${b:-unset}"; status=1
  fi
done
[ "$status" -eq 0 ] && echo "platform-parent and platform-dependencies agree on lombok, mapstruct and spring-boot."
exit "$status"
