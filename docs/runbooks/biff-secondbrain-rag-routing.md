# Biff SecondBrain RAG Routing Runbook

## Purpose

Keep Discord Biff fast while allowing explicit SecondBrain/Obsidian lookups to receive compact, read-only context.

## Live policy

- Casual/direct prompts skip RAG and tools.
- Explicit SecondBrain/Obsidian/Smart Connections lookup prompts may add one bounded SQLite FTS context block.
- Broad/deep/archive-wide SecondBrain prompts route to background/Quill instead of live-turn retrieval.
- Smart Connections is provenance/status only on the live path. If it is locked, dataless, slow, or errors with iCloud/FileProvider `errno 11`, Biff falls back to SQLite or skips; it must not block Discord.
- The live RAG context is top-N snippets only, clipped, source-attributed, and read-only. It must not mutate the vault.

## Runtime files

- Router: `agent/biff_rag_router.py`
- Planner integration: `agent/biff_intent_router.py`
- Hot-context integration: `gateway/biff_hot_context.py`
- Synthetic checks: `gateway/biff_synthetic_checks.py`
- Focused tests: `tests/agent/test_biff_rag_router.py`, `tests/gateway/test_biff_synthetic_checks.py`
- Default index: `~/.hermes/indexes/secondbrain.sqlite`

## Verification commands

From `/Users/marco/.hermes/hermes-agent-biff-runtime`:

```bash
python -m pytest tests/agent/test_biff_rag_router.py tests/gateway/test_biff_synthetic_checks.py -q

python - <<'PY'
from gateway.biff_synthetic_checks import run_synthetic_checks, summarize_synthetic_checks
names = [
    'casual_hi_no_rag',
    'explicit_secondbrain_lookup',
    'smart_connections_fallback_to_bounded_lookup',
    'broad_secondbrain_deep_research_background',
]
results = run_synthetic_checks(names)
print(summarize_synthetic_checks(results))
for result in results:
    print(result.name, result.status, result.detail, result.observed)
PY

python - <<'PY'
from time import perf_counter
from gateway.biff_hot_context import build_biff_hot_context, clear_biff_hot_context_cache
from agent.biff_rag_router import classify_biff_rag_request, secondbrain_rag_context
config = {'biff': {'platforms': {'discord': {'hot_context': True}}}}
for name, prompt in [
    ('casual_hi', 'hi'),
    ('explicit_lookup', 'Look up SecondBrain notes about Biff routing'),
    ('broad_background', 'Search my entire SecondBrain and Smart Connections history for every old decision about Biff and summarize all of it'),
]:
    clear_biff_hot_context_cache()
    t0 = perf_counter(); decision = classify_biff_rag_request(prompt); t1 = perf_counter()
    ctx = secondbrain_rag_context(prompt); t2 = perf_counter()
    hot = build_biff_hot_context(config, platform_key='discord', session_key='rag-smoke', query=prompt); t3 = perf_counter()
    print(name, decision.to_dict(), 'classify_ms=', round((t1-t0)*1000, 2), 'rag_ms=', round((t2-t1)*1000, 2), 'hot_ms=', round((t3-t2)*1000, 2), 'rag=', 'Biff SecondBrain RAG Context' in hot)
PY
```

Passing bar:

- Focused pytest exits 0.
- Synthetic summary is `{'pass': 4, 'fail': 0}` for the four RAG routing scenarios.
- Casual `hi` has `action=skip`, no RAG block, and no live tool budget.
- Explicit lookup has `action=sqlite_fts`, `max_live_tool_calls=1`, `max_latency_ms<=750`, and a `Biff SecondBrain RAG Context` block.
- Broad/deep lookup has `action=background` and no live RAG block.
- Repeated explicit lookups should hit the short-lived context cache.

## Rollback / disable

Fast rollback options, in order:

1. Disable hot context for Discord in config if the problem is prompt-size/latency from context injection.
2. Remove or bypass `secondbrain_rag_context(...)` injection in `gateway/biff_hot_context.py` if only RAG context is suspect.
3. Revert the RAG routing changes in `agent/biff_rag_router.py`, `agent/biff_intent_router.py`, and associated tests.
4. Restart the owning Biff gateway service only after code/config is reverted.

Expected Biff runtime path: `/Users/marco/.hermes/hermes-agent-biff-runtime`.

Preferred restart helper when restart is approved/needed:

```bash
/Users/marco/.hermes/hermes-agent-biff-runtime/scripts/restart-hermes-gateway.sh
```

If launchd requires privileged restart, run it on the Mac that owns `system/ai.hermes.gateway`, not from another client machine.

## Safety notes

- Do not run archive-wide vault scans in the live Discord turn.
- Do not treat Smart Connections readiness as required for live RAG; it is optional provenance only.
- Do not print secrets from retrieved snippets; router clipping redacts common secret assignment patterns.
- Keep Postgres migration/indexing as a separate feature slice. The current live path is SQLite FTS because it is bounded and local.
