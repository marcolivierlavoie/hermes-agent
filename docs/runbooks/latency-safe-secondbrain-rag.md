# Latency-safe SecondBrain RAG Runbook

## Purpose

Biff can use Marco's SecondBrain during live Discord turns without making ordinary chat slow. The live path is intentionally conservative: direct/casual turns skip retrieval, explicit SecondBrain/Obsidian lookup turns get a bounded SQLite FTS context capsule, and broad/deep research routes to background work instead of blocking Discord.

## Production behavior

### 1. Direct/casual turns

Examples: `hi`, `thanks`, `ok`, ordinary quick answers with no SecondBrain intent.

Expected behavior:
- No SecondBrain RAG context.
- No live retrieval tools.
- Direct answer runtime.

Verification command:

```bash
python - <<'PY'
from gateway.biff_synthetic_checks import run_synthetic_checks
for result in run_synthetic_checks(['casual_hi_no_rag']):
    print(result.name, result.status, result.observed)
PY
```

### 2. Explicit SecondBrain lookup

Examples:
- `Look up SecondBrain notes about Biff routing.`
- `Check Obsidian for my note about X.`

Expected behavior:
- Route action/runtime: `secondbrain_lookup`.
- Live budget: one bounded lookup/tool-call budget.
- Source: SQLite FTS index at `~/.hermes/indexes/secondbrain.sqlite`.
- Output context includes compact source attribution such as `SecondBrain SQLite FTS: <path>`.
- No vault mutation.

Verification commands:

```bash
pytest tests/agent/test_biff_rag_router.py tests/gateway/test_biff_synthetic_checks.py -q
python - <<'PY'
from pathlib import Path
from agent.biff_rag_router import secondbrain_rag_context
print(secondbrain_rag_context(
    'Look up SecondBrain notes about Codex Biff runbook',
    db_path=Path.home() / '.hermes/indexes/secondbrain.sqlite',
)[:1200])
PY
```

### 3. Smart Connections status/provenance

Smart Connections artifacts live in the Obsidian vault under `.smart-env`. They are not a hard dependency for live Discord. Biff may use their status/provenance signal only when artifacts are readable and schema/provenance checks pass.

Current rule:
- `available`: report as provenance/status; SQLite snippets remain the live lookup source unless a future verified semantic adapter is promoted.
- `absent`, `present_empty`, locked, dataless, or unreadable: fall back to SQLite without user-visible failure.

Verification command:

```bash
python /Users/marco/.hermes/scripts/secondbrain_sqlite_index.py smart-status --sample-limit 3
```

Healthy example shape:

```json
{
  "state": "available",
  "enabled_artifacts_present": true,
  "files": 433,
  "readable_samples": 3,
  "locked_or_unreadable_samples": 0
}
```

### 4. Broad/deep research

Examples:
- `Search my entire SecondBrain and Smart Connections history for every old decision about Biff and summarize all of it.`

Expected behavior:
- Route to background work.
- Specialist: Quill.
- Do not block the live Discord turn with a deep retrieval sweep.

Verification command:

```bash
python - <<'PY'
from gateway.biff_synthetic_checks import run_synthetic_checks
for result in run_synthetic_checks(['broad_secondbrain_deep_research_background']):
    print(result.name, result.status, result.observed)
PY
```

## SQLite index operations and refresh cadence

Canonical DB:

```text
~/.hermes/indexes/secondbrain.sqlite
```

Canonical vault:

```text
/Users/marco/Library/Mobile Documents/iCloud~md~obsidian/Documents/SecondBrain
```

Refresh/index command:

```bash
python /Users/marco/.hermes/scripts/secondbrain_sqlite_index.py index
```

Operational cadence:
- Current host check found **no active crontab or LaunchAgent** for `secondbrain_sqlite_index.py` / `refresh_secondbrain_sqlite_index.sh`.
- Treat refresh as **manual/on-demand** before relying on fresh Obsidian changes in live RAG.
- Wrapper available: `/Users/marco/.hermes/scripts/refresh_secondbrain_sqlite_index.sh`.
- The wrapper writes the last successful refresh JSON to `~/.hermes/indexes/secondbrain_last_index.json`.
- If a future scheduler is added, document the exact LaunchAgent/cron label here and keep the manual wrapper as the recovery path.

Manual wrapper refresh:

```bash
/Users/marco/.hermes/scripts/refresh_secondbrain_sqlite_index.sh
```

Last-refresh status check:

```bash
test -f ~/.hermes/indexes/secondbrain_last_index.json \
  && python -m json.tool ~/.hermes/indexes/secondbrain_last_index.json \
  || echo "No recorded successful wrapper refresh yet"
```

Query smoke:

```bash
python /Users/marco/.hermes/scripts/secondbrain_sqlite_index.py query Biff --limit 2 --json
```

DB health smoke:

```bash
python - <<'PY'
import sqlite3
from pathlib import Path
p = Path.home() / '.hermes/indexes/secondbrain.sqlite'
con = sqlite3.connect(p)
print('notes', con.execute('select count(*) from notes').fetchone()[0])
print('notes_fts', con.execute('select count(*) from notes_fts').fetchone()[0])
PY
```

## Troubleshooting

### iCloud/FileProvider locked or dataless files

Symptoms:
- Smart status reports locked/unreadable samples.
- Exceptions include `errno 11`, resource temporarily unavailable, FileProvider, or dataless/materialization issues.

Response:
1. Do not fail live Discord lookup.
2. Keep SQLite FTS as the live source.
3. Re-run `smart-status` later after iCloud/Obsidian materializes files.
4. If needed, open Obsidian/iCloud locally to force materialization, then re-run the status command.

### SQLite DB missing or stale

Response:
1. Run the index command.
2. Re-run the DB health smoke.
3. Re-run focused tests if code changed.
4. Do not enable Smart-only retrieval as a workaround; SQLite remains the required fast baseline.

### Ordinary chat feels slow

Response:
1. Run the casual synthetic check and confirm `hi` skips RAG.
2. Check gateway latency logs/metrics for non-RAG overhead before adding more retrieval optimizations.
3. Do not special-case only one phrase unless the broad baseline is already healthy.

## K-1373 verification evidence, 2026-05-24

Executed in `/Users/marco/.hermes/hermes-agent-biff-runtime`:

```text
pytest tests/agent/test_biff_rag_router.py tests/gateway/test_biff_synthetic_checks.py tests/gateway/test_biff_hot_context.py -q
=> 12 passed in 1.56s
```

Synthetic checks passed:
- `casual_hi_no_rag`
- `explicit_secondbrain_lookup`
- `smart_connections_fallback_to_bounded_lookup`
- `broad_secondbrain_deep_research_background`

Live status evidence:
- Smart Connections status: `available`
- `.smart-env` files: `433`
- readable samples: `3`
- locked/unreadable samples: `0`
- SQLite DB: `~/.hermes/indexes/secondbrain.sqlite`
- `notes`: `431`
- `notes_fts`: `431`

Independent Vex review:
- K-1372 code path: PASS
- Remaining K-1373 gap before this file: missing runbook

## Safety contract

- Never write into the Obsidian vault from the live RAG path.
- Never make Smart Connections a hard dependency for ordinary chat.
- Keep snippets compact and source-attributed.
- Redact obvious secret assignments before context injection.
- Route broad/deep retrieval to background work.
