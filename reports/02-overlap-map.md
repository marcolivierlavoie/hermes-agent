# 02 — Upstream Tool Router overlap map for Biff runtime

Date: 2026-05-28
Runtime: `/Users/marco/.hermes/hermes-agent-biff-runtime`
Scope: Map which Biff-specific routing/hygiene mechanisms should stay, be adapted, or become retirement candidates if upstream Hermes Tool Router/tool-schema policy lands and passes tests.

## Executive answer

Upstream Tool Router should be treated as a **schema-selection primitive**, not as a replacement for Biff’s operating policy.

If upstream passes tests, the best migration path is:

1. Keep Biff’s product/ops semantics: consent-gated role routing, live Discord budgets, Kanban source-of-truth behavior, SecondBrain retrieval policy, and provider fallback policy.
2. Replace only the brittle fixed tool-schema narrowing and profile-specific allowlist machinery with a feature-flagged router integration.
3. Keep all Biff-specific safety and continuity layers until synthetic Discord checks prove parity.

## Current Biff mechanisms inspected

### 1. Fixed Biff Discord tool schema profiles

Current code has explicit Biff allowlists:

- `BIFF_CORE_TOOL_SCHEMA_TOOLSETS`
- `BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS`
- `BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS`
- `BIFF_TURN_TOOLSET_PROFILES`
- `BIFF_BUNDLE_TOOLSET_ESCALATIONS`
- `apply_biff_tool_schema_profile(...)`
- `apply_biff_turn_toolset_plan(...)`

Observed behavior:

- Discord defaults to the `v3` schema profile.
- `v3` keeps `web`, `search`, `terminal`, `file`, `memory`, `skills-read`, `todo`, and `kanban`.
- It drops heavier/high-risk always-on schemas unless bundles or planner decisions widen them.
- The second-stage planner uses `agent.biff_intent_router.plan_biff_turn(...)` to narrow/expand toolsets by turn type.

Recommendation: **Adapt / likely retire later.**

This is the most obvious overlap with upstream Tool Router. If upstream can select tool schemas reliably by intent/policy, Biff should stop maintaining parallel hard-coded profile lists. But do this only behind a flag, because these lists currently encode hard-earned Discord latency and safety behavior.

### 2. Biff intent router

Current code: `agent/biff_intent_router.py`.

Observed behavior:

- Classifies live Discord turns into status, Kanban, resume, SecondBrain, web, vision, mental-health, dashboard, base/specialist/command lanes.
- Detects explicit named-role handoff with dispatch verbs; bare role mentions are intentionally non-triggering.
- Separates broad/slow work from quick live-turn work.
- Drives `apply_biff_turn_toolset_plan(...)` indirectly.

Recommendation: **Keep, then maybe slim.**

Upstream Tool Router may reduce the need for low-level toolset lists, but it should not replace Biff’s user-specific operating policy. The intent router encodes Marco-specific behavior: consent-based subagent handoff, Discord-first live budget, board/source-of-truth conventions, and mental-health/dashboard care lanes.

Possible future shape: Biff intent router emits policy labels; upstream Tool Router resolves actual tool schemas.

### 3. Biff RAG / SecondBrain router

Current code: `agent/biff_rag_router.py`.

Observed behavior:

- Classifies casual/direct prompts as skip.
- Routes broad SecondBrain/archive lookups to background/continuation work.
- Allows bounded explicit SecondBrain lookup via SQLite FTS with tiny live budget.
- Treats Smart Connections as optional provenance and protects live Discord from iCloud/FileProvider stalls.
- Shapes FTS queries and clips/redacts output.

Recommendation: **Keep.**

This does not meaningfully overlap with upstream Tool Router. It is retrieval policy and latency management, not generic tool-schema selection. Tool Router could decide whether a retrieval tool is exposed, but it does not decide whether Biff should use SecondBrain, SQLite FTS, Smart Connections, or background continuation under Marco’s Discord contract.

### 4. Live prompt/tool-output budgets and operating modes

Current code: `gateway/session_hygiene.py`.

Observed behavior:

- Biff operating modes: `normal`, `economy`, `emergency`, `evidence-only`.
- Runtime instability detection can tighten behavior after recent SIGTERM/restart/tool-loop symptoms.
- Prompt budgets apply to ordinary Discord live turns, not selected specialist bundles.
- Tool-output caps and slowdown guards are model-facing only; transcripts are preserved.
- Evidence-only mode restricts toolsets to read/evidence surfaces.

Recommendation: **Keep.**

Upstream Tool Router can reduce schema bloat, but it does not replace turn-level budget policy, instability response, transcript preservation, or evidence-only degradation. These are resilience controls, not just routing.

### 5. Bundle-specific widening

Current code: `BIFF_BUNDLE_TOOLSET_ESCALATIONS` and `widen_biff_toolsets_for_bundle(...)`.

Observed behavior:

- Biff bundle invocations widen from the lean default to task-appropriate specialist surfaces.
- Examples: runtime-change/issue-execution can regain code execution, delegation, skills, web, vision; research can regain browser/session search/web/vision; automation can regain cron/delegation.
- Widening only grants toolsets configured for the platform.

Recommendation: **Adapt.**

This is policy that should remain, but the implementation can become cleaner if upstream Tool Router accepts a policy/context input such as “bundle = biff-hermes-runtime-change”. Biff should keep bundle semantics; upstream can decide the exact schema set.

### 6. Slow-work deflection / Kanban continuation

Current code: `maybe_build_slow_work_deflection(...)` and related slow-work regexes.

Observed behavior:

- Broad archive/migration/audit/backlog/repo work is pushed out of live Discord into Kanban-style continuation.
- Fast direct questions remain inline.

