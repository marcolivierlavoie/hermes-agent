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
readonly LAUNCHCTL="${HERMES_GATEWAY_LAUNCHCTL:-/bin/launchctl}"
readonly SUDO="${HERMES_GATEWAY_SUDO:-/usr/bin/sudo}"
readonly CURL="${HERMES_GATEWAY_CURL:-/usr/bin/curl}"
readonly MAX_LAUNCHD_WAIT_SECONDS="${HERMES_GATEWAY_LAUNCHD_WAIT_SECONDS:-30}"
readonly MAX_LOG_WAIT_SECONDS="${HERMES_GATEWAY_LOG_WAIT_SECONDS:-45}"
readonly GATEWAY_LOG="${HERMES_GATEWAY_LOG:-/Users/marco/.hermes/logs/gateway.log}"
readonly API_HEALTH_URL="${HERMES_GATEWAY_API_HEALTH_URL:-http://127.0.0.1:8642/health}"
RESTARTED_PID=""

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
  local before_pid="$1"
  local deadline now print_output state pid runs
  deadline=$((SECONDS + MAX_LAUNCHD_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    if print_output="$(launchd_print)"; then
      state="$(field_from_print "$print_output" '^[[:space:]]*state[[:space:]]*=')"
      pid="$(field_from_print "$print_output" '^[[:space:]]*pid[[:space:]]*=')"
      runs="$(field_from_print "$print_output" '^[[:space:]]*(runs|run count)[[:space:]]*=')"
      launchd_snapshot "$print_output"
      if [[ "$state" == "running" && "$pid" =~ ^[0-9]+$ && -n "$runs" ]]; then
        if [[ "$before_pid" =~ ^[0-9]+$ && "$pid" == "$before_pid" ]]; then
          printf 'launchd still reports pre-restart PID %s; waiting for a replacement process\n' "$pid" >&2
        else
          RESTARTED_PID="$pid"
          return 0
        fi
      fi
    else
      printf 'launchctl print failed for %s:\n%s\n' "$DOMAIN_TARGET" "$print_output" >&2
    fi
    sleep 1
  done

  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  fail "${DOMAIN_TARGET} did not reach launchd running state with a fresh PID/run count by ${now}"
}

log_mtime_epoch() {
  if [[ -f "$GATEWAY_LOG" ]]; then
    /usr/bin/stat -f '%m' "$GATEWAY_LOG" 2>/dev/null || printf '0'
  else
    printf '0'
  fi
}

log_line_count() {
  if [[ -f "$GATEWAY_LOG" ]]; then
    /usr/bin/wc -l < "$GATEWAY_LOG" | tr -d '[:space:]'
  else
    printf '0'
  fi
}

new_log_lines_since() {
  local previous_lines="$1"
  local start_line
  if [[ ! -f "$GATEWAY_LOG" ]]; then
    return 0
  fi
  if [[ "$previous_lines" =~ ^[0-9]+$ ]]; then
    start_line=$((previous_lines + 1))
  else
    start_line=1
  fi
  /usr/bin/tail -n +"$start_line" "$GATEWAY_LOG" 2>/dev/null || true
}

wait_for_gateway_runtime_ready() {
  local previous_mtime="$1"
  local previous_lines="$2"
  local deadline current_mtime current_lines new_lines missing=()
  deadline=$((SECONDS + MAX_LOG_WAIT_SECONDS))

  if [[ ! -f "$GATEWAY_LOG" ]]; then
    printf 'Gateway log not found yet; waiting for launch wrapper to create it: %s\n' "$GATEWAY_LOG" >&2
  fi

  while (( SECONDS <= deadline )); do
    current_mtime="$(log_mtime_epoch)"
    current_lines="$(log_line_count)"
    if { [[ "$current_mtime" =~ ^[0-9]+$ && "$current_mtime" -gt "$previous_mtime" ]]; } || { [[ "$current_lines" =~ ^[0-9]+$ && "$previous_lines" =~ ^[0-9]+$ && "$current_lines" -gt "$previous_lines" ]]; }; then
      new_lines="$(new_log_lines_since "$previous_lines")"
      missing=()
      grep -q 'biff_runtime_diagnostic:' <<< "$new_lines" || missing+=("biff_runtime_diagnostic")
      grep -Eq '\[Api_Server\] API server listening on http://127\.0\.0\.1:8642|✓ api_server connected' <<< "$new_lines" || missing+=("api_server listen/connect")
      grep -Eq '\[Discord\] Connected as |✓ discord connected' <<< "$new_lines" || missing+=("discord connected")
      if (( ${#missing[@]} == 0 )); then
        if "$CURL" -fsS --max-time 3 "$API_HEALTH_URL" >/dev/null 2>&1; then
          printf 'Gateway runtime verification OK: pid=%s log_mtime=%s health=%s\n' "${RESTARTED_PID:-unknown}" "$current_mtime" "$API_HEALTH_URL"
          return 0
        fi
        printf 'Gateway startup markers are present but API health is not ready yet: %s\n' "$API_HEALTH_URL" >&2
      else
        printf 'Gateway log advanced but post-restart startup markers are incomplete; missing: %s\n' "${missing[*]}" >&2
      fi
    fi
    sleep 1
  done

  fail "Gateway did not produce post-restart biff_runtime_diagnostic + api_server ready + Discord connected markers and healthy API within ${MAX_LOG_WAIT_SECONDS}s: ${GATEWAY_LOG}"
}

require_executable "$LAUNCHCTL"
require_executable "$SUDO"
require_executable "$CURL"

printf 'Before restart:\n'
before_log_mtime="$(log_mtime_epoch)"
before_log_lines="$(log_line_count)"
before_print="$(launchd_print)" || fail "Cannot inspect launchd target ${DOMAIN_TARGET}: ${before_print}"
before_pid="$(field_from_print "$before_print" '^[[:space:]]*pid[[:space:]]*=')"
launchd_snapshot "$before_print"

printf 'Restarting with exact command: sudo -n %s kickstart -k %s\n' "$LAUNCHCTL" "$DOMAIN_TARGET"
"$SUDO" -n "$LAUNCHCTL" kickstart -k "$DOMAIN_TARGET" || fail "launchctl kickstart failed; install scripts/install-gateway-restart-sudoers.sh once if sudo reports a password is required"

printf 'After restart launchd verification:\n'
wait_for_launchd_running "$before_pid"

printf 'Gateway runtime/log/API verification:\n'
wait_for_gateway_runtime_ready "$before_log_mtime" "$before_log_lines"

printf 'Hermes gateway restart verified successfully.\n'
