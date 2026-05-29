# BIF-1514 — Recovery Runbook & Rollback Paths

> **Owner:** Marco (Marc-Oliviers-Mac-mini)  
> **Runtime:** `~/.hermes/hermes-agent-biff-runtime`  
> **Python:** `~/.hermes/hermes-agent-biff-runtime/venv/bin/python`  
> **Config:** `~/.hermes/config.yaml`  
> **Logs:** `~/.hermes/logs/`  
> **HEAD:** `ac0e74511` (branch `biff/runtime-fork`)  

---

## 1. Service Ownership & Recovery Commands

### 1.1 Gateway (Discord messaging + Fast Biff API)

| Property | Value |
|----------|-------|
| **launchd domain** | `system` (LaunchDaemon) |
| **launchd label** | `ai.hermes.gateway` |
| **Plist** | `/Library/LaunchDaemons/ai.hermes.gateway.plist` |
| **Launch script** | `~/.hermes/scripts/hermes_gateway_launch.sh` |
| **Runtime dir** | `~/.hermes/hermes-agent-biff-runtime` |
| **Health check** | `http://127.0.0.1:8765/health` |
| **Logs** | `~/.hermes/logs/gateway.log`, `~/.hermes/logs/gateway.error.log` |

```bash
# Status (is it running?)
sudo launchctl list ai.hermes.gateway

# Last exit code (non-zero = crash)
sudo launchctl list ai.hermes.gateway | awk '{print $1}'

# Stop gracefully (launchd will respawn via KeepAlive)
sudo launchctl kickstart -k system/ai.hermes.gateway

# Hard stop (prevents respawn)
sudo launchctl bootout system/ai.hermes.gateway

# Restart (kickstart = start if not running, -k = kill first)
sudo launchctl kickstart -kp system/ai.hermes.gateway

# Quick health check
curl -s http://127.0.0.1:8765/health | python -m json.tool

# Recent logs (last 50 lines)
tail -50 ~/.hermes/logs/gateway.log
tail -50 ~/.hermes/logs/gateway.error.log

# Grep for errors
grep -iE 'error|traceback|crash|oom|killed' ~/.hermes/logs/gateway.log | tail -30
```

### 1.2 Dashboard / Fast Biff API

| Property | Value |
|----------|-------|
| **launchd domain** | `system` (LaunchDaemon) |
| **launchd label** | `ai.hermes.dashboard` |
| **Plist** | `/Library/LaunchDaemons/ai.hermes.dashboard.plist` |
| **Port** | `9119` |
| **Logs** | `~/.hermes/logs/dashboard.log`, `~/.hermes/logs/dashboard.err.log` |

```bash
sudo launchctl list ai.hermes.dashboard
sudo launchctl kickstart -kp system/ai.hermes.dashboard
sudo launchctl bootout system/ai.hermes.dashboard
tail -50 ~/.hermes/logs/dashboard.log
tail -50 ~/.hermes/logs/dashboard.err.log
```

### 1.3 n8n

n8n runs as a standalone process or Docker container. Lookup with:

```bash
# Docker-based n8n
docker ps --filter name=n8n
docker logs n8n --tail 50
docker restart n8n

# macOS-native n8n (process-based)
pgrep -fl n8n
ps aux | grep n8n | grep -v grep
```

Watchdog scripts: `~/.hermes/scripts/n8n_failure_watchdog.py`,  
`~/.hermes/scripts/n8n_run_hermes_script_and_deliver.py`

### 1.4 Kanban Dispatcher

The Kanban dispatcher runs as a systemd-style service (macOS launchd-equivalent):

| Property | Value |
|----------|-------|
| **Plugin path** | `plugins/kanban/systemd/hermes-kanban-dispatcher.service` |
| **Kanban board** | Accessed via `tools/kanban_tools.py` |
|| **Role channels** (Discord) | Kanban dispatcher routes by assignee name via `~/.hermes/config.yaml dispatcher.channel_by_assignee` |

```bash
# Find running dispatcher process
pgrep -fl kanban
ps aux | grep -i kanban | grep -v grep

# If using launchd wrapper
sudo launchctl list | grep kanban
```

### 1.5 Cron / Watchdogs

