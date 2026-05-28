# Biff Tool Resolution Path — Reference Document

## Overview

Biff (the Hermes Discord bot) resolves tool availability through a multi-stage
pipeline that considers platform profile, turn classification, instability guard,
bundle escalation, and operating mode. Each stage narrows or widens the toolset
surface before the model sees the tool schema.

---

## 1. Resolution Stages (in order)

### Stage 1: Platform Tool Configuration (`model_tools.get_tool_definitions`)
- Entry: `_HERMES_CORE_TOOLS` in `toolsets.py`
- Platform-specific toolset (e.g. `hermes-discord` adds discord + discord_admin)
- Filtered by `check_fn` gates (credential presence, feature flags)
- Result: the *configured ceiling* — the maximum tool surface the operator allows

### Stage 2: Fixed Tool-Schema Profile (`apply_biff_tool_schema_profile`)
- `session_hygiene.py:1089` — applies v3 or core narrowing
- v3 profile keeps: web, search, terminal, file, memory, skills-read, todo, kanban
- Drops: delegation, code_execution, browser, cronjob, image_gen, tts, clarify, messaging
- Controlled by `HERMES_BIFF_TOOL_SCHEMA_PROFILE` env or config key
- Legacy compatibility wrapper; replaced by the feature-flagged router when enabled

### Stage 3: Turn Planner / Toolset Plan (`apply_biff_turn_toolset_plan`)
- `session_hygiene.py:1123` — classifies the turn via `plan_biff_turn()` (intent router)
- For Discord, consults `BIFF_TURN_TOOLSET_PROFILES` (line 524)
- Route profiles: `status`, `terminal`, `memory`, `kanban`, `resume`, `secondbrain`, `web`, `vision`, `mental_health`, `dashboard`, `base`, `specialist`, `command`
- Feature-flagged router (`biff_toolset_router.py`) may further narrow by route class
- Bundles (detected by `extract_biff_bundle_key`) bypass turn-plan narrowing entirely

### Stage 4: Bundle Escalation (`widen_biff_toolsets_for_bundle`)
- `session_hygiene.py:line~550` — `BIFF_BUNDLE_TOOLSET_ESCALATIONS`
- Known bundles add their required toolsets to the current surface
- `biff-hermes-runtime-change` → adds `code_execution`, `delegation`, `skills`, `web`, `vision`
- `biff-issue-execution` → adds same
- Other bundles have similar escalations

### Stage 5: Operating Mode Filter (`filter_biff_mode_enabled_toolsets`)
- `session_hygiene.py:1185` — applies BiffOperatingMode restrictions
- Only `evidence-only` mode filters toolsets: reduces to `BIFF_EVIDENCE_ONLY_SAFE_TOOLSETS` (search, session_search, web, vision)
- `emergency`, `economy`, `normal` pass through unchanged
- Mode is resolved by `resolve_biff_operating_mode` from env/config

### Stage 6: Instability Guard (`apply_biff_runtime_instability_guard`)
- `session_hygiene.py:289` — detects SIGTERM/codex-empty/repeated-failure signals
- Non-signal, soft-severity, or already-degraded modes: pass through
- Extreme severity (≥8 SIGTERM, ≥6 failures, security lockdown): degrades to evidence-only
- Repeated tool-loop (≥2 failures): degrades to emergency mode

### Stage 7: Guard Relaxation for Recovery (`relax_biff_runtime_instability_guard_for_turn`)
- `session_hygiene.py:309` — restores emergency mode for approved turn classes
- Approved routes: `tool_access_recovery`, `continuation`, `kanban_read`, `kanban_admin`, `biff-hermes-runtime-change`, `runtime_change`
- Approved actions: `resume_context`, `kanban_status`, `kanban_admin`
- Only fires when current mode is `evidence-only`; restores to `emergency` (not full normal)

### Stage 8: Tool Budget Guardrails (`apply_biff_runtime_instability_tool_guardrails`)
- `session_hygiene.py:343` — narrows max_tool_calls and terminal_timeout
- Soft severity: caps tool_calls at 24, timeout at 45s
- Extreme severity: caps tool_calls at 2, timeout at 10s
- Disabled by config/env: sets `runtime_instability_guard.disabled=True`

### Stage 9: Tool Recall On Miss (`widen_agent_tools_for_missing_tool`)
- `agent/toolset_recall.py:109` — widens agent.tools at runtime when model requests missing tool
- Only for Discord; max 1 recall attempt
- Safe recall toolsets: web, search, browser, terminal, file, kanban, memory, session_search, vision
- Never grants specialist/delegation/cron/admin tools

---

## 2. Turn-Type Tool-Resolution Walkthroughs

### Discord Biff (casual chat)
1. Configured: full platform tools
2. Profile: v3 narrows to core set
3. Planner: routes to `base`/`command` profile → keeps v3 set
4. Bundle: none → no escalation
5. Mode: normal → no filter
6. Guard: may degrade if instability
7. Result: web, search, terminal, file, memory, skills-read, todo, kanban

