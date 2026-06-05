#!/usr/bin/env bash
# Restart the n8n production LaunchDaemon using the narrowly scoped sudoers
# grant installed by scripts/install-n8n-restart-sudoers.sh.
#
# This helper is intentionally no-prompt: sudo -n must succeed or the script
# exits loudly. It does not request broader sudo privileges and does not use
# wildcard launchctl commands.

set -euo pipefail

readonly LABEL="com.marco.n8n-production"
readonly DOMAIN_TARGET="system/${LABEL}"
readonly LAUNCHCTL="/bin/launchctl"
readonly SUDO="/usr/bin/sudo"
readonly SUDOERS_DROPIN="/private/etc/sudoers.d/com-marco-n8n-production-restart"
readonly LOCAL_HEALTHZ_URL="http://127.0.0.1:5678/healthz"
readonly TAILNET_HEALTHZ_URL="https://biff.tail460c2.ts.net:5678/healthz"
readonly MAX_LAUNCHD_WAIT_SECONDS="${N8N_PRODUCTION_LAUNCHD_WAIT_SECONDS:-30}"
readonly MAX_HTTP_WAIT_SECONDS="${N8N_PRODUCTION_HTTP_WAIT_SECONDS:-60}"
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

http_status() {
  local url="$1"
  "$CURL" --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 5 "$url" 2>/dev/null || true
}

wait_for_healthz_ready() {
  local url="$1"
  local label="$2"
  local deadline code
  deadline=$((SECONDS + MAX_HTTP_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    code="$(http_status "$url")"
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
      printf '%s readiness OK: %s returned HTTP %s\n' "$label" "$url" "$code"
      return 0
    fi
    printf '%s readiness not ready: %s returned HTTP %s\n' "$label" "$url" "${code:-curl_failed}" >&2
    sleep 1
  done

  fail "${label} n8n /healthz did not become ready at ${url} within ${MAX_HTTP_WAIT_SECONDS}s"
}

require_executable "$LAUNCHCTL"
require_executable "$SUDO"
[[ -n "$CURL" && -x "$CURL" ]] || fail "curl not found; cannot verify n8n /healthz readiness"

if [[ ! -e "$SUDOERS_DROPIN" ]]; then
  fail "Required sudoers drop-in missing: ${SUDOERS_DROPIN}; run scripts/install-n8n-restart-sudoers.sh once from an interactive admin shell"
fi

printf 'Before restart:\n'
before_print="$(launchd_print)" || fail "Cannot inspect launchd target ${DOMAIN_TARGET}: ${before_print}"
launchd_snapshot "$before_print"

printf 'Restarting with exact command: sudo -n %s kickstart -k %s\n' "$LAUNCHCTL" "$DOMAIN_TARGET"
"$SUDO" -n "$LAUNCHCTL" kickstart -k "$DOMAIN_TARGET" || fail "launchctl kickstart failed; required no-prompt sudoers grant may be missing or invalid: ${SUDOERS_DROPIN}"

printf 'After restart launchd verification:\n'
wait_for_launchd_running

printf 'n8n /healthz readiness verification:\n'
wait_for_healthz_ready "$LOCAL_HEALTHZ_URL" "Local"
wait_for_healthz_ready "$TAILNET_HEALTHZ_URL" "Tailnet"

printf 'n8n production restart verified successfully.\n'