Hermes cron runs on a schedule controlled by `cron/scheduler.py`. Individual watchdog scripts live in `~/.hermes/scripts/`:

```bash
# Key watchdog scripts
ls -la ~/.hermes/scripts/*watchdog* ~/.hermes/scripts/*watch*

# View cron schedule
grep -r 'schedule\|interval\|cron' ~/.hermes/config.yaml | head -20

# Active cron jobs from the runtime
python -c "from cron.scheduler import get_scheduler; s=get_scheduler(); print(list(s.jobs.keys())[:20])" 2>/dev/null

# Disable all cron (emergency)
touch ~/.hermes/cron.DISABLED
```

Important watchdogs:
- `n8n_failure_watchdog.py` — monitors n8n health
- `mnemosyne_hygiene_watchdog.py` — memory hygiene
- `app_health_watchdog.py` — overall app health
- `proxmox_core_watchdog.py` — Proxmox VM health
- `unifi_network_monitor.py` — network health
- `uptime_kuma_ct130_watchdog.py` — Uptime Kuma bridge
- `start-daily-health-flow.py` — daily health flow

### 1.6 Role Invocation Paths

Role dispatch now uses **Kanban-native profiles**. Forge, Vex, Quill, and Ranger are Kanban assignee names — the dispatcher spawns named workers on `kanban_create` with `assignee=<role>`.

- **Dispatch:** `kanban_create(title="...", assignee="forge", body="...")`
- **Channel routing:** Configured in `~/.hermes/config.yaml` → `dispatcher.notifications.channel_by_assignee`
- **Pattern:** Biff creates Kanban cards → dispatcher spawns profile workers → workers report back via `kanban_complete`

```bash
# Dispatch via kanban_create (recommended — durable, observable, survivable)
# Kanban tools handle this; no custom script needed.

# Check dispatcher health
kanban_list --assignee forge

# Inspect running dispatcher
pgrep -fl kanban

# Check dispatcher config
grep -A 10 'notifications:' ~/.hermes/config.yaml | grep -A 5 'channel_by_assignee'
```

---

## 2. Host/Service Tuple for Privileged Commands

| Field | Value |
|-------|-------|
| **Hostname** | `Marc-Oliviers-Mac-mini` |
| **launchd domain** | `system` |
| **launchd label** | `ai.hermes.gateway` |
| **Runtime path** | `~/.hermes/hermes-agent-biff-runtime` |
| **Venue** | `~/.hermes/hermes-agent-biff-runtime/venv` |
| **Current HEAD** | `ac0e74511` |
| **Branch** | `biff/runtime-fork` |
| **Health endpoint** | `http://127.0.0.1:8765/health` |
| **Lock file** | `~/.hermes/gateway.lock` (JSON with PID) |
| **Log root** | `~/.hermes/logs/` |
| **Config** | `~/.hermes/config.yaml` |
| **Secrets** | `~/.hermes/secrets/` (API tokens) |
| **Credential cache** | `~/.hermes/cred_cache/` (Infisical-backed) |

**Privileged command quick-reference:**

```bash
# Verify repo HEAD
cd ~/.hermes/hermes-agent-biff-runtime && git rev-parse HEAD

# Verify health
curl -s http://127.0.0.1:8765/health

# Check gateway PID from lock file
python -c "import json; d=json.load(open('/Users/marco/.hermes/gateway.lock')); print(f'PID: {d.get(\"pid\")}')"

# Check launchd process state
sudo launchctl list ai.hermes.gateway
```

---

## 3. Rollback Options

### 3.1 Git Revert / Reset Points

```bash
cd ~/.hermes/hermes-agent-biff-runtime

# Current HEAD
git log --oneline -1

# Safe revert: create a new commit that undoes specific changes
# Example: revert the intent router changes
git revert --no-commit 91a28a2f5   # then verify, then git commit

# Hard reset to a known-good state (WILL LOSE LOCAL CHANGES)
git reset --hard c06d365dd  # BIF-1516 smoke test (before toolset router changes)
git reset --hard eaa6193ec  # Before intent router broadening (safer)

# To just see what changed
git log --oneline -10
git diff ac0e74511..c06d365dd --stat
```

