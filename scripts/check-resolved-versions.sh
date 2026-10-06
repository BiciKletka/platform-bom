#!/usr/bin/env bash
# Asserts that the pins platform-dependencies exists for really land in what a service resolves.
# Usage: scripts/check-resolved-versions.sh   (after `mvn -Drevision=$REVISION install` at the root)
set -euo pipefail
cd "$(dirname "$0")/.."
REVISION="${REVISION:-0.0.0-PR}"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

mvn -B -q -f ci/resolved-set/pom.xml -Drevision="$REVISION" \
  org.apache.maven.plugins:maven-dependency-plugin:3.8.1:list -DoutputFile="$OUT" -DincludeScope=runtime

# group:artifact:type:version, one per line (the plugin appends the scope after a trailing colon)
EXPECTED=(
  org.springframework.boot:spring-boot:jar:4.1.1
  org.apache.tomcat.embed:tomcat-embed-core:jar:11.0.26
  org.apache.tomcat.embed:tomcat-embed-el:jar:11.0.26
  org.apache.tomcat.embed:tomcat-embed-websocket:jar:11.0.26
  tools.jackson.core:jackson-databind:jar:3.1.7
  tools.jackson.core:jackson-core:jar:3.1.7
  com.fasterxml.jackson.core:jackson-databind:jar:2.21.7
  com.fasterxml.jackson.core:jackson-core:jar:2.21.7
  org.springdoc:springdoc-openapi-starter-webmvc-ui:jar:3.1.1
  org.eclipse.paho:org.eclipse.paho.client.mqttv3:jar:1.2.5
  software.amazon.awssdk:s3:jar:2.29.29
  org.mapstruct:mapstruct:jar:1.6.3
)
status=0
for want in "${EXPECTED[@]}"; do
  if ! grep -qE "^ *${want//./\\.}:" "$OUT"; then
    got="$(grep -E "^ *${want%:*:*}:" "$OUT" | head -1 | tr -d ' ' || true)"
    echo "::error::expected ${want}, resolved ${got:-nothing}"
    status=1
  fi
done
[ "$status" -eq 0 ] && echo "All ${#EXPECTED[@]} pins resolve as expected."
exit "$status"
