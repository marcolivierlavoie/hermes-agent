#!/usr/bin/env bash
# Install a least-privilege sudoers drop-in that lets user "marco" restart only
# the n8n production LaunchDaemon without an interactive sudo password.
#
# This script intentionally requires one interactive sudo session to install the
# drop-in. It validates the candidate file with visudo before copying it into
# /private/etc/sudoers.d.

set -euo pipefail

readonly TARGET_USER="marco"
readonly LAUNCHCTL="/bin/launchctl"
readonly SUDOERS_DIR="/private/etc/sudoers.d"
readonly DROPIN_NAME="com-marco-n8n-production-restart"
readonly DROPIN_PATH="${SUDOERS_DIR}/${DROPIN_NAME}"
readonly EXACT_COMMAND="${LAUNCHCTL} kickstart -k system/com.marco.n8n-production"
readonly SUDOERS_RULE="${TARGET_USER} ALL=(root) NOPASSWD: ${EXACT_COMMAND}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -x "$path" ]] || fail "Required executable not found or not executable: ${path}"
}

require_file "$LAUNCHCTL"
VISUDO="$(command -v visudo || true)"
readonly VISUDO
[[ -n "$VISUDO" ]] || fail "visudo not found in PATH"
[[ -x "$VISUDO" ]] || fail "visudo is not executable: ${VISUDO}"

if [[ "$(id -un)" != "$TARGET_USER" ]]; then
  fail "Run this installer as ${TARGET_USER}; current user is $(id -un)"
fi

if [[ ! -d "$SUDOERS_DIR" ]]; then
  fail "sudoers drop-in directory does not exist: ${SUDOERS_DIR}"
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
candidate="${workdir}/${DROPIN_NAME}"

umask 077
{
  printf '# Managed by Hermes Agent repo script: scripts/install-n8n-restart-sudoers.sh\n'
  printf '# Least-privilege grant for restarting only the n8n production LaunchDaemon.\n'
  printf '# Do not widen this command or add wildcards.\n'
  printf '%s\n' "$SUDOERS_RULE"
} > "$candidate"

# Self-audit: exactly one non-comment rule, exactly the intended command, no wildcard.
non_comment_rules="$(grep -Ev '^[[:space:]]*(#|$)' "$candidate")"
[[ "$non_comment_rules" == "$SUDOERS_RULE" ]] || fail "Candidate sudoers rule does not match the expected exact rule"
if grep -Ev '^[[:space:]]*(#|$)' "$candidate" | grep -q '[*?]'; then
  fail "Candidate sudoers rule unexpectedly contains wildcard characters"
fi

printf 'Validating candidate with visudo: %s\n' "$candidate"
"$VISUDO" -cf "$candidate" >/dev/null

printf 'Installing sudoers drop-in with one-time sudo: %s\n' "$DROPIN_PATH"
printf 'This will prompt for Marco password if sudo is not already authenticated.\n'
sudo /usr/bin/install -o root -g wheel -m 0440 "$candidate" "$DROPIN_PATH"

printf 'Validating installed drop-in with visudo: %s\n' "$DROPIN_PATH"
sudo "$VISUDO" -cf "$DROPIN_PATH" >/dev/null

printf 'Validating complete sudoers configuration.\n'
sudo "$VISUDO" -c >/dev/null

printf 'Installed least-privilege sudoers drop-in successfully.\n'
printf 'Granted NOPASSWD command: sudo -n %s\n' "$EXACT_COMMAND"
printf 'No other launchctl commands were granted by this drop-in.\n'
