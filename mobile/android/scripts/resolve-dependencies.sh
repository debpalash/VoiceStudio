#!/usr/bin/env bash
# Downloads every Gradle dependency before the build, retrying with backoff when
# Maven Central rate-limits (HTTP 429) the shared CI runner IP. Run it from the
# Gradle project directory; extra tasks to run alongside (for example
# :app:prepareRobolectricSdk) are arguments. Any other failure is left to the
# build step, which reports it with its full context.
set -uo pipefail
scripts="$(cd "$(dirname "$0")" && pwd)"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
for delay in ${RESOLVE_RETRY_DELAYS:-0 30 90 180}; do
  sleep "$delay"
  ./gradlew --no-daemon -I "$scripts/resolve-all.init.gradle" "$@" resolveAllDependencies 2>&1 | tee "$log"
  [ "${PIPESTATUS[0]}" -eq 0 ] && exit 0
  if ! grep -qE 'status code 429|Too Many Requests' "$log"; then
    echo "::warning::Dependency download failed for a reason other than rate limiting; the build step reports it"
    exit 0
  fi
  echo "::warning::Maven Central rate-limited the dependency download; retrying"
done
echo "::error::Maven Central still rate-limits the dependency download after every retry"
exit 1
