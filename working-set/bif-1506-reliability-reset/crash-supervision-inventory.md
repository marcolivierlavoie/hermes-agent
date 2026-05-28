# BIF-1508 — Crash-Safe Gateway/Runtime Supervision Inventory

> **Owner:** Marco (Marc-Oliviers-Mac-mini)
> **Host:** macOS 26.5 (Darwin)
> **Runtime:** `~/.hermes/hermes-agent-biff-runtime`
> **HEAD:** `62b8528f7`
> **Branch:** `biff/runtime-fork`
> **Date:** 2026-05-28

---

## 1. Service Supervision Landscape

### 1.1 Gateway — launchd label `ai.hermes.gateway`

| Property | Current State |
|----------|--------------|
| **launchd domain** | `system` (LaunchDaemon) |
| **launchd label** | `ai.hermes.gateway` |
| **Plist** | `/Library/LaunchDaemons/ai.hermes.gateway.plist` |
| **Restart behavior** | `KeepAlive` — launchd auto-restarts on crash |
| **Restart wrappers** | `~/.hermes/scripts/hermes_gateway_launch.sh` (sets HERMES_HOME, cwd, env) |
| **Graceful stop** | `sudo launchctl kickstart -k system/ai.hermes.gateway` |
| **Hard stop** | `sudo launchctl bootout system/ai.hermes.gateway` |

### 1.2 Dashboard / Fast Biff API — launchd label `ai.hermes.dashboard`

| Property | Current State |
|----------|--------------|
| **launchd domain** | `system` (LaunchDaemon) |
| **launchd label** | `ai.hermes.dashboard` |
| **Plist** | `/Library/LaunchDaemons/ai.hermes.dashboard.plist` |
| **Port** | `9119` |
| **Restart behavior** | `KeepAlive` — launchd auto-restarts |

### 1.3 Gateway Lock Behavior

The gateway uses **two independent mechanisms** to prevent duplicate instances:

1. **PID file** (`~/.hermes/gateway.pid`) — JSON record containing `pid`, `kind`, `argv`, `start_time`. Written atomically via `O_CREAT | O_EXCL` at the end of `start_gateway()`. Cleaned up via `atexit.register(remove_pid_file)`.

2. **Runtime file lock** (`~/.hermes/gateway.lock`) — OS-level `flock` (POSIX) / byte-range lock (Windows). The lock is **released automatically by the kernel when the process dies**. A secondary JSON record is written into the lock file for identity.

3. **Scoped locks** (`~/.local/state/hermes/gateway-locks/*.lock`) — Token-scoped locks for per-session/queue operations. Released via `release_all_scoped_locks()` during `--replace`.

**Current weaknesses:**
- PID file is best-effort cleanup via `atexit` — a `SIGKILL` leaves stale PID files that `get_running_pid()` detects by checking `_pid_exists()`, so this is handled.
- Lock file is auto-released on crash, but the JSON metadata inside it persists on disk. `is_gateway_runtime_lock_active()` correctly checks via `flock`, so stale metadata is harmless.
- No crash counter or restart-loop detection in the lock file.
- No "last crash reason" or "last exit code" persisted.
- Scoped locks from zombie (`Ctrl+Z`-stopped) processes leak; `--replace` mitigation exists but there's no routine scavenger.

### 1.4 Discord Adapter Reconnect

The Discord adapter lives in `gateway/platforms/discord.py`. Reconnect behavior is handled by the Discord.py library's `run()` / `close()` loop. The gateway restart cycle calls `adapter.disconnect()` then `adapter.connect()`.