**Key commit anchor points (newest → oldest):**

| Commit | Description |
|--------|-------------|
| `ac0e74511` | Current HEAD — synthetic crash/resilience test suite |
| `c06d365dd` | BIF-1516 capped task auto-resume smoke test |
| `91a28a2f5` | Intent router: broaden remember match + forget/dismiss |
| `eaa6193ec` | Retire v2 tool schema, inline v3 toolsets |
| `c9f93a354` | Add Biff tool router telemetry |
| `361470f09` | Tier Biff memory context routing |
| `c292b4d84` | Adopt selective Biff toolset routes |
| `c2d6d326f` | **Feature-flagged Biff toolset router (first router commit)** |
| `1d39a836c` | Add compact Biff identity tier |
| `915e65e5d` | Discord specialist completion recaps |

### 3.2 Config Toggles / Kill Switches

**Feature-flagged toolset router:**
```yaml
# In ~/.hermes/config.yaml under biff.platforms.discord:
toolset_router: false    # Disable the toolset router
```

**Or via environment variable (overrides config):**
```bash
export HERMES_BIFF_TOOLSET_ROUTER=0   # Disable router
```

**Other kill switches:**
```yaml
# Disable fast memory
biff.fast_memory.enabled: false

# Disable hot context
biff.hot_context.enabled: false

# Roll back to full tool schema (instead of v3 allowlist)
biff.platforms.discord.tool_schema_profile: full

# Disable parallel chat
biff.platforms.discord.parallel_chat: false
```

### 3.3 Disabling Noisy Jobs

```bash
# Cron kill switch (prevents ALL cron jobs from running)
touch ~/.hermes/cron.DISABLED

# Or disable individual job by finding and commenting it in config
# Look for cron job definitions
grep -B2 -A5 'schedule\|interval' ~/.hermes/config.yaml | head -40

# Stop specific watchdogs by making them non-executable
chmod -x ~/.hermes/scripts/n8n_failure_watchdog.py
chmod -x ~/.hermes/scripts/uptime_kuma_ct130_watchdog.py
```

### 3.4 Restoring Previous launchd Config

```bash
# Backup current plist
sudo cp /Library/LaunchDaemons/ai.hermes.gateway.plist \
       /Library/LaunchDaemons/ai.hermes.gateway.plist.bak.$(date +%Y%m%d_%H%M%S)

# Restore from backup (choose the right one)
sudo cp /Library/LaunchDaemons/ai.hermes.gateway.plist.bak.20260514_180013 \
       /Library/LaunchDaemons/ai.hermes.gateway.plist

# Re-load the service
sudo launchctl bootout system/ai.hermes.gateway 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist

# Same for dashboard
sudo launchctl bootout system/ai.hermes.dashboard 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.dashboard.plist
```

### 3.5 Stopping Orphan Roles / Processes

```bash
# Find all orphan Hermes-related python processes
ps aux | grep 'python.*hermes' | grep -v grep

# Kill by PID
kill -9 <PID>

# Or find and kill gateway orphans by lock file
python -c "
import json, os, signal
try:
    d = json.load(open('/Users/marco/.hermes/gateway.lock'))
    pid = int(d.get('pid', 0))
    if pid > 0:
        os.kill(pid, signal.SIGKILL)
        print(f'Killed gateway PID {pid}')
except Exception as e:
    print(f'Could not kill: {e}')
"

# Remove stale lock file
rm -f ~/.hermes/gateway.lock

# Kill by process name
pkill -f 'python.*hermes_cli.main gateway' || true
pkill -f 'python.*dashboard' || true

# Handle zombie processes
ps aux | awk '{if ($8 ~ /Z/) print $2}' | xargs -r kill -9 2>/dev/null || true
```

---

## 4. Marco-Readable Triage Tree

