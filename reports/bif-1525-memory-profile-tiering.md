# BIF-1525 Memory/Profile Tier Decision Table

Goal: keep Discord Biff fast without under-loading the policy/memory context that protects source-of-truth, role-consent, and writeback behavior.

| Tier | Trigger | Mnemosyne snapshot | Tool lane expectation | Risk guard |
| --- | --- | --- | --- | --- |
| `no-memory` | Casual/direct replies, mental-health/dashboard handoff surfaces | None | No memory tools required | Avoids unnecessary personal-memory prefetch and keeps private surfaces lean. |
| `compact-memory` | Biff OS work, Kanban/status/admin, role-consent/safety/runtime policy, workflow/continuation turns | LLM-free bounded snapshot, 650 chars | Memory tools not forced; recall-on-miss may recover safe tools if configured | Preserves stable Biff policy/source-of-truth context without dragging full history into every turn. |
| `normal-memory` | Preference/profile questions, explicit memory lookup, `remember this`/candidate writeback | LLM-free bounded snapshot, 1200 chars | Memory/session-search/terminal lane | Keeps “remember this” on Mnemosyne/candidate flow and makes preference answers auditable. |
| `full-memory-history` | Session/history/transcript/resume questions | LLM-free bounded snapshot, 1800 chars | Memory/session-search/terminal lane plus resume context | Prevents context under-loading when Marco asks what happened before or resumes after refresh/rollover. |

## Acceptance mapping

- Casual: `hi` → `no-memory`, compact identity, no Mnemosyne snapshot.
- Preference: “What do you remember about my Biff tool preferences?” → `normal-memory`.
- Remember-this: “Remember this: …” → `normal-memory`, memory tool lane preserved.
- History: “What did we discuss in the previous session …?” → `full-memory-history`.
- Biff OS task: “Continue BIF-1525 …” → `compact-memory`.
- Safety-policy turn: role-consent/policy language → `compact-memory`, so policy context is not stripped.

## Known risks / follow-ons

- This slice tiers the LLM-free hot-context snapshot and toolset planner behavior. It does not yet make `AIAgent(skip_memory=...)` vary per cached agent turn, because cached-agent identity/memory lifecycle needs a separate cache-signature design if we want constructor-level persistent-memory loading to change safely per turn.
- Snapshot budgets are deliberately conservative; if measured wall-clock/token metrics show memory snapshots dominate, tighten `compact-memory` first rather than weakening safety-policy coverage.