**Reconnect risks:**
- No exponential backoff in the adapter reconnect path itself — relies on gateway-level restart retry.
- No persistent "reconnect count" metric that survives restarts.
- No health-check endpoint surfaced for external monitoring (Dashboard API has `/health` but it's a separate process).

### 1.5 Stale/Orphan Process Risks

| Risk | Assessment |
|------|-----------|
| **Stale launchd services** | Low — launchd tracks PID and cleans up on crash |
| **Orphaned `--replace` old PIDs** | Low — `terminate_pid` with wait + force fallback |
| **Zombie stopped processes** | Low — scoped lock cleanup exists, but no scheduled scavenger |
| **Background terminal processes** | Medium — `process_registry` tracks them per-session; crash resets are handled via `has_active_processes_fn` in `SessionStore` expiration |
| **Kanban worker subprocesses** | Medium — spawned via `KanbanTaskRunner.run()`, tracked via session, but a gateway crash during active work leaves orphan workers |

### 1.6 Signal Handling

| Signal | Handler | Behavior |
|--------|---------|----------|
| `SIGTERM` | `shutdown_signal_handler()` | Checks takeover/planned-stop markers; `_pre_mark_shutdown_resume_pending = True` for unexpected SIGTERMs; logs shutdown context; spawns async diagnostic; calls `runner.stop()` |
| `SIGINT` | `shutdown_signal_handler()` | Treated as planned stop; exits cleanly |
| `SIGUSR1` | `restart_signal_handler()` | Calls `runner.request_restart(detached=False, via_service=True)` |

---

## 2. Target Crash Model

### 2.1 Graceful Restart (`/restart` command or `SIGUSR1`)

1. Drain requested → `_restart_requested = True`, `_draining = True`
2. Active sessions notified (`_notify_active_sessions_of_shutdown`)
3. Wait for active agents to complete (up to `_restart_drain_timeout` — default 120s)
4. Adapters disconnected
5. If under service manager: exit 0 (launchd/systemd respawn)
6. If detached: `_launch_detached_restart_command()` spawns new process, then exit 0

### 2.2 Crash (unexpected exit / unhandled exception)

1. No drain — active agents killed abruptly
2. No continuation artifacts written for mid-turn work
3. launchd detects non-zero exit → restarts via `KeepAlive`
4. On restart, Biff sees a new gateway process with no memory of the interrupted turn
5. **Current mitigation:** `_pre_mark_shutdown_resume_pending` is set for unexpected SIGTERM, but not for true crashes (segfault, unhandled exception, OOM)
6. **Missing:** No post-crash diagnostics on startup (should check for prior crash markers)

### 2.3 SIGTERM Mid-Turn

1. Signal handler fires → `shutdown_signal_handler()`
2. `_pre_mark_shutdown_resume_pending = True` (for unexpected SIGTERM)
3. `asyncio.create_task(runner.stop())` — fires within the event loop
4. Running agent is interrupted mid-tool-call or mid-LLM-generation
5. No continuation artifact is written — the interruption happens at the gateway level, not at the per-turn level

### 2.4 Model/Tool Exception

1. Exception propagates to `_run_agent_on_event()` or `handle_message()` try/except
2. Error is logged, user receives a "model provider failed" message
3. **Current behavior:** does NOT trigger a gateway restart — the exception is contained
4. **Risk:** repeated model failures trigger `BiffRuntimeInstabilitySignal` → degradation to evidence-only mode

### 2.5 Startup Failure

1. Config validation fails → `start_gateway` returns `False` → launchd respawns (may flap)
2. Biff runtime policy violation → `start_gateway` returns `False`
3. PID file race → lock acquisition fails → `start_gateway` returns `False`
4. **Missing:** Rate-limiting of launchd restarts — if config is permanently broken, launchd flaps forever

### 2.6 Adapter Reconnect

1. Adapter connection lost → adapter-specific reconnect logic fires
2. Discord: Discord.py reconnects via gateway opcode 7 (Resume)
3. If adapter cannot reconnect → gateway shutdown → launchd restart cycle
4. **Missing:** Persistent reconnect counter that survives adapter restart

---

## 3. Idempotent Startup Guards

### 3.1 Current Stale Lock Handling

- `start_gateway()` calls `acquire_gateway_runtime_lock()` → `flock(LOCK_EX | LOCK_NB)` — OS auto-releases on process death
- `get_running_pid()` → reads PID file, checks `_pid_exists()`, cleans up stale files via `_cleanup_invalid_pid_path()`
- `--replace` flag: explicitly kills old PID, waits up to 10s, force-kills if needed, removes PID file, releases scoped locks

**Gap:** No stale lock detection when the old PID file is valid but the old process is in a broken state (hung, stopped, defunct).

### 3.2 Module-Origin Diagnostics

`start_gateway()` calls `collect_gateway_runtime_diagnostics()` before destructive actions:
- Git HEAD
- Python executable path
- Working directory
- Runtime home
- PID + cmdline

**Gap:** No check that the runtime directory matches the expected origin (detached worktree vs. main checkout).

### 3.3 Active PID/Path Checks

- PID file checked before starting → `get_running_pid()`
- Process start time compared via `/proc/<pid>/stat` (Linux) or ps (macOS)
- `_looks_like_gateway_process()` validates cmdline

**Gap:** No start-time tracking on macOS (relies on ps fallback). No path-origin check (e.g., wrong-venv or wrong-worktree guard).

### 3.4 Startup Health Markers

**Missing feature:** No health marker is written on successful startup. No "last healthy startup" timestamp. No crash counter. This means:
- Biff cannot know whether the last exit was clean or a crash
- There is no way to rate-limit restart loops
- There is no diagnostic data for "what happened before the crash"

---

## 4. Recovery Behavior for Interrupted Turns

### 4.1 No Infinite Auto-Resume Loop

Auto-resume is controlled by `auto_continue` in continuation artifacts. Current safeguards:
- `_pre_mark_shutdown_resume_pending` flag prevents resume on planned shutdowns
- Continuation artifacts carry `auto_continue_started` boolean
- `run_agent.py` has iteration budget limits

**Gap:** No guard against an auto-resume loop when the gateway keeps crashing mid-turn and restarting (launchd re-spawn → pick up pending message → crash again → re-spawn → ...).

### 4.2 No Empty/System-Only User Response

**Current checks:**
- `_biff_trivial_greeting_fast_response()` returns a local "Hi Marco" for trivial greetings, avoiding an API call
- `MIN_BIFF_DISCORD_PROMPT_BUDGET_TOKENS` ensures minimum budget

**Gap:** No guard against empty post-restart continuation that produces no user-facing output.

### 4.3 No Duplicate Background Executions

- `process_registry` tracks active processes per session-key
- `SessionStore._is_session_expired()` checks `has_active_processes_fn`

**Gap:** Gateway crash loses the in-memory `_running_agents` dict and `_running_agents_ts`. On restart, there is no way to know which sessions had active agents.

---

## 5. Rollback Procedure

### 5.1 Rollback to Previous Commit

```bash
cd ~/.hermes/hermes-agent-biff-runtime
# Identify the previous known-good commit
git log --oneline -20
# Roll back
git reset --hard <previous-known-good-sha>
# Overwrite any uncommitted changes in gateway/run.py and gateway/status.py
git checkout -- gateway/run.py gateway/status.py gateway/session_hygiene.py
# Restart gateway
sudo launchctl kickstart -kp system/ai.hermes.gateway
```

### 5.2 Safe Manual Recovery Commands

```bash
# 1. Check gateway status
sudo launchctl list ai.hermes.gateway
echo "PID=$(sudo launchctl list ai.hermes.gateway | awk '{print $2}')"

# 2. Check if dashboard is up
sudo launchctl list ai.hermes.dashboard
curl -s http://127.0.0.1:8765/health 2>/dev/null || echo "Dashboard not responding"

# 3. Force-restart gateway (kill + launchd respawn via KeepAlive)
sudo launchctl kickstart -kp system/ai.hermes.gateway

# 4. If gateway is stuck in restart loop, stop it and debug:
sudo launchctl bootout system/ai.hermes.gateway
tail -100 ~/.hermes/logs/gateway.log
tail -100 ~/.hermes/logs/gateway.error.log

# 5. Remove stale lock files
rm -f ~/.hermes/gateway.lock ~/.hermes/gateway.pid

# 6. Start gateway manually for debugging
cd ~/.hermes/hermes-agent-biff-runtime
source venv/bin/activate
python -m gateway.run 2>&1 | head -100

# 7. Restart via launchd when debugged
sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist
```

### 5.3 Stale Lock Cleanup

```bash
# Remove stale gateway lock and PID file
rm -f ~/.hermes/gateway.lock ~/.hermes/gateway.pid

# Remove stale scoped locks
rm -f ~/.local/state/hermes/gateway-locks/*.lock

# Verify no remaining gateway processes
pgrep -f "gateway/run.py" || echo "No gateway processes"
```

### 5.4 Rollback Artifact Cleanup

```bash
# Remove continuation artifacts from crashed turns
rm -f ~/.hermes/working-set/live-continuations/*.json
rm -f ~/.hermes/working-set/live-continuations/*.md

# Clear instability signal state
rm -f ~/.hermes/logs/gateway-shutdown-diag.log
```