```
GATEWAY DOWN / UNHEALTHY
│
├─ Is it running?
│  ├─ sudo launchctl list ai.hermes.gateway
│  │  ├─ Returns PID → running
│  │  └─ Returns "not found" or "-" → stopped
│  │
│  ├─ curl -s http://127.0.0.1:8765/health
│  │  ├─ 200 OK → service is healthy
│  │  └─ Connection refused → gateway not accepting connections
│  │
│  └─ ACTION: sudo launchctl kickstart -kp system/ai.hermes.gateway
│
├─ Check logs for crash reason
│  ├─ tail -100 ~/.hermes/logs/gateway.log
│  ├─ tail -100 ~/.hermes/logs/gateway.error.log
│  │
│  ├─ See "out of memory" / "OOM"?
│  │  └─ ACTION: free -h; check memory pressure; restart
│  │
│  ├─ See "Traceback" / "Error"?
│  │  └─ ACTION: review traceback; fix in code; git commit; restart
│  │
│  └─ No errors, just stopped?
│     └─ ACTION: sudo launchctl kickstart -kp system/ai.hermes.gateway
│
├─ Check repo state
│  ├─ cd ~/.hermes/hermes-agent-biff-runtime
│  ├─ git rev-parse HEAD
│  └─ git status  (uncommitted changes?)
│
└─ Dead loop (starts then crashes immediately)?
   ├─ sudo launchctl bootout system/ai.hermes.gateway
   ├─ Fix the issue
   └─ sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist

DASHBOARD DOWN
│
├─ sudo launchctl list ai.hermes.dashboard
├─ tail -50 ~/.hermes/logs/dashboard.err.log
└─ sudo launchctl kickstart -kp system/ai.hermes.dashboard

TOOL ROUTER MISBEHAVING
│
├─ Check current state
│  ├─ grep toolset_router ~/.hermes/config.yaml
│  ├─ echo $HERMES_BIFF_TOOLSET_ROUTER
│  │
│  └─ If enabled and causing issues:
│     ├─ export HERMES_BIFF_TOOLSET_ROUTER=0  (immediate, no restart needed in most cases)
│     ├─ Edit ~/.hermes/config.yaml: set toolset_router: false
│     └─ sudo launchctl kickstart -kp system/ai.hermes.gateway

N8N DOWN
│
├─ docker ps --filter name=n8n
├─ docker logs n8n --tail 30
├─ Check n8n_failure_watchdog.py logs
├─ Action: docker restart n8n
└─ If Docker not used: pgrep -fl n8n; check service logs

CRON / WATCHDOGS NOT FIRING
│
├─ Check cron kill switch: ls -la ~/.hermes/cron.DISABLED
├─ Check scheduler health: python cron/scheduler.py --status 2>/dev/null
├─ Check individual watchdog logs in ~/.hermes/logs/
└─ Remove cron kill switch: rm -f ~/.hermes/cron.DISABLED

KANBAN DISPATCHER NOT WORKING
│
├─ pgrep -fl kanban
├─ ps aux | grep -i biff | grep -v grep
├─ kanban_list --assignee forge
└─ kanban_create(title="dispatcher-test", assignee="forge", body="status check")

ROLE INVOCATION FAILURE
│
├─ Check Discord bot is online (gateway running)
├─ Verify channel IDs in ~/.hermes/config.yaml → channel_by_assignee
├─ Check gateway.error.log for Discord API errors
├─ Verify DISCORD_BOT_TOKEN is valid
└─ Test kanban_create with a tiny body and verify dispatcher picks it up
import json
from pathlib import Path
d = json.loads(Path('$HOME/.hermes/gateway.lock').read_text())
print(f'Gateway PID: {d.get(\"pid\")}')
"

FULL RECOVERY (NUCLEAR OPTION)
│
├─ 1. Stop all services
│  sudo launchctl bootout system/ai.hermes.gateway 2>/dev/null || true
│  sudo launchctl bootout system/ai.hermes.dashboard 2>/dev/null || true
│  pkill -f 'python.*hermes_cli' 2>/dev/null || true
│  rm -f ~/.hermes/gateway.lock
│
├─ 2. Verify repo state and rollback if needed
│  cd ~/.hermes/hermes-agent-biff-runtime
│  git status
│  git reset --hard c06d365dd   # or another known-good anchor
│
├─ 3. Restart gateway
│  sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist
│
└─ 4. Verify
   sleep 5
   curl -s http://127.0.0.1:8765/health
   sudo launchctl list ai.hermes.gateway
```