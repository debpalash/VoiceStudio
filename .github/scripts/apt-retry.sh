#!/usr/bin/env bash
# Retrying apt for CI: runner mirrors flake with transient DNS errors
# ("Temporary failure resolving ...") that exit apt-get with 100.
#   apt-retry.sh install <pkg>...   update + install, retried as a unit
#   apt-retry.sh retry <cmd>...     retry any command (e.g. playwright install-deps)
set -uo pipefail
DELAYS=(10 30 60)
OPTS=(-o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

# Runner DNS has failed for hours on the Ubuntu mirror names while public
# resolvers answered them, so retries alone cannot help. When a mirror name
# does not resolve, add public resolvers for the rest of this job (CI only).
MIRROR_HOSTS=(azure.archive.ubuntu.com archive.ubuntu.com security.ubuntu.com ports.ubuntu.com)
PUBLIC_DNS_SET=0
mirrors_resolve() {
  local host
  for host in "${MIRROR_HOSTS[@]}"; do
    getent hosts "$host" >/dev/null 2>&1 || return 1
  done
}
use_public_dns() {
  [ "$PUBLIC_DNS_SET" = 1 ] && return 0
  PUBLIC_DNS_SET=1
  echo "::warning::Runner DNS cannot resolve the Ubuntu mirrors; using public resolvers for this job" >&2
  if [ -d /run/systemd/resolve ] && command -v resolvectl >/dev/null 2>&1; then
    $SUDO mkdir -p /etc/systemd/resolved.conf.d
    printf '[Resolve]\nDNS=1.1.1.1 8.8.8.8 2606:4700:4700::1111\nFallbackDNS=9.9.9.9\n' |
      $SUDO tee /etc/systemd/resolved.conf.d/90-ci-public-dns.conf >/dev/null
    $SUDO systemctl restart systemd-resolved || true
  else
    printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' | $SUDO tee /etc/resolv.conf >/dev/null
  fi
}
check_dns() { mirrors_resolve || use_public_dns; }

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
  check_dns
  $SUDO apt-get "${OPTS[@]}" update && \
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${OPTS[@]}" install -y "$@"
}

case "${1:-}" in
  install) shift; [ $# -gt 0 ] || { echo "apt-retry: no packages" >&2; exit 2; }
           with_retry apt_install "$@" ;;
  retry)   shift; [ $# -gt 0 ] || { echo "apt-retry: no command" >&2; exit 2; }
           check_dns; with_retry "$@" ;;
  *) echo "usage: apt-retry.sh install <pkg>... | retry <cmd>..." >&2; exit 2 ;;
esac
