# Background Job & Watchdog Containment — Working-Set Artifact

## Inventory of Biff/Hermes Background Execution Paths

### 1. Cron Scheduler (`cron/scheduler.py`, `cron/jobs.py`)
- **Mechanism**: `scheduler.tick()` polls every ~30s, spawns `threading.Thread` per due job
- **Failure modes**:
  - Job thread crashes silently → no output produced
  - Concurrent tick races → duplicate execution
  - Stale job processes accumulate
  - `jobs.json` corruption from concurrent writes
  - Croniter parse failure for bad schedule expressions
- **Containment**: `ONESHOT_GRACE_SECONDS=120`, `_jobs_file_lock`, atomic file writes

### 2. Background Review (`agent/background_review.py`)
- **Mechanism**: `spawn_background_review()` forks daemon `AIAgent` thread per turn
- **Failure modes**:
  - Forked agent consumes model budget from same provider pool
  - `run_conversation` loops forever on tool-call chains
  - Memory provider leak across forks
  - Duplicate reviews for overlapping conversation segments
- **Containment**: Auto-deny approval callback, tool whitelist (memory+skills only), `shutdown_memory_provider()` in `finally`, `daemon=True` thread

### 3. Background Command (`gateway/run.py` — `_start_background_agent`)
- **Mechanism**: Spawns detached agent process for long-running Discord requests
- **Failure modes**:
  - Orphan background agent on gateway restart
  - Resource exhaustion from unbounded background agents
  - Race between completion callback and gateway drain
- **Containment**: Session-scoped tracking, gateway drain cancels pending

### 4. Background Specialist Dispatch (`gateway/run.py`)
- **Mechanism**: Non-blocking dispatch to Quill/Vex/Ranger via Kanban
- **Failure modes**:
  - Dispatch queue overflow
  - Specialist agent never starts
  - Blocked by runtime instability (extreme only)
- **Containment**: `biff_nonblocking_specialist_dispatch_blocked` guard, degraded notice reply

### 5. Watchdog: Uptime Kuma CT130 (`scripts/uptime-kuma-ct130-watchdog.py`)
- **Mechanism**: Checks `https://biff.tail460c2.ts.net:3001/`, invokes `watchdog-remediate.py`
- **Failure modes**:
  - DNS resolution failure → false "down" alarm
  - Remediation executes before endpoint actually down
  - Remediation loop on intermittent network blips
  - State file corruption
- **Containment**: Cooldown (default 1800s), kill switch env `UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE=1`, opt-in via `UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE=1`

### 6. Watchdog: Alert Intake (`scripts/watchdog-alert-intake.py`)
- **Mechanism**: Consumes external alerts, classifies, routes to remediation
- **Failure modes**:
  - Downstream analyzer unreachable
  - Malformed alert payloads
  - Infinite loop on recurring same alert
- **Containment**: Bounded retries, alert dedup

### 7. Watchdog: Remediation Runner (`scripts/watchdog-remediate.py`)
- **Mechanism**: Executes remediation rules from `config/watchdog-remediation-rules.json`
- **Failure modes**:
  - Remediation script itself fails
  - Multiple concurrent remediations collide
  - Escalation loop
- **Containment**: Rule-level cooldowns, `--allow-production` gate

### 8. Busy Queue Merge (`gateway/run.py` — `merge_pending_message_event`)
- **Mechanism**: Queues messages during active session, replays on next turn
- **Failure modes**:
  - Queue store failure → message lost
  - Queue corruption → replay garbage
- **Containment**: Fail-degraded reply ("could not queue"), no retry

### 9. Update Watcher / Prompt Fallback
- **Mechanism**: Watches for gateway updates, tries to send prompt notification
- **Failure modes**:
  - Discord send fails → retry loop
- **Containment**: Bounded retry, `except Exception: continue`, logs warning

### 10. Gateway Polling Loops (`gateway/run.py`)
- **Mechanism**: Multiple `while True` loops for platform polling (Discord, Telegram, etc.)
- **Failure modes**:
  - Empty terminal frame recovery loops
  - WebSocket reconnect storms
  - Rate-limit retry loops
- **Containment**: Backoff, jitter, max reconnect attempts

---

## Containment Rules Matrix

