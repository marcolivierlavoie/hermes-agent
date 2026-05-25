# Biff runtime guardrails operator note

Scope touched in this change:
- `gateway/run.py` busy/drain queue handling.
- `gateway/run.py` Discord non-blocking specialist dispatch guard.
- `gateway/run.py` update prompt fallback-send handling.
- Synthetic/pytest smoke coverage for live-Biff runtime guardrails.

Rollback:
1. Revert the change set that modified `gateway/run.py`, `gateway/biff_synthetic_checks.py`, and the related tests/docs.
2. Do not edit secrets, credentials, provider config, or launchd plist state during rollback.
3. Restart the gateway after rollback so the long-lived process reloads `gateway/run.py`.

Restart/check:
- Do not restart the gateway from a role lane. Service restarts require explicit Biff/controller approval for the current task and must use the system LaunchDaemon plist path `/Library/LaunchDaemons/ai.hermes.gateway.plist`, not restart wrapper scripts.
- Do not run ad-hoc/concurrent gateway sessions for verification; a second live gateway process can race the LaunchDaemon-managed process and make Discord responsiveness evidence unreliable.
- Status checks before approval should use direct launchctl/health probes only:
  - `launchctl print system/ai.hermes.gateway`
  - `hermes gateway status`
- If Biff/controller explicitly approves a restart, use direct launchctl commands against the LaunchDaemon plist:
  - `sudo launchctl bootout system /Library/LaunchDaemons/ai.hermes.gateway.plist`
  - `sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist`
  - `launchctl print system/ai.hermes.gateway`
  - `hermes gateway status`
- After an approved restart, verify one current-chat Discord reply, one busy queued message, one `/steer` during an active run, and one non-blocking specialist dispatch acknowledgement before considering production healthy.

Expected degraded behavior:
- If busy queue merge or busy/drain acknowledgement fails, the gateway logs a warning and returns from the busy path instead of wedging live chat.
- If non-blocking specialist dispatch cannot be scheduled, Biff replies with a degraded no-op notice instead of falling through into a blocking live specialist run.
- If update prompt fallback delivery fails, the update watcher logs and keeps polling rather than crashing the gateway watcher.
