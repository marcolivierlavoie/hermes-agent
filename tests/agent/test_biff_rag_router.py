import errno
import sqlite3
import time

from agent.biff_intent_router import plan_biff_turn
from agent.biff_rag_router import (
    _RAG_CONTEXT_CACHE,
    classify_biff_rag_request,
    merge_secondbrain_results,
    query_secondbrain_fts,
    secondbrain_rag_context,
    shape_fts_query,
    smart_connections_status,
    smart_status_is_available,
)


def _make_index(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE notes(path TEXT PRIMARY KEY, title TEXT, content TEXT);
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            path UNINDEXED,
            title,
            content,
            content='notes',
            content_rowid='rowid',
            tokenize='porter unicode61'
        );
        INSERT INTO notes(rowid, path, title, content) VALUES
            (1, 'Projects/Biff.md', 'Biff routing', 'Decision: Biff should skip RAG for casual Discord turns.'),
            (2, 'Areas/SecondBrain.md', 'SecondBrain RAG', 'Use SQLite FTS top snippets before Smart Connections.'),
            (3, 'Archive/Other.md', 'Other', 'A very unrelated note.');
        INSERT INTO notes_fts(rowid, path, title, content)
            SELECT rowid, path, title, content FROM notes;
        """
    )
    con.commit()
    con.close()


def test_casual_hi_skips_rag_and_tools(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("casual/direct prompts must not inspect Smart status")

    monkeypatch.setattr("agent.biff_rag_router.smart_connections_status", fail_if_called)

    decision = classify_biff_rag_request("hi")
    plan = plan_biff_turn("hi")

    assert decision.action == "skip"
    assert decision.max_live_tool_calls == 0
    assert decision.max_latency_ms == 0
    assert decision.reason == "casual/direct prompt"
    assert plan.action == "answer_now"
    assert plan.toolset_profile == "none"
    assert plan.max_live_tool_calls == 0


def test_explicit_secondbrain_lookup_gets_bounded_fast_policy(tmp_path):
    db = tmp_path / "secondbrain.sqlite"
    _make_index(db)

    decision = classify_biff_rag_request("Look up SecondBrain notes about Biff routing")
    rows = query_secondbrain_fts(db, "Look up SecondBrain notes about Biff routing", limit=2)
    context = secondbrain_rag_context("Look up SecondBrain notes about Biff routing", db_path=db)
    plan = plan_biff_turn("Look up SecondBrain notes about Biff routing")

    assert decision.action == "sqlite_fts"
    assert decision.max_live_tool_calls == 1
    assert decision.max_latency_ms <= 750
    assert decision.top_n == 3
    assert shape_fts_query("Look up SecondBrain notes about Biff routing") == '"secondbrain" OR "notes" OR "biff" OR "routing"'
    assert len(rows) == 2
    assert set(rows[0]) == {"path", "title", "snippet", "score", "source", "attribution"}
    assert rows[0]["source"] == "SecondBrain SQLite FTS"
    assert rows[0]["attribution"].startswith("SecondBrain SQLite FTS:")
    assert all(len(row["snippet"]) < 260 for row in rows)
    assert "Biff SecondBrain RAG Context" in context
    assert "[SecondBrain SQLite FTS:" in context
    assert "Projects/Biff.md" in context or "Areas/SecondBrain.md" in context
    assert plan.action == "secondbrain_lookup"
    assert plan.runtime == "secondbrain_lookup"
    assert plan.max_live_tool_calls == 1


def test_broad_deep_research_gets_background_continuation_not_rag():
    decision = classify_biff_rag_request(
        "Search my entire SecondBrain and Smart Connections history for every old decision about Biff and summarize all of it"
    )
    plan = plan_biff_turn(
        "Search my entire SecondBrain and Smart Connections history for every old decision about Biff and summarize all of it"
    )

    assert decision.action == "background"
    assert decision.background is True
    assert decision.max_live_tool_calls == 1
    assert plan.action == "background"
    assert plan.runtime == "background"
    assert plan.background is True


def test_smart_unavailable_or_errno_11_falls_back_to_sqlite(monkeypatch, tmp_path):
    db = tmp_path / "secondbrain.sqlite"
    _make_index(db)

    def locked_status(*args, **kwargs):
        raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

    monkeypatch.setattr("agent.biff_rag_router.smart_connections_status", locked_status)

    decision = classify_biff_rag_request("Use Smart Connections to find notes about routing")
    context = secondbrain_rag_context("Use Smart Connections to find notes about routing", db_path=db)

    assert decision.action == "sqlite_fts"
    assert decision.smart_allowed is False
    assert "Smart Connections skipped" in context
    assert "errno 11" in context
    assert "Biff SecondBrain RAG Context" in context


def test_slow_smart_status_helper_times_out_fail_closed(monkeypatch):
    class SlowIndexer:
        @staticmethod
        def smart_connections_status(*args, **kwargs):
            time.sleep(0.5)
            return {"state": "available", "files": 10, "readable_samples": 3}

    monkeypatch.setattr("agent.biff_rag_router._load_indexer_module", lambda: SlowIndexer)

    started = time.monotonic()
    status = smart_connections_status(timeout_ms=25)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    assert elapsed_ms < 200
    assert status == {"state": "unavailable", "reason": "timed_out", "timeout_ms": 25}


def test_smart_status_timeout_context_falls_back_to_sqlite_without_raw_payload(monkeypatch, tmp_path):
    db = tmp_path / "secondbrain.sqlite"
    _make_index(db)
    _RAG_CONTEXT_CACHE.clear()

    def timed_out_status(*args, **kwargs):
        return {"state": "unavailable", "reason": "timed_out", "timeout_ms": 25}

    monkeypatch.setattr("agent.biff_rag_router.smart_connections_status", timed_out_status)

    decision = classify_biff_rag_request("Use Smart Connections to find notes about routing")
    context = secondbrain_rag_context("Use Smart Connections to find notes about routing", db_path=db)

    assert decision.action == "sqlite_fts"
    assert decision.smart_allowed is False
    assert "Smart Connections skipped: timed_out" in context
    assert "[SecondBrain SQLite FTS:" in context
    assert "available', 'files'" not in context
    assert "Traceback" not in context


def test_result_merger_dedupes_limits_and_preserves_best_attribution():
    rows = merge_secondbrain_results(
        [
            {"path": "A.md", "title": "Alpha", "snippet": "same", "score": 2.0, "source": "SQLite"},
            {"path": "B.md", "title": "Beta", "snippet": "other", "score": 1.0, "source": "Smart provenance"},
        ],
        [
            {"path": "A.md", "title": "Alpha", "snippet": "same", "score": 0.5, "source": "SQLite refreshed"},
            {"path": "C.md", "title": "Gamma", "snippet": "third", "score": 3.0},
        ],
        limit=2,
    )

    assert [row["path"] for row in rows] == ["A.md", "B.md"]
    assert rows[0]["score"] == 0.5
    assert rows[0]["source"] == "SQLite refreshed"
    assert rows[0]["attribution"] == "SQLite refreshed: A.md"


def test_secondbrain_context_uses_cache_until_db_changes(monkeypatch, tmp_path):
    db = tmp_path / "secondbrain.sqlite"
    _make_index(db)
    _RAG_CONTEXT_CACHE.clear()
    calls = {"count": 0}

    def counted_status(*args, **kwargs):
        calls["count"] += 1
        return {"state": "absent", "reason": "test"}

    monkeypatch.setattr("agent.biff_rag_router.smart_connections_status", counted_status)

    first = secondbrain_rag_context("Look up SecondBrain notes about Biff routing", db_path=db)
    second = secondbrain_rag_context("Look up SecondBrain notes about Biff routing", db_path=db)

    assert first == second
    assert calls["count"] == 1
    assert _RAG_CONTEXT_CACHE


def test_smart_status_requires_available_provenance():
    assert smart_status_is_available({"state": "available", "files": 10, "readable_samples": 1}) is True
    assert smart_status_is_available({"state": "present_but_locked_or_dataless", "files": 10, "readable_samples": 0}) is False
    assert smart_status_is_available({"state": "absent"}) is False
