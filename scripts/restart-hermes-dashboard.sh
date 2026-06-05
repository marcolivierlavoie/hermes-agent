#!/usr/bin/env bash
# Restart the Hermes dashboard LaunchDaemon using the narrowly scoped sudoers
# grant installed by scripts/install-dashboard-restart-sudoers.sh.
#
# This helper is intentionally no-prompt: sudo -n must succeed or the script
# exits loudly. It does not request broader sudo privileges and does not use
# wildcard launchctl commands.

set -euo pipefail

readonly LABEL="ai.hermes.dashboard"
readonly DOMAIN_TARGET="system/${LABEL}"
readonly LAUNCHCTL="/bin/launchctl"
readonly SUDO="/usr/bin/sudo"
CURL="$(command -v curl || true)"
readonly CURL
readonly DEFAULT_DASHBOARD_URL="http://127.0.0.1:9119"
readonly DASHBOARD_URL="${HERMES_DASHBOARD_URL:-${DEFAULT_DASHBOARD_URL}}"
readonly MAX_LAUNCHD_WAIT_SECONDS="${HERMES_DASHBOARD_LAUNCHD_WAIT_SECONDS:-20}"
readonly MAX_HTTP_WAIT_SECONDS="${HERMES_DASHBOARD_HTTP_WAIT_SECONDS:-45}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_executable() {
  local path="$1"
  [[ -x "$path" ]] || fail "Required executable not found or not executable: ${path}"
}

trim_trailing_slash() {
  local value="$1"
  while [[ "$value" == */ ]]; do
    value="${value%/}"
  done
  printf '%s' "$value"
}

launchd_print() {
  "$LAUNCHCTL" print "$DOMAIN_TARGET" 2>&1
}

field_from_print() {
  local text="$1"
  local key_regex="$2"
  awk -F'= ' -v key_regex="$key_regex" '
    $0 ~ key_regex {
      value = $2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      print value
      exit
    }
  ' <<< "$text"
}

launchd_snapshot() {
  local print_output="$1"
  local state pid runs
  state="$(field_from_print "$print_output" '^[[:space:]]*state[[:space:]]*=')"
  pid="$(field_from_print "$print_output" '^[[:space:]]*pid[[:space:]]*=')"
  runs="$(field_from_print "$print_output" '^[[:space:]]*(runs|run count)[[:space:]]*=')"

  [[ -n "$state" ]] || state="unknown"
  [[ -n "$pid" ]] || pid="unknown"
  [[ -n "$runs" ]] || runs="unknown"
  printf 'launchd state=%s pid=%s runs=%s\n' "$state" "$pid" "$runs"
}

wait_for_launchd_running() {
  local deadline now print_output state pid runs
  deadline=$((SECONDS + MAX_LAUNCHD_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    if print_output="$(launchd_print)"; then
      state="$(field_from_print "$print_output" '^[[:space:]]*state[[:space:]]*=')"
      pid="$(field_from_print "$print_output" '^[[:space:]]*pid[[:space:]]*=')"
      runs="$(field_from_print "$print_output" '^[[:space:]]*(runs|run count)[[:space:]]*=')"
      launchd_snapshot "$print_output"
      if [[ "$state" == "running" && "$pid" =~ ^[0-9]+$ && -n "$runs" ]]; then
        return 0
      fi
    else
      printf 'launchctl print failed for %s:\n%s\n' "$DOMAIN_TARGET" "$print_output" >&2
    fi
    sleep 1
  done

  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  fail "${DOMAIN_TARGET} did not reach launchd running state with PID/run count by ${now}"
}

http_ready_url() {
  local base url code
  base="$(trim_trailing_slash "$DASHBOARD_URL")"
  for path in "/api/status" "/cockpit"; do
    url="${base}${path}"
    code="$($CURL --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 2 "$url" 2>/dev/null || true)"
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
      printf '%s' "$url"
      return 0
    fi
    printf 'Readiness check not ready: %s returned HTTP %s\n' "$url" "${code:-curl_failed}" >&2
  done
  return 1
}

wait_for_http_ready() {
  local deadline ready
  deadline=$((SECONDS + MAX_HTTP_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    if ready="$(http_ready_url)"; then
      printf 'Dashboard readiness OK: %s\n' "$ready"
      return 0
    fi
    sleep 1
  done

  fail "Dashboard did not become ready at ${DASHBOARD_URL}/api/status or ${DASHBOARD_URL}/cockpit within ${MAX_HTTP_WAIT_SECONDS}s"
}

require_executable "$LAUNCHCTL"
require_executable "$SUDO"
[[ -n "$CURL" && -x "$CURL" ]] || fail "curl not found; cannot verify local dashboard readiness"

printf 'Before restart:\n'
before_print="$(launchd_print)" || fail "Cannot inspect launchd target ${DOMAIN_TARGET}: ${before_print}"
launchd_snapshot "$before_print"

printf 'Restarting with exact command: sudo -n %s kickstart -k %s\n' "$LAUNCHCTL" "$DOMAIN_TARGET"
"$SUDO" -n "$LAUNCHCTL" kickstart -k "$DOMAIN_TARGET" || fail "launchctl kickstart failed"

printf 'After restart launchd verification:\n'
wait_for_launchd_running

printf 'HTTP readiness verification against base URL: %s\n' "$DASHBOARD_URL"
wait_for_http_ready

printf 'Hermes dashboard restart verified successfully.\n'
