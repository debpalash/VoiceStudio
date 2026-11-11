#!/usr/bin/env bash
# Retrying apt for CI: runner mirrors flake with transient DNS errors
# ("Temporary failure resolving ...") that exit apt-get with 100.
#   apt-retry.sh install <pkg>...   update + install, retried as a unit
#   apt-retry.sh retry <cmd>...     retry any command (e.g. playwright install-deps)
# Env (tests): APT_RETRY_DELAYS="10 30 60", APT_RETRY_HOSTS_FILE, APT_RETRY_CONF_DIR
set -uo pipefail
read -r -a DELAYS <<<"${APT_RETRY_DELAYS:-10 30 60}"
OPTS=(-o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
HOSTS_FILE="${APT_RETRY_HOSTS_FILE:-/etc/hosts}"

# Apply the same retry/timeout settings to every apt-get on the job, including
# ones we do not invoke (playwright install-deps).
APT_CONF_DIR="${APT_RETRY_CONF_DIR:-/etc/apt/apt.conf.d}"
write_apt_conf() {
  [ -d "$APT_CONF_DIR" ] || return 0
  printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' |
    $SUDO tee "$APT_CONF_DIR/80-ci-retries" >/dev/null 2>&1 || true
}

# Runner DNS has failed for hours on the Ubuntu mirror names while public
# resolvers answered them, so retries alone cannot help. Escalate: public
# resolvers, then pin the mirror names in /etc/hosts. CI only.
MIRROR_HOSTS=(azure.archive.ubuntu.com archive.ubuntu.com security.ubuntu.com ports.ubuntu.com)
DNS_FIXED=0
mirror_hosts() {
  printf '%s\n' "${MIRROR_HOSTS[@]}"
  { cat /etc/apt/apt-mirrors.txt 2>/dev/null
    cat /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; } |
    grep -oE 'https?://[^/ ]+' | sed -E 's#https?://##; s#:.*##' || true
}
mirrors_resolve() {
  local host
  while read -r host; do
    [ -n "$host" ] || continue
    getent hosts "$host" >/dev/null 2>&1 || return 1
  done < <(mirror_hosts | sort -u)
}
resolve_a() {  # host -> IPv4 on stdout
  local host="$1" ip=""
  if command -v dig >/dev/null 2>&1; then
    ip=$(dig +short +time=5 +tries=1 "@1.1.1.1" "$host" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)
    [ -n "$ip" ] || ip=$(dig +short +time=5 +tries=1 "@8.8.8.8" "$host" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)
  fi
  if [ -z "$ip" ] && command -v curl >/dev/null 2>&1; then
    ip=$(curl -fsS --max-time 15 -H 'accept: application/dns-json' \
      "https://cloudflare-dns.com/dns-query?name=${host}&type=A" 2>/dev/null |
      grep -oE '"data":"[0-9.]+"' | head -1 | grep -oE '[0-9.]+' | head -1)
  fi
  [ -n "$ip" ] && echo "$ip"
}
pin_hosts() {
  local host ip
  while read -r host; do
    [ -n "$host" ] || continue
    getent hosts "$host" >/dev/null 2>&1 && continue
    if ip=$(resolve_a "$host") && [ -n "$ip" ]; then
      echo "$ip $host" | $SUDO tee -a "$HOSTS_FILE" >/dev/null
      echo "apt-retry: pinned $host -> $ip in $HOSTS_FILE" >&2
    else
      echo "apt-retry: could not resolve $host via public DNS" >&2
    fi
  done < <(mirror_hosts | sort -u)
}
use_public_dns() {
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
check_dns() {
  [ "$DNS_FIXED" = 1 ] && return 0
  mirrors_resolve && return 0
  use_public_dns
  sleep "${APT_RETRY_DNS_SETTLE:-2}"
  if mirrors_resolve; then
    echo "apt-retry: mirrors resolve after switching resolvers" >&2
    DNS_FIXED=1; return 0
  fi
  echo "apt-retry: resolvers changed but mirrors still do not resolve; pinning in $HOSTS_FILE" >&2
  pin_hosts
  if mirrors_resolve; then
    echo "apt-retry: mirrors resolve after /etc/hosts pinning" >&2
    DNS_FIXED=1; return 0
  fi
  echo "apt-retry: mirrors still do not resolve; continuing, apt may fail" >&2
  return 0
}

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

apt_update() {
  # apt-get update can exit 0 after transient index failures; treat them as failure.
  local out rc
  out=$($SUDO apt-get "${OPTS[@]}" -o APT::Update::Error-Mode=any update 2>&1); rc=$?
  printf '%s\n' "$out"
  [ "$rc" -eq 0 ] || return "$rc"
  if printf '%s' "$out" | grep -qE 'Failed to fetch|Temporary failure|Some index files failed'; then
    echo "apt-retry: apt-get update reported fetch failures" >&2
    return 1
  fi
}

apt_install() {
  check_dns
  apt_update && \
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${OPTS[@]}" install -y "$@"
}

# Tests source this file to reach the functions.
[ "${APT_RETRY_SOURCE_ONLY:-0}" = 1 ] && return 0

write_apt_conf
case "${1:-}" in
  install) shift; [ $# -gt 0 ] || { echo "apt-retry: no packages" >&2; exit 2; }
           with_retry apt_install "$@" ;;
  retry)   shift; [ $# -gt 0 ] || { echo "apt-retry: no command" >&2; exit 2; }
           check_dns; with_retry "$@" ;;
  *) echo "usage: apt-retry.sh install <pkg>... | retry <cmd>..." >&2; exit 2 ;;
esac