Recommendation: **Keep.**

This is a live-ops behavior choice, not a schema-routing concern. Upstream Tool Router may help avoid exposing broad tools in casual turns, but it does not decide when a request should leave the chat lane and become structured work.

### 7. Provider fallback chain

Current config:

```yaml
model:
  provider: openai-codex
  default: gpt-5.5
fallback_providers:
  - provider: openrouter
    model: deepseek/deepseek-v4-pro
  - provider: openrouter
    model: deepseek/deepseek-v4-flash
```

Docs confirm fallback providers are turn-scoped and preserve conversation/tool context during a provider/model failure.

Recommendation: **Keep.**

Upstream Tool Router is unrelated to model/provider failover. It may influence available tools; it does not handle GPT quota/auth exhaustion, OpenRouter fallthrough, or DeepSeek backup behavior.

## Overlap matrix

| Biff mechanism | Overlap with upstream Tool Router | Recommendation | Why |
|---|---:|---|---|
| Fixed `v2`/`v3` schema allowlists | High | Adapt / retire after parity | Same problem space: which tool schemas to expose. |
| Turn planner → toolset profile mapping | Medium-high | Adapt | Keep Biff intent labels, let upstream resolve schema sets. |
| Bundle widening | Medium | Adapt | Keep Biff bundle semantics, route schema selection through upstream if supported. |
| Consent-gated role handoff | Low | Keep | User-specific policy, not tool routing. |
| RAG / SecondBrain router | Low | Keep | Retrieval semantics + latency policy, not generic schema selection. |
| Prompt budget / tool-output caps | Low | Keep | Context hygiene and resilience controls. |
| Operating modes / evidence-only | Low-medium | Keep | May feed router, but policy must remain Biff-owned. |
| Slow-work deflection to Kanban | Low | Keep | Live-ops workflow choice. |
| Provider fallback chain | None | Keep | Model failover, unrelated to tool schemas. |
| Gateway quick-lane narrowing | Medium | Review later | Some duplicated intent/schema decisions could move upstream if synthetic checks pass. |

## Recommended integration plan

### Phase 0 — no behavior change

- Keep current Biff runtime behavior.
- Add a thin feature flag, e.g. `biff.platforms.discord.use_upstream_tool_router` or env override.
- Add trace-only mode where upstream router decisions are computed and logged but not enforced.

### Phase 1 — parity harness

Run synthetic Biff checks for representative cases:

- Casual direct question → minimal/no tools.
- BIF/Kanban status question → Kanban/status tools only.
- Runtime implementation request → file/terminal and relevant build tools.
- Explicit role handoff request → delegation available only when consent phrase is present.
- Bare role mention → no delegation.
- SecondBrain bounded lookup → retrieval/file surfaces only.
- Broad archive/audit → continuation/deflection, not live broad tool sprawl.
- Evidence-only mode → read/evidence tools only.
- Bundle invocations → expected widened surfaces.

Acceptance criterion: upstream-router-enforced output must match or improve Biff’s current synthetic expectations without increasing live Discord latency/tool bloat or weakening role-consent behavior.

### Phase 2 — feature-flagged enforcement

- Use Biff intent/router outputs as policy inputs.
- Let upstream Tool Router produce final tool schema selection.
- Preserve Biff’s final safety clamps:
  - evidence-only filter
  - platform configured-toolset ceiling
  - explicit role-handoff consent
  - prompt/tool-output budgets
  - slow-work deflection

### Phase 3 — retire duplicated code

Only after logs and synthetic checks pass:

- Remove or simplify `BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS` and `BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS`.
- Collapse `apply_biff_tool_schema_profile(...)` into a compatibility/rollback wrapper.
- Keep `plan_biff_turn(...)` as the Biff policy classifier unless upstream also grows first-class Biff policy inputs.

## Specific keep/adapt/retire list

### Keep

- `agent/biff_rag_router.py`
- provider fallback config and fallback-provider runtime
- Biff operating modes (`normal`, `economy`, `emergency`, `evidence-only`)
- runtime instability detection
- prompt budget / tool-output hygiene
- slow-work Kanban deflection
- explicit role-handoff consent policy
- SecondBrain/SQLite/Smart Connections latency protections

### Adapt

- `apply_biff_turn_toolset_plan(...)`
- `widen_biff_toolsets_for_bundle(...)`
- `BIFF_TURN_TOOLSET_PROFILES`
- Biff synthetic checks to compare current vs upstream decisions

### Retire candidates, later only

- `BIFF_DISCORD_V2_TOOL_SCHEMA_TOOLSETS`
- `BIFF_DISCORD_V3_TOOL_SCHEMA_TOOLSETS`
- most of `apply_biff_tool_schema_profile(...)`
- gateway-specific duplicate quick-lane schema narrowing, if upstream decisions prove equivalent

## Current DeepSeek fallback note

Config evidence shows intended order:

```text
openai-codex/gpt-5.5
→ openrouter/deepseek/deepseek-v4-pro
→ openrouter/deepseek/deepseek-v4-flash
```

Recent gateway log evidence I inspected showed `model=gpt-5.5` for the visible completed Discord turns, not a confirmed DeepSeek fallthrough. Treat DeepSeek as configured backup, not proven active for the current turn, unless a later log line explicitly shows OpenRouter/DeepSeek fallback activation.

## Bottom line

Do not replace Biff OS policy with upstream Tool Router. Use upstream Tool Router to eliminate duplicated schema-selection plumbing, while Biff keeps the policy brain: consent, care lanes, Discord latency, Kanban continuation, SecondBrain retrieval rules, and fallback/resilience behavior.
