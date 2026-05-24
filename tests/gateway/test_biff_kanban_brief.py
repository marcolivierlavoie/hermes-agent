from pathlib import Path

import pytest

from gateway.biff_kanban_brief import build_kanban_brief, extract_k_ids, render_kanban_brief
from hermes_cli import kanban_db as kb


@pytest.fixture
def isolated_kanban(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_extract_k_ids_is_distinct_and_ordered():
    assert extract_k_ids("Check k-1331, K-1350, and K-1331 again") == ["K-1331", "K-1350"]


def test_kanban_brief_returns_compact_status_comments_events_and_attention(isolated_kanban):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Needs status", assignee="ranger", triage=False)
        task = kb.get_task(conn, tid)
        assert task is not None
        kid = task.display_id or task.id
        kb.add_comment(conn, tid, "vex", "Blocked: needs Marco approval before closeout.")
        conn.execute("UPDATE tasks SET status='blocked', result=? WHERE id=?", ("blocked on manual approval", tid))
        conn.commit()

        brief = build_kanban_brief([kid])
    finally:
        conn.close()

    assert brief["missing"] == []
    item = brief["items"][0]
    assert item["id"] == kid
    assert item["status"] == "blocked"
    assert item["assignee"] == "ranger"
    assert item["recent_comments"][-1]["author"] == "vex"
    assert "status=blocked" in item["attention"]
    assert "recent_comment_mentions_attention" in item["attention"]
    rendered = render_kanban_brief(brief)
    assert kid in rendered
    assert "attention=" in rendered
    assert "last comment" in rendered
