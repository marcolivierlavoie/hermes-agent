#!/usr/bin/env bash
# Restart only Proxmox CT 130 (Uptime Kuma) on the fixed Proxmox host.
#
# Safety properties:
# - no user-supplied host or CT ID;
# - no Proxmox host reboot;
# - no broad guest loop/wildcard operation;
# - captures before/after pct status for CT 130 only;
# - verifies the public Uptime Kuma endpoint after the restart.

set -euo pipefail

readonly PROXMOX_HOST="root@192.168.1.248"
readonly CT_ID="130"
readonly UPTIME_KUMA_URL="https://biff.tail460c2.ts.net:3001/"
readonly SSH="/usr/bin/ssh"
readonly MAX_CT_WAIT_SECONDS="${UPTIMEKUMA_CT130_WAIT_SECONDS:-60}"
readonly MAX_HTTP_WAIT_SECONDS="${UPTIMEKUMA_CT130_HTTP_WAIT_SECONDS:-90}"
CURL="$(command -v curl || true)"
readonly CURL

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
  "$CURL" --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 8 "$UPTIME_KUMA_URL" 2>/dev/null || true
}

wait_for_uptime_kuma_ready() {
  local deadline code
  deadline=$((SECONDS + MAX_HTTP_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    code="$(http_status)"
    if [[ "$code" =~ ^2[0-9][0-9]$|^3[0-9][0-9]$ ]]; then
      printf 'Uptime Kuma readiness OK: %s returned HTTP %s\n' "$UPTIME_KUMA_URL" "$code"
      return 0
    fi
    printf 'Uptime Kuma readiness not ready: %s returned HTTP %s\n' "$UPTIME_KUMA_URL" "${code:-curl_failed}" >&2
    sleep 2
  done

  fail "Uptime Kuma did not respond successfully at ${UPTIME_KUMA_URL} within ${MAX_HTTP_WAIT_SECONDS}s"
}

require_executable "$SSH"
[[ -n "$CURL" && -x "$CURL" ]] || fail "curl not found; cannot verify Uptime Kuma readiness"

printf 'Before restart:\n'
before_status="$(ct_status)" || fail "Cannot inspect Proxmox CT ${CT_ID}: ${before_status}"
printf 'pct status %s: %s\n' "$CT_ID" "$before_status"

printf 'Restarting only Proxmox CT %s on %s with exact command: pct reboot %s\n' "$CT_ID" "$PROXMOX_HOST" "$CT_ID"
ssh_pct reboot "$CT_ID" || fail "pct reboot ${CT_ID} failed on ${PROXMOX_HOST}"

printf 'After restart CT status verification:\n'
wait_for_ct_running

printf 'Uptime Kuma HTTPS readiness verification:\n'
wait_for_uptime_kuma_ready

printf 'Uptime Kuma CT %s restart verified successfully.\n' "$CT_ID"
