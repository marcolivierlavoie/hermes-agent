# BIF-1509 — Durable State Audit and Continuation Contract

> **Owner:** Marco (Marc-Oliviers-Mac-mini)
> **Host:** macOS 26.5 (Darwin)
> **Runtime:** `~/.hermes/hermes-agent-biff-runtime`
> **HEAD:** `62b8528f7`
> **Branch:** `biff/runtime-fork`
> **Date:** 2026-05-28

---

## 1. State Inventory

### 1.1 Session DB (SQLite via SessionDB / hermes_state.py)

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Message transcripts | `~/.hermes/state/hermes_state.db` (SQLite) | **Durable** — written per-turn | Low — WAL mode, crash-safe |
| Session metadata | Same DB | **Durable** | Low |
| FTS5 search index | Same DB | **Durable** | Low |
| Model overrides | In-memory `_session_model_overrides` dict | **RAM only** | Medium — lost on crash |
| Reasoning overrides | In-memory `_session_reasoning_overrides` dict | **RAM only** | Medium — lost on crash |

### 1.2 Gateway Runner Active Agents

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Active agent instances | `GatewayRunner._running_agents` dict (in-memory) | **RAM only** | **HIGH** — all agents lost on crash |
| Running agent timestamps | `GatewayRunner._running_agents_ts` dict | **RAM only** | **HIGH** |
| Pending messages | `GatewayRunner._pending_messages` dict | **RAM only** | **HIGH** — queued messages lost |
| Queued events | `GatewayRunner._queued_events` dict | **RAM only** | **HIGH** — FIFO backlog lost |
| Session sources cache | `GatewayRunner._session_sources` OrderedDict | **RAM only** | Medium — rebuilt from session store |
| Background task refs | `GatewayRunner._background_tasks` set | **RAM only** | Medium — fire-and-forget asyncio tasks |

### 1.3 Kanban Board

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Board data | `tools/kanban_tools.py` reads/writes JSON | **Durable** — written per-operation | Low |
| Active card state | Kanban board + in-memory role session | Medium | Medium — card status is durable but "in progress" status may be stale |
| Kanban dispatcher state | `plugins/kanban/` | **Durable** — file-based | Low |

### 1.4 Role Session JSON / state.db

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Role session files | `~/.hermes/role-sessions/` (JSON files) | **Durable** — written per role invocation | Low |
| Specialist direct lane state | `agent/forge_direct_lane.py`, etc. | In-memory + temp files | Medium |
| Role consent cache | `agent/biff_role_consent.py` | In-memory | Medium |

### 1.5 Working-Set Files

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Continuation artifacts | `working-set/live-continuations/*.json` | **Durable** — written on cap/crash events | Low — written before exit |
| Recovery runbook | `working-set/bif-1506-reliability-reset/recovery-runbook.md` | **Durable** — static | None |
| Tool router closeout | `working-set/bif-1506-reliability-reset/tool-router-closeout.md` | **Durable** — static | None |

### 1.6 Logs

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Agent log | `~/.hermes/logs/agent.log` | **Durable** — appended per-message | Low |
| Errors log | `~/.hermes/logs/errors.log` | **Durable** — appended per-error | Low |
| Gateway log | `~/.hermes/logs/gateway.log` | **Durable** — appended per-turn | Low |
| Gateway error log | `~/.hermes/logs/gateway.error.log` | **Durable** | Low |
| Shutdown diagnostics | `~/.hermes/logs/gateway-shutdown-diag.log` | **Durable** — written on shutdown | Low |

### 1.7 Cron/Job State

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| Scheduler lock | `~/.hermes/cron/.tick.lock` | **Durable** — file-based flock | Low — auto-released on crash |
| Jobs state | `~/.hermes/cron/jobs.json` | **Durable** | Low |
| In-memory job tracking | `cron/scheduler.py` + `cron/jobs.py` | In-memory | Medium — next tick re-evaluates |

### 1.8 Gateway State Files

| State | Location | Durability | Loss Risk |
|-------|----------|-----------|---|
| PID file | `~/.hermes/gateway.pid` | **Durable** — JSON record | Low — cleanup on startup if stale |
| Runtime lock | `~/.hermes/gateway.lock` | **Durable** — flock file | Low — kernel auto-releases |
| Runtime status | `~/.hermes/gateway_state.json` | **Durable** | Low — overwritten on each state change |
| Scoped locks | `~/.local/state/hermes/gateway-locks/*.lock` | **Durable** | Low — cleaned up by --replace |

---

## 2. RAM-Only State (Loss-Prone)

### 2.1 High-Risk RAM-Only State

These are lost on gateway crash with NO durable recovery path:

1. **`_running_agents` dict** — Active agent instances per session. Each agent holds:
   - LLM client connection state
   - Tool schema cache
   - In-progress conversation messages (partial turn)
   - Memory provider handles
   
2. **`_pending_messages` dict** — Queued messages from adapter-level interrupts. If a user sent a follow-up message while Biff was processing, and the gateway crashes, that message is lost.

3. **`_queued_events` dict** — `/queue` commands waiting in FIFO backlog.

4. **`_session_model_overrides` / `_session_reasoning_overrides`** — These should be persisted to the session DB.

### 2.2 Medium-Risk RAM-Only State

1. **`_session_sources` cache** — Rebuilt from session store on restart, so low actual loss.
2. **`_background_tasks`** — asyncio Tasks that are fire-and-forget; they'll be restarted if the process crashes.
3. **Kanban worker in-memory queue** — Next poll cycle re-reads the board.
4. **Specialist direct lane temp state** — Each role invocation writes output to temp files; only the in-progress state is lost.

