# K-1373 production verification — latency-safe SecondBrain RAG

Timestamp: 2026-05-24 session `20260524_200843_207a3341`
Runtime path: `/Users/marco/.hermes/hermes-agent-biff-runtime`

## Scope verified

- K-1371/K-1372 live routing behavior remains intact.
- Default SecondBrain SQLite index exists at `~/.hermes/indexes/secondbrain.sqlite`.
- Casual Discord prompts skip RAG.
- Explicit SecondBrain lookup gets bounded, read-only SQLite FTS context.
- Smart Connections fallback stays bounded and non-blocking.
- Broad/deep SecondBrain requests route background/Quill instead of live RAG.
- Runbook added: `docs/runbooks/biff-secondbrain-rag-routing.md`.

## Evidence

### Index presence

```text
/Users/marco/.hermes/indexes/secondbrain.sqlite
exists= True
size= 6111232
mtime_ns= 1779666322110111156
```

### Focused tests

Command:

```bash
python -m pytest tests/agent/test_biff_rag_router.py tests/gateway/test_biff_synthetic_checks.py -q
```

Result:

```text
9 passed in 1.50s
```

### Synthetic routing checks

Command ran four RAG routing scenarios.

Result:

```text
{'pass': 4, 'fail': 0}
casual_hi_no_rag pass
explicit_secondbrain_lookup pass
smart_connections_fallback_to_bounded_lookup pass
broad_secondbrain_deep_research_background pass
```

Observed contracts:

- `casual_hi_no_rag`: `action=answer_now`, `runtime=direct_answer`, `toolset_profile=none`, `max_iterations=2`.
- `explicit_secondbrain_lookup`: `action=secondbrain_lookup`, `runtime=secondbrain_lookup`, `toolset_profile=secondbrain`, `enabled_toolsets=['file', 'terminal']`, `max_tool_calls=1`, `max_iterations=2`.
- `smart_connections_fallback_to_bounded_lookup`: same bounded lookup contract.
- `broad_secondbrain_deep_research_background`: `action=background`, `runtime=background`, `specialist=quill`, `max_tool_calls=1`, `max_iterations=4`.

### Production-adjacent hot-context smoke

Results:

```text
casual_hi
 decision.action=skip classify_ms=0.0 rag_len=0 rag_ms=0.0 hot_has_rag=False hot_ms=201.94

explicit_lookup
 decision.action=sqlite_fts max_latency_ms=750 max_live_tool_calls=1 smart_allowed=True classify_ms=5.02 rag_len=953 rag_ms=23.33 hot_has_rag=True hot_ms=201.97

broad_background
 decision.action=background classify_ms=0.01 rag_len=0 rag_ms=0.01 hot_has_rag=False hot_ms=196.52

cache_check same=True first_ms=0.03 second_ms=0.01
```

## Caveats

- The working tree has many unrelated dirty files from other workstreams. K-1373 touched only the runbook/report in this pass; RAG runtime files were already clean relative to HEAD at the start of this continuation.
- A previous direct role invocation for Vex hung with no output and was killed. A fresh independent review is still required before treating K-1373 as Vex-closed.
