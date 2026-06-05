# Biff Continuity Packet

Generated: 2026-05-27 16:08:51 EDT
Source surface: Discord `#hermes`
Runtime cwd: `/Users/marco/.hermes/hermes-agent-biff-runtime`
Mode: Emergency / concise execution mode

## Current user ask
Marco said: “Ok make the continuity packet.”

Immediate prior topic visible in the trimmed live prompt: Biff explained it cannot directly see OpenAI consumption unless Marco provides/accesses usage data, a local billing integration exists, or exported/logged data is available. Biff offered to check for a safe local usage/billing integration without printing secrets.

## Standing operating contract
- Biff is Marco’s chief of staff/operator; Discord `#hermes` is primary live command surface.
- For Biff OS work, native Kanban board `biff-os` is source of truth. Linear is legacy/reference only unless explicitly requested.
- Do not hand off to Forge/Vex/Quill/Ranger just because a role is mentioned. Explicit approval with action intent is required.
- Direct Biff execution is preferred for small/urgent checks. Multi-step/specialized approved work should usually use Forge → Vex.
- Use tools for actual action; do not end with promises. Verify before saying done/fixed/deployed.
- Emergency mode: be concise, avoid context/tool bloat, complete bounded deliverables in the current turn when safe.

## Memory/context anchors
- Main Obsidian vault: `SecondBrain`, durable source of truth for docs/runbooks/decisions.
- Infisical preferred for secrets when available; use `~/.local/bin/get_credential.sh`; never print secret values.
- Biff runtime: `/Users/marco/.hermes/hermes-agent-biff-runtime`, branch noted in memory as `biff/runtime-fork`.
- Biff URL: `https://biff.tail460c2.ts.net/biff`.
- Restart scripts: `scripts/restart-hermes-dashboard.sh`, `scripts/restart-hermes-gateway.sh`.
- Gateway dispatch is off per hot context.
- If live tool-call/tool-budget limit interrupts active execution, next turn should resume from the last concrete checkpoint rather than wait for Marco.

## Current Kanban snapshot checked
Board: `biff-os`; fetched top 10 tasks at packet creation.

| Internal id | Title | Assignee | Status | Priority |
|---|---|---:|---|---:|
| `t_c0a18c9a` | Reliability reset: make Hermes stop stealing assistant time from Marco | biff | scheduled | 170 |
| `t_d69815d5` | Resilience project: failure ledger and coupling map | biff | review | 169 |
| `t_e6cfdcf9` | Resilience project: crash-safe gateway/runtime supervision | biff | ready | 168 |
| `t_18c9fecb` | Resilience project: durable state and continuation contract | biff | ready | 167 |
| `t_44f8431c` | Resilience project: tool availability and emergency repair mode | biff | ready | 166 |
| `t_5459da2c` | Resilience project: enforce continuation after live turn/tool caps | biff | review | 166 |
| `t_4762291a` | Resilience project: background job and watchdog containment | biff | ready | 164 |
| `t_4631277b` | Resilience project: synthetic crash/resilience test suite | biff | ready | 163 |
| `t_78340741` | Resilience project: recovery runbook and rollback paths | biff | ready | 162 |
| `t_f93789c0` | Resilience project: release gate and independent verification plan | biff | review | 161 |

Note: Kanban tool returned internal `t_...` ids. Marco-facing convention is BIF-### when available; avoid exposing `t_...` unless no alias is available.

## Recommended next action
If Marco asks to continue from the OpenAI usage question: perform a narrow local check for an existing OpenAI usage/billing integration without printing secrets. Suggested low-risk checks:
1. Search scoped runtime/config paths for non-secret references to usage/billing scripts or OpenAI usage endpoints.
2. If a brokered credential is required, use `~/.local/bin/get_credential.sh` and avoid displaying values.
3. Report only whether integration exists and what safe next step is needed.

## Do not lose
- The active failure mode Marco is pushing on is continuity/reliability: Biff should not consume live attention with partial plans or stall after trimmed context/tool limits.
- For this packet, the durable artifact is this file: `/Users/marco/.hermes/hermes-agent-biff-runtime/continuity-packet.md`.
