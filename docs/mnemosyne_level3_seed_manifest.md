# Mnemosyne Level 3 Seed Manifest

## Scope

This manifest documents the current small approved Mnemosyne seed set used by Biff's selective-prefetch / Level 3 path. It intentionally does **not** bulk-import Obsidian, raw session history, or transcripts.

## Source-of-truth boundaries

- Linear remains the source of truth for Biff OS issue/backlog/status/closure work.
- Obsidian `SecondBrain` remains the durable source of truth for docs, runbooks, notes, and decision records.
- Built-in Hermes memory/profile remains compact always-injected context.
- Mnemosyne supplies auditable recall/prefetch context and candidate writeback, not a replacement source of truth.

## Approved seed records

| Memory ID | Source | Topic | Confidence | Sensitivity | Stability | Current-request safe | Rollback |
|---|---|---|---|---|---|---|---|
| `mn_c9fc93006940` | BIF-581 Level 2 seed from current Biff persona/profile | Primary live command surface | high | non_sensitive | current | true | suppress by ID with rationale |
| `mn_79ce8d463362` | BIF-581 Level 2 seed from user memory and Linear operating rule | Biff OS issue source of truth | high | non_sensitive | stable | true | suppress by ID with rationale |
| `mn_2e324ec250e1` | BIF-581 Level 2 seed from current Biff memory | Durable documentation source of truth | high | non_sensitive | stable | true | suppress by ID with rationale |
| `mn_7def593ae872` | BIF-581 Level 2 seed from current Biff memory | Biff autonomy and approval boundaries | high | non_sensitive | stable | true | suppress by ID with rationale |
| `mn_b2de52fb036e` | BIF-581 Level 2 seed from current Biff persona/profile | Named subagent routing protocol | high | non_sensitive | stable | true | suppress by ID with rationale |
| `mn_18784e8975b1` | BIF-581 Level 2 seed from current Biff memory and Linear credential rules | Credential source of truth | high | non_sensitive | stable | true | suppress by ID with rationale |

## Excluded from automatic seed set

The following current memories remain in the corpus for audit history or explicit recall, but are not part of the approved automatic seed set because they lack the required high-confidence/non-sensitive/stable/current-request-safe eligibility metadata or are intentionally marked sensitive:

| Memory ID | Reason excluded from automatic prefetch |
|---|---|
| `mn_c036fa5ba101` | BIF-565 live canary / validation memory; not approved as high-confidence automatic context. |
| `mn_a2d45a2e79f2` | BIF-566 explicit-memory canary; lacks automatic-prefetch eligibility metadata. |
| `mn_8d0b0779d73c` | BIF-566 Vex canary; lacks automatic-prefetch eligibility metadata. |
| `mn_eabe20288e26` | BIF-566 live enablement verification; useful audit context but lacks automatic-prefetch eligibility metadata. |
| `mn_389a43ffc7fe` | BIF-565 live canary / validation memory; not approved as high-confidence automatic context. |
| `mn_8466a08deaa9` | Sensitive user-correction memory; explicit recall only, excluded from selective prefetch. |

Any future automatic seed must be added to the approved-seed table above with source, rationale, confidence, sensitivity, stability, current-request safety, and rollback instructions.

## Rollback procedure

For a targeted rollback, suppress the relevant seed ID with an explicit rationale:

```text
mnemosyne_memory(action="suppress", memory_id="<seed-id>", source="BIF-577 rollback", rationale="Rollback approved Mnemosyne seed from Level 3 manifest")
```

For a full seed rollback, suppress all six approved seed IDs listed above. Suppression is non-destructive and can be reverted with:

```text
mnemosyne_memory(action="unsuppress", memory_id="<seed-id>", rationale="Rollback test complete; restore approved Level 3 seed")
```

## Config rollback path

- Explicit-only mode: set `selective_prefetch_enabled` to `false` or remove the Mnemosyne prefetch config.
- Selective-prefetch-only mode: keep prefetch gates but disable candidate generation if needed.
- Full-disable mode: change Hermes memory provider away from `mnemosyne` or disable memory in the profile config after capturing manifests.
