#!/usr/bin/env bash
# Restart the Hermes gateway LaunchDaemon using the narrowly scoped sudoers
# grant installed by scripts/install-gateway-restart-sudoers.sh.
#
# This helper is intentionally no-prompt: sudo -n must succeed or the script
# exits loudly. It does not request broader sudo privileges and does not use
# wildcard launchctl commands.
#
# Operational caveat: if this is run from a Discord/Slack turn handled by the
# gateway itself, the current turn may be interrupted while launchd replaces the
# process. Prefer using it as the final action in a deployment/restart sequence,
# then verify on the next inbound message or from local logs.

set -euo pipefail

readonly LABEL="ai.hermes.gateway"
readonly DOMAIN_TARGET="system/${LABEL}"
readonly LAUNCHCTL="/bin/launchctl"
readonly SUDO="/usr/bin/sudo"
readonly MAX_LAUNCHD_WAIT_SECONDS="${HERMES_GATEWAY_LAUNCHD_WAIT_SECONDS:-30}"
readonly MAX_LOG_WAIT_SECONDS="${HERMES_GATEWAY_LOG_WAIT_SECONDS:-45}"
readonly GATEWAY_LOG="${HERMES_GATEWAY_LOG:-/Users/marco/.hermes/logs/gateway.log}"

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

log_mtime_epoch() {
  if [[ -f "$GATEWAY_LOG" ]]; then
    /usr/bin/stat -f '%m' "$GATEWAY_LOG" 2>/dev/null || printf '0'
  else
    printf '0'
  fi
}

wait_for_gateway_log_freshness() {
  local previous_mtime="$1"
  local deadline current_mtime newest_line
  deadline=$((SECONDS + MAX_LOG_WAIT_SECONDS))

  if [[ ! -f "$GATEWAY_LOG" ]]; then
    printf 'Gateway log not found yet; waiting for launch wrapper to create it: %s\n' "$GATEWAY_LOG" >&2
  fi

  while (( SECONDS <= deadline )); do
    current_mtime="$(log_mtime_epoch)"
    if [[ "$current_mtime" =~ ^[0-9]+$ && "$current_mtime" -gt "$previous_mtime" ]]; then
      newest_line="$(/usr/bin/tail -n 80 "$GATEWAY_LOG" 2>/dev/null | grep -E 'gateway\.run|gateway\.platforms|Gateway|Discord|Homeassistant|Api_Server|memory_monitor' | tail -n 1 || true)"
      if [[ -n "$newest_line" ]]; then
        printf 'Gateway log freshness OK: mtime %s > %s; %s\n' "$current_mtime" "$previous_mtime" "$newest_line"
        return 0
      fi
      printf 'Gateway log mtime advanced but no recognizable runtime line yet: %s\n' "$GATEWAY_LOG" >&2
    fi
    sleep 1
  done

  fail "Gateway log did not advance beyond mtime ${previous_mtime} with a recognizable runtime line within ${MAX_LOG_WAIT_SECONDS}s: ${GATEWAY_LOG}"
}

require_executable "$LAUNCHCTL"
require_executable "$SUDO"

printf 'Before restart:\n'
before_log_mtime="$(log_mtime_epoch)"
before_print="$(launchd_print)" || fail "Cannot inspect launchd target ${DOMAIN_TARGET}: ${before_print}"
launchd_snapshot "$before_print"

printf 'Restarting with exact command: sudo -n %s kickstart -k %s\n' "$LAUNCHCTL" "$DOMAIN_TARGET"
"$SUDO" -n "$LAUNCHCTL" kickstart -k "$DOMAIN_TARGET" || fail "launchctl kickstart failed; install scripts/install-gateway-restart-sudoers.sh once if sudo reports a password is required"

printf 'After restart launchd verification:\n'
wait_for_launchd_running

printf 'Gateway runtime/log verification:\n'
wait_for_gateway_log_freshness "$before_log_mtime"

printf 'Hermes gateway restart verified successfully.\n'
