#!/usr/bin/env bash
# Retrying apt for CI: runner mirrors flake with transient DNS errors
# ("Temporary failure resolving ...") that exit apt-get with 100.
#   apt-retry.sh install <pkg>...   update + install, retried as a unit
#   apt-retry.sh retry <cmd>...     retry any command (e.g. playwright install-deps)
set -uo pipefail
DELAYS=(10 30 60)
OPTS=(-o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

with_retry() {
  local attempt=1 max=$((${#DELAYS[@]} + 1))
  while true; do
    "$@" && return 0
    if [ "$attempt" -ge "$max" ]; then
      echo "apt-retry: failed after $max attempts: $*" >&2
      return 1
    fi
    local d=${DELAYS[$((attempt - 1))]}
    echo "apt-retry: attempt $attempt/$max failed, retrying in ${d}s: $*" >&2
    sleep "$d"; attempt=$((attempt + 1))
  done
}

apt_install() {
  $SUDO apt-get "${OPTS[@]}" update && \
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${OPTS[@]}" install -y "$@"
}

case "${1:-}" in
  install) shift; [ $# -gt 0 ] || { echo "apt-retry: no packages" >&2; exit 2; }
           with_retry apt_install "$@" ;;
  retry)   shift; [ $# -gt 0 ] || { echo "apt-retry: no command" >&2; exit 2; }
           with_retry "$@" ;;
  *) echo "usage: apt-retry.sh install <pkg>... | retry <cmd>..." >&2; exit 2 ;;
esac
