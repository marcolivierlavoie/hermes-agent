#!/usr/bin/env bash
# Restart only Proxmox CT 101 (AdGuard/DNS) on the fixed Proxmox host.
#
# Safety properties:
# - no user-supplied host or CT ID;
# - no Proxmox host reboot;
# - no broad guest loop/wildcard operation;
# - no DNS/DHCP/router configuration changes;
# - captures before/after pct status for CT 101 only;
# - performs read-only HTTP and DNS smokes before/after the restart where tools are available.

set -euo pipefail

readonly PROXMOX_HOST="root@192.168.1.248"
readonly CT_ID="101"
readonly ADGUARD_HTTP_URL="https://biff.tail460c2.ts.net:3000/"
readonly ADGUARD_DNS_SERVER="192.168.1.162"
readonly DNS_SMOKE_NAME="example.com"
readonly SSH="/usr/bin/ssh"
readonly MAX_CT_WAIT_SECONDS="${ADGUARD_CT101_WAIT_SECONDS:-60}"
readonly MAX_HTTP_WAIT_SECONDS="${ADGUARD_CT101_HTTP_WAIT_SECONDS:-90}"
readonly MAX_DNS_WAIT_SECONDS="${ADGUARD_CT101_DNS_WAIT_SECONDS:-90}"
CURL="$(command -v curl || true)"
DIG="$(command -v dig || true)"
NSLOOKUP="$(command -v nslookup || true)"
readonly CURL DIG NSLOOKUP

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_executable() {
  local path="$1"
  [[ -x "$path" ]] || fail "Required executable not found or not executable: ${path}"
}

ssh_pct() {
  "$SSH" \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=accept-new \
    "$PROXMOX_HOST" \
    pct "$@"
}

ct_status() {
  ssh_pct status "$CT_ID" 2>&1
}

wait_for_ct_running() {
  local deadline status_output
  deadline=$((SECONDS + MAX_CT_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    status_output="$(ct_status)" || {
      printf 'pct status %s failed:\n%s\n' "$CT_ID" "$status_output" >&2
      sleep 2
      continue
    }
    printf 'pct status %s: %s\n' "$CT_ID" "$status_output"
    if [[ "$status_output" == "status: running" ]]; then
      return 0
    fi
    sleep 2
  done

  fail "CT ${CT_ID} did not report status: running within ${MAX_CT_WAIT_SECONDS}s"
}

http_status() {
  "$CURL" --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 8 "$ADGUARD_HTTP_URL" 2>/dev/null || true
}

check_http_ready_once() {
  local code
  if [[ -z "$CURL" || ! -x "$CURL" ]]; then
    printf 'AdGuard HTTP smoke skipped: curl not found\n'
    return 0
  fi
  code="$(http_status)"
  if [[ "$code" =~ ^2[0-9][0-9]$|^3[0-9][0-9]$ ]]; then
    printf 'AdGuard HTTP smoke OK: %s returned HTTP %s\n' "$ADGUARD_HTTP_URL" "$code"
    return 0
  fi
  printf 'AdGuard HTTP smoke not ready: %s returned HTTP %s\n' "$ADGUARD_HTTP_URL" "${code:-curl_failed}" >&2
  return 1
}

wait_for_http_ready() {
  local deadline
  if [[ -z "$CURL" || ! -x "$CURL" ]]; then
    printf 'AdGuard HTTP readiness skipped: curl not found\n'
    return 0
  fi

  deadline=$((SECONDS + MAX_HTTP_WAIT_SECONDS))
  while (( SECONDS <= deadline )); do
    if check_http_ready_once; then
      return 0
    fi
    sleep 2
  done

  fail "AdGuard HTTP endpoint did not respond successfully at ${ADGUARD_HTTP_URL} within ${MAX_HTTP_WAIT_SECONDS}s"
}

dns_query_once() {
  if [[ -n "$DIG" && -x "$DIG" ]]; then
    "$DIG" +time=3 +tries=1 "@${ADGUARD_DNS_SERVER}" "$DNS_SMOKE_NAME" A >/dev/null 2>&1
    return $?
  fi
  if [[ -n "$NSLOOKUP" && -x "$NSLOOKUP" ]]; then
    "$NSLOOKUP" -timeout=3 "$DNS_SMOKE_NAME" "$ADGUARD_DNS_SERVER" >/dev/null 2>&1
    return $?
  fi
  return 127
}

check_dns_ready_once() {
  if [[ -z "$DIG" && -z "$NSLOOKUP" ]]; then
    printf 'AdGuard DNS smoke skipped: neither dig nor nslookup found\n'
    return 0
  fi
  if dns_query_once; then
    printf 'AdGuard DNS smoke OK: %s resolved via %s\n' "$DNS_SMOKE_NAME" "$ADGUARD_DNS_SERVER"
    return 0
  fi
  printf 'AdGuard DNS smoke not ready: %s did not resolve via %s\n' "$DNS_SMOKE_NAME" "$ADGUARD_DNS_SERVER" >&2
  return 1
}

wait_for_dns_ready() {
  local deadline
  if [[ -z "$DIG" && -z "$NSLOOKUP" ]]; then
    printf 'AdGuard DNS readiness skipped: neither dig nor nslookup found\n'
    return 0
  fi

  deadline=$((SECONDS + MAX_DNS_WAIT_SECONDS))
  while (( SECONDS <= deadline )); do
    if check_dns_ready_once; then
      return 0
    fi
    sleep 2
  done

  fail "AdGuard DNS did not resolve ${DNS_SMOKE_NAME} via ${ADGUARD_DNS_SERVER} within ${MAX_DNS_WAIT_SECONDS}s"
}

require_executable "$SSH"

printf 'Before restart CT status:\n'
before_status="$(ct_status)" || fail "Cannot inspect Proxmox CT ${CT_ID}: ${before_status}"
printf 'pct status %s: %s\n' "$CT_ID" "$before_status"

printf 'Before restart HTTP/DNS smokes:\n'
check_http_ready_once || true
check_dns_ready_once || true

printf 'Restarting only Proxmox CT %s on %s with exact command: pct reboot %s\n' "$CT_ID" "$PROXMOX_HOST" "$CT_ID"
ssh_pct reboot "$CT_ID" || fail "pct reboot ${CT_ID} failed on ${PROXMOX_HOST}"

printf 'After restart CT status verification:\n'
wait_for_ct_running

printf 'After restart HTTP readiness verification:\n'
wait_for_http_ready

printf 'After restart DNS readiness verification:\n'
wait_for_dns_ready

printf 'AdGuard/DNS CT %s restart verified successfully.\n' "$CT_ID"