### Runtime-Change Bundle Turn
1. Profile: v3
2. Planner: bundle detected → preserves incoming surface (skip narrowing)
3. Bundle: `biff-hermes-runtime-change` → adds code_execution, delegation, skills, web, vision
4. Guard relaxation: approved route `biff-hermes-runtime-change` → restores emergency if degraded
5. Budget: high (80 max_tool_calls, 60s timeout)
6. Result: terminal, file, kanban, web, code_execution, delegation, skills, memory, todo, vision

### Issue-Execution Bundle Turn
1. Same as runtime-change but with 60 max_tool_calls, 45s timeout

### Post-Restart Continuation Turn
1. Planner: route `resume_context` → profile includes session_search, terminal, file, kanban
2. Guard relaxation: approved route `continuation` or `resume_context` → restores emergency
3. Budget: moderate (3-16 max_tool_calls depending on plan)

### Direct Role Invocation (Forge/Quill/Ranger/Vex)
1. Planner: routes to `forge_direct`/`quill_direct`/etc → `specialist_work` runtime
2. Conservative router class: `conservative_full` (preserves incoming surface)
3. Budget: 24 max_tool_calls, 45s timeout
4. Specialist toolsets = BIFF_V3_SPECIALIST_TOOLSETS (file, kanban, memory, search, skills-read, terminal, todo, web)

### Evidence-Only / Degraded Mode
1. Guard detects extreme instability → degrades to `evidence-only`
2. Operating mode filter restricts to: search, session_search, web, vision
3. No terminal, file, kanban, memory editing
4. Guard relaxation can restore emergency for specific recovery turns

---

## 3. Emergency Operator Tool Profile

Minimum toolset an operator needs for approved repair work:

| Toolset | Tools | Purpose |
|---------|-------|---------|
| file | read_file, write_file, patch, search_files | Read/edit config and source |
| terminal | terminal, process | Run builds, tests, git commands |
| kanban | kanban_show/comment/create/admin | Track repair status |
| memory | memory | Remember context |
| skills | skills_list, skill_view, skill_manage | Load/update repair skills |
| web | web_search, web_extract | Bounded research |
| search | web_search | Quick lookups |
| code_execution | execute_code | Run verification scripts |
| delegation | delegate_task | Fork subagents for complex fixes |

These are guaranteed present when:
- Mode is `emergency` (not `evidence-only`)
- Route is `tool_access_recovery`, `continuation`, `kanban_read`, `kanban_admin`, or a bundle key in `BIFF_BUNDLE_TOOLSET_ESCALATIONS`
- Action is `resume_context`, `kanban_status`, or `kanban_admin`

---

## 4. Monotonic Guard Invariant

The instability guard is **monotonic for approved repair turns**:

- `apply_biff_runtime_instability_guard()` → may degrade normal mode
- `relax_biff_runtime_instability_guard_for_turn()` → restores ONLY for approved routes
- The relaxation is one-way: evidence-only → emergency (never full normal)
- Bundle escalations via `widen_biff_toolsets_for_bundle()` add tools on top

The key invariant: **no approved repair turn ever loses terminal, file, kanban, or web toolsets** due to the instability guard alone. The guard can reduce `max_tool_calls` and `terminal_timeout`, but the tool schema surface must include base operator tools for approved contexts.

---

## 5. Key Code References

| Component | File | Lines |
|-----------|------|-------|
| Toolset definitions | `toolsets.py` | 31-543 |
| Tool discovery | `tools/registry.py:discover_builtin_tools` | -- |
| Tool definition resolver | `model_tools.py:get_tool_definitions` | -- |
| Toolset router (feature-flagged) | `gateway/biff_toolset_router.py` | 1-137 |
| Turn plan toolset selection | `gateway/session_hygiene.py:apply_biff_turn_toolset_plan` | 1123-1182 |
| Tool schema profile | `gateway/session_hygiene.py:apply_biff_tool_schema_profile` | 1089-1120 |
| Operating mode filter | `gateway/session_hygiene.py:filter_biff_mode_enabled_toolsets` | 1185-1198 |
| Instability guard | `gateway/session_hygiene.py:apply_biff_runtime_instability_guard` | 289-306 |
| Guard relaxation | `gateway/session_hygiene.py:relax_biff_runtime_instability_guard_for_turn` | 309-340 |
| Budget guardrails | `gateway/session_hygiene.py:apply_biff_runtime_instability_tool_guardrails` | 343-383 |
| Tool recall on miss | `agent/toolset_recall.py:widen_agent_tools_for_missing_tool` | 109-178 |
| Safe recall toolsets | `agent/toolset_recall.py:SAFE_RECALL_TOOLSETS` | 16-28 |
| Bundle tool escalations | `gateway/session_hygiene.py:BIFF_BUNDLE_TOOLSET_ESCALATIONS` | 545-552 |
| Turn toolset profiles | `gateway/session_hygiene.py:BIFF_TURN_TOOLSET_PROFILES` | 524-543 |
| Synthetic checks | `gateway/biff_synthetic_checks.py` | 1-404 |