| Rule | Applied To | Implementation |
|------|-----------|---------------|
| Deduplication | Cron jobs, background review, watchdog alerts | `_jobs_file_lock`, session-scoped review tracking, alert ID dedup |
| Cooldown | Remediation, watchdog checks | `REMEDIATION_COOLDOWN_SECONDS=1800`, rule-level cooldowns |
| Kill Switch | Instability guard, watchdog remediation | `BIFF_DISABLE_INSTABILITY_GUARD=1`, `UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE=1` |
| No-action on infra failure | Uptime Kuma DNS failures | DNS resolution failure → classified as watchdog infra failure, NOT service-down |
| Bounded Retries | Prompt fallback, alert intake | Max retry count, `continue` on failure |
| Stale Process Cleanup | Background agents, cron jobs | `daemon=True`, gateway drain, session key cleanup |
| Final Relay Fallback | Busy queue, specialist dispatch | Degraded notice reply instead of silent failure |

---

## DNS Failure Classification (Uptime Kuma / Tailscale)

DNS resolution failure during watchdog checks MUST be classified as watchdog
infrastructure failure, not as the monitored service being down.

**Rationale**: Tailscale DNS (MagicDNS or CustomResolver) can flap intermittently
without the uptime target itself being affected. Actioning a DNS failure as a
service-down event would trigger unnecessary remediation (restart containers,
cycle services) for a transient network-routing issue.

**Implementation in `uptime-kuma-ct130-watchdog.py`** (lines 93-105):
- `check_endpoint()` uses `urllib.request.urlopen()` which raises
  `urllib.error.URLError` with `[Errno 8]` for DNS resolution failures
- DNS failures should produce `(False, "DNS_RESOLUTION_FAILURE", None)` instead
  of triggering remediation
- Currently: any non-2xx/3xx that raises an exception returns False → passes the
  pattern check; DNS failure is NOT explicitly distinguished from HTTP failure

**Current gap**: The watchdog does not explicitly check for `[Errno 8]`
(gaierror) in its `except` handler. This means a DNS flapping event could
increment the failure count and trigger remediation cooldown.

---

## Top 3 Noisy Loop Paths

1. **Background Review per turn** (`agent/background_review.py:spawn_background_review_thread`)
   - Fires after every `run_conversation` call
   - Most conversations produce "nothing to save" → wasted model calls
   - *Mitigation*: review is tool-whitelisted and uses cached system prompt

2. **Cron Scheduler tick** (`cron/scheduler.py`)
   - Polls every 30s, checks all jobs for due time
   - Most ticks are no-ops
   - *Mitigation*: lightweight in-memory check, no I/O when no jobs due

3. **Busy Queue polling for active sessions** (`gateway/run.py`)
   - Pending message loop spins during active agent run
   - *Mitigation*: bounded iterations, fail-degraded on store error

---

## Auto-Act Gates

| Background System | Auto-Act Allowed? | Gate Conditions |
|------------------|-------------------|-----------------|
| Background Review | Yes | After every `run_conversation`, whitelisted tools only, daemon thread |
| Cron Scheduled Jobs | Yes | Jobs defined in `cron/jobs.json`, run in-thread at due time |
| Background Agent (Discord) | Yes | Initiated by user request, session-scoped, cancelled on drain |
| Specialist Dispatch (Kanban) | Yes | Non-blocking, blocked by extreme runtime instability only |
| Uptime Kuma Watchdog | No (check only) | Remediation requires `UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE=1` |
| Watchdog Alert Intake | Yes (classification) | Remediation requires separate gate per rule |
| Watchdog Remediation | No | Requires `--allow-production` flag or explicit env enable |
| Busy Queue Merge | Yes | Fail-degraded on error, message drop risk accepted |
| Update Watcher | Yes | Bounded retries, non-blocking |
| Gateway Poll Loops | Yes | Implemented with backoff/jitter |

---

## Cross-References

- `gateway/run.py` lines 8627-8649: runtime instability → blocks specialist dispatch
- `gateway/run.py` lines 17927-17949: instability guard startup logic
- `gateway/session_hygiene.py` lines 289-340: guard + relaxation
- `agent/background_review.py` lines 1-582: background review system
- `cron/jobs.py` lines 1-1203: cron job execution
- `scripts/uptime-kuma-ct130-watchdog.py` lines 93-105: endpoint check with DNS gap
- `scripts/watchdog-remediate.py`: remediation framework
- `scripts/watchdog-alert-intake.py`: alert classification pipeline