---

## 3. Proposed Durable Checkpoints

### 3.1 Checkpoint: Gateway Runtime Status File

Add a `gateway_state.json` runtime status file that is atomically updated on key lifecycle events:

**Lifecycle events to record:**
- `starting` — gateway process begins boot
- `started` — all adapters connected, gateway ready
- `stopping` — graceful shutdown initiated
- `stopped_clean` — clean exit
- `crashed` — unexpected exit (detected on next startup)
- `restart_requested` — /restart or SIGUSR1 received

**Fields to track:**
```json
{
  "gateway_state": "started",
  "pid": 12345,
  "start_time": "2026-05-28T12:00:00+00:00",
  "last_crash_time": null,
  "last_crash_exit_code": null,
  "last_restart_time": null,
  "restart_count_since_clean": 0,
  "healthy_startup_count": 42,
  "active_sessions": 3,
  "platforms": {"discord": "connected", "telegram": "disconnected"},
  "updated_at": "2026-05-28T12:05:00+00:00"
}
```

### 3.2 Checkpoint: Health Marker File

Write a timestamped health marker on successful startup:
- Path: `~/.hermes/.gateway-health-marker`
- Content: JSON with PID, start time, git HEAD, module paths
- Removed on clean shutdown, present on startup = previous crash

### 3.3 Checkpoint: Active Session Snapshot

Periodically (or on shutdown) snapshot active session keys:
- Path: `~/.hermes/active-sessions-snapshot.json`
- Content: List of session_keys that had active agents
- On restart: Biff knows which sessions to inspect for pending state

---

## 4. Recovery Procedure — "What Happened?"

### 4.1 On Fresh Turn After Crash/Restart

When Biff receives a user message after a gateway crash/restart, it should follow this recovery sequence:

**Step 1: Check for prior crash**
```python
# Check for crash marker
health_marker = Path("~/.hermes/.gateway-health-marker")
if health_marker.exists():
    # Previous startup was interrupted — read crash info
    crash_info = json.loads(health_marker.read_text())
    # The health marker from last startup survived = it was never cleaned up
```

**Step 2: Inspect gateway state file**
```python
state = read_json("~/.hermes/gateway_state.json")
if state and state["gateway_state"] != "stopped_clean":
    # The gateway did not exit cleanly
    last_crash_time = state.get("last_crash_time")
    restart_count = state.get("restart_count_since_clean", 0)
```

**Step 3: Check continuation artifacts**
```python
artifacts = sorted(Path("~/.hermes/working-set/live-continuations/").glob("*.json"))
if artifacts:
    latest = json.loads(artifacts[-1].read_text())
    # Answer: "What happened?"
    # - crash_time, exit_code, restart_count
    # - interrupted work: latest["work_handle"]["active_card"]
    # - last known state: latest["work_already_verified"]
```

**Step 4: Check for crash-loop**
```python
if restart_count >= 5:
    # Crash loop detected — enter emergency mode
    # Recommend manual intervention before accepting work
```

**Step 5: Answer "what happened?"**
```python
summary = {
    "last_exit": "crash" if crash_marker_found else "clean",
    "last_crash_time": crash_info.get("start_time") if crash_marker_found else None,
    "restart_count": restart_count,
    "interrupted_work": latest_card if continuation_found else "none",
    "active_sessions_before_crash": snapshot_session_keys if snapshot_found else "unknown",
}
```

### 4.2 Recovery Implementation

The recovery procedure should be implemented as a module `gateway/recovery.py` that:
1. `detect_prior_crash()` — Checks health marker, state file, logs
2. `detect_crash_loop()` — Checks restart count in state file
3. `find_interrupted_work()` — Scans continuation artifacts
4. `answer_what_happened()` — Returns a structured dict for Biff's system prompt
5. `initialize_startup_marker()` — Writes health marker on boot
6. `finalize_clean_shutdown()` — Removes health marker on clean exit
7. `increment_crash_counter()` — Updates restart count

---

## 5. Checkpoint Behavior Before High-Risk Operations

### 5.1 Definition of High-Risk Operations

Operations that warrant a pre-execution checkpoint:

| Operation | Risk | Checkpoint Action |
|-----------|------|-------------------|
| Kanban card movement (in-progress → done) | State change with side effects | Snapshot current board, write pre-mutation checkpoint |
| Role invocation (Forge/Vex/Quill) | Long-running subprocess | Save continuation artifact with request fingerprint |
| Background terminal process | Orphan risk on crash | Register in process_registry before exec |
| Config file rewrite | Corrupt config on crash | Atomic write with fsync |
| File system mutation (bulk) | Partial state on crash | Write plan + progress marker before starting |

### 5.2 Checkpoint Contract

Every high-risk operation should:

1. **Before execution:** Write a `.checkpoint.pending` file with intent, expected outcome, and rollback steps
2. **During execution:** Update `.checkpoint.progress` with current step (removed after each completed sub-step)
3. **After completion:** Remove checkpoint files; write `.checkpoint.done` with verification hash

### 5.3 Implementation Plan

Checkpoints should be stored in `~/.hermes/working-set/checkpoints/{card_id}/` with:
- `intent.json` — What the operation intends to do
- `pre_state.json` — Snapshot of state before mutation
- `progress.json` — Current step indicator (updated during execution)
- `done.json` — Verification hash and completion marker
- Cleanup: automated scrubber removes checkpoints older than 7 days