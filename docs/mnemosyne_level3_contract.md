# Mnemosyne Level 3 Contract for Biff

## Enabled modes

- Level 1: explicit Mnemosyne actions (`add`, `recall`, `inspect`, `list`, `hygiene_report`, `suppress`, `unsuppress`).
- Level 2: selective high-confidence prefetch for approved, unsuppressed, non-sensitive memories.
- Level 3: governed automatic memory system: selective prefetch + candidate writeback + supersession/conflict handling + observability + continuous non-mutating hygiene + live rollback drill.
- Level 3 rollout helpers are manifest/report-only unless a user explicitly approves a candidate or suppression action; they do not mutate production Hermes config or Linear.

## Authority order

1. Current user instruction and safety boundary.
2. Linear for Biff OS issue/backlog/status/closure truth.
3. Obsidian `SecondBrain` and source docs for durable notes/runbooks/decisions.
4. Built-in Hermes memory/profile for compact always-needed facts.
5. Mnemosyne for auditable memory context and approved candidates.
6. `session_search` for past-conversation recall when no durable source exists.

## Automatic-memory classes allowed

- Stable/current user preferences and Biff operating protocols.
- Source-of-truth rules such as Linear/SecondBrain boundaries.
- Safe environment facts and low-risk runtime conventions.
- Approved role-routing and autonomy/approval boundaries.

## Exclusions

- Secrets, credentials, private keys, tokens, raw credentials, or credential values.
- Medical/legal/financial advice, high-stakes personal inference, or sensitive private/family material.
- Raw transcripts, bulk Obsidian imports, unreviewed session history, stale/conflicting facts without a clear winner.

## Rollback modes

- Explicit-only: disable or remove `$HERMES_HOME/mnemosyne/config.json` selective gates.
- Selective-prefetch-only: keep prefetch on, set `l3_auto_capture_enabled: false`, and disable candidate generation if needed.
- Full-disable: change Hermes memory provider away from `mnemosyne` or disable memory in config, after capturing manifests.

## Writeback policy

Normal conversations do not silently become trusted memories. Level 3 stores proposed memory candidates first. Candidates require explicit approval or a tightly defined low-risk auto-approval class before entering recall/prefetch. Rejections and approvals are auditable.

## Observability and hygiene

`prefetch_trace`, `observability_summary`, `hygiene_report`, and rollout manifests are non-mutating debug/report surfaces. Optional local prefetch event logs omit context bodies and redact query previews for blocked/risky prompts.

## Seeding and rollback

Source-aware seeding is capped, candidate-only, and returns a rollback manifest listing queued candidate IDs. Rolling back a seed means rejecting pending candidates; seed operations never create trusted memories directly